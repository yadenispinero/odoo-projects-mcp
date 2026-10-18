import pytest

from odoo_mcp.auth import MCPUser, current_mcp_user
from odoo_mcp.config import OdooInstance
from odoo_mcp.server import create_server


def tools_for(server):
    return {tool.name: tool.fn for tool in server._tool_manager.list_tools()}


def task_editor():
    return MCPUser("editor", "digest", frozenset({"task_editor"}), frozenset({"demo"}))


@pytest.mark.asyncio
async def test_create_task_sets_assignees(monkeypatch):
    captured = {}

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def create(self, model, values):
            captured.update(model=model, values=values)
            return 158

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        result = await tools["create_task"](
            instance="demo", name="Assigned task", project_id=3, user_ids=[5, 9], ctx=None,
        )
    finally:
        current_mcp_user.reset(reset)

    assert result == {"task_id": 158}
    assert captured == {
        "model": "project.task",
        "values": {"name": "Assigned task", "project_id": 3, "user_ids": [[6, 0, [5, 9]]]},
    }


@pytest.mark.asyncio
async def test_update_task_sets_or_clears_assignees(monkeypatch):
    writes = []

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def write(self, model, record_id, values):
            writes.append((model, record_id, values))
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        await tools["update_task"](instance="demo", task_id=158, user_ids=[5, 9], ctx=None)
        await tools["update_task"](instance="demo", task_id=158, user_ids=[], ctx=None)
    finally:
        current_mcp_user.reset(reset)

    assert writes == [
        ("project.task", 158, {"user_ids": [[6, 0, [5, 9]]]}),
        ("project.task", 158, {"user_ids": [[6, 0, []]]}),
    ]


@pytest.mark.asyncio
async def test_task_assignee_ids_must_be_positive(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance):
            raise AssertionError("Invalid assignees must be rejected before calling Odoo")

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        with pytest.raises(ValueError, match="user_id must be a positive integer"):
            await tools["create_task"](
                instance="demo", name="Bad assignment", project_id=3, user_ids=[0], ctx=None,
            )
    finally:
        current_mcp_user.reset(reset)
