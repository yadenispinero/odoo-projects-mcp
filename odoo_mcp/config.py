from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OdooInstance:
    name: str
    base_url: str
    api_key: str | None = None
    database: str | None = None


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
        )
    return instances
