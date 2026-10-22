import pytest

from odoo_mcp.admin import AdminPortMiddleware


async def _call(admin_port, path, server_port):
    calls, sent = [], []

    async def app(scope, receive, send):
        calls.append(scope["path"])

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": path, "server": ("0.0.0.0", server_port), "headers": []}
    await AdminPortMiddleware(app, admin_port)(scope, None, send)
    status = sent[0]["status"] if sent else None
    return calls, status


@pytest.mark.parametrize("path", ["/admin", "/admin/", "/admin/api/users"])
async def test_admin_paths_are_hidden_on_the_mcp_port(path):
    calls, status = await _call(8001, path, 8000)
    assert calls == [] and status == 404


@pytest.mark.parametrize("path", ["/admin", "/admin/api/users"])
async def test_admin_paths_are_served_on_the_admin_port(path):
    calls, status = await _call(8001, path, 8001)
    assert calls == [path] and status is None


async def test_mcp_path_is_hidden_on_the_admin_port():
    calls, status = await _call(8001, "/mcp", 8001)
    assert calls == [] and status == 404


async def test_mcp_path_is_served_on_the_mcp_port():
    calls, status = await _call(8001, "/mcp", 8000)
    assert calls == ["/mcp"] and status is None


async def test_paths_starting_with_admin_are_not_admin_paths():
    calls, status = await _call(8001, "/administrator", 8000)
    assert calls == ["/administrator"] and status is None


async def test_without_admin_port_everything_is_served_on_one_port():
    for path in ("/admin", "/mcp"):
        calls, status = await _call(None, path, 8000)
        assert calls == [path] and status is None
