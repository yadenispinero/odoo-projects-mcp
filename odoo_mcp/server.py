from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import socket
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import urljoin

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from .admin import AdminPortMiddleware, create_admin_routes
from .auth import BearerAuthMiddleware, UserDirectory, require_tool_access
from .config import InstanceDirectory, OdooInstance, load_instances
from .marked_text import MarkedTextError, convert_marked_text, pair_translation_terms
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
CONTENT_FORMATS = frozenset({"html", "marked_text"})
BLOG_POST_SEARCH_FIELDS = [
    "id", "name", "subtitle", "blog_id", "tag_ids", "author_id", "website_id",
    "is_published", "post_date", "published_date", "website_url",
]
PURCHASE_ORDER_FIELDS = [
    "id", "name", "partner_id", "partner_ref", "date_order", "state", "invoice_status",
    "invoice_ids", "amount_untaxed", "amount_tax", "amount_total", "currency_id", "user_id",
]
PURCHASE_LINE_FIELDS_XMLRPC = [
    "id", "order_id", "product_id", "name", "product_qty", "product_uom", "price_unit",
    "date_planned", "taxes_id", "price_subtotal", "price_total",
]
PURCHASE_LINE_FIELDS_JSON2 = [
    "id", "order_id", "product_id", "name", "product_qty", "product_uom_id", "price_unit",
    "date_planned", "tax_ids", "price_subtotal", "price_total",
]
VENDOR_BILL_FIELDS = ["id", "name", "state", "move_type", "invoice_date", "ref", "amount_total"]
SUPPORT_TICKET_FIELDS = [
    "id", "name", "description", "priority", "team_id", "user_id", "partner_id", "stage_id",
]
SUPPORT_TICKET_PRIORITIES = frozenset({"0", "1", "2", "3"})
# Chatter entries that are real conversation: received/sent emails and posted
# messages or internal notes. Excludes "notification" (field tracking, system
# events). "email_outgoing" only exists from Odoo 17; older versions ignore it.
SUPPORT_TICKET_MESSAGE_TYPES = ["email", "comment", "email_outgoing"]
SUPPORT_TICKET_MESSAGE_FIELDS = [
    "id", "date", "author_id", "email_from", "message_type", "subtype_id", "is_internal", "body",
]
# Upper bound for chatter rows read to count messages of one search page
# (at most MAX_LIMIT tickets); exceeding it is reported instead of undercounting.
MAX_MESSAGES_COUNTED = 20000


def _limit(value: int) -> int:
    if value < 1 or value > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _offset(value: int) -> int:
    if value < 0:
        raise ValueError("offset must be zero or a positive integer")
    return value


