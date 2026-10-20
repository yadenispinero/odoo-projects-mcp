import pytest
import inspect
import base64
import json
from datetime import datetime, timedelta, timezone

from odoo_mcp.auth import MCPUser, ROLE_TOOLS, current_mcp_user
from odoo_mcp.config import OdooInstance
from odoo_mcp.odoo_api import OdooApiError
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
    assert "set_blog_post_translation" in editor
    assert "get_blog_post_translation_terms" in editor
    assert "set_blog_post_cover" in editor
    assert "schedule_blog_post" in ROLE_TOOLS["blog_scheduler"]


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
            assert domain == [["id", "=", 55], "|", ["website_id", "=", 10], ["website_id", "=", False]]
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
            assert domain == [["id", "=", 55], "|", ["website_id", "=", 10], ["website_id", "=", False]]
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
        "|", ["website_id", "=", 10], ["website_id", "=", False],
        ["blog_id", "=", 20], "|", ["name", "ilike", "a post"], ["subtitle", "ilike", "a post"],
    ]
    assert "published_date" in captured["fields"]
    assert posts[0]["website_url"] == "https://news.example.test/blog/news/a-post"


@pytest.mark.asyncio
async def test_create_blog_post_accepts_blog_without_website(monkeypatch):
    captured = {}

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def read(self, model, record_id, fields):
            assert (model, record_id, fields) == ("blog.blog", 20, ["id", "website_id"])
            return {"id": 20, "website_id": False}

        async def create(self, model, values):
            captured.update(model=model, values=values)
            return 56

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["create_blog_post"](
            instance="demo", website_id=10, blog_id=20, name="Global blog post", ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {"post_id": 56}
    assert captured["values"]["is_published"] is False


@pytest.mark.asyncio
async def test_get_blog_post_reads_selected_translation(monkeypatch):
    captured = {}

    class FakeOdoo:
        instance = OdooInstance("demo", "https://odoo.example.test")
        def __init__(self, instance): self.instance = instance
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            if model == "blog.post": return [{"id": 55}]
            return [{"id": 9, "code": "sr@latin"}]
        async def read(self, model, record_id, fields, context=None):
            captured.setdefault("reads", []).append((model, record_id, context))
            if model == "blog.post": return {"id": 55, "name": "Título", "website_url": "/blog/x"}
            return {"domain": "https://news.example.test"}
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        post = await tools["get_blog_post"](
            instance="demo", website_id=10, post_id=55, lang="sr@latin", ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)
    assert post["name"] == "Título"
    assert post["website_url"] == "https://news.example.test/blog/x"
    assert ("blog.post", 55, {"lang": "sr@latin"}) in captured["reads"]


@pytest.mark.asyncio
async def test_get_blog_post_rejects_uninstalled_language_with_clear_error(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance): pass
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            return [{"id": 55}] if model == "blog.post" else []
        async def close(self): pass
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        with pytest.raises(ValueError, match="Language fr_FR is not installed or active"):
            await tools["get_blog_post"](
                instance="demo", website_id=10, post_id=55, lang="fr_FR", ctx=None,
            )
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["json2", "xmlrpc"])
async def test_get_blog_post_translation_terms_returns_primary_terms_and_current_translation(monkeypatch, api_type):
    captured = {}

    class FakeOdoo:
        def __init__(self, instance): self.instance = instance
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            if model == "blog.post": return [{"id": 55}]
            return [{"id": 2, "code": "en_US"}, {"id": 3, "code": "es_419"}]
        async def read(self, model, record_id, fields, context=None):
            return {"language_ids": [2, 3], "default_lang_id": [2, "English"]}
        async def get_field_translations(self, model, record_id, field_name, langs):
            captured["request"] = (model, record_id, field_name, langs)
            return ([
                {"lang": "en_US", "source": "Hello", "value": ""},
                {"lang": "es_419", "source": "Hello", "value": "Hola"},
            ], {"translation_type": "text"})
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test", api_type=api_type)}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["get_blog_post_translation_terms"](
            instance="demo", website_id=10, post_id=55, lang="es_419", ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {
        "source_lang": "en_US", "lang": "es_419", "content_terms": {"Hello": "Hola"},
    }
    assert captured["request"] == ("blog.post", 55, "content", ["en_US", "es_419"])


