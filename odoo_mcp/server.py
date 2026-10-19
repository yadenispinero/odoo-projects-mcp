from __future__ import annotations

import base64
import binascii
import json
import os
import re
from datetime import date
from typing import Any
from urllib.parse import urljoin

from mcp.server.fastmcp import Context, FastMCP

from .admin import create_admin_routes
from .auth import BearerAuthMiddleware, UserDirectory, require_tool_access
from .config import InstanceDirectory, OdooInstance, load_instances
from .odoo_api import OdooApiError, OdooJson2

MAX_LIMIT = 100
TASK_STATES = frozenset({
    "01_in_progress", "02_changes_requested", "03_approved", "1_done", "1_canceled",
})
TASK_FIELDS = [
    "id", "name", "project_id", "description", "user_ids", "date_deadline", "stage_id", "active", "state",
]
PROJECT_FIELDS = [
    "id", "name", "description", "active", "privacy_visibility", "user_id", "partner_id",
    "date_start", "date",
]
PAGE_FIELDS = ["id", "name", "url", "is_published", "view_id", "website_id"]
BLOG_POST_FIELDS = [
    "id", "name", "subtitle", "content", "blog_id", "tag_ids", "author_id", "website_id",
    "website_meta_title", "website_meta_description", "website_meta_keywords", "website_meta_og_img",
    "seo_name", "is_published", "post_date", "published_date", "website_url",
]
BLOG_POST_SEARCH_FIELDS = [
    "id", "name", "subtitle", "blog_id", "tag_ids", "author_id", "website_id",
    "is_published", "post_date", "published_date", "website_url",
]


def _limit(value: int) -> int:
    if value < 1 or value > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _positive_id(value: int, label: str = "id") -> int:
    if value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _relation_id(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], int):
        return value[0] if value[0] > 0 else None
    return None


async def _blog_for_website(api: OdooJson2, blog_id: int, website_id: int) -> None:
    blog = await api.read("blog.blog", blog_id, ["id", "website_id"])
    blog_website_id = _relation_id(blog.get("website_id"))
    if blog_website_id not in (None, website_id):
        raise ValueError("blog_id must belong to the selected website or have no website assigned")


def _website_domain(website_id: int) -> list:
    """Match records assigned to this website and records shared across websites."""
    return ["|", ["website_id", "=", website_id], ["website_id", "=", False]]


async def _blog_post_on_website(api: OdooJson2, post_id: int, website_id: int,
                                fields: list[str]) -> dict[str, Any]:
    posts = await api.search_read("blog.post", [
        ["id", "=", post_id], *_website_domain(website_id),
    ], fields, 1)
    if not posts:
        raise OdooApiError(f"No blog post {post_id} found on website {website_id}")
    return posts[0]


def _image_info(image_base64: str, filename: str) -> tuple[bytes, str]:
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
        raise ValueError("filename must be a non-empty filename of at most 255 characters")
    if not isinstance(image_base64, str) or len(image_base64) > 7 * 1024 * 1024:
        raise ValueError("Image must be no larger than 5 MB")
    try:
        content = base64.b64decode(image_base64, validate=True)
    except (TypeError, binascii.Error) as exc:
        raise ValueError("image_base64 must contain valid base64 data") from exc
    if not content or len(content) > 5 * 1024 * 1024:
        raise ValueError("Image must be non-empty and no larger than 5 MB")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Image must be a PNG, JPEG, or WebP file")
    suffix = os.path.splitext(filename)[1].lower()
    matching_suffix = {"image/png": {".png"}, "image/jpeg": {".jpg", ".jpeg"},
                       "image/webp": {".webp"}}
    if suffix not in matching_suffix[mime]:
        raise ValueError("filename extension must match the PNG, JPEG, or WebP image data")
    return content, mime


async def _website_url_base(api: OdooJson2, website_id: int) -> str:
    website = await api.read("website", website_id, ["domain"])
    domain = website.get("domain") or api.instance.base_url
    if isinstance(domain, str) and domain and not domain.startswith(("https://", "http://")):
        domain = "https://" + domain
    return domain.rstrip("/") + "/"


