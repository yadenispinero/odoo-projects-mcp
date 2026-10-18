import pytest

from odoo_mcp.auth import MCPUser, ROLE_TOOLS, current_mcp_user
from odoo_mcp.config import OdooInstance
from odoo_mcp.server import create_server


def tools_for(server):
    return {tool.name: tool.fn for tool in server._tool_manager.list_tools()}


def task_editor():
    return MCPUser("editor", "digest", frozenset({"task_editor"}), frozenset({"demo"}))


def user_for(role):
    return MCPUser(role, "digest", frozenset({role}), frozenset({"demo"}))


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


@pytest.mark.asyncio
async def test_list_task_stages_filters_to_project(monkeypatch):
    captured = {}

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def search_read(self, model, domain, fields, limit, order="id desc", context=None):
            captured.update(model=model, domain=domain, fields=fields, limit=limit, order=order)
            return [{"id": 2, "name": "In Progress", "sequence": 10, "fold": False}]

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        stages = await tools["list_task_stages"](instance="demo", project_id=4, ctx=None)
    finally:
        current_mcp_user.reset(reset)

    assert stages[0]["id"] == 2
    assert captured == {
        "model": "project.task.type",
        "domain": [["project_ids", "=", 4]],
        "fields": ["id", "name", "sequence", "fold"],
        "limit": 100,
        "order": "sequence asc, id asc",
    }


@pytest.mark.asyncio
async def test_set_task_stage_rejects_stage_from_other_project(monkeypatch):
    writes = []

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def read(self, model, record_id, fields, context=None):
            if model == "project.task":
                assert (record_id, fields, context) == (10, ["id", "project_id"], {"active_test": False})
                return {"id": 10, "project_id": [4, "Project 4"]}
            assert (model, record_id, fields) == ("project.task.type", 8, ["id", "project_ids"])
            return {"id": 8, "project_ids": [5]}

        async def write(self, *args):
            writes.append(args)
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        with pytest.raises(ValueError, match="Stage 8 does not belong to project 4"):
            await tools["set_task_stage"](instance="demo", task_id=10, stage_id=8, ctx=None)
    finally:
        current_mcp_user.reset(reset)
    assert writes == []


@pytest.mark.asyncio
async def test_set_task_stage_writes_stage_from_same_project(monkeypatch):
    calls = []

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def read(self, model, record_id, fields, context=None):
            if model == "project.task":
                return {"id": record_id, "project_id": [4, "Project 4"]}
            return {"id": record_id, "project_ids": [4, 8]}

        async def write(self, model, record_id, values):
            calls.append((model, record_id, values))
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        assert await tools["set_task_stage"](
            instance="demo", task_id=10, stage_id=8, ctx=None,
        ) == {"updated": True}
    finally:
        current_mcp_user.reset(reset)
    assert calls == [("project.task", 10, {"stage_id": 8})]


@pytest.mark.asyncio
async def test_set_task_state_rejects_non_odoo_states(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance):
            raise AssertionError("Invalid state must be rejected before an Odoo call")

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        with pytest.raises(ValueError, match="state must be one of"):
            await tools["set_task_state"](instance="demo", task_id=10, state="done", ctx=None)
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    "01_in_progress", "02_changes_requested", "03_approved", "04_waiting_normal", "1_done", "1_canceled",
])
async def test_set_task_state_writes_only_an_allowed_state(monkeypatch, state):
    calls = []

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def write(self, model, record_id, values):
            calls.append((model, record_id, values))
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(task_editor())
    try:
        assert await tools["set_task_state"](
            instance="demo", task_id=10, state=state, ctx=None,
        ) == {"updated": True}
    finally:
        current_mcp_user.reset(reset)
    assert calls == [("project.task", 10, {"state": state})]