@pytest.mark.asyncio
async def test_set_blog_post_translation_rejects_unknown_content_terms(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance): pass
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            if model == "blog.post": return [{"id": 55}]
            return [{"id": 2, "code": "en_US"}, {"id": 3, "code": "es_419"}]
        async def read(self, model, record_id, fields, context=None):
            return {"language_ids": [2, 3], "default_lang_id": [2, "English"]}
        async def get_field_translations(self, *args, **kwargs):
            return ([{"lang": "en_US", "source": "Hello", "value": ""}], {})
        async def update_field_translations(self, *args, **kwargs): raise AssertionError("must reject before update")
        async def write(self, *args, **kwargs): raise AssertionError("must reject before write")
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        with pytest.raises(ValueError, match="source terms not present"):
            await tools["set_blog_post_translation"](
                instance="demo", website_id=10, post_id=55, lang="es_419", name="x", subtitle="x",
                content_terms={"Not in the post": "No es parte"}, website_meta_title="x",
                website_meta_description="x", website_meta_keywords="x", ctx=None,
            )
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["json2", "xmlrpc"])
@pytest.mark.parametrize("lang", ["es_ES", "es_419", "sr@latin"])
async def test_set_blog_post_translation_uses_active_website_language_and_context(monkeypatch, api_type, lang):
    captured = {}
    post_content = {"en_US": "<p>Hello</p>", lang: "<p>Hola</p>"}

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def search_read(self, model, domain, fields, limit, order="id desc"):
            if model == "blog.post":
                assert domain == [["id", "=", 55], "|", ["website_id", "=", 10], ["website_id", "=", False]]
                return [{"id": 55}]
            assert model == "res.lang"
            return [{"id": 2, "code": "en_US"}, {"id": 3, "code": lang}, {"id": 4, "code": "de_DE"}]

        async def read(self, model, record_id, fields, context=None):
            assert (model, record_id, fields) == ("website", 10, ["language_ids", "default_lang_id"])
            return {"language_ids": [2, 3], "default_lang_id": [2, "English"]}

        async def get_field_translations(self, model, record_id, field_name, langs):
            assert (model, record_id, field_name, langs) == ("blog.post", 55, "content", ["en_US", lang])
            return ([
                {"lang": "en_US", "source": "<p>Hello</p>", "value": ""},
                {"lang": lang, "source": "<p>Hello</p>", "value": "<p>Hola</p>"},
            ], {"translation_type": "text"})

        async def update_field_translations(self, model, record_id, field_name, translations, source_lang):
            assert (model, record_id, field_name, source_lang) == ("blog.post", 55, "content", "en_US")
            captured["content_terms"] = translations
            post_content.update(translations[lang])
            return True

        async def write(self, model, record_id, values, context=None):
            assert "content" not in values
            captured.update(model=model, record_id=record_id, values=values, context=context)
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test", api_type=api_type)}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["set_blog_post_translation"](
            instance="demo", website_id=10, post_id=55, lang=lang, name="Título",
            subtitle="Subtítulo", content_terms={"<p>Hello</p>": "<p>Hola</p>"}, website_meta_title="SEO",
            website_meta_description="Descripción", website_meta_keywords="palabras", seo_name="titulo", ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {"updated": True}
    assert captured["context"] == {"lang": lang}
    assert captured["content_terms"] == {lang: {"<p>Hello</p>": "<p>Hola</p>"}}
    assert post_content["en_US"] == "<p>Hello</p>"
    assert post_content[lang] == "<p>Hola</p>"
    assert "is_published" not in captured["values"]


