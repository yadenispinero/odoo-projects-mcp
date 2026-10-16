import json

import pytest

from odoo_mcp.config import load_instances


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
