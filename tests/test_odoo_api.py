import json

import httpx
import pytest
import xmlrpc.client

from odoo_mcp.config import OdooInstance
from odoo_mcp.odoo_api import OdooApiError, OdooJson2


def xmlrpc_response(value):
    return httpx.Response(200, content=xmlrpc.client.dumps((value,), methodresponse=True).encode())


@pytest.mark.asyncio
async def test_json2_search_read_uses_bearer_and_database_header():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["database"] = request.headers["x-odoo-database"]
        seen["payload"] = request.read()
        return httpx.Response(200, json=[{"id": 7, "name": "Website"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret", "db1"), client)
    result = await api.search_read("project.project", [], ["id", "name"], 10)
    assert result == [{"id": 7, "name": "Website"}]
    assert seen["url"].endswith("/json/2/project.project/search_read")
    assert seen["auth"] == "Bearer secret"
    assert seen["database"] == "db1"
    assert b'"limit":10' in seen["payload"]
    await client.aclose()


@pytest.mark.asyncio
async def test_json2_archived_record_context_is_in_payload():
    calls = []

    def handler(request):
        calls.append((request.url.path, request.read()))
        if request.url.path.endswith("/res.users/context_get"):
            return httpx.Response(200, json={"lang": "es_419"})
        if request.url.path.endswith("/search_read"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"id": 7, "name": "Archived"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret"), client)
    context = {"active_test": False}
    await api.search_read("project.task", [], ["id"], 10, context=context)
    await api.read("project.project", 7, ["id", "name"], context=context)
    assert [(path, json.loads(body)) for path, body in calls] == [
        ("/json/2/res.users/context_get", {}),
        ("/json/2/project.task/search_read", {
            "domain": [], "fields": ["id"], "limit": 10, "order": "id desc",
            "context": {**context, "lang": "es_419"},
        }),
        ("/json/2/project.project/read", {
            "ids": [7], "fields": ["id", "name"], "context": {**context, "lang": "es_419"},
        }),
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_missing_api_key_fails_before_request():
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test"))
    with pytest.raises(OdooApiError, match="No API key"):
        await api.call("project.task", "read", {})
    await api.close()


@pytest.mark.asyncio
async def test_error_does_not_expose_response_body():
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, text="token secret should not appear")
    ))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret"), client)
    with pytest.raises(OdooApiError) as error:
        await api.call("project.task", "read", {})
    assert "token secret" not in str(error.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_create_uses_json2_vals_list_shape():
    seen = {}

    def handler(request):
        seen["payload"] = request.read()
        # Odoo 19 JSON-2 returns the IDs created for vals_list.
        return httpx.Response(200, json=[42])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret"), client)
    assert await api.create("project.task", {"name": "Test"}) == 42
    assert b'"vals_list":[{"name":"Test"}]' in seen["payload"]
    await client.aclose()


@pytest.mark.asyncio
async def test_create_accepts_xmlrpc_scalar_id():
    def handler(request):
        params, method = xmlrpc.client.loads(request.read())
        if request.url.path.endswith("/common"):
            assert method == "authenticate"
            return xmlrpc_response(7)
        assert request.url.path.endswith("/object")
        assert method == "execute_kw"
        if params[3:5] == ("res.users", "context_get"):
            return xmlrpc_response({"lang": "es_419"})
        assert params[3:6] == ("project.task", "create", [{"name": "Test"}])
        assert params[6] == {"context": {"lang": "es_419"}}
        return xmlrpc_response(42)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance(
        "test", "https://odoo.example.test", "secret", "db1", "xmlrpc", "user@example.test"
    ), client)
    assert await api.create("project.task", {"name": "Test"}) == 42
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("response_value", [[], [1, 2], [True], 0, True, "42"])
async def test_create_rejects_unexpected_ids_response(response_value):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_value)
    ))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret"), client)
    with pytest.raises(OdooApiError, match="Unexpected response"):
        await api.create("project.task", {"name": "Test"})
    await client.aclose()


