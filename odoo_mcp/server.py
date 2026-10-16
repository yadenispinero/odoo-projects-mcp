from __future__ import annotations

import os
from datetime import date
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .admin import create_admin_routes
from .auth import BearerAuthMiddleware, UserDirectory, require_tool_access
from .config import OdooInstance, load_instances
from .odoo_api import OdooApiError, OdooJson2

MAX_LIMIT = 100
TASK_FIELDS = ["id", "name", "project_id", "description", "user_ids", "date_deadline", "stage_id"]
PROJECT_FIELDS = [
    "id", "name", "description", "active", "privacy_visibility", "user_id", "partner_id",
    "date_start", "date",
]
PAGE_FIELDS = ["id", "name", "url", "is_published", "view_id", "website_id"]


def _limit(value: int) -> int:
    if value < 1 or value > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _positive_id(value: int, label: str = "id") -> int:
    if value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


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
    user_directory = users if users is not None else UserDirectory.load(required=False)
    mcp_path = os.getenv("MCP_PATH", "/mcp")
    mcp = FastMCP("Odoo Projects and Website", host=os.getenv("MCP_HOST", "127.0.0.1"),
                  port=int(os.getenv("MCP_PORT", "8000")), streamable_http_path=mcp_path)

    def target(instance: str) -> OdooJson2:
        if instance not in configured:
            raise ValueError(f"Unknown Odoo instance {instance!r}; choose a configured instance explicitly")
        return OdooJson2(configured[instance])

    @mcp.tool()
    async def check_odoo_connection(instance: str, ctx: Context) -> dict[str, Any]:
        """Read the public Odoo version endpoint for a named instance; no writes or credentials are used."""
        require_tool_access(ctx.headers, user_directory, "check_odoo_connection")
        api = target(instance)
        try:
            version = await api.version()
            return {"instance": instance, "version": version.get("server_version", version.get("version")),
                    "version_info": version.get("server_version_info", version.get("version_info"))}
        finally:
            await api.close()

    @mcp.tool()
    async def search_projects(instance: str, ctx: Context, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Search Odoo projects by name."""
        require_tool_access(ctx.headers, user_directory, "search_projects")
        limit = _limit(limit)
        domain = [["name", "ilike", query]] if query else []
        api = target(instance)
        try:
            return await api.search_read("project.project", domain, PROJECT_FIELDS, limit)
        finally:
            await api.close()

    @mcp.tool()
    async def get_project(instance: str, project_id: int, ctx: Context) -> dict[str, Any]:
        """Get one project by ID."""
        require_tool_access(ctx.headers, user_directory, "get_project")
        api = target(instance)
        try:
            return await api.read("project.project", _positive_id(project_id, "project_id"), PROJECT_FIELDS)
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
        require_tool_access(ctx.headers, user_directory, "create_project")
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
        require_tool_access(ctx.headers, user_directory, "update_project")
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
    async def search_tasks(instance: str, ctx: Context, query: str = "", project_id: int | None = None,
                          limit: int = 20) -> list[dict[str, Any]]:
        """Search Odoo tasks by name and optional project."""
        require_tool_access(ctx.headers, user_directory, "search_tasks")
        domain: list = []
        if query:
            domain.append(["name", "ilike", query])
        if project_id is not None:
            domain.append(["project_id", "=", _positive_id(project_id, "project_id")])
        api = target(instance)
        try:
            return await api.search_read("project.task", domain, TASK_FIELDS, _limit(limit))
        finally:
            await api.close()

    @mcp.tool()
    async def get_task(instance: str, task_id: int, ctx: Context) -> dict[str, Any]:
        """Get one task by ID."""
        require_tool_access(ctx.headers, user_directory, "get_task")
        api = target(instance)
        try:
            return await api.read("project.task", _positive_id(task_id, "task_id"), TASK_FIELDS)
        finally:
            await api.close()

    @mcp.tool()
    async def create_task(instance: str, name: str, project_id: int, ctx: Context, description: str = "",
                          date_deadline: str | None = None) -> dict[str, int]:
        """Create a task with a name, project, optional description, and optional YYYY-MM-DD deadline."""
        require_tool_access(ctx.headers, user_directory, "create_task")
        if not name.strip():
            raise ValueError("name must not be empty")
        values: dict[str, Any] = {"name": name.strip(), "project_id": _positive_id(project_id, "project_id")}
        if description:
            values["description"] = description
        if date_deadline:
            values["date_deadline"] = date_deadline
        api = target(instance)
        try:
            return {"task_id": await api.create("project.task", values)}
        finally:
            await api.close()

    @mcp.tool()
    async def update_task(instance: str, task_id: int, ctx: Context, name: str | None = None,
                          description: str | None = None, date_deadline: str | None = None,
                          project_id: int | None = None) -> dict[str, bool]:
        """Update only the supplied task fields."""
        require_tool_access(ctx.headers, user_directory, "update_task")
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
        if not values:
            raise ValueError("Provide at least one field to update")
        api = target(instance)
        try:
            return {"updated": await api.write("project.task", _positive_id(task_id, "task_id"), values)}
        finally:
            await api.close()

    @mcp.tool()
    async def search_pages(instance: str, ctx: Context, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Search website pages by name or URL."""
        require_tool_access(ctx.headers, user_directory, "search_pages")
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
        require_tool_access(ctx.headers, user_directory, "get_page")
        api = target(instance)
        try:
            return await api.read("website.page", _positive_id(page_id, "page_id"), PAGE_FIELDS)
        finally:
            await api.close()

    @mcp.tool()
    async def update_page_content(instance: str, page_id: int, html: str, ctx: Context) -> dict[str, bool]:
        """Replace a page's view architecture with supplied HTML. Test on a disposable page first."""
        require_tool_access(ctx.headers, user_directory, "update_page_content")
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
        require_tool_access(ctx.headers, user_directory, "publish_page")
        api = target(instance)
        try:
            return {"published": await api.write("website.page", _positive_id(page_id, "page_id"),
                                                  {"is_published": True})}
        finally:
            await api.close()

    @mcp.tool()
    async def unpublish_page(instance: str, page_id: int, ctx: Context) -> dict[str, bool]:
        """Unpublish a website page as an explicit action."""
        require_tool_access(ctx.headers, user_directory, "unpublish_page")
        api = target(instance)
        try:
            return {"unpublished": await api.write("website.page", _positive_id(page_id, "page_id"),
                                                    {"is_published": False})}
        finally:
            await api.close()

    return mcp


def main() -> None:
    import uvicorn

    users = UserDirectory.load(required=True)
    mcp = create_server(users=users)
    app = mcp.streamable_http_app()
    app.router.routes.extend(create_admin_routes(users))
    app.add_middleware(BearerAuthMiddleware, directory=users, mcp_path=os.getenv("MCP_PATH", "/mcp"))
    uvicorn.run(app, host=os.getenv("MCP_HOST", "127.0.0.1"), port=int(os.getenv("MCP_PORT", "8000")))
