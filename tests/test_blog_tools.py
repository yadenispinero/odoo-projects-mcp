import pytest
import inspect

from odoo_mcp.auth import MCPUser, ROLE_TOOLS, current_mcp_user
from odoo_mcp.config import OdooInstance
from odoo_mcp.server import create_server


def tools_for(server):
    return {tool.name: tool.fn for tool in server._tool_manager.list_tools()}


def mcp_user(role):
    return MCPUser("test", "digest", frozenset({role}), frozenset({"demo"}))


def test_blog_roles_are_separate_from_page_roles():
    editor = ROLE_TOOLS["blog_editor"]
    publisher = ROLE_TOOLS["blog_publisher"]
    assert {"search_blog_posts", "get_blog_post", "create_blog_post", "update_blog_post"} <= editor
    assert {"publish_blog_post", "unpublish_blog_post"}.isdisjoint(editor)
    assert {"publish_blog_post", "unpublish_blog_post", "search_blog_posts", "get_blog_post"} <= publisher
    assert {"create_blog_post", "update_blog_post"}.isdisjoint(publisher)
    blog_tools = editor | publisher
    assert {"search_pages", "get_page", "update_page_content", "publish_page", "unpublish_page"}.isdisjoint(blog_tools)
    assert "update_page_content" not in editor
    assert "create_blog_post" not in ROLE_TOOLS["website_editor"]


@pytest.mark.asyncio
async def test_create_blog_post_has_field_allowlist_and_is_unpublished(monkeypatch):
    captured = {}

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def read(self, model, record_id, fields):
            assert (model, record_id, fields) == ("blog.blog", 20, ["id", "website_id"])
            return {"id": 20, "website_id": [10, "Main"]}

        async def create(self, model, values):
            captured["model"] = model
            captured["values"] = values
            return 55

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["create_blog_post"](
            instance="demo", website_id=10, blog_id=20, name="A post", ctx=None,
            subtitle="Summary", content="<p>Body</p>", tag_ids=[3, 4], author_id=8,
            website_meta_title="SEO title", website_meta_description="SEO description",
            website_meta_keywords="one,two", website_meta_og_img="/image.png", seo_name="a-post",
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {"post_id": 55}
    assert captured["model"] == "blog.post"
    values = captured["values"]
    assert values["is_published"] is False
    assert values["tag_ids"] == [[6, 0, [3, 4]]]
    assert values["blog_id"] == 20
    assert "website_id" not in values  # Odoo derives this from the selected blog.
    assert set(values) == {
        "name", "blog_id", "is_published", "subtitle", "content", "tag_ids", "author_id",
        "website_meta_title", "website_meta_description", "website_meta_keywords",
        "website_meta_og_img", "seo_name",
    }
    assert "is_published" not in inspect.signature(tools["create_blog_post"]).parameters
    assert "is_published" not in inspect.signature(tools["update_blog_post"]).parameters


@pytest.mark.asyncio
async def test_update_blog_post_only_writes_allowed_content_fields(monkeypatch):
    captured = {}

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def search_read(self, model, domain, fields, limit, order="id desc"):
            assert model == "blog.post"
            assert domain == [["id", "=", 55], ["website_id", "=", 10]]
            return [{"id": 55}]

        async def write(self, model, record_id, values):
            captured.update(model=model, record_id=record_id, values=values)
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["update_blog_post"](
            instance="demo", website_id=10, post_id=55, ctx=None, subtitle="Updated",
            content="<p>New text</p>", website_meta_title="Updated SEO",
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {"updated": True}
    assert captured == {
        "model": "blog.post", "record_id": 55,
        "values": {"subtitle": "Updated", "content": "<p>New text</p>",
                   "website_meta_title": "Updated SEO"},
    }


@pytest.mark.asyncio
async def test_blog_editor_cannot_publish_and_publisher_cannot_edit(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance):
            raise AssertionError("Authorization must reject before creating an Odoo API client")

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))

    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        with pytest.raises(PermissionError):
            await tools["publish_blog_post"](instance="demo", website_id=10, post_id=55, ctx=None)
    finally:
        current_mcp_user.reset(reset)
    reset = current_mcp_user.set(mcp_user("blog_publisher"))
    try:
        with pytest.raises(PermissionError):
            await tools["update_blog_post"](
                instance="demo", website_id=10, post_id=55, ctx=None, name="Changed",
            )
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "published", "result_key"), [
    ("publish_blog_post", True, "published"),
    ("unpublish_blog_post", False, "unpublished"),
])
async def test_blog_publisher_uses_explicit_publication_write(monkeypatch, tool_name, published, result_key):
    captured = {}

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def search_read(self, model, domain, fields, limit, order="id desc"):
            assert model == "blog.post"
            assert domain == [["id", "=", 55], ["website_id", "=", 10]]
            return [{"id": 55}]

        async def write(self, model, record_id, values):
            captured.update(model=model, record_id=record_id, values=values)
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_publisher"))
    try:
        result = await tools[tool_name](instance="demo", website_id=10, post_id=55, ctx=None)
    finally:
        current_mcp_user.reset(reset)
    assert result == {result_key: True}
    assert captured == {
        "model": "blog.post", "record_id": 55, "values": {"is_published": published},
    }

@pytest.mark.asyncio
async def test_search_blog_posts_scopes_site_and_returns_absolute_url(monkeypatch):
    captured = {}

    class FakeOdoo:
        instance = OdooInstance("demo", "https://odoo.example.test")

        def __init__(self, instance):
            self.instance = instance

        async def search_read(self, model, domain, fields, limit, order="id desc"):
            captured.update(model=model, domain=domain, fields=fields, limit=limit)
            return [{"id": 55, "website_url": "/blog/news/a-post", "published_date": "2026-10-10 12:00:00"}]

        async def read(self, model, record_id, fields):
            assert (model, record_id, fields) == ("website", 10, ["domain"])
            return {"domain": "https://news.example.test"}

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        posts = await tools["search_blog_posts"](
            instance="demo", website_id=10, blog_id=20, query="a post", limit=12, ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)
    assert captured["model"] == "blog.post"
    assert captured["domain"] == [
        ["website_id", "=", 10], ["blog_id", "=", 20], "|",
        ["name", "ilike", "a post"], ["subtitle", "ilike", "a post"],
    ]
    assert "published_date" in captured["fields"]
    assert posts[0]["website_url"] == "https://news.example.test/blog/news/a-post"

