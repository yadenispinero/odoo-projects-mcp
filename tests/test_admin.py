import hashlib

import httpx
import pytest
from starlette.applications import Starlette

from odoo_mcp.admin import ADMIN_PAGE, ROLE_GROUPS, create_admin_routes
from odoo_mcp.auth import MCPUser, UserDirectory
from odoo_mcp.config import InstanceDirectory, OdooInstance
from odoo_mcp.odoo_api import OdooApiError


def test_task_lifecycle_roles_are_exposed_in_admin_ui():
    roles = {role for group in ROLE_GROUPS for role in group["roles"]}
    assert {"task_editor", "task_archiver", "project_manager"} <= roles
    assert "task_archiver: 'Archivar y restaurar tareas'" in ADMIN_PAGE


@pytest.fixture
async def admin_client(tmp_path, monkeypatch):
    token = "owner-test-token-that-is-long-enough"
    owner = MCPUser("owner", hashlib.sha256(token.encode()).hexdigest(), frozenset({"administrator"}))
    directory = UserDirectory([owner], instance_names=["demo"])
    instances = InstanceDirectory(
        str(tmp_path / "odoo-instances.json"),
        {"demo": OdooInstance("demo", "https://demo.odoo.example.test", "demo-key")},
    )
    app = Starlette(routes=create_admin_routes(directory, instance_directory=instances))

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def test_credentials(self):
            if self.instance.base_url.startswith("https://invalid"):
                raise OdooApiError("Odoo rejected project.project.search_read (HTTP 401)")

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.admin.OdooJson2", FakeOdoo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, instances


async def test_create_instance_requires_successful_authenticated_read(admin_client):
    client, instances = admin_client
    response = await client.post("/admin/api/instances", json={
        "name": "new-target", "base_url": "https://invalid.odoo.example.test", "api_key": "test-key",
    })
    assert response.status_code == 400
    assert instances.names(active_only=False) == ["demo"]

    response = await client.post("/admin/api/instances", json={
        "name": "new-target", "base_url": "https://valid.odoo.example.test", "api_key": "test-key",
    })
    assert response.status_code == 201
    assert instances.names(active_only=False) == ["demo", "new-target"]


async def test_update_instance_is_not_saved_when_credential_check_fails(admin_client):
    client, instances = admin_client
    response = await client.put("/admin/api/instances/demo", json={
        "base_url": "https://invalid.odoo.example.test", "database": "", "api_key": "",
        "clear_api_key": False,
    })
    assert response.status_code == 400
    record = instances.public_records()[0]
    assert record["base_url"] == "https://demo.odoo.example.test"
    assert record["api_key_configured"] is True


async def test_api_key_removal_requires_deactivation_and_blocks_reactivation(admin_client):
    client, instances = admin_client
    response = await client.post("/admin/api/instances", json={
        "name": "other", "base_url": "https://other.odoo.example.test", "api_key": "other-key",
    })
    assert response.status_code == 201

    response = await client.put("/admin/api/instances/demo/status", json={"active": False})
    assert response.status_code == 200
    response = await client.put("/admin/api/instances/demo", json={
        "base_url": "https://demo.odoo.example.test", "database": "", "api_key": "",
        "clear_api_key": True,
    })
    assert response.status_code == 200
    assert instances.public_records()[0]["api_key_configured"] is False

    response = await client.put("/admin/api/instances/demo/status", json={"active": True})
    assert response.status_code == 400
