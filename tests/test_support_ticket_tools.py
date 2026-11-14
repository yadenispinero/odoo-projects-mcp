import pytest

from odoo_mcp.auth import MCPUser, current_mcp_user
from odoo_mcp.config import OdooInstance
from odoo_mcp.odoo_api import OdooApiError
from odoo_mcp.server import create_server


def tools_for(server):
    return {tool.name: tool.fn for tool in server._tool_manager.list_tools()}


def viewer():
    return MCPUser("viewer", "digest", frozenset({"viewer"}), frozenset({"demo"}))


def fake_odoo(calls, tickets, messages):
    class FakeOdoo:
        def __init__(self, instance):
            self.instance = instance

        async def search_read(self, model, domain, fields, limit, order="id desc",
                              context=None, offset=0):
            calls.append((model, domain, fields, limit, order, offset))
            if model == "helpdesk.ticket":
                return [dict(t) for t in tickets]
            return [dict(m) for m in messages][:limit]

        async def read(self, model, record_id, fields, context=None):
            calls.append((model, record_id, fields))
            return {"id": record_id, "name": "Ticket"}

        async def close(self):
            pass

    return FakeOdoo


async def call(monkeypatch, tool, fake, **kwargs):
    monkeypatch.setattr("odoo_mcp.server.OdooJson2", fake)
    tools = tools_for(create_server({"demo": OdooInstance("demo", "https://odoo.example.test")}))
    reset = current_mcp_user.set(viewer())
    try:
        return await tools[tool](instance="demo", ctx=None, **kwargs)
    finally:
        current_mcp_user.reset(reset)


@pytest.mark.asyncio
async def test_search_support_tickets_adds_message_count_and_pages(monkeypatch):
    calls = []
    fake = fake_odoo(calls, [{"id": 7}, {"id": 9}],
                     [{"res_id": 7}, {"res_id": 7}, {"res_id": 9}, {"res_id": 7}])
    result = await call(monkeypatch, "search_support_tickets", fake,
                        limit=2, offset=100, include_description=False)

    assert result == [{"id": 7, "message_count": 3}, {"id": 9, "message_count": 1}]
    ticket_call, message_call = calls
    assert ticket_call[0] == "helpdesk.ticket"
    assert "description" not in ticket_call[2]
    assert ticket_call[5] == 100
    assert message_call[0] == "mail.message"
    assert ["res_id", "in", [7, 9]] in message_call[1]
    assert ["message_type", "in", ["email", "comment", "email_outgoing"]] in message_call[1]


@pytest.mark.asyncio
async def test_search_support_tickets_rejects_negative_offset(monkeypatch):
    with pytest.raises(ValueError, match="offset"):
        await call(monkeypatch, "search_support_tickets", fake_odoo([], [], []), offset=-1)


@pytest.mark.asyncio
async def test_message_count_refuses_to_undercount(monkeypatch, ):
    monkeypatch.setattr("odoo_mcp.server.MAX_MESSAGES_COUNTED", 2)
    fake = fake_odoo([], [{"id": 7}], [{"res_id": 7}] * 3)
    with pytest.raises(OdooApiError, match="Too many"):
        await call(monkeypatch, "search_support_tickets", fake)


@pytest.mark.asyncio
async def test_get_support_ticket_messages_reads_conversation_oldest_first(monkeypatch):
    calls = []
    fake = fake_odoo(calls, [], [{"id": 1, "body": "<p>Hola</p>"}])
    result = await call(monkeypatch, "get_support_ticket_messages", fake, ticket_id=12, limit=10)

    assert result == [{"id": 1, "body": "<p>Hola</p>"}]
    assert calls[0] == ("helpdesk.ticket", 12, ["id"])
    model, domain, fields, limit, order, offset = calls[1]
    assert model == "mail.message"
    assert ["res_id", "=", 12] in domain and ["model", "=", "helpdesk.ticket"] in domain
    assert "body" in fields and "is_internal" in fields
    assert (limit, order, offset) == (10, "date asc, id asc", 0)
