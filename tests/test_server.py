import pytest

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
