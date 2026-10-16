from __future__ import annotations

from typing import Any

import httpx

from .config import OdooInstance


class OdooApiError(RuntimeError):
    """An Odoo API call failed without leaking authentication details."""


class OdooJson2:
    def __init__(self, instance: OdooInstance, client: httpx.AsyncClient | None = None):
        self.instance = instance
        self.client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def version(self) -> dict[str, Any]:
        try:
            response = await self.client.get(f"{self.instance.base_url}/web/version")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OdooApiError("Could not read Odoo version endpoint") from exc

    async def test_credentials(self) -> None:
        """Check the configured credential/database with a minimal authenticated read."""
        result = await self.search_read("project.project", [], ["id"], 1)
        if not isinstance(result, list):
            raise OdooApiError("Odoo returned an unexpected credential-check response")

    async def call(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        if not self.instance.api_key:
            raise OdooApiError("No API key is configured for this Odoo instance")
        headers = {
            "Authorization": f"Bearer {self.instance.api_key}",
            "Content-Type": "application/json",
        }
        if self.instance.database:
            headers["X-Odoo-Database"] = self.instance.database
        try:
            response = await self.client.post(
                f"{self.instance.base_url}/json/2/{model}/{method}",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            # Do not propagate response bodies; Odoo tracebacks can include sensitive details.
            raise OdooApiError(f"Odoo rejected {model}.{method} (HTTP {exc.response.status_code})") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OdooApiError(f"Could not call Odoo method {model}.{method}") from exc

    async def search_read(
        self, model: str, domain: list, fields: list[str], limit: int, order: str = "id desc"
    ) -> list[dict[str, Any]]:
        result = await self.call(model, "search_read", {
            "domain": domain, "fields": fields, "limit": limit, "order": order,
        })
        if not isinstance(result, list):
            raise OdooApiError(f"Unexpected response from {model}.search_read")
        return result

    async def read(self, model: str, record_id: int, fields: list[str]) -> dict[str, Any]:
        result = await self.call(model, "read", {"ids": [record_id], "fields": fields})
        if not isinstance(result, list) or not result:
            raise OdooApiError(f"No {model} record found with id {record_id}")
        return result[0]

    async def create(self, model: str, values: dict[str, Any]) -> int:
        result = await self.call(model, "create", {"vals_list": [values]})
        if not isinstance(result, int):
            raise OdooApiError(f"Unexpected response from {model}.create")
        return result

    async def write(self, model: str, record_id: int, values: dict[str, Any]) -> bool:
        result = await self.call(model, "write", {"ids": [record_id], "vals": values})
        if result is not True:
            raise OdooApiError(f"Unexpected response from {model}.write")
        return result