def _positive_id(value: int, label: str = "id") -> int:
    if value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _positive_number(value: Any, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {qualifier}")
    return float(value)


def _relation_ids(values: Any) -> list[int]:
    if not isinstance(values, (list, tuple)):
        return []
    ids: list[int] = []
    for value in values:
        record_id = value if isinstance(value, int) and not isinstance(value, bool) else (
            value[0] if isinstance(value, (list, tuple)) and value
            and isinstance(value[0], int) and not isinstance(value[0], bool) else None
        )
        if record_id is not None and record_id > 0:
            ids.append(record_id)
    return ids


def _purchase_datetime(value: str, label: str) -> str:
    return _validate_date(value, label) + " 00:00:00"


async def _purchase_order(api: OdooJson2, order_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    return await api.read("purchase.order", _positive_id(order_id, "purchase_order_id"),
                          fields or PURCHASE_ORDER_FIELDS)


async def _require_editable_purchase_order(api: OdooJson2, order_id: int) -> dict[str, Any]:
    order = await _purchase_order(api, order_id, ["id", "state"])
    if order.get("state") not in {"draft", "sent"}:
        raise ValueError("Only draft or sent RFQs can be edited through the MCP")
    return order


async def _purchase_line_values(
    api: OdooJson2, product_id: int, quantity: Any, unit_price: Any,
    expected_date: str, description: str | None = None,
    fiscal_position_id: int | None = None,
) -> dict[str, Any]:
    product_id = _positive_id(product_id, "product_id")
    quantity = _positive_number(quantity, "quantity")
    unit_price = _positive_number(unit_price, "unit_price", allow_zero=True)
    expected_datetime = _purchase_datetime(expected_date, "expected_date")
    products = await api.search_read(
        "product.product", [["id", "=", product_id], ["purchase_ok", "=", True]],
        ["id", "name", "uom_id", "supplier_taxes_id"], 1,
    )
    if not products:
        raise ValueError(f"Product {product_id} does not exist or is not purchasable")
    product = products[0]
    unit_id = _relation_id(product.get("uom_id"))
    if unit_id is None:
        raise ValueError(f"Product {product_id} has no usable unit of measure")
    tax_ids = _relation_ids(product.get("supplier_taxes_id"))
    if fiscal_position_id and tax_ids:
        mapped = await api.call(
            "account.fiscal.position", "map_tax",
            {"ids": [_positive_id(fiscal_position_id, "fiscal_position_id")], "taxes": tax_ids},
        )
        if not isinstance(mapped, (list, tuple)):
            raise OdooApiError("Odoo returned an unexpected fiscal-position tax mapping")
        tax_ids = _relation_ids(mapped)

    values: dict[str, Any] = {
        "product_id": product_id,
        "product_qty": quantity,
        "price_unit": unit_price,
        "name": description.strip() if isinstance(description, str) and description.strip()
        else product.get("name", ""),
        "date_planned": expected_datetime,
    }
    if api.instance.api_type == "json2":
        values["product_uom_id"] = unit_id
        values["tax_ids"] = [[6, 0, tax_ids]]
    else:
        values["product_uom"] = unit_id
        values["taxes_id"] = [[6, 0, tax_ids]]
    return values


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


async def _blog_website_languages(api: OdooJson2, website_id: int) -> tuple[str, set[str]]:
    website = await api.read("website", website_id, ["language_ids", "default_lang_id"])
    language_ids = {
        item if isinstance(item, int) and not isinstance(item, bool)
        else item[0] for item in website.get("language_ids", [])
        if (isinstance(item, int) and not isinstance(item, bool))
        or (isinstance(item, (list, tuple)) and item
            and isinstance(item[0], int) and not isinstance(item[0], bool))
    }
    languages = await api.search_read("res.lang", [["active", "=", True]], ["id", "code"], 1000)
    active_website_codes = {
        item.get("code") for item in languages if item.get("id") in language_ids and item.get("code")
    }
    default_lang_id = _relation_id(website.get("default_lang_id"))
    default_lang = next((item.get("code") for item in languages if item.get("id") == default_lang_id), None)
    if not default_lang or default_lang not in active_website_codes:
        raise ValueError(f"Website {website_id} has no active primary language")
    return default_lang, active_website_codes


def _translation_terms_by_source(result: Any, source_lang: str, lang: str) -> dict[str, str]:
    if not isinstance(result, (list, tuple)) or not result or not isinstance(result[0], list):
        raise OdooApiError("Odoo returned an unexpected get_field_translations response")
    grouped: dict[str, dict[str, str]] = {}
    for row in result[0]:
        if not isinstance(row, dict) or not isinstance(row.get("source"), str):
            continue
        source = row["source"]
        row_lang = row.get("lang")
        value = row.get("value")
        if row_lang in (source_lang, lang):
            grouped.setdefault(source, {})[row_lang] = value if isinstance(value, str) and value else source
    return {
        values.get(source_lang, source): values.get(lang, source)
        for source, values in grouped.items()
    }


def _blog_content(content: str | None, content_format: str,
                  name: str | None) -> tuple[str | None, str | None]:
    """Return (content HTML, name) after applying the requested content format."""
    if content_format not in CONTENT_FORMATS:
        raise ValueError('content_format must be "html" or "marked_text"')
    if content_format == "html" or content is None:
        return content, name
    try:
        document = convert_marked_text(content)
    except MarkedTextError as exc:
        raise ValueError(str(exc)) from exc
    return document.html, name if name is not None else document.title


async def _primary_lang_context(api: OdooJson2, website_id: int, lang: str | None) -> dict[str, str] | None:
    """Return a write context for lang, which must be the website's primary language."""
    if lang is None:
        return None
    source_lang, _ = await _blog_website_languages(api, website_id)
    if lang != source_lang:
        raise ValueError(
            f"Website {website_id} primary language is {source_lang}, not {lang}; write the post in "
            f"{source_lang} and add {lang} with set_blog_post_translation"
        )
    return {"lang": source_lang}


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


_COVER_IMAGE_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


def _cover_image_url(cover_properties: Any, base_url: str) -> str | None:
    """Return the absolute URL of a blog post cover image, or None without one."""
    if isinstance(cover_properties, str):
        try:
            cover_properties = json.loads(cover_properties or "{}")
        except json.JSONDecodeError:
            return None
    if not isinstance(cover_properties, dict):
        return None
    match = _COVER_IMAGE_URL.search(str(cover_properties.get("background-image") or ""))
    return urljoin(base_url, match.group(1).lstrip("/")) if match else None


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


def _validate_lang_code(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2}|_[0-9]{3})?(?:@[a-z]+)?", value):
        raise ValueError("lang must use Odoo res.lang.code format, for example es_ES, es_419, or sr@latin")
    return value


def _future_blog_date(value: str) -> tuple[datetime, str]:
    if not isinstance(value, str):
        raise ValueError("post_date must be an ISO 8601 datetime with a timezone or UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("post_date must be an ISO 8601 datetime with a timezone or UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("post_date must include a timezone or Z for UTC")
    utc_date = parsed.astimezone(timezone.utc)
    if utc_date < datetime.now(timezone.utc) + timedelta(minutes=10):
        raise ValueError("post_date must be at least 10 minutes in the future")
    odoo_datetime = utc_date.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    return utc_date, odoo_datetime


def _odoo_datetime_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
                  port=int(os.getenv("MCP_PORT", "8000")), streamable_http_path=mcp_path,
                  stateless_http=True, json_response=True)

    def target(instance: str, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> OdooJson2:
        if instance not in configured:
            raise ValueError(f"Unknown Odoo instance {instance!r}; choose a configured instance explicitly")
        return OdooJson2(configured[instance], company=company)

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
    async def list_companies(instance: str, ctx: Context) -> list[dict[str, Any]]:
        """List companies available to the configured Odoo user for explicit selection."""
        require_tool_access("list_companies", instance)
        if instance not in configured:
            raise ValueError(f"Unknown Odoo instance {instance!r}; choose a configured instance explicitly")
        api = OdooJson2(configured[instance], enforce_company=False)
        try:
            return await api.search_read("res.company", [], ["id", "name"], 1000, "name asc, id asc")
        finally:
            await api.close()

    @mcp.tool()
    async def search_projects(instance: str, ctx: Context, query: str = "", limit: int = 20,
                             include_archived: bool = False, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Search Odoo projects by name."""
        require_tool_access("search_projects", instance)
        limit = _limit(limit)
        domain = [["name", "ilike", query]] if query else []
        api = target(instance, company)
        try:
            return await api.search_read(
                "project.project", domain, PROJECT_FIELDS, limit,
                context={"active_test": not include_archived},
            )
        finally:
            await api.close()

    @mcp.tool()
    async def get_project(instance: str, project_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Get one project by ID."""
        require_tool_access("get_project", instance)
        api = target(instance, company)
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
    company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, int]:
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
        api = target(instance, company)
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
    company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
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
        api = target(instance, company)
        try:
            return {"updated": await api.write("project.project", _positive_id(project_id, "project_id"), values)}
        finally:
            await api.close()

    @mcp.tool()
    async def archive_project(instance: str, project_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Archive a project and all its tasks by changing its active flag."""
        require_tool_access("archive_project", instance)
        api = target(instance, company)
        try:
            return {"archived": await api.write(
                "project.project", _positive_id(project_id, "project_id"), {"active": False}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def unarchive_project(instance: str, project_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Restore a project and all its tasks, including tasks archived individually beforehand."""
        require_tool_access("unarchive_project", instance)
        api = target(instance, company)
        try:
            return {"unarchived": await api.write(
                "project.project", _positive_id(project_id, "project_id"), {"active": True}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def search_tasks(instance: str, ctx: Context, query: str = "", project_id: int | None = None,
                          limit: int = 20, include_archived: bool = False, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Search Odoo tasks by name and optional project."""
        require_tool_access("search_tasks", instance)
        domain: list = []
        if query:
            domain.append(["name", "ilike", query])
        if project_id is not None:
            domain.append(["project_id", "=", _positive_id(project_id, "project_id")])
        api = target(instance, company)
        try:
            return await api.search_read(
                "project.task", domain, TASK_FIELDS, _limit(limit),
                context={"active_test": not include_archived},
            )
        finally:
            await api.close()

    @mcp.tool()
    async def get_task(instance: str, task_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Get one task by ID."""
        require_tool_access("get_task", instance)
        api = target(instance, company)
        try:
            return await api.read("project.task", _positive_id(task_id, "task_id"), TASK_FIELDS,
                                  context={"active_test": False})
        finally:
            await api.close()

    async def _support_ticket_message_counts(api: OdooJson2, ticket_ids: list[int]) -> dict[int, int]:
        if not ticket_ids:
            return {}
        rows = await api.search_read(
            "mail.message",
            [["model", "=", "helpdesk.ticket"], ["res_id", "in", ticket_ids],
             ["message_type", "in", SUPPORT_TICKET_MESSAGE_TYPES]],
            ["res_id"], MAX_MESSAGES_COUNTED + 1, order="id asc",
        )
        if len(rows) > MAX_MESSAGES_COUNTED:
            raise OdooApiError("Too many ticket messages to count; use a smaller limit")
        counts = dict.fromkeys(ticket_ids, 0)
        for row in rows:
            if row.get("res_id") in counts:
                counts[row["res_id"]] += 1
        return counts

    @mcp.tool()
    async def search_support_tickets(instance: str, ctx: Context, query: str = "",
                                     team_id: int | None = None, limit: int = 20, offset: int = 0,
                                     include_description: bool = True, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Search installed Odoo Helpdesk tickets by subject and optional team, newest first.

        Each ticket includes message_count: emails, posted messages and internal notes in its
        chatter (system notifications excluded). Use offset to page past the first results and
        include_description=false to omit the long HTML description.
        """
        require_tool_access("search_support_tickets", instance)
        domain: list = []
        if query:
            domain.append(["name", "ilike", query])
        if team_id is not None:
            domain.append(["team_id", "=", _positive_id(team_id, "team_id")])
        fields = [f for f in SUPPORT_TICKET_FIELDS if include_description or f != "description"]
        api = target(instance, company)
        try:
            tickets = await api.search_read(
                "helpdesk.ticket", domain, fields, _limit(limit), offset=_offset(offset)
            )
            counts = await _support_ticket_message_counts(api, [t["id"] for t in tickets])
            for ticket in tickets:
                ticket["message_count"] = counts.get(ticket["id"], 0)
            return tickets
        finally:
            await api.close()

    @mcp.tool()
    async def get_support_ticket(instance: str, ticket_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Read one installed Odoo Helpdesk ticket by ID, including its message_count."""
        require_tool_access("get_support_ticket", instance)
        ticket_id = _positive_id(ticket_id, "ticket_id")
        api = target(instance, company)
        try:
            ticket = await api.read("helpdesk.ticket", ticket_id, SUPPORT_TICKET_FIELDS)
            counts = await _support_ticket_message_counts(api, [ticket_id])
            ticket["message_count"] = counts.get(ticket_id, 0)
            return ticket
        finally:
            await api.close()

    @mcp.tool()
    async def get_support_ticket_messages(instance: str, ticket_id: int, ctx: Context,
                                          limit: int = 50, offset: int = 0, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Read a Helpdesk ticket's conversation, oldest first.

        Returns emails, posted messages and internal notes (is_internal=true); system
        notifications such as stage changes are excluded. Bodies are Odoo HTML.
        """
        require_tool_access("get_support_ticket_messages", instance)
        ticket_id = _positive_id(ticket_id, "ticket_id")
        api = target(instance, company)
        try:
            await api.read("helpdesk.ticket", ticket_id, ["id"])
            return await api.search_read(
                "mail.message",
                [["model", "=", "helpdesk.ticket"], ["res_id", "=", ticket_id],
                 ["message_type", "in", SUPPORT_TICKET_MESSAGE_TYPES]],
                SUPPORT_TICKET_MESSAGE_FIELDS, _limit(limit), order="date asc, id asc",
                offset=_offset(offset),
            )
        finally:
            await api.close()

    @mcp.tool()
    async def update_support_ticket(instance: str, ticket_id: int, ctx: Context,
                                    name: str | None = None, description: str | None = None,
                                    priority: str | None = None, team_id: int | None = None,
                                    user_id: int | None = None,
                                    stage_id: int | None = None,
                                    clear_assignee: bool = False, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Update supplied ticket fields. Set clear_assignee to true to remove the current assignee."""
        require_tool_access("update_support_ticket", instance)
        values: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be empty")
            values["name"] = name.strip()
        if description is not None:
            values["description"] = description
        if priority is not None:
            if priority not in SUPPORT_TICKET_PRIORITIES:
                raise ValueError("priority must be one of: 0, 1, 2, 3")
            values["priority"] = priority
        if team_id is not None:
            values["team_id"] = _positive_id(team_id, "team_id")
        if user_id is not None:
            if clear_assignee:
                raise ValueError("Use either user_id or clear_assignee, not both")
            values["user_id"] = _positive_id(user_id, "user_id")
        if stage_id is not None:
            values["stage_id"] = _positive_id(stage_id, "stage_id")
        if clear_assignee:
            values["user_id"] = False
        if not values:
            raise ValueError("Provide at least one ticket field to update")
        api = target(instance, company)
        try:
            return {"updated": await api.write(
                "helpdesk.ticket", _positive_id(ticket_id, "ticket_id"), values
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def create_task(instance: str, name: str, project_id: int, ctx: Context, description: str = "",
                          date_deadline: str | None = None,
                          user_ids: list[int] | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, int]:
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
        api = target(instance, company)
        try:
            return {"task_id": await api.create("project.task", values)}
        finally:
            await api.close()

    @mcp.tool()
    async def update_task(instance: str, task_id: int, ctx: Context, name: str | None = None,
                          description: str | None = None, date_deadline: str | None = None,
                          project_id: int | None = None,
                          user_ids: list[int] | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
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
        api = target(instance, company)
        try:
            return {"updated": await api.write("project.task", _positive_id(task_id, "task_id"), values)}
        finally:
            await api.close()

    @mcp.tool()
    async def list_task_stages(instance: str, project_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """List active workflow stages assigned to a project."""
        require_tool_access("list_task_stages", instance)
        project_id = _positive_id(project_id, "project_id")
        api = target(instance, company)
        try:
            return await api.search_read(
                "project.task.type",
                [["project_ids", "=", project_id]],
                ["id", "name", "sequence", "fold"], 100, "sequence asc, id asc",
            )
        finally:
            await api.close()

    @mcp.tool()
    async def set_task_stage(instance: str, task_id: int, stage_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Move a task to a stage assigned to the same project."""
        require_tool_access("set_task_stage", instance)
        task_id = _positive_id(task_id, "task_id")
        stage_id = _positive_id(stage_id, "stage_id")
        api = target(instance, company)
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
    async def set_task_state(instance: str, task_id: int, state: str, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Set a task to one of Odoo 18/19's supported workflow states."""
        require_tool_access("set_task_state", instance)
        task_id = _positive_id(task_id, "task_id")
        if not isinstance(state, str) or state not in TASK_STATES:
            raise ValueError(f"state must be one of: {', '.join(sorted(TASK_STATES))}")
        api = target(instance, company)
        try:
            return {"updated": await api.write("project.task", task_id, {"state": state})}
        finally:
            await api.close()

    @mcp.tool()
    async def archive_task(instance: str, task_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Archive a task by changing only its active flag."""
        require_tool_access("archive_task", instance)
        api = target(instance, company)
        try:
            return {"archived": await api.write(
                "project.task", _positive_id(task_id, "task_id"), {"active": False}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def unarchive_task(instance: str, task_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Restore an archived task by changing only its active flag."""
        require_tool_access("unarchive_task", instance)
        api = target(instance, company)
        try:
            return {"unarchived": await api.write(
                "project.task", _positive_id(task_id, "task_id"), {"active": True}
            )}
        finally:
            await api.close()

    @mcp.tool()
    async def search_purchase_orders(instance: str, ctx: Context, query: str = "",
                                    vendor_id: int | None = None, limit: int = 20, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Search RFQs and purchase orders by reference/vendor and optional vendor ID."""
        require_tool_access("search_purchase_orders", instance)
        domain: list = []
        if query:
            domain = ["|", ["name", "ilike", query], ["partner_ref", "ilike", query]]
        if vendor_id is not None:
            domain.append(["partner_id", "=", _positive_id(vendor_id, "vendor_id")])
        api = target(instance, company)
        try:
            return await api.search_read(
                "purchase.order", domain, PURCHASE_ORDER_FIELDS, _limit(limit), "date_order desc, id desc"
            )
        finally:
            await api.close()

    @mcp.tool()
    async def search_purchase_representatives(instance: str, query: str, ctx: Context,
                                              limit: int = 20,
                                              company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Find active internal users by display name for assignment as purchase representatives."""
        require_tool_access("search_purchase_representatives", instance)
        if not query.strip():
            raise ValueError("query must not be empty")
        api = target(instance, company)
        try:
            return await api.search_read(
                "res.users", [["active", "=", True], ["share", "=", False],
                              ["name", "ilike", query.strip()]],
                ["id", "name"], _limit(limit), "name asc, id asc",
            )
        finally:
            await api.close()

    @mcp.tool()
    async def get_purchase_order(instance: str, purchase_order_id: int,
                                 ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Read one RFQ/purchase order and its item lines."""
        require_tool_access("get_purchase_order", instance)
        purchase_order_id = _positive_id(purchase_order_id, "purchase_order_id")
        api = target(instance, company)
        try:
            order = await _purchase_order(api, purchase_order_id)
            note_field = "note" if api.instance.api_type == "json2" else "notes"
            order[note_field] = (await api.read("purchase.order", purchase_order_id, [note_field])).get(note_field)
            lines: list[dict[str, Any]] = []
            line_fields = PURCHASE_LINE_FIELDS_JSON2 if api.instance.api_type == "json2" else PURCHASE_LINE_FIELDS_XMLRPC
            offset = 0
            while True:
                batch = await api.search_read(
                    "purchase.order.line", [["order_id", "=", purchase_order_id]],
                    line_fields, MAX_LIMIT, "sequence asc, id asc", offset=offset,
                )
                lines.extend(batch)
                if len(batch) < MAX_LIMIT:
                    break
                offset += MAX_LIMIT
                if offset >= 1000:
                    raise ValueError("This order has more than 1,000 lines; request a smaller scope in Odoo")
            return {"purchase_order": order, "lines": lines}
        finally:
            await api.close()

    @mcp.tool()
    async def create_purchase_order(instance: str, vendor_id: int,
                                    lines: list[dict[str, Any]], ctx: Context,
                                    vendor_reference: str = "", notes: str = "",
                                    order_date: str | None = None,
                                    currency_id: int | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Create a draft RFQ with product lines; every line needs product_id, quantity, unit_price, expected_date."""
        require_tool_access("create_purchase_order", instance)
        vendor_id = _positive_id(vendor_id, "vendor_id")
        if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LIMIT:
            raise ValueError(f"lines must contain between 1 and {MAX_LIMIT} purchase lines")
        api = target(instance, company)
        try:
            vendor = await api.read("res.partner", vendor_id, ["id", "property_account_position_id"])
            fiscal_position_id = _relation_id(vendor.get("property_account_position_id"))
            order_values: dict[str, Any] = {"partner_id": vendor_id}
            if vendor_reference:
                order_values["partner_ref"] = vendor_reference.strip()
            if notes:
                order_values["note" if api.instance.api_type == "json2" else "notes"] = notes
            if order_date is not None:
                order_values["date_order"] = _purchase_datetime(order_date, "order_date")
            if currency_id is not None:
                order_values["currency_id"] = _positive_id(currency_id, "currency_id")

            commands = []
            allowed_keys = {"product_id", "quantity", "unit_price", "expected_date", "description"}
            for index, line in enumerate(lines, start=1):
                if not isinstance(line, dict) or set(line) - allowed_keys:
                    raise ValueError(f"line {index} must be an object with only supported purchase-line fields")
                missing = {"product_id", "quantity", "unit_price", "expected_date"} - set(line)
                if missing:
                    raise ValueError(f"line {index} is missing required fields: {', '.join(sorted(missing))}")
                commands.append([0, 0, await _purchase_line_values(
                    api, line["product_id"], line["quantity"], line["unit_price"],
                    line["expected_date"], line.get("description"), fiscal_position_id,
                )])
            order_values["order_line"] = commands
            purchase_order_id = await api.create("purchase.order", order_values)
            order = await _purchase_order(api, purchase_order_id)
            return {"purchase_order_id": purchase_order_id, "name": order.get("name"),
                    "state": order.get("state"), "amount_total": order.get("amount_total"),
                    "currency_id": order.get("currency_id")}
        finally:
            await api.close()

    @mcp.tool()
    async def update_purchase_order(instance: str, purchase_order_id: int, ctx: Context,
                                    vendor_reference: str | None = None, notes: str | None = None,
                                    order_date: str | None = None, currency_id: int | None = None,
                                    user_id: int | None = None,
                                    company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Update vendor reference, notes, date, currency, or purchase representative on a draft/sent RFQ."""
        require_tool_access("update_purchase_order", instance)
        purchase_order_id = _positive_id(purchase_order_id, "purchase_order_id")
        if instance not in configured:
            raise ValueError(f"Unknown Odoo instance {instance!r}; choose a configured instance explicitly")
        values: dict[str, Any] = {}
        if vendor_reference is not None:
            values["partner_ref"] = vendor_reference.strip()
        if notes is not None:
            values["note" if configured[instance].api_type == "json2" else "notes"] = notes
        if order_date is not None:
            values["date_order"] = _purchase_datetime(order_date, "order_date")
        if currency_id is not None:
            values["currency_id"] = _positive_id(currency_id, "currency_id")
        if user_id is not None:
            values["user_id"] = _positive_id(user_id, "user_id")
        if not values:
            raise ValueError("Provide at least one RFQ field to update")
        api = target(instance, company)
        try:
            await _require_editable_purchase_order(api, purchase_order_id)
            return {"updated": await api.write("purchase.order", purchase_order_id, values)}
        finally:
            await api.close()

    @mcp.tool()
    async def add_purchase_order_line(instance: str, purchase_order_id: int,
                                      product_id: int, quantity: float, unit_price: float,
                                      expected_date: str, ctx: Context,
                                      description: str | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, int]:
        """Add a purchasable product line to a draft/sent RFQ."""
        require_tool_access("add_purchase_order_line", instance)
        purchase_order_id = _positive_id(purchase_order_id, "purchase_order_id")
        api = target(instance, company)
        try:
            await _require_editable_purchase_order(api, purchase_order_id)
            order_context = await api.read("purchase.order", purchase_order_id, ["partner_id", "fiscal_position_id"])
            line_values = await _purchase_line_values(
                api, product_id, quantity, unit_price, expected_date, description,
                _relation_id(order_context.get("fiscal_position_id")),
            )
            line_values["order_id"] = purchase_order_id
            return {"purchase_order_line_id": await api.create("purchase.order.line", line_values)}
        finally:
            await api.close()

    @mcp.tool()
    async def update_purchase_order_line(instance: str, purchase_order_id: int,
                                         line_id: int, ctx: Context,
                                         quantity: float | None = None,
                                         unit_price: float | None = None,
                                         expected_date: str | None = None,
                                         description: str | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Update quantity, unit price, expected date, or description on an RFQ line."""
        require_tool_access("update_purchase_order_line", instance)
        purchase_order_id = _positive_id(purchase_order_id, "purchase_order_id")
        line_id = _positive_id(line_id, "line_id")
        values: dict[str, Any] = {}
        if quantity is not None:
            values["product_qty"] = _positive_number(quantity, "quantity")
        if unit_price is not None:
            values["price_unit"] = _positive_number(unit_price, "unit_price", allow_zero=True)
        if expected_date is not None:
            values["date_planned"] = _purchase_datetime(expected_date, "expected_date")
        if description is not None:
            if not description.strip():
                raise ValueError("description must not be empty")
            values["name"] = description.strip()
        if not values:
            raise ValueError("Provide at least one purchase-line field to update")
        api = target(instance, company)
        try:
            await _require_editable_purchase_order(api, purchase_order_id)
            line = await api.read("purchase.order.line", line_id, ["id", "order_id"])
            if _relation_id(line.get("order_id")) != purchase_order_id:
                raise ValueError(f"Line {line_id} does not belong to purchase order {purchase_order_id}")
            return {"updated": await api.write("purchase.order.line", line_id, values)}
        finally:
            await api.close()

    @mcp.tool()
    async def remove_purchase_order_line(instance: str, purchase_order_id: int,
                                         line_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Remove an item line from a draft/sent RFQ."""
        require_tool_access("remove_purchase_order_line", instance)
        purchase_order_id = _positive_id(purchase_order_id, "purchase_order_id")
        line_id = _positive_id(line_id, "line_id")
        api = target(instance, company)
        try:
            await _require_editable_purchase_order(api, purchase_order_id)
            line = await api.read("purchase.order.line", line_id, ["id", "order_id"])
            if _relation_id(line.get("order_id")) != purchase_order_id:
                raise ValueError(f"Line {line_id} does not belong to purchase order {purchase_order_id}")
            return {"removed": await api.unlink("purchase.order.line", line_id)}
        finally:
            await api.close()

    @mcp.tool()
    async def confirm_purchase_order(instance: str, purchase_order_id: int,
                                     ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Confirm a draft/sent RFQ into a purchase order; Odoo may route it for approval."""
        require_tool_access("confirm_purchase_order", instance)
        purchase_order_id = _positive_id(purchase_order_id, "purchase_order_id")
        api = target(instance, company)
        try:
            order = await _purchase_order(api, purchase_order_id, ["id", "name", "state"])
            if order.get("state") not in {"draft", "sent"}:
                raise ValueError("Only a draft or sent RFQ can be confirmed")
            await api.call("purchase.order", "button_confirm", {"ids": [purchase_order_id]})
            updated = await _purchase_order(api, purchase_order_id, ["id", "name", "state"])
            return {"purchase_order_id": purchase_order_id, "name": updated.get("name"),
                    "state": updated.get("state")}
        finally:
            await api.close()

    @mcp.tool()
    async def create_vendor_bill_from_purchase_order(instance: str, purchase_order_id: int,
                                                     ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Create the draft vendor bill from invoiceable lines on a confirmed purchase order."""
        require_tool_access("create_vendor_bill_from_purchase_order", instance)
        purchase_order_id = _positive_id(purchase_order_id, "purchase_order_id")
        api = target(instance, company)
        try:
            order = await _purchase_order(
                api, purchase_order_id, ["id", "name", "state", "invoice_status", "invoice_ids"]
            )
            if order.get("state") not in {"purchase", "done"}:
                raise ValueError("A vendor bill can only be created from a confirmed purchase order")
            if order.get("invoice_status") != "to invoice":
                raise ValueError("This purchase order has no invoiceable quantity; check receipts and existing bills")
            existing_bill_ids = set(_relation_ids(order.get("invoice_ids")))
            action = await api.call("purchase.order", "action_create_invoice", {"ids": [purchase_order_id]})
            updated = await _purchase_order(api, purchase_order_id, ["id", "invoice_ids"])
            bill_ids = sorted(set(_relation_ids(updated.get("invoice_ids"))) - existing_bill_ids)
            if not bill_ids and isinstance(action, dict):
                action_id = action.get("res_id")
                if isinstance(action_id, int) and not isinstance(action_id, bool) and action_id > 0:
                    bill_ids = [action_id]
            if not bill_ids:
                raise OdooApiError("Odoo completed the invoice action but returned no new vendor bill")
            bills = [await api.read("account.move", bill_id, VENDOR_BILL_FIELDS) for bill_id in bill_ids]
            return {"purchase_order_id": purchase_order_id, "vendor_bills": bills,
                    "created_as_draft": all(bill.get("state") == "draft" for bill in bills)}
        finally:
            await api.close()

    @mcp.tool()
    async def list_websites(instance: str, ctx: Context, limit: int = 100, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """List Odoo websites so blog tools can target the correct site by ID."""
        require_tool_access("list_websites", instance)
        api = target(instance, company)
        try:
            return await api.search_read("website", [], ["id", "name", "domain"], _limit(limit), "id asc")
        finally:
            await api.close()

    @mcp.tool()
    async def search_blogs(instance: str, website_id: int, ctx: Context, query: str = "",
                           limit: int = 50, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """List blogs on one website, optionally matching a name."""
        require_tool_access("search_blogs", instance)
        website_id = _positive_id(website_id, "website_id")
        domain: list = _website_domain(website_id)
        if query:
            domain.append(["name", "ilike", query])
        api = target(instance, company)
        try:
            return await api.search_read("blog.blog", domain, ["id", "name", "subtitle", "website_id"],
                                         _limit(limit), "name asc")
        finally:
            await api.close()

    @mcp.tool()
    async def update_blog(instance: str, website_id: int, blog_id: int, lang: str, ctx: Context,
                          name: str | None = None, subtitle: str | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Rename a blog or change its subtitle in one active website language.

        The blog must belong to the selected website or have no website assigned. Renaming
        changes the blog's URL slug; Odoo redirects old post URLs that keep the same IDs.
        """
        require_tool_access("update_blog", instance)
        website_id = _positive_id(website_id, "website_id")
        blog_id = _positive_id(blog_id, "blog_id")
        _validate_lang_code(lang)
        values: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be empty")
            values["name"] = name.strip()
        if subtitle is not None:
            values["subtitle"] = subtitle
        if not values:
            raise ValueError("Provide name or subtitle to update")
        api = target(instance, company)
        try:
            await _blog_for_website(api, blog_id, website_id)
            _, active_codes = await _blog_website_languages(api, website_id)
            if lang not in active_codes:
                raise ValueError(f"Language {lang} is not active on website {website_id}")
            return {"updated": await api.write("blog.blog", blog_id, values, context={"lang": lang})}
        finally:
            await api.close()

    @mcp.tool()
    async def search_blog_posts(instance: str, website_id: int, ctx: Context, query: str = "",
                                blog_id: int | None = None, limit: int = 20, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Search blog posts on the selected website; results include publish date and URL."""
        require_tool_access("search_blog_posts", instance)
        website_id = _positive_id(website_id, "website_id")
        domain: list = _website_domain(website_id)
        if blog_id is not None:
            domain.append(["blog_id", "=", _positive_id(blog_id, "blog_id")])
        if query:
            domain.extend(["|", ["name", "ilike", query], ["subtitle", "ilike", query]])
        api = target(instance, company)
        try:
            posts = await api.search_read("blog.post", domain, BLOG_POST_SEARCH_FIELDS, _limit(limit))
            base_url = await _website_url_base(api, website_id)
            return [_absolute_blog_post_url(post, base_url) for post in posts]
        finally:
            await api.close()

    @mcp.tool()
    async def get_blog_post(instance: str, website_id: int, post_id: int,
                            ctx: Context, lang: str | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Read an allowlisted set of fields from one post on the selected website.

        cover_image_url is the absolute URL of the cover image, or null without one;
        it can be used as website_meta_og_img.
        """
        require_tool_access("get_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        if lang is not None:
            _validate_lang_code(lang)
        api = target(instance, company)
        try:
            post = await _blog_post_on_website(api, post_id, website_id,
                                               BLOG_POST_FIELDS + ["cover_properties"])
            cover_properties = post.pop("cover_properties", None)
            if lang is not None:
                languages = await api.search_read(
                    "res.lang", [["code", "=", lang], ["active", "=", True]], ["id", "code"], 1
                )
                if not languages:
                    raise ValueError(f"Language {lang} is not installed or active in Odoo")
                post = await api.read("blog.post", post_id, BLOG_POST_FIELDS, context={"lang": lang})
            base_url = await _website_url_base(api, website_id)
            post["cover_image_url"] = _cover_image_url(cover_properties, base_url)
            return _absolute_blog_post_url(post, base_url)
        finally:
            await api.close()

    @mcp.tool()
    async def get_blog_post_translation_terms(
        instance: str, website_id: int, post_id: int, lang: str, ctx: Context,
    company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Get HTML content terms in the website's primary language and their translation."""
        require_tool_access("get_blog_post_translation_terms", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        _validate_lang_code(lang)
        api = target(instance, company)
        try:
            await _blog_post_on_website(api, post_id, website_id, ["id"])
            source_lang, active_codes = await _blog_website_languages(api, website_id)
            if lang not in active_codes:
                raise ValueError(f"Language {lang} is not active on website {website_id}")
            result = await api.get_field_translations(
                "blog.post", post_id, "content", [source_lang, lang],
            )
            return {
                "source_lang": source_lang,
                "lang": lang,
                "content_terms": _translation_terms_by_source(result, source_lang, lang),
            }
        finally:
            await api.close()

    @mcp.tool()
    async def set_blog_post_translation(
        instance: str, website_id: int, post_id: int, lang: str, subtitle: str,
        website_meta_title: str, website_meta_description: str, website_meta_keywords: str,
        ctx: Context, name: str | None = None, content_terms: dict[str, str] | None = None,
        content_marked_text: str | None = None, seo_name: str | None = None,
    company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Write translatable fields and HTML content terms in one active website language.

        Pass either content_terms (source term -> translation) or content_marked_text:
        the translated marked text with the same blocks as the primary-language content,
        paired with its terms by position. An H1 line in it is the translated name unless
        name is given.
        """
        require_tool_access("set_blog_post_translation", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        _validate_lang_code(lang)
        if (content_terms is None) == (content_marked_text is None):
            raise ValueError("Provide exactly one of content_terms or content_marked_text")
        translated_document = None
        if content_marked_text is not None:
            try:
                translated_document = convert_marked_text(content_marked_text)
            except MarkedTextError as exc:
                raise ValueError(str(exc)) from exc
            if name is None:
                name = translated_document.title
        elif not isinstance(content_terms, dict) or any(
            not isinstance(term, str) or not isinstance(translation, str)
            for term, translation in content_terms.items()
        ):
            raise ValueError("content_terms must be an object mapping source terms to translations")
        if name is None:
            raise ValueError("name is required unless content_marked_text has an H1 line")
        api = target(instance, company)
        try:
            post = await _blog_post_on_website(api, post_id, website_id, ["id"])
            source_lang, active_codes = await _blog_website_languages(api, website_id)
            if lang not in active_codes:
                raise ValueError(f"Language {lang} is not active on website {website_id}")
            current_terms_result = await api.get_field_translations(
                "blog.post", post["id"], "content", [source_lang, lang],
            )
            current_terms = _translation_terms_by_source(current_terms_result, source_lang, lang)
            if translated_document is not None:
                source_post = await api.read("blog.post", post["id"], ["content"],
                                             context={"lang": source_lang})
                try:
                    content_terms = pair_translation_terms(
                        source_post.get("content") or "", list(current_terms), translated_document,
                    )
                except MarkedTextError as exc:
                    raise ValueError(str(exc)) from exc
            unknown_terms = set(content_terms) - set(current_terms)
            if unknown_terms:
                raise ValueError("content_terms contains source terms not present in this blog post")
            if content_terms:
                await api.update_field_translations(
                    "blog.post", post["id"], "content", {lang: content_terms}, source_lang,
                )
            values: dict[str, Any] = {
                "name": name, "subtitle": subtitle,
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
    company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, str]:
        """Attach a public PNG, JPEG, or WebP image as a blog post cover."""
        require_tool_access("set_blog_post_cover", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        content, mime = _image_info(image_base64, filename)
        api = target(instance, company)
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
            try:
                await api.write("blog.post", post_id, values)
            except Exception as write_error:
                try:
                    await api.unlink("ir.attachment", attachment_id)
                except Exception as cleanup_error:
                    raise OdooApiError(
                        "Blog cover update failed and the newly created attachment could not be removed"
                    ) from cleanup_error
                raise write_error
            return {"url": urljoin(await _website_url_base(api, website_id), image_url.lstrip("/"))}
        finally:
            await api.close()

    @mcp.tool()
    async def create_blog_post(instance: str, website_id: int, blog_id: int,
                               ctx: Context, name: str | None = None,
                               subtitle: str | None = None, content: str | None = None,
                               content_format: str = "html",
                               tag_ids: list[int] | None = None, author_id: int | None = None,
                               website_meta_title: str | None = None,
                               website_meta_description: str | None = None,
                               website_meta_keywords: str | None = None,
                               website_meta_og_img: str | None = None,
                               seo_name: str | None = None, lang: str | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, int]:
        """Create an unpublished blog post using only the documented content and SEO fields.

        content_format "html" (default) stores content as given; "marked_text" converts
        the generator's marked text to HTML, and its H1 line is the name unless name is given.
        lang, when given, must be the website's primary language; the post is then written
        in that language regardless of the Odoo user's language.
        """
        require_tool_access("create_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        blog_id = _positive_id(blog_id, "blog_id")
        content, name = _blog_content(content, content_format, name)
        if name is None or not name.strip():
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
        if lang is not None:
            _validate_lang_code(lang)
        api = target(instance, company)
        try:
            await _blog_for_website(api, blog_id, website_id)
            context = await _primary_lang_context(api, website_id, lang)
            if context is None:
                return {"post_id": await api.create("blog.post", values)}
            return {"post_id": await api.create("blog.post", values, context=context)}
        finally:
            await api.close()

    @mcp.tool()
    async def update_blog_post(instance: str, website_id: int, post_id: int, ctx: Context,
                               name: str | None = None, subtitle: str | None = None,
                               content: str | None = None, content_format: str = "html",
                               blog_id: int | None = None,
                               tag_ids: list[int] | None = None, author_id: int | None = None,
                               website_meta_title: str | None = None,
                               website_meta_description: str | None = None,
                               website_meta_keywords: str | None = None,
                               website_meta_og_img: str | None = None,
                               seo_name: str | None = None, lang: str | None = None, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Edit allowlisted blog content and SEO fields; publication cannot be changed here.

        content_format "html" (default) stores content as given; "marked_text" converts
        the generator's marked text to HTML, and its H1 line is the name unless name is given.
        lang, when given, must be the website's primary language; fields are then written
        in that language regardless of the Odoo user's language.
        """
        require_tool_access("update_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        content, name = _blog_content(content, content_format, name)
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
        if lang is not None:
            _validate_lang_code(lang)
        api = target(instance, company)
        try:
            await _blog_post_on_website(api, post_id, website_id, ["id"])
            if blog_id is not None:
                await _blog_for_website(api, blog_id, website_id)
            context = await _primary_lang_context(api, website_id, lang)
            if context is None:
                return {"updated": await api.write("blog.post", post_id, values)}
            return {"updated": await api.write("blog.post", post_id, values, context=context)}
        finally:
            await api.close()

    @mcp.tool()
    async def publish_blog_post(instance: str, website_id: int, post_id: int,
                                ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Publish a blog post on the selected website. Requires blog_publisher."""
        require_tool_access("publish_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        api = target(instance, company)
        try:
            await _blog_post_on_website(api, post_id, website_id, ["id"])
            return {"published": await api.write("blog.post", post_id, {"is_published": True})}
        finally:
            await api.close()

    @mcp.tool()
    async def unpublish_blog_post(instance: str, website_id: int, post_id: int,
                                  ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Unpublish a blog post on the selected website. Requires blog_publisher."""
        require_tool_access("unpublish_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        api = target(instance, company)
        try:
            await _blog_post_on_website(api, post_id, website_id, ["id"])
            return {"unpublished": await api.write("blog.post", post_id, {"is_published": False})}
        finally:
            await api.close()

    @mcp.tool()
    async def schedule_blog_post(instance: str, website_id: int, post_id: int,
                                 post_date: str, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Schedule a blog post for at least ten minutes in the future."""
        require_tool_access("schedule_blog_post", instance)
        website_id = _positive_id(website_id, "website_id")
        post_id = _positive_id(post_id, "post_id")
        scheduled_utc, odoo_post_date = _future_blog_date(post_date)
        api = target(instance, company)
        try:
            post = await _blog_post_on_website(api, post_id, website_id, ["id", "is_published", "published_date"])
            current_date = _odoo_datetime_utc(post.get("published_date"))
            if post.get("is_published") and current_date is not None and current_date < datetime.now(timezone.utc):
                raise ValueError("Cannot schedule a blog post that is already published with a past date")
            await api.write("blog.post", post_id, {
                "published_date": odoo_post_date,
                "is_published": True,
            })
            return {
                "scheduled": True,
                "post_date": scheduled_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "is_published": True,
            }
        finally:
            await api.close()

    @mcp.tool()
    async def search_pages(instance: str, ctx: Context, query: str = "", limit: int = 20, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> list[dict[str, Any]]:
        """Search website pages by name or URL."""
        require_tool_access("search_pages", instance)
        domain: list = []
        if query:
            domain = ["|", ["name", "ilike", query], ["url", "ilike", query]]
        api = target(instance, company)
        try:
            return await api.search_read("website.page", domain, PAGE_FIELDS, _limit(limit))
        finally:
            await api.close()

    @mcp.tool()
    async def get_page(instance: str, page_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, Any]:
        """Get a website page's metadata by ID."""
        require_tool_access("get_page", instance)
        api = target(instance, company)
        try:
            return await api.read("website.page", _positive_id(page_id, "page_id"), PAGE_FIELDS)
        finally:
            await api.close()

    @mcp.tool()
    async def update_page_content(instance: str, page_id: int, html: str, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Replace a page's view architecture with supplied HTML. Test on a disposable page first."""
        require_tool_access("update_page_content", instance)
        if not html.strip():
            raise ValueError("html must not be empty")
        api = target(instance, company)
        try:
            page = await api.read("website.page", _positive_id(page_id, "page_id"), ["id", "view_id"])
            view_id = page.get("view_id")
            if not isinstance(view_id, list) or not view_id or not isinstance(view_id[0], int):
                raise OdooApiError("Page has no readable website view")
            return {"updated": await api.write("ir.ui.view", view_id[0], {"arch": html})}
        finally:
            await api.close()

    @mcp.tool()
    async def publish_page(instance: str, page_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Publish a website page as an explicit action."""
        require_tool_access("publish_page", instance)
        api = target(instance, company)
        try:
            return {"published": await api.write("website.page", _positive_id(page_id, "page_id"),
                                                  {"is_published": True})}
        finally:
            await api.close()

    @mcp.tool()
    async def unpublish_page(instance: str, page_id: int, ctx: Context, company: Annotated[str | None, Field(description="Exact Odoo company name. Required when multiple companies are available; omit only when exactly one is available.")] = None) -> dict[str, bool]:
        """Unpublish a website page as an explicit action."""
        require_tool_access("unpublish_page", instance)
        api = target(instance, company)
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
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    admin_port = int(os.environ["MCP_ADMIN_PORT"]) if os.getenv("MCP_ADMIN_PORT") else None
    if admin_port is None:
        uvicorn.run(app, host=host, port=port)
        return
    if admin_port == port:
        raise ValueError("MCP_ADMIN_PORT must differ from MCP_PORT")
    # One server, two sockets: the admin port can then be published to loopback only.
    app.add_middleware(AdminPortMiddleware, admin_port=admin_port)
    sockets = [socket.create_server((host, port)),
               socket.create_server((os.getenv("MCP_ADMIN_HOST", host), admin_port))]
    uvicorn.Server(uvicorn.Config(app)).run(sockets=sockets)
