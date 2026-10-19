from __future__ import annotations

from typing import Any
import xmlrpc.client

import httpx

from .config import OdooInstance


class OdooApiError(RuntimeError):
    """An Odoo API call failed without leaking authentication details."""


class OdooJson2:
    def __init__(self, instance: OdooInstance, client: httpx.AsyncClient | None = None):
        self.instance = instance
        self.client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        self._owns_client = client is None
        self._default_lang: str | None = None
        self._default_lang_loaded = False

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def version(self) -> dict[str, Any]:
        if self.instance.api_type == "xmlrpc":
            result = await self._xmlrpc_request("/xmlrpc/2/common", "version", ())
            if not isinstance(result, dict):
                raise OdooApiError("Odoo returned an unexpected XML-RPC version response")
            return result
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
        payload = dict(payload)
        if (model, method) != ("res.users", "context_get"):
            lang = await self._get_default_lang()
            context = dict(payload.get("context") or {})
            if lang:
                context.setdefault("lang", lang)
            if context:
                payload["context"] = context
        if self.instance.api_type == "xmlrpc":
            if not self.instance.database or not self.instance.username:
                raise OdooApiError("XML-RPC requires a database and username/login")
            return await self._xmlrpc_call(model, method, payload)
        return await self._json2_request(model, method, payload)

    async def _get_default_lang(self) -> str | None:
        if self._default_lang_loaded:
            return self._default_lang
        try:
            if self.instance.api_type == "xmlrpc":
                uid = await self._xmlrpc_authenticate()
                context = await self._xmlrpc_request(
                    "/xmlrpc/2/object", "execute_kw",
                    (self.instance.database, uid, self.instance.api_key,
                     "res.users", "context_get", [], {}),
                )
            else:
                context = await self._json2_request("res.users", "context_get", {})
            if isinstance(context, dict) and isinstance(context.get("lang"), str):
                self._default_lang = context["lang"]
            self._default_lang_loaded = True
            return self._default_lang
        except OdooApiError:
            raise
        except Exception as exc:
            raise OdooApiError("Could not read the Odoo user's default language") from exc

    async def _json2_request(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self.instance.api_key}", "Content-Type": "application/json"}
        if self.instance.database:
            headers["X-Odoo-Database"] = self.instance.database
        try:
            response = await self.client.post(
                f"{self.instance.base_url}/json/2/{model}/{method}", headers=headers, json=payload,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise OdooApiError(f"Odoo rejected {model}.{method} (HTTP {exc.response.status_code})") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OdooApiError(f"Could not call Odoo method {model}.{method}") from exc

    async def _xmlrpc_authenticate(self) -> int:
        uid = await self._xmlrpc_request("/xmlrpc/2/common", "authenticate",
            (self.instance.database, self.instance.username, self.instance.api_key, {}))
        if not isinstance(uid, int) or uid <= 0:
            raise OdooApiError("Odoo XML-RPC authentication failed")
        return uid

    async def _xmlrpc_call(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        """Call Odoo 18's documented XML-RPC external API; the API key is the password."""
        try:
            uid = await self._xmlrpc_authenticate()
            args, kwargs = self._xmlrpc_arguments(method, payload)
            return await self._xmlrpc_request("/xmlrpc/2/object", "execute_kw",
                (self.instance.database, uid, self.instance.api_key, model, method, args, kwargs))
        except OdooApiError:
            raise
        except Exception as exc:
            raise OdooApiError(f"Odoo rejected {model}.{method} over XML-RPC") from exc

    async def _xmlrpc_request(self, path: str, method: str, params: tuple[Any, ...]) -> Any:
        body = xmlrpc.client.dumps(params, methodname=method, allow_none=True).encode("utf-8")
        try:
            response = await self.client.post(
                f"{self.instance.base_url}{path}",
                headers={"Content-Type": "text/xml"}, content=body,
            )
            response.raise_for_status()
            values, _ = xmlrpc.client.loads(response.content)
            return values[0] if values else None
        except xmlrpc.client.Fault as exc:
            raise OdooApiError("Odoo returned an XML-RPC fault") from exc
        except (httpx.HTTPError, xmlrpc.client.Error, ValueError) as exc:
            raise OdooApiError("Could not complete the Odoo XML-RPC request") from exc

    @staticmethod
    def _xmlrpc_arguments(method: str, payload: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        if method == "search_read":
            kwargs = {key: payload[key] for key in ("fields", "limit", "order", "context") if key in payload}
            return [payload.get("domain", [])], kwargs
        if method == "read":
            kwargs = {"fields": payload.get("fields", [])}
            if "context" in payload:
                kwargs["context"] = payload["context"]
            return [payload["ids"]], kwargs
        if method == "create":
            vals_list = payload.get("vals_list", [])
            kwargs = {"context": payload["context"]} if "context" in payload else {}
            return [vals_list[0]], kwargs
        if method == "write":
            kwargs = {"context": payload["context"]} if "context" in payload else {}
            return [payload["ids"], payload["vals"]], kwargs
        if method == "get_field_translations":
            kwargs = {"context": payload["context"]} if "context" in payload else {}
            return [payload["ids"], payload["field_name"], payload.get("langs")], kwargs
        if method == "update_field_translations":
            kwargs = {key: payload[key] for key in ("source_lang", "context") if key in payload}
            return [payload["ids"], payload["field_name"], payload["translations"]], kwargs
        if method == "unlink":
            kwargs = {"context": payload["context"]} if "context" in payload else {}
            return [payload["ids"]], kwargs
        raise OdooApiError(f"Unsupported XML-RPC method {method}")

    async def search_read(
        self, model: str, domain: list, fields: list[str], limit: int, order: str = "id desc",
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = {
            "domain": domain, "fields": fields, "limit": limit, "order": order,
        }
        if context is not None:
            payload["context"] = context
        result = await self.call(model, "search_read", payload)
        if not isinstance(result, list):
            raise OdooApiError(f"Unexpected response from {model}.search_read")
        return result

    async def read(self, model: str, record_id: int, fields: list[str],
                   context: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"ids": [record_id], "fields": fields}
        if context is not None:
            payload["context"] = context
        result = await self.call(model, "read", payload)
        if not isinstance(result, list) or not result:
            raise OdooApiError(f"No {model} record found with id {record_id}")
        return result[0]

    async def create(self, model: str, values: dict[str, Any],
                     context: dict[str, Any] | None = None) -> int:
        payload: dict[str, Any] = {"vals_list": [values]}
        if context is not None:
            payload["context"] = context
        result = await self.call(model, "create", payload)
        # JSON-2 returns one ID per vals_list entry, while XML-RPC create
        # returns the single ID directly.
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
        if not isinstance(result, int) or isinstance(result, bool) or result < 1:
            raise OdooApiError(f"Unexpected response from {model}.create")
        return result

    async def write(self, model: str, record_id: int, values: dict[str, Any],
                    context: dict[str, Any] | None = None) -> bool:
        payload: dict[str, Any] = {"ids": [record_id], "vals": values}
        if context is not None:
            payload["context"] = context
        result = await self.call(model, "write", payload)
        if result is not True:
            raise OdooApiError(f"Unexpected response from {model}.write")
        return result

    async def unlink(self, model: str, record_id: int) -> bool:
        result = await self.call(model, "unlink", {"ids": [record_id]})
        if result is not True:
            raise OdooApiError(f"Unexpected response from {model}.unlink")
        return result

    async def get_field_translations(
        self, model: str, record_id: int, field_name: str, langs: list[str],
    ) -> Any:
        return await self.call(model, "get_field_translations", {
            "ids": [record_id], "field_name": field_name, "langs": langs,
        })

    async def update_field_translations(
        self, model: str, record_id: int, field_name: str, translations: dict[str, Any],
        source_lang: str,
    ) -> bool:
        result = await self.call(model, "update_field_translations", {
            "ids": [record_id], "field_name": field_name,
            "translations": translations, "source_lang": source_lang,
        })
        if result is not True:
            raise OdooApiError(f"Unexpected response from {model}.update_field_translations")
        return result
