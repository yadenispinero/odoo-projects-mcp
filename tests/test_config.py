import json

import pytest

from odoo_mcp.config import InstanceDirectory, OdooInstance, load_instances


def test_load_named_https_instance_and_env_api_key(tmp_path, monkeypatch):
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({"test": {
        "base_url": "https://odoo.example.test/", "database": "db1", "api_key_env": "TEST_KEY"
    }}))
    monkeypatch.setenv("TEST_KEY", "secret")
    instance = load_instances(str(path))["test"]
    assert instance.base_url == "https://odoo.example.test"
    assert instance.database == "db1"
    assert instance.api_key == "secret"


def test_reject_http_target(tmp_path):
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({"test": {"base_url": "http://odoo.example.test"}}))
    with pytest.raises(ValueError, match="HTTPS"):
        load_instances(str(path))


@pytest.mark.parametrize("api_type", ["soap", None, 18])
def test_reject_invalid_api_type(tmp_path, api_type):
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({"test": {"base_url": "https://odoo.example.test", "api_type": api_type}}))
    with pytest.raises(ValueError, match="api_type"):
        load_instances(str(path))


def test_xmlrpc_instance_requires_username_and_database(tmp_path):
    directory = InstanceDirectory(str(tmp_path / "instances.json"), {
        "demo": OdooInstance("demo", "https://odoo.example.test", "key")
    })
    with pytest.raises(ValueError, match="username/login"):
        directory.candidate("demo", "https://odoo.example.test", database="db1",
                            api_type="xmlrpc")
    with pytest.raises(ValueError, match="database"):
        directory.candidate("demo", "https://odoo.example.test", api_type="xmlrpc",
                            username="user@example.test")


def test_xmlrpc_bootstrap_config_requires_username(tmp_path):
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({"test": {
        "base_url": "https://odoo.example.test", "api_type": "xmlrpc", "database": "db1"
    }}))
    with pytest.raises(ValueError, match="username/login"):
        load_instances(str(path))
