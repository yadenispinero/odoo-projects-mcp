from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class OdooInstance:
    name: str
    base_url: str
    api_key: str | None = None
    database: str | None = None
    api_type: str = "json2"
    username: str | None = None


def validate_instance_name(name: object) -> str:
    if not isinstance(name, str):
        raise ValueError("Instance name must be text")
    normalized = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", normalized):
        raise ValueError("Instance name must be 1 to 64 letters, numbers, hyphens, or underscores")
    return normalized


def validate_base_url(base_url: object) -> str:
    if not isinstance(base_url, str):
        raise ValueError("Base URL must be text")
    normalized = base_url.strip().rstrip("/")
    parts = urlsplit(normalized)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment):
        raise ValueError("Base URL must be an HTTPS URL without embedded credentials, query, or fragment")
    return normalized


def load_instances(path: str | None = None) -> dict[str, OdooInstance]:
    """Load named Odoo targets from a JSON file; API keys are resolved from env vars."""
    config_path = path or os.getenv("ODOO_INSTANCES_FILE", "odoo-instances.json")
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Odoo instance configuration must be a non-empty JSON object")

    instances: dict[str, OdooInstance] = {}
    for name, item in raw.items():
        if not isinstance(name, str) or not name or not isinstance(item, dict):
            raise ValueError("Each Odoo instance must have a non-empty name and object config")
        base_url = item.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith("https://"):
            raise ValueError(f"Instance {name!r} base_url must use HTTPS")
        api_key_env = item.get("api_key_env")
        api_key = os.getenv(api_key_env) if api_key_env else None
        instances[name] = OdooInstance(
            name=name,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            database=item.get("database"),
            api_type=item.get("api_type", "json2"),
            username=item.get("username"),
        )
    return instances


class InstanceDirectory:
    """Persist configured Odoo targets outside the source tree in a private JSON file."""

    def __init__(self, path: str, initial_instances: dict[str, OdooInstance]):
        self.path = Path(path)
        if self.path.exists():
            self._records = self._read()
        else:
            self._records = {
                validate_instance_name(name): {
                    "base_url": validate_base_url(instance.base_url),
                    "database": instance.database,
                    "api_key": instance.api_key,
                    "api_type": instance.api_type,
                    "username": instance.username,
                    "active": True,
                }
                for name, instance in initial_instances.items()
            }
            if not self._records:
                raise ValueError("At least one Odoo instance must be configured")
            self._write()

    def _read(self) -> dict[str, dict[str, object]]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not raw:
            raise ValueError("Persisted Odoo instance configuration must be a non-empty JSON object")
        records: dict[str, dict[str, object]] = {}
        for name, item in raw.items():
            if not isinstance(item, dict):
                raise ValueError("Each persisted Odoo instance must be an object")
            normalized_name = validate_instance_name(name)
            base_url = validate_base_url(item.get("base_url"))
            database = item.get("database")
            api_key = item.get("api_key")
            api_type = item.get("api_type", "json2")
            username = item.get("username")
            active = item.get("active", True)
            if database is not None and not isinstance(database, str):
                raise ValueError(f"Instance {normalized_name!r} database must be text")
            if api_key is not None and not isinstance(api_key, str):
                raise ValueError(f"Instance {normalized_name!r} API key must be text")
            if not isinstance(active, bool):
                raise ValueError(f"Instance {normalized_name!r} active flag must be boolean")
            if api_type not in {"json2", "xmlrpc"}:
                raise ValueError(f"Instance {normalized_name!r} api_type must be json2 or xmlrpc")
            if username is not None and not isinstance(username, str):
                raise ValueError(f"Instance {normalized_name!r} username must be text")
            records[normalized_name] = {
                "base_url": base_url,
                "database": database.strip() if isinstance(database, str) and database.strip() else None,
                "api_key": api_key,
                "api_type": api_type,
                "username": username.strip() if isinstance(username, str) and username.strip() else None,
                "active": active,
            }
        if not any(item["active"] for item in records.values()):
            raise ValueError("At least one Odoo instance must remain active")
        return records

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(self._records, ensure_ascii=False, indent=2) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix=".odoo-instances-", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def active_instances(self) -> dict[str, OdooInstance]:
        return {
            name: OdooInstance(name, str(item["base_url"]), item["api_key"], item["database"],
                               str(item["api_type"]), item["username"])
            for name, item in self._records.items() if item["active"]
        }

    def names(self, *, active_only: bool = True) -> list[str]:
        return sorted(name for name, item in self._records.items()
                      if not active_only or item["active"])

    def public_records(self) -> list[dict[str, object]]:
        return [
            {"name": name, "base_url": item["base_url"], "database": item["database"],
             "api_type": item["api_type"], "username": item["username"],
             "active": item["active"], "api_key_configured": bool(item["api_key"])}
            for name, item in sorted(self._records.items())
        ]

    def candidate(self, name: object, base_url: object, database: object = None,
                  api_key: object = None, clear_api_key: bool = False,
                  api_type: object = "json2", username: object = None) -> OdooInstance:
        normalized_name = validate_instance_name(name)
        normalized_url = validate_base_url(base_url)
        if database is not None and not isinstance(database, str):
            raise ValueError("Database must be text")
        normalized_database = database.strip() if isinstance(database, str) and database.strip() else None
        if normalized_database and len(normalized_database) > 255:
            raise ValueError("Database name must be 255 characters or fewer")
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("API key must be text")
        if isinstance(api_key, str) and len(api_key) > 4096:
            raise ValueError("API key is too long")
        if api_type not in {"json2", "xmlrpc"}:
            raise ValueError("API type must be json2 or xmlrpc")
        if username is not None and not isinstance(username, str):
            raise ValueError("Username must be text")
        normalized_username = username.strip() if isinstance(username, str) and username.strip() else None
        if username is None and normalized_name in self._records:
            normalized_username = self._records[normalized_name].get("username")
        if api_type == "xmlrpc" and not normalized_username:
            raise ValueError("A username/login is required for XML-RPC connections")
        previous = self._records.get(normalized_name, {})
        key = None if clear_api_key else ((api_key.strip() if api_key and api_key.strip() else None)
                                          or previous.get("api_key"))
        return OdooInstance(normalized_name, normalized_url, key, normalized_database,
                            str(api_type), normalized_username)

    def save(self, instance: OdooInstance, *, clear_api_key: bool = False) -> None:
        name = validate_instance_name(instance.name)
        if name not in self._records and not instance.api_key:
            raise ValueError("An API key is required when adding an Odoo instance")
        previous = self._records.get(name, {})
        self._records[name] = {
            "base_url": validate_base_url(instance.base_url),
            "database": instance.database,
            "api_type": instance.api_type,
            "username": instance.username,
            "api_key": None if clear_api_key else (instance.api_key or previous.get("api_key")),
            "active": bool(previous.get("active", True)),
        }
        self._write()

    def set_active(self, name: str, active: bool) -> None:
        if name not in self._records:
            raise KeyError(name)
        if not active and self._records[name]["active"]:
            if sum(bool(item["active"]) for item in self._records.values()) <= 1:
                raise ValueError("At least one Odoo instance must remain active")
        self._records[name]["active"] = active
        self._write()