@pytest.mark.asyncio
async def test_archive_tools_write_only_active(monkeypatch):
    calls = []

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def write(self, model, record_id, values):
            calls.append((model, record_id, values))
            return True

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    cases = [
        ("archive_task", user_for("task_archiver"), {"task_id": 10}, {"archived": True}),
        ("unarchive_task", user_for("task_archiver"), {"task_id": 11}, {"unarchived": True}),
        ("archive_project", user_for("project_manager"), {"project_id": 20}, {"archived": True}),
        ("unarchive_project", user_for("project_manager"), {"project_id": 21}, {"unarchived": True}),
    ]
    for tool_name, user, record, result in cases:
        reset = current_mcp_user.set(user)
        try:
            assert await tools[tool_name](instance="demo", ctx=None, **record) == result
        finally:
            current_mcp_user.reset(reset)
    assert calls == [
        ("project.task", 10, {"active": False}),
        ("project.task", 11, {"active": True}),
        ("project.project", 20, {"active": False}),
        ("project.project", 21, {"active": True}),
    ]


@pytest.mark.asyncio
async def test_role_boundaries_for_task_lifecycle_tools(monkeypatch):
    class FakeOdoo:
        def __init__(self, instance):
            raise AssertionError("Role check must reject before connecting to Odoo")

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    attempts = [
        (user_for("task_editor"), "archive_task", {"task_id": 10}),
        (user_for("task_archiver"), "set_task_stage", {"task_id": 10, "stage_id": 3}),
        (user_for("viewer"), "set_task_state", {"task_id": 10, "state": "1_done"}),
    ]
    for user, tool_name, arguments in attempts:
        reset = current_mcp_user.set(user)
        try:
            with pytest.raises(PermissionError):
                await tools[tool_name](instance="demo", ctx=None, **arguments)
        finally:
            current_mcp_user.reset(reset)

    assert {"list_task_stages", "set_task_stage", "set_task_state"} <= ROLE_TOOLS["task_editor"]
    assert {"archive_task", "unarchive_task"} == ROLE_TOOLS["task_archiver"]
    assert {"archive_project", "unarchive_project", "list_task_stages"} <= ROLE_TOOLS["project_manager"]
    assert "archive_task" not in ROLE_TOOLS["task_editor"]
    assert "set_task_stage" not in ROLE_TOOLS["task_archiver"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entity,search_tool,model", [
    ("project", "search_projects", "project.project"),
    ("task", "search_tasks", "project.task"),
])
async def test_search_archived_option_sets_active_test_context(monkeypatch, entity, search_tool, model):
    calls = []

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def search_read(self, model_name, domain, fields, limit, order="id desc", context=None):
            calls.append((model_name, context))
            rows = [{"id": 1, "active": True}, {"id": 2, "active": False}]
            return rows if context["active_test"] is False else [row for row in rows if row["active"]]

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(user_for("viewer"))
    try:
        default_rows = await tools[search_tool](instance="demo", ctx=None)
        archived_rows = await tools[search_tool](instance="demo", ctx=None, include_archived=True)
    finally:
        current_mcp_user.reset(reset)

    assert [row["id"] for row in default_rows] == [1]
    assert [row["id"] for row in archived_rows] == [1, 2]
    assert calls == [(model, {"active_test": True}), (model, {"active_test": False})]


@pytest.mark.asyncio
async def test_get_archived_task_and_project_use_active_test_false(monkeypatch):
    calls = []

    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def read(self, model, record_id, fields, context=None):
            calls.append((model, record_id, fields, context))
            return {"id": record_id, "active": False}

        async def close(self):
            pass

    monkeypatch.setattr("odoo_mcp.server.OdooJson2", FakeOdoo)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(user_for("viewer"))
    try:
        await tools["get_task"](instance="demo", task_id=10, ctx=None)
        await tools["get_project"](instance="demo", project_id=20, ctx=None)
    finally:
        current_mcp_user.reset(reset)
    assert calls == [
        ("project.task", 10, ["id", "name", "project_id", "description", "user_ids", "date_deadline", "stage_id"],
         {"active_test": False}),
        ("project.project", 20, ["id", "name", "description", "active", "privacy_visibility", "user_id", "partner_id",
                                  "date_start", "date"], {"active_test": False}),
    ]