@pytest.mark.parametrize(("method", "payload", "expected"), [
    ("search_read", {"domain": [["name", "ilike", "x"]], "fields": ["id"], "limit": 3, "order": "id"},
     ([[["name", "ilike", "x"]]], {"fields": ["id"], "limit": 3, "order": "id"})),
    ("search_read", {"domain": [], "fields": ["id"], "limit": 3, "order": "id",
                      "context": {"active_test": False}},
     ([[]], {"fields": ["id"], "limit": 3, "order": "id", "context": {"active_test": False}})),
    ("read", {"ids": [9], "fields": ["name"]}, ([[9]], {"fields": ["name"]})),
    ("read", {"ids": [9], "fields": ["name"], "context": {"active_test": False}},
     ([[9]], {"fields": ["name"], "context": {"active_test": False}})),
    ("create", {"vals_list": [{"name": "X"}]}, ([{"name": "X"}], {})),
    ("write", {"ids": [9], "vals": {"name": "Y"}}, ([[9], {"name": "Y"}], {})),
    ("write", {"ids": [9], "vals": {"name": "Y"}, "context": {"lang": "es_ES"}},
     ([[9], {"name": "Y"}], {"context": {"lang": "es_ES"}})),
    ("get_field_translations", {"ids": [9], "field_name": "content", "langs": ["en_US", "es_419"]},
     ([[9], "content", ["en_US", "es_419"]], {})),
    ("update_field_translations", {"ids": [9], "field_name": "content",
                                    "translations": {"es_419": {"Hello": "Hola"}},
                                    "source_lang": "en_US"},
     ([[9], "content", {"es_419": {"Hello": "Hola"}}], {"source_lang": "en_US"})),
    ("unlink", {"ids": [9]}, ([[9]], {})),
])
def test_xmlrpc_operation_arguments(method, payload, expected):
    assert OdooJson2._xmlrpc_arguments(method, payload) == expected


@pytest.mark.asyncio
async def test_json2_unlink_uses_ids_and_returns_true():
    seen = {}
    def handler(request):
        payload = json.loads(request.read())
        if request.url.path.endswith("/context_get"):
            return httpx.Response(200, json={"lang": "es_419"})
        seen["payload"] = payload
        return httpx.Response(200, json=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret"), client)
    assert await api.unlink("ir.attachment", 9) is True
    assert seen["payload"] == {"ids": [9], "context": {"lang": "es_419"}}
    await client.aclose()


@pytest.mark.asyncio
async def test_xmlrpc_unlink_uses_ids_argument():
    def handler(request):
        params, method = xmlrpc.client.loads(request.read())
        if request.url.path.endswith("/common"):
            assert method == "authenticate"
            return xmlrpc_response(7)
        if params[3:5] == ("res.users", "context_get"):
            return xmlrpc_response({"lang": "es_419"})
        assert (params[3], params[4], params[5]) == ("ir.attachment", "unlink", [[9]])
        assert params[6] == {"context": {"lang": "es_419"}}
        return xmlrpc_response(True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance(
        "test", "https://odoo.example.test", "secret", "db1", "xmlrpc", "user@example.test"
    ), client)
    assert await api.unlink("ir.attachment", 9) is True
    await client.aclose()


@pytest.mark.asyncio
async def test_json2_write_preserves_language_context():
    seen = {}

    def handler(request):
        payload = json.loads(request.read())
        if request.url.path.endswith("/context_get"):
            return httpx.Response(200, json={"lang": "es_419"})
        seen["payload"] = payload
        return httpx.Response(200, json=True)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret"), client)
    assert await api.write("blog.post", 9, {"name": "Título"}, context={"lang": "es_ES"}) is True
    assert seen["payload"] == {
        "ids": [9], "vals": {"name": "Título"}, "context": {"lang": "es_ES"},
    }
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["json2", "xmlrpc"])
@pytest.mark.parametrize(
    ("explicit_context", "expected_lang"),
    [(None, "es_419"), ({"lang": "de_DE"}, "de_DE")],
)
async def test_write_uses_default_user_language_and_explicit_context_overrides(
    api_type, explicit_context, expected_lang,
):
    model_payloads = []

    def handler(request):
        if api_type == "json2":
            payload = json.loads(request.read())
            if request.url.path.endswith("/res.users/context_get"):
                return httpx.Response(200, json={"lang": "es_419"})
            model_payloads.append(payload)
            return httpx.Response(200, json=True)

        params, method = xmlrpc.client.loads(request.read())
        if request.url.path.endswith("/common"):
            return xmlrpc_response(7)
        if params[3:5] == ("res.users", "context_get"):
            return xmlrpc_response({"lang": "es_419"})
        model_payloads.append(params[6])
        return xmlrpc_response(True)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance(
        "test", "https://odoo.example.test", "secret", "db1",
        api_type, "user@example.test" if api_type == "xmlrpc" else None,
    ), client)
    assert await api.write("blog.post", 29, {"name": "Título"}, context=explicit_context) is True
    assert model_payloads == [
        {"ids": [29], "vals": {"name": "Título"}, "context": {"lang": expected_lang}}
        if api_type == "json2" else {"context": {"lang": expected_lang}}
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_xmlrpc_login_failure_stops_before_model_call():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return xmlrpc_response(False)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("legacy", "https://odoo.example.test", "secret", "db1",
                                  "xmlrpc", "user@example.test"), client)
    with pytest.raises(OdooApiError, match="authentication failed"):
        await api.search_read("project.project", [], ["id"], 1)
    assert requests == ["/xmlrpc/2/common"]
    await client.aclose()