def _absolute_blog_post_url(post: dict[str, Any], base_url: str) -> dict[str, Any]:
    path = post.get("website_url")
    if isinstance(path, str) and path:
        post["website_url"] = urljoin(base_url, path.lstrip("/"))
    return post


def _validate_date(value: str, label: str) -> str:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from exc
    return value


def _validate_visibility(value: str) -> None:
    allowed = {"followers", "invited_users", "employees", "portal"}
    if value not in allowed:
        raise ValueError(f"privacy_visibility must be one of: {', '.join(sorted(allowed))}")


def create_server(instances: dict[str, OdooInstance] | None = None,
                  users: UserDirectory | None = None) -> FastMCP:
    configured = instances if instances is not None else load_instances()
    user_directory = users if users is not None else UserDirectory.load(
        required=False, instance_names=configured.keys()
    )
    mcp_path = os.getenv("MCP_PATH", "/mcp")
    mcp = FastMCP("Odoo Projects and Website", host=os.getenv("MCP_HOST", "127.0.0.1"),
                  port=int(os.getenv("MCP_PORT", "8000")), streamable_http_path=mcp_path)

    def target(instance: str) -> OdooJson2:
        if instance not in configured:
            raise ValueError(f"Unknown Odoo instance {instance!r}; choose a configured instance explicitly")
        return OdooJson2(configured[instance])

    @mcp.tool()
    async def check_odoo_connection(instance: str, ctx: Context) -> dict[str, Any]:
        """Read the public Odoo version endpoint for a named instance; no writes are made."""
        require_tool_access("check_odoo_connection", instance)
        api = target(instance)
        try:
            version = await api.version()
            return {"instance": instance, "version": version.get("server_version", version.get("version")),
                    "version_info": version.get("server_version_info", version.get("version_info"))}
        finally:
            await api.close()

    @mcp.tool()
    async def search_projects(instance: str, ctx: Context, query: str = "", limit: int = 20,
                             include_archived: bool = False) -> list[dict[str, Any]]:
        """Search Odoo projects by name."""
        require_tool_access("search_projects", instance)
        limit = _limit(limit)
        domain = [["name", "ilike", query]] if query else []
        api = target(instance)
        try:
            return await api.search_read(
                "project.project", domain, PROJECT_FIELDS, limit,
                context={"active_test": not include_archived},
            )
        finally:
            await api.close()

    @mcp.tool()
    async def get_project(instance: str, project_id: int, ctx: Context) -> dict[str, Any]:
        """Get one project by ID."""
        require_tool_access("get_project", instance)
        api = target(instance)
        try:
            return await api.read("project.project", _positive_id(project_id, "project_id"), PROJECT_FIELDS,
                                  context={"active_test": False})
        finally:
            await api.close()

    @mcp.tool()
    async def create_project(
        instance: str,
        name: str,
        ctx: Context,
        description: str = "",
        privacy_visibility: str | None = None,
        user_id: int | None = None,
        partner_id: int | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
    ) -> dict[str, int]:
        """Create a project using an explicit allowlist of general project fields."""
        require_tool_access("create_project", instance)
        if not name.strip():
            raise ValueError("name must not be empty")
        values: dict[str, Any] = {"name": name.strip()}
        if description:
            values["description"] = description
        if privacy_visibility is not None:
            _validate_visibility(privacy_visibility)
            values["privacy_visibility"] = privacy_visibility
        if user_id is not None:
            values["user_id"] = _positive_id(user_id, "user_id")
        if partner_id is not None:
            values["partner_id"] = _positive_id(partner_id, "partner_id")
        if date_start:
            values["date_start"] = _validate_date(date_start, "date_start")
        if date_end:
            values["date"] = _validate_date(date_end, "date_end")
        api = target(instance)
        try:
            return {"project_id": await api.create("project.project", values)}
        finally:
            await api.close()

    @mcp.tool()
    async def update_project(
        instance: str,
        project_id: int,
        ctx: Context,
        name: str | None = None,
        description: str | None = None,
        active: bool | None = None,
        privacy_visibility: str | None = None,
        user_id: int | None = None,
        partner_id: int | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
    ) -> dict[str, bool]:
        """Update only supplied general project fields from the tool's allowlist."""
        require_tool_access("update_project", instance)
        values: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be empty")
            values["name"] = name.strip()
        if description is not None:
            values["description"] = description
        if active is not None:
            values["active"] = active
        if privacy_visibility is not None:
            _validate_visibility(privacy_visibility)
            values["privacy_visibility"] = privacy_visibility
        if user_id is not None:
            values["user_id"] = _positive_id(user_id, "user_id")
        if partner_id is not None:
            values["partner_id"] = _positive_id(partner_id, "partner_id")
        if date_start is not None:
            values["date_start"] = _validate_date(date_start, "date_start") if date_start else False
        if date_end is not None:
            values["date"] = _validate_date(date_end, "date_end") if date_end else False
        if not values:
            raise ValueError("Provide at least one project field to update")
        api = target(instance)
        try:
            return {"updated": await api.write("project.project", _positive_id(project_id, "project_id"), values)}
        finally:
            await api.close()

    @mcp.tool()
    async def archive_project(instance: str, project_id: int, ctx: Context) -> dict[str, bool]:
        """Archive a project and all its tasks by changing its active flag."""
        require_tool_access("archive_project", instance)
        api = target(instance)
        try:
            return {"archived": await api.write(
                "project.project", _positive_id(project_id, "project_id"), {"active": False}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def unarchive_project(instance: str, project_id: int, ctx: Context) -> dict[str, bool]:
        """Restore a project and all its tasks, including tasks archived individually beforehand."""
        require_tool_access("unarchive_project", instance)
        api = target(instance)
        try:
            return {"unarchived": await api.write(
                "project.project", _positive_id(project_id, "project_id"), {"active": True}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def search_tasks(instance: str, ctx: Context, query: str = "", project_id: int | None = None,
                          limit: int = 20, include_archived: bool = False) -> list[dict[str, Any]]:
        """Search Odoo tasks by name and optional project."""
        require_tool_access("search_tasks", instance)
        domain: list = []
        if query:
            domain.append(["name", "ilike", query])
        if project_id is not None:
            domain.append(["project_id", "=", _positive_id(project_id, "project_id")])
        api = target(instance)
        try:
            return await api.search_read(
                "project.task", domain, TASK_FIELDS, _limit(limit),
                context={"active_test": not include_archived},
            )
        finally:
            await api.close()

    @mcp.tool()
    async def get_task(instance: str, task_id: int, ctx: Context) -> dict[str, Any]:
        """Get one task by ID."""
        require_tool_access("get_task", instance)
        api = target(instance)
        try:
            return await api.read("project.task", _positive_id(task_id, "task_id"), TASK_FIELDS,
                                  context={"active_test": False})
        finally:
            await api.close()

    @mcp.tool()
    async def create_task(instance: str, name: str, project_id: int, ctx: Context, description: str = "",
                          date_deadline: str | None = None,
                          user_ids: list[int] | None = None) -> dict[str, int]:
        """Create a task with optional assignees, description, and YYYY-MM-DD deadline."""
        require_tool_access("create_task", instance)
        if not name.strip():
            raise ValueError("name must not be empty")
        values: dict[str, Any] = {"name": name.strip(), "project_id": _positive_id(project_id, "project_id")}
        if description:
            values["description"] = description
        if date_deadline:
            values["date_deadline"] = date_deadline
        if user_ids is not None:
            values["user_ids"] = [[6, 0, [_positive_id(user_id, "user_id") for user_id in user_ids]]]
        api = target(instance)
        try:
            return {"task_id": await api.create("project.task", values)}
        finally:
            await api.close()

    @mcp.tool()
    async def update_task(instance: str, task_id: int, ctx: Context, name: str | None = None,
                          description: str | None = None, date_deadline: str | None = None,
                          project_id: int | None = None,
                          user_ids: list[int] | None = None) -> dict[str, bool]:
        """Update only the supplied task fields."""
        require_tool_access("update_task", instance)
        values: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be empty")
            values["name"] = name.strip()
        if description is not None:
            values["description"] = description
        if date_deadline is not None:
            values["date_deadline"] = date_deadline
        if project_id is not None:
            values["project_id"] = _positive_id(project_id, "project_id")
        if user_ids is not None:
            values["user_ids"] = [[6, 0, [_positive_id(user_id, "user_id") for user_id in user_ids]]]
        if not values:
            raise ValueError("Provide at least one field to update")
        api = target(instance)
        try:
            return {"updated": await api.write("project.task", _positive_id(task_id, "task_id"), values)}
        finally:
            await api.close()

    @mcp.tool()
    async def list_task_stages(instance: str, project_id: int, ctx: Context) -> list[dict[str, Any]]:
        """List active workflow stages assigned to a project."""
        require_tool_access("list_task_stages", instance)
        project_id = _positive_id(project_id, "project_id")
        api = target(instance)
        try:
            return await api.search_read(
                "project.task.type",
                [["project_ids", "=", project_id]],
                ["id", "name", "sequence", "fold"], 100, "sequence asc, id asc",
            )
        finally:
            await api.close()

    @mcp.tool()
    async def set_task_stage(instance: str, task_id: int, stage_id: int, ctx: Context) -> dict[str, bool]:
        """Move a task to a stage assigned to the same project."""
        require_tool_access("set_task_stage", instance)
        task_id = _positive_id(task_id, "task_id")
        stage_id = _positive_id(stage_id, "stage_id")
        api = target(instance)
        try:
            task = await api.read("project.task", task_id, ["id", "project_id"],
                                  context={"active_test": False})
            project_id = _relation_id(task.get("project_id"))
            if project_id is None:
                raise ValueError(f"Task {task_id} has no project, so a project stage cannot be selected")
            stage = await api.read("project.task.type", stage_id, ["id", "project_ids"])
            project_ids = stage.get("project_ids") or []
            if project_id not in project_ids:
                raise ValueError(f"Stage {stage_id} does not belong to project {project_id}")
            return {"updated": await api.write("project.task", task_id, {"stage_id": stage_id})}
        finally:
            await api.close()

    @mcp.tool()
    async def set_task_state(instance: str, task_id: int, state: str, ctx: Context) -> dict[str, bool]:
        """Set a task to one of Odoo 18/19's supported workflow states."""
        require_tool_access("set_task_state", instance)
        task_id = _positive_id(task_id, "task_id")
        if not isinstance(state, str) or state not in TASK_STATES:
            raise ValueError(f"state must be one of: {', '.join(sorted(TASK_STATES))}")
        api = target(instance)
        try:
            return {"updated": await api.write("project.task", task_id, {"state": state})}
        finally:
            await api.close()

    @mcp.tool()
    async def archive_task(instance: str, task_id: int, ctx: Context) -> dict[str, bool]:
        """Archive a task by changing only its active flag."""
        require_tool_access("archive_task", instance)
        api = target(instance)
        try:
            return {"archived": await api.write(
                "project.task", _positive_id(task_id, "task_id"), {"active": False}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def unarchive_task(instance: str, task_id: int, ctx: Context) -> dict[str, bool]:
        """Restore an archived task by changing only its active flag."""
        require_tool_access("unarchive_task", instance)
        api = target(instance)
        try:
            return {"unarchived": await api.write(
                "project.task", _positive_id(task_id, "task_id"), {"active": True}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def list_websites(instance: str, ctx: Context, limit: int = 100) -> list[dict[str, Any]]:
        """List Odoo websites so blog tools can target the correct site by ID."""
        require_tool_access("list_websites", instance)
        api = target(instance)
        try:
            return await api.search_read("website", [], ["id", "name", "domain"], _limit(limit), "id asc")
        finally:
            await api.close()

    @mcp.tool()
    async def search_blogs(instance: str, website_id: int, ctx: Context, query: str = "",
                           limit: int = 50) -> list[dict[str, Any]]:
        """List blogs on one website, optionally matching a name."""
        require_tool_access("search_blogs", instance)
        website_id = _positive_id(website_id, "website_id")
        domain: list = _website_domain(website_id)
        if query:
            domain.append(["name", "ilike", query])
        api = target(instance)
        try:
            return await api.search_read("blog.blog", domain, ["id", "name", "subtitle", "website_id"],
                                         _limit(limit), "name asc")
        finally:
            await api.close()

    @mcp.tool()
    async def search_blog_posts(instance: str, website_id: int, ctx: Context, query: str = "",
                                blog_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Search blog posts on the selected website; results include publish date and URL."""
        require_tool_access("search_blog_posts", instance)
        website_id = _positive_id(website_id, "website_id")
        domain: list = _website_domain(website_id)
        if blog_id is not None:
            domain.append(["blog_id", "=", _positive_id(blog_id, "blog_id")])
        if query:
            domain.extend(["|", ["name", "ilike", query], ["subtitle", "ilike", query]])
        api = target(instance)
        try:
            posts = await api.search_read("blog.post", domain, BLOG_POST_SEARCH_FIELDS, _limit(limit))
            base_url = await _website_url_base(api, website_id)
            return [_absolute_blog_post_url(post, base_url) for post in posts]
        finally:
            await api.close()

    @mcp.tool()
    async def get_blog_post(instance: str, website_id: int, post_id: int,
                            ctx: Context, lang: str | None = None) -> dict[str, Any]:
        """Read an allowlisted set of fields from one post on the selected website."""
        require_tool_access("get_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        api = target(instance)
        try:
            post = await _blog_post_on_website(api, post_id, website_id, BLOG_POST_FIELDS)
            if lang is not None:
                post = await api.read("blog.post", post_id, BLOG_POST_FIELDS, context={"lang": lang})
            return _absolute_blog_post_url(post, await _website_url_base(api, website_id))
        finally:
            await api.close()

    @mcp.tool()
    async def set_blog_post_translation(
        instance: str, website_id: int, post_id: int, lang: str, name: str, subtitle: str,
        content: str, website_meta_title: str, website_meta_description: str,
        website_meta_keywords: str, ctx: Context, seo_name: str | None = None,
    ) -> dict[str, bool]:
        """Write a blog post translation in one active language for the selected website."""
        require_tool_access("set_blog_post_translation", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        if not isinstance(lang, str) or not re.fullmatch(r"[a-z]{2,3}_[A-Z]{2,3}", lang):
            raise ValueError("lang must be a language code such as es_ES or de_DE")
        api = target(instance)
        try:
            post = await _blog_post_on_website(api, post_id, website_id, ["id"])
            website = await api.read("website", website_id, ["language_ids"])
            language_ids = [item[0] for item in website.get("language_ids", [])
                            if isinstance(item, (list, tuple)) and item and isinstance(item[0], int)]
            languages = await api.search_read("res.lang", [["active", "=", True]], ["id", "code"], 1000)
            active_codes = {item.get("code") for item in languages if item.get("id") in language_ids}
            if lang not in active_codes:
                raise ValueError(f"Language {lang} is not active on website {website_id}")
            values: dict[str, Any] = {
                "name": name, "subtitle": subtitle, "content": content,
                "website_meta_title": website_meta_title,
                "website_meta_description": website_meta_description,
                "website_meta_keywords": website_meta_keywords,
            }
            if seo_name is not None:
                values["seo_name"] = seo_name
            return {"updated": await api.write("blog.post", post["id"], values, context={"lang": lang})}
        finally:
            await api.close()

    @mcp.tool()
    async def set_blog_post_cover(
        instance: str, website_id: int, post_id: int, image_base64: str, filename: str,
        ctx: Context, set_og_image: bool = True,
    ) -> dict[str, str]:
        """Attach a public PNG, JPEG, or WebP image as a blog post cover."""
        require_tool_access("set_blog_post_cover", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        content, mime = _image_info(image_base64, filename)
        api = target(instance)
        try:
            post = await _blog_post_on_website(api, post_id, website_id,
                                               ["id", "cover_properties"])
            cover = post.get("cover_properties") or "{}"
            if isinstance(cover, str):
                try:
                    cover = json.loads(cover)
                except json.JSONDecodeError as exc:
                    raise OdooApiError("Blog post cover properties are not valid JSON") from exc
            if not isinstance(cover, dict):
                raise OdooApiError("Blog post cover properties have an unexpected format")
            attachment_id = await api.create("ir.attachment", {
                "name": filename.strip(), "type": "binary", "datas": base64.b64encode(content).decode("ascii"),
                "mimetype": mime, "public": True, "res_model": "blog.post", "res_id": post_id,
            })
            image_url = f"/web/image/ir.attachment/{attachment_id}/datas"
            cover["background-image"] = f"url({image_url})"
            values: dict[str, Any] = {"cover_properties": json.dumps(cover, ensure_ascii=False)}
            if set_og_image:
                values["website_meta_og_img"] = image_url
            await api.write("blog.post", post_id, values)
            return {"url": urljoin(await _website_url_base(api, website_id), image_url.lstrip("/"))}
        finally:
            await api.close()

    @mcp.tool()
    async def create_blog_post(instance: str, website_id: int, blog_id: int, name: str,
                               ctx: Context, subtitle: str | None = None, content: str | None = None,
                               tag_ids: list[int] | None = None, author_id: int | None = None,
                               website_meta_title: str | None = None,
                               website_meta_description: str | None = None,
                               website_meta_keywords: str | None = None,
                               website_meta_og_img: str | None = None,
                               seo_name: str | None = None) -> dict[str, int]:
        """Create an unpublished blog post using only the documented content and SEO fields."""
        require_tool_access("create_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        blog_id = _positive_id(blog_id, "blog_id")
        if not name.strip():
            raise ValueError("name must not be empty")
        values: dict[str, Any] = {"name": name.strip(), "blog_id": blog_id, "is_published": False}
        for field, value in {
            "subtitle": subtitle, "content": content, "website_meta_title": website_meta_title,
            "website_meta_description": website_meta_description,
            "website_meta_keywords": website_meta_keywords, "website_meta_og_img": website_meta_og_img,
            "seo_name": seo_name,
        }.items():
            if value is not None:
                values[field] = value
        if tag_ids is not None:
            values["tag_ids"] = [[6, 0, [_positive_id(tag_id, "tag_id") for tag_id in tag_ids]]]
        if author_id is not None:
            values["author_id"] = _positive_id(author_id, "author_id")
        api = target(instance)
        try:
            await _blog_for_website(api, blog_id, website_id)
            return {"post_id": await api.create("blog.post", values)}
        finally:
            await api.close()

    @mcp.tool()
    async def update_blog_post(instance: str, website_id: int, post_id: int, ctx: Context,
                               name: str | None = None, subtitle: str | None = None,
                               content: str | None = None, blog_id: int | None = None,
                               tag_ids: list[int] | None = None, author_id: int | None = None,
                               website_meta_title: str | None = None,
                               website_meta_description: str | None = None,
                               website_meta_keywords: str | None = None,
                               website_meta_og_img: str | None = None,
                               seo_name: str | None = None) -> dict[str, bool]:
        """Edit allowlisted blog content and SEO fields; publication cannot be changed here."""
        require_tool_access("update_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        values: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be empty")
            values["name"] = name.strip()
        for field, value in {
            "subtitle": subtitle, "content": content, "website_meta_title": website_meta_title,
            "website_meta_description": website_meta_description,
            "website_meta_keywords": website_meta_keywords, "website_meta_og_img": website_meta_og_img,
            "seo_name": seo_name,
        }.items():
            if value is not None:
                values[field] = value
        if blog_id is not None:
            blog_id = _positive_id(blog_id, "blog_id")
            values["blog_id"] = blog_id
        if tag_ids is not None:
            values["tag_ids"] = [[6, 0, [_positive_id(tag_id, "tag_id") for tag_id in tag_ids]]]
        if author_id is not None:
            values["author_id"] = _positive_id(author_id, "author_id")
        if not values:
            raise ValueError("Provide at least one blog post field to update")
        api = target(instance)
        try:
            await _blog_post_on_website(api, post_id, website_id, ["id"])
            if blog_id is not None:
                await _blog_for_website(api, blog_id, website_id)
            return {"updated": await api.write("blog.post", post_id, values)}
        finally:
            await api.close()

    @mcp.tool()
    async def publish_blog_post(instance: str, website_id: int, post_id: int,
                                ctx: Context) -> dict[str, bool]:
        """Publish a blog post on the selected website. Requires blog_publisher."""
        require_tool_access("publish_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        api = target(instance)
        try:
            await _blog_post_on_website(api, post_id, website_id, ["id"])
            return {"published": await api.write("blog.post", post_id, {"is_published": True})}
        finally:
            await api.close()

    @mcp.tool()
    async def unpublish_blog_post(instance: str, website_id: int, post_id: int,
                                  ctx: Context) -> dict[str, bool]:
        """Unpublish a blog post on the selected website. Requires blog_publisher."""
        require_tool_access("unpublish_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        api = target(instance)
        try:
            await _blog_post_on_website(api, post_id, website_id, ["id"])
            return {"unpublished": await api.write("blog.post", post_id, {"is_published": False})}
        finally:
            await api.close()

    @mcp.tool()
    async def search_pages(instance: str, ctx: Context, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Search website pages by name or URL."""
        require_tool_access("search_pages", instance)
        domain: list = []
        if query:
            domain = ["|", ["name", "ilike", query], ["url", "ilike", query]]
        api = target(instance)
        try:
            return await api.search_read("website.page", domain, PAGE_FIELDS, _limit(limit))
        finally:
            await api.close()

    @mcp.tool()
    async def get_page(instance: str, page_id: int, ctx: Context) -> dict[str, Any]:
        """Get a website page's metadata by ID."""
        require_tool_access("get_page", instance)
        api = target(instance)
        try:
            return await api.read("website.page", _positive_id(page_id, "page_id"), PAGE_FIELDS)
        finally:
            await api.close()

    @mcp.tool()
    async def update_page_content(instance: str, page_id: int, html: str, ctx: Context) -> dict[str, bool]:
        """Replace a page's view architecture with supplied HTML. Test on a disposable page first."""
        require_tool_access("update_page_content", instance)
        if not html.strip():
            raise ValueError("html must not be empty")
        api = target(instance)
        try:
            page = await api.read("website.page", _positive_id(page_id, "page_id"), ["id", "view_id"])
            view_id = page.get("view_id")
            if not isinstance(view_id, list) or not view_id or not isinstance(view_id[0], int):
                raise OdooApiError("Page has no readable website view")
            return {"updated": await api.write("ir.ui.view", view_id[0], {"arch": html})}
        finally:
            await api.close()

    @mcp.tool()
    async def publish_page(instance: str, page_id: int, ctx: Context) -> dict[str, bool]:
        """Publish a website page as an explicit action."""
        require_tool_access("publish_page", instance)
        api = target(instance)
        try:
            return {"published": await api.write("website.page", _positive_id(page_id, "page_id"),
                                                  {"is_published": True})}
        finally:
            await api.close()

    @mcp.tool()
    async def unpublish_page(instance: str, page_id: int, ctx: Context) -> dict[str, bool]:
        """Unpublish a website page as an explicit action."""
        require_tool_access("unpublish_page", instance)
        api = target(instance)
        try:
            return {"unpublished": await api.write("website.page", _positive_id(page_id, "page_id"),
                                                    {"is_published": False})}
        finally:
            await api.close()

    return mcp


def main() -> None:
    import uvicorn

    source_instances = load_instances()
    instance_directory = InstanceDirectory(
        os.getenv("ODOO_INSTANCES_STORE", "/data/odoo-instances.json"), source_instances
    )
    instances = instance_directory.active_instances()
    users = UserDirectory.load(required=True, instance_names=instance_directory.names(active_only=False))
    mcp = create_server(instances=instances, users=users)
    app = mcp.streamable_http_app()

    def refresh_instances(active_instances):
        instances.clear()
        instances.update(active_instances)

    app.router.routes.extend(create_admin_routes(
        users, instances.keys(), instance_directory=instance_directory,
        on_instances_changed=refresh_instances,
    ))
    app.add_middleware(BearerAuthMiddleware, directory=users, mcp_path=os.getenv("MCP_PATH", "/mcp"))
    uvicorn.run(app, host=os.getenv("MCP_HOST", "127.0.0.1"), port=int(os.getenv("MCP_PORT", "8000")))
