import httpx
import pytest

from odoo_mcp.config import OdooInstance
from odoo_mcp.odoo_api import OdooApiError, OdooJson2


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
        return httpx.Response(200, json=42)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = OdooJson2(OdooInstance("test", "https://odoo.example.test", "secret"), client)
    assert await api.create("project.task", {"name": "Test"}) == 42
    assert b'"vals_list":[{"name":"Test"}]' in seen["payload"]
    await client.aclose()