@pytest.mark.asyncio
async def test_xmlrpc_fault_does_not_expose_api_key():
    secret = "top-secret-api-key"

    def handler(request):
        return httpx.Response(200, content=xmlrpc.client.dumps(
            xmlrpc.client.Fault(1, f"failure included {secret}"),
            methodresponse=True,
        ).encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("legacy", "https://odoo.example.test", secret, "db1",
                                  "xmlrpc", "user@example.test"), client)
    with pytest.raises(OdooApiError) as exc:
        await api.version()
    assert secret not in str(exc.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_xmlrpc_version_uses_common_xmlrpc_method():
    def handler(request):
        params, method = xmlrpc.client.loads(request.read())
        assert request.url.path == "/xmlrpc/2/common"
        assert method == "version"
        assert params == ()
        return xmlrpc_response({"server_version": "18.0+e", "server_version_info": [18, 0, 0]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("legacy", "https://odoo.example.test", api_type="xmlrpc"), client)
    assert (await api.version())["server_version"].startswith("18")
    await client.aclose()


@pytest.mark.asyncio
async def test_xmlrpc_archived_record_context_is_execute_kw_argument():
    calls = []

    def handler(request):
        params, method = xmlrpc.client.loads(request.read())
        if request.url.path.endswith("/common"):
            return xmlrpc_response(7)
        assert method == "execute_kw"
        if params[3:5] == ("res.users", "context_get"):
            return xmlrpc_response({"lang": "es_419"})
        calls.append(params[3:])
        return xmlrpc_response([] if params[4] == "search_read" else [{"id": 7, "name": "Archived"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance(
        "legacy", "https://odoo.example.test", "secret", "db1", "xmlrpc", "user@example.test"
    ), client)
    context = {"active_test": False}
    await api.search_read("project.task", [], ["id"], 10, context=context)
    await api.read("project.project", 7, ["id", "name"], context=context)
    assert calls == [
        ("project.task", "search_read", [[]], {
            "fields": ["id"], "limit": 10, "order": "id desc", "context": {**context, "lang": "es_419"},
        }),
        ("project.project", "read", [[7]], {
            "fields": ["id", "name"], "context": {**context, "lang": "es_419"},
        }),
    ]
    await client.aclose()