@pytest.mark.asyncio
async def test_set_blog_post_translation_rejects_language_not_enabled_for_website(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance): pass
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            return [{"id": 55}] if model == "blog.post" else [{"id": 2, "code": "en_US"}, {"id": 3, "code": "es_ES"}]
        async def read(self, model, record_id, fields, context=None):
            return {"language_ids": [2], "default_lang_id": [2, "English"]}
        async def write(self, *args, **kwargs): raise AssertionError("must reject before write")
        async def get_field_translations(self, *args, **kwargs): raise AssertionError("must reject before terms read")
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        with pytest.raises(ValueError, match="not active"):
            await tools["set_blog_post_translation"](
                instance="demo", website_id=10, post_id=55, lang="es_ES", name="x", subtitle="x",
                content_terms={}, website_meta_title="x", website_meta_description="x",
                website_meta_keywords="x", ctx=None,
            )
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["json2", "xmlrpc"])
async def test_set_blog_post_cover_creates_public_attachment_and_merges_cover(monkeypatch, api_type):
    captured = {}
    original_cover = {"opacity": "0.8", "background-color": "#fff"}
    # A minimal, signature-valid PNG header is enough for this validation test.
    png = b"\x89PNG\r\n\x1a\n" + b"test"

    class FakeOdoo:
        def __init__(self, instance): self.instance = instance
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            if model == "blog.post":
                return [{"id": 55, "cover_properties": json.dumps(original_cover)}]
            raise AssertionError(model)
        async def read(self, model, record_id, fields, context=None):
            assert (model, record_id, fields) == ("website", 10, ["domain"])
            return {"domain": "https://news.example.test"}
        async def create(self, model, values):
            captured["attachment"] = (model, values)
            return 99
        async def write(self, model, record_id, values):
            captured["post_write"] = (model, record_id, values)
            return True
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test", api_type=api_type)}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["set_blog_post_cover"](
            instance="demo", website_id=10, post_id=55, image_base64=base64.b64encode(png).decode(),
            filename="cover.png", ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)
    url = "/web/image/ir.attachment/99/datas"
    assert result == {"url": "https://news.example.test" + url}
    model, attachment = captured["attachment"]
    assert model == "ir.attachment"
    assert attachment["public"] is True and attachment["res_model"] == "blog.post" and attachment["res_id"] == 55
    model, record_id, values = captured["post_write"]
    assert (model, record_id) == ("blog.post", 55)
    updated_cover = json.loads(values["cover_properties"])
    assert updated_cover["opacity"] == "0.8" and updated_cover["background-color"] == "#fff"
    assert updated_cover["background-image"] == f"url({url})"
    assert values["website_meta_og_img"] == url


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["json2", "xmlrpc"])
async def test_set_blog_post_cover_removes_new_attachment_when_post_write_fails(monkeypatch, api_type):
    deleted = []
    png = b"\x89PNG\r\n\x1a\ntrailer"

    class FakeOdoo:
        def __init__(self, instance): self.instance = instance
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            return [{"id": 55, "cover_properties": "{}"}]
        async def create(self, model, values): return 101
        async def write(self, model, record_id, values): raise OdooApiError("post write failed")
        async def unlink(self, model, record_id): deleted.append((model, record_id)); return True
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test", api_type=api_type)}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        with pytest.raises(OdooApiError, match="post write failed"):
            await tools["set_blog_post_cover"](
                instance="demo", website_id=10, post_id=55,
                image_base64=base64.b64encode(png).decode(), filename="cover.png", ctx=None,
            )
    finally:
        current_mcp_user.reset(reset)
    assert deleted == [("ir.attachment", 101)]


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["json2", "xmlrpc"])
async def test_schedule_blog_post_sets_utc_date_and_published_state(monkeypatch, api_type):
    captured = {}
    future = (datetime.now(timezone.utc) + timedelta(minutes=20)).replace(microsecond=0)
    iso_date = future.isoformat().replace("+00:00", "Z")

    class FakeOdoo:
        def __init__(self, instance): self.instance = instance
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            assert model == "blog.post"
            return [{"id": 55, "is_published": False, "published_date": "2020-01-01 00:00:00"}]
        async def write(self, model, record_id, values):
            captured.update(model=model, record_id=record_id, values=values)
            return True
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test", api_type=api_type)}))
    reset = current_mcp_user.set(mcp_user("blog_scheduler"))
    try:
        result = await tools["schedule_blog_post"](
            instance="demo", website_id=10, post_id=55, post_date=iso_date, ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {"scheduled": True, "post_date": iso_date, "is_published": True}
    assert captured == {
        "model": "blog.post", "record_id": 55,
        "values": {"published_date": future.strftime("%Y-%m-%d %H:%M:%S"), "is_published": True},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("post_date", ["2020-01-01T00:00:00Z", "2030-01-01T00:00:00"])
async def test_schedule_blog_post_rejects_nonfuture_or_timezone_free_dates(monkeypatch, post_date):
    class FakeOdoo:
        def __init__(self, instance): raise AssertionError("validate before contacting Odoo")
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_scheduler"))
    try:
        with pytest.raises(ValueError):
            await tools["schedule_blog_post"](
                instance="demo", website_id=10, post_id=55, post_date=post_date, ctx=None,
            )
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
async def test_schedule_blog_post_rejects_published_past_post(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance): self.instance = instance
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            return [{"id": 55, "is_published": True, "published_date": "2020-01-01 00:00:00"}]
        async def write(self, *args, **kwargs): raise AssertionError("must reject before write")
        async def close(self): pass
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_scheduler"))
    try:
        with pytest.raises(ValueError, match="already published"):
            await tools["schedule_blog_post"](
                instance="demo", website_id=10, post_id=55,
                post_date=(datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(), ctx=None,
            )
    finally:
        current_mcp_user.reset(reset)



class _CreateOdoo:
    captured: dict = {}

    def __init__(self, instance): self.instance = instance
    async def read(self, model, record_id, fields): return {"id": 20, "website_id": [10, "Main"]}
    async def create(self, model, values):
        _CreateOdoo.captured = values
        return 56
    async def close(self): pass


@pytest.mark.asyncio
@pytest.mark.parametrize("name, expected_name", [(None, "From H1"), ("Given name", "Given name")])
async def test_create_blog_post_converts_marked_text(monkeypatch, name, expected_name):
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", _CreateOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["create_blog_post"](
            instance="demo", website_id=10, blog_id=20, name=name, ctx=None,
            content="H1: From H1\n\nH2: Part\n\n• one  \n\n• two", content_format="marked_text",
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {"post_id": 56}
    assert _CreateOdoo.captured["name"] == expected_name
    assert _CreateOdoo.captured["content"] == "<h2>Part</h2>\n<ul>\n<li>one</li>\n<li>two</li>\n</ul>"


@pytest.mark.asyncio
async def test_create_blog_post_requires_name_or_h1_and_known_format(monkeypatch):
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", _CreateOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        with pytest.raises(ValueError, match="name must not be empty"):
            await tools["create_blog_post"](instance="demo", website_id=10, blog_id=20, ctx=None,
                                            content="Body only", content_format="marked_text")
        with pytest.raises(ValueError, match="content_format"):
            await tools["create_blog_post"](instance="demo", website_id=10, blog_id=20, name="x",
                                            ctx=None, content="x", content_format="markdown")
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
async def test_update_blog_post_converts_marked_text_and_keeps_html_default(monkeypatch):
    writes = []

    class FakeOdoo:
        def __init__(self, instance): pass
        async def search_read(self, model, domain, fields, limit, order="id desc"): return [{"id": 55}]
        async def write(self, model, record_id, values, context=None):
            writes.append(values)
            return True
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        await tools["update_blog_post"](instance="demo", website_id=10, post_id=55, ctx=None,
                                        content="H1: New title\n\nText & more", content_format="marked_text")
        await tools["update_blog_post"](instance="demo", website_id=10, post_id=55, ctx=None,
                                        content="H1: kept as given")
    finally:
        current_mcp_user.reset(reset)
    assert writes == [
        {"name": "New title", "content": "<p>Text &amp; more</p>"},
        {"content": "H1: kept as given"},
    ]


def _translation_odoo(captured):
    class FakeOdoo:
        def __init__(self, instance): pass
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            if model == "blog.post": return [{"id": 55}]
            return [{"id": 2, "code": "en_US"}, {"id": 3, "code": "es_ES"}]
        async def read(self, model, record_id, fields, context=None):
            if model == "blog.post":
                assert fields == ["content"] and context == {"lang": "en_US"}
                return {"content": "<h2>Section</h2>\n<ul>\n<li>first</li>\n<li>second</li>\n</ul>"}
            return {"language_ids": [2, 3], "default_lang_id": [2, "English"]}
        async def get_field_translations(self, *args, **kwargs):
            return ([
                {"lang": "en_US", "source": term, "value": ""} for term in ("Section", "first", "second")
            ], {})
        async def update_field_translations(self, model, record_id, field_name, translations, source_lang):
            captured["terms"] = translations
            return True
        async def write(self, model, record_id, values, context=None):
            captured["values"] = values
            return True
        async def close(self): pass
    return FakeOdoo


TRANSLATION_ARGS = dict(
    instance="demo", website_id=10, post_id=55, lang="es_ES", subtitle="s",
    website_meta_title="t", website_meta_description="d", website_meta_keywords="k", ctx=None,
)


@pytest.mark.asyncio
async def test_set_blog_post_translation_pairs_marked_text_terms(monkeypatch):
    captured = {}
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", _translation_odoo(captured))
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        result = await tools["set_blog_post_translation"](
            **TRANSLATION_ARGS, content_marked_text="H1: Título\n\nH2: Sección\n\n• primero\n\n• segundo",
        )
    finally:
        current_mcp_user.reset(reset)
    assert result == {"updated": True}
    assert captured["terms"] == {"es_ES": {"Section": "Sección", "first": "primero", "second": "segundo"}}
    assert captured["values"]["name"] == "Título"


@pytest.mark.asyncio
async def test_set_blog_post_translation_rejects_mismatched_marked_text_without_writing(monkeypatch):
    captured = {}
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", _translation_odoo(captured))
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        with pytest.raises(ValueError, match="same structure"):
            await tools["set_blog_post_translation"](
                **TRANSLATION_ARGS, name="Título", content_marked_text="H2: Sección\n\nprimero\n\nsegundo",
            )
        with pytest.raises(ValueError, match="exactly one"):
            await tools["set_blog_post_translation"](
                **TRANSLATION_ARGS, name="x", content_terms={}, content_marked_text="x",
            )
        with pytest.raises(ValueError, match="name is required"):
            await tools["set_blog_post_translation"](**TRANSLATION_ARGS, content_terms={})
    finally:
        current_mcp_user.reset(reset)
    assert captured == {}


def _primary_lang_odoo(captured):
    class FakeOdoo:
        def __init__(self, instance): pass
        async def read(self, model, record_id, fields, context=None):
            if model == "blog.blog": return {"id": 20, "website_id": [10, "Main"]}
            return {"language_ids": [2, 3], "default_lang_id": [2, "English"]}
        async def search_read(self, model, domain, fields, limit, order="id desc"):
            if model == "blog.post": return [{"id": 55}]
            return [{"id": 2, "code": "en_US"}, {"id": 3, "code": "es_ES"}]
        async def create(self, model, values, context=None):
            captured["create"] = (values, context)
            return 57
        async def write(self, model, record_id, values, context=None):
            captured["write"] = (values, context)
            return True
        async def close(self): pass
    return FakeOdoo


@pytest.mark.asyncio
async def test_blog_post_lang_must_be_website_primary_language(monkeypatch):
    captured = {}
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", _primary_lang_odoo(captured))
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(mcp_user("blog_editor"))
    try:
        assert await tools["create_blog_post"](instance="demo", website_id=10, blog_id=20, name="Post",
                                               ctx=None, lang="en_US") == {"post_id": 57}
        assert await tools["update_blog_post"](instance="demo", website_id=10, post_id=55, ctx=None,
                                               name="Post", lang="en_US") == {"updated": True}
        assert captured["create"][1] == {"lang": "en_US"}
        assert captured["write"][1] == {"lang": "en_US"}
        captured.clear()
        with pytest.raises(ValueError, match="primary language is en_US, not es_ES"):
            await tools["create_blog_post"](instance="demo", website_id=10, blog_id=20, name="Post",
                                            ctx=None, lang="es_ES")
        with pytest.raises(ValueError, match="primary language is en_US, not es_ES"):
            await tools["update_blog_post"](instance="demo", website_id=10, post_id=55, ctx=None,
                                            name="Post", lang="es_ES")
    finally:
        current_mcp_user.reset(reset)
    assert captured == {}


def test_cover_image_url_reads_background_image():
    from odoo_mcp.server import _cover_image_url
    base = "https://www.example.test/"
    assert _cover_image_url('{"background-image": "url(/web/image/12-ab/cover.png)"}', base) == \
        "https://www.example.test/web/image/12-ab/cover.png"
    assert _cover_image_url({"background-image": "url('/web/image/ir.attachment/7/datas')"}, base) == \
        "https://www.example.test/web/image/ir.attachment/7/datas"
    assert _cover_image_url('{"background-image": "none"}', base) is None
    assert _cover_image_url("not json", base) is None
    assert _cover_image_url(False, base) is None
