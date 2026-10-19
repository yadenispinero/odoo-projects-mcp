import hashlib
import asyncio
import pytest
import httpx

from odoo_mcp.auth import BearerAuthMiddleware, MCPUser, UserDirectory
from odoo_mcp.config import OdooInstance
from odoo_mcp.server import _limit, _positive_id, create_server


def test_bounds_validation():
    assert _limit(100) == 100
    with pytest.raises(ValueError):
        _limit(101)
    with pytest.raises(ValueError):
        _positive_id(0)


def test_server_constructs_with_explicit_named_target():
    server = create_server({"testing": OdooInstance("testing", "https://odoo.example.test")})
    assert server.name == "Odoo Projects and Website"


def test_stateless_http_tools_call_works_in_one_authenticated_post(monkeypatch):
    token = "t" * 64
    user = MCPUser("viewer", hashlib.sha256(token.encode()).hexdigest(), frozenset({"viewer"}),
                   frozenset({"demo"}))
    directory = UserDirectory([user], instance_names=["demo"])

    class FakeOdoo:
        def __init__(self, instance): self.instance = instance
        async def search_read(self, *args, **kwargs): return []
        async def close(self): pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    mcp = create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}, directory)
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, directory=directory, mcp_path="/mcp")

    async def call_once():
        async with mcp.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client:
                return await asyncio.wait_for(client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2025-11-25",
                    },
                    json={
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "search_pages", "arguments": {"instance": "demo"}},
                    },
                ), timeout=5)

    response = asyncio.run(call_once())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {"result": []}


@pytest.mark.asyncio
async def test_live_odoo_19_version_endpoint():
    import os

    import httpx

    from odoo_mcp.odoo_api import OdooJson2

    base_url = os.getenv("ODOO_TEST_BASE_URL")
    if not base_url:
        pytest.skip("Set ODOO_TEST_BASE_URL to enable the read-only live probe")
    api = OdooJson2(OdooInstance("testing", base_url))
    try:
        data = await api.version()
    finally:
        await api.close()
    version = data.get("server_version", data.get("version", ""))
    assert str(version).startswith("19"), f"Expected Odoo 19, received {version!r}"
