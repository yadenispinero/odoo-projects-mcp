from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


ROLE_TOOLS: dict[str, frozenset[str]] = {
    "viewer": frozenset(
        {
            "check_odoo_connection",
            "search_projects",
            "get_project",
            "search_tasks",
            "get_task",
            "search_pages",
            "get_page",
        }
    ),
    "task_editor": frozenset({"create_task", "update_task"}),
    "website_editor": frozenset({"update_page_content"}),
    "publisher": frozenset({"publish_page", "unpublish_page"}),
}
ROLE_TOOLS["project_manager"] = frozenset(
    {"search_projects", "get_project", "create_project", "update_project"}
)
ROLE_TOOLS["administrator"] = frozenset().union(*ROLE_TOOLS.values())


@dataclass(frozen=True)
class MCPUser:
    username: str
    token_digest: str
    roles: frozenset[str]
    instances: frozenset[str] = frozenset({"*"})

    def can_use(self, tool_name: str) -> bool:
        return any(tool_name in ROLE_TOOLS[role] for role in self.roles)

    def can_access_instance(self, instance: str) -> bool:
        return "*" in self.instances or instance in self.instances


class UserDirectory:
    """SQLite-backed MCP identities, initially bootstrapped from a local config file."""

    def __init__(self, users: Iterable[MCPUser] = (), db_path: str | None = None,
                 instance_names: Iterable[str] | None = None):
        self.users = tuple(users)
        self.db_path = db_path
        self.instance_names = frozenset(instance_names) if instance_names is not None else None
        if self.db_path:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS mcp_users ("
                    "username TEXT PRIMARY KEY, token_digest TEXT NOT NULL UNIQUE, "
                    "roles_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
                    "instances_json TEXT NOT NULL DEFAULT '[\"*\"]')"
                )
                columns = {row[1] for row in db.execute("PRAGMA table_info(mcp_users)")}
                if "instances_json" not in columns:
                    db.execute(
                        "ALTER TABLE mcp_users ADD COLUMN instances_json TEXT NOT NULL DEFAULT '[\"*\"]'"
                    )
                count = db.execute("SELECT COUNT(*) FROM mcp_users").fetchone()[0]
                if count == 0:
                    db.executemany(
                        "INSERT INTO mcp_users(username, token_digest, roles_json, active, instances_json) "
                        "VALUES (?, ?, ?, 1, ?)",
                        [(u.username, u.token_digest, json.dumps(sorted(u.roles)),
                          json.dumps(sorted(u.instances))) for u in self.users],
                    )

    @classmethod
    def load(cls, path: str | None = None, *, required: bool = False,
             instance_names: Iterable[str] | None = None) -> UserDirectory:
        db_path = os.getenv("MCP_USER_DB")
        config_path = Path(path or os.getenv("MCP_USERS_FILE", "mcp-users.json"))

        # If the persistent store is already initialized, it is the source of truth.
        if db_path and Path(db_path).exists():
            with sqlite3.connect(db_path) as db:
                initialized = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mcp_users'"
                ).fetchone()
                if initialized and db.execute("SELECT COUNT(*) FROM mcp_users").fetchone()[0] > 0:
                    return cls(db_path=db_path, instance_names=instance_names)

        if not config_path.exists():
            if required:
                raise ValueError(
                    f"MCP user configuration {str(config_path)!r} is missing; configure at least one user"
                )
            return cls((), db_path=db_path)

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        entries = raw.get("users") if isinstance(raw, dict) else None
        if not isinstance(entries, dict) or not entries:
            raise ValueError("MCP user configuration must contain a non-empty 'users' object")

        users: list[MCPUser] = []
        seen_digests: set[str] = set()
        for username, entry in entries.items():
            if not isinstance(username, str) or not username.strip() or not isinstance(entry, dict):
                raise ValueError("Each MCP user must have a username and object configuration")
            token_env = entry.get("token_env")
            roles = entry.get("roles")
            instances = entry.get("instances", ["*"])
            if not isinstance(token_env, str) or not token_env:
                raise ValueError(f"MCP user {username!r} must specify token_env")
            validated_roles = validate_roles(roles)
            validated_instances = validate_instances(instances, instance_names)
            token = os.getenv(token_env, "")
            if len(token) < 32:
                raise ValueError(f"Environment variable {token_env} must contain a token of at least 32 characters")
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if digest in seen_digests:
                raise ValueError("Each MCP user must have a unique bearer token")
            seen_digests.add(digest)
            users.append(MCPUser(username, digest, validated_roles, validated_instances))

        return cls(users, db_path=db_path, instance_names=instance_names)

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path:
            raise RuntimeError("MCP user database is not configured")
        return sqlite3.connect(self.db_path, timeout=5)

    def _records(self) -> list[MCPUser]:
        if not self.db_path:
            return list(self.users)
        with self._connect() as db:
            rows = db.execute(
                "SELECT username, token_digest, roles_json, instances_json "
                "FROM mcp_users WHERE active=1 ORDER BY username"
            ).fetchall()
        return [MCPUser(name, digest, frozenset(json.loads(roles)), frozenset(json.loads(instances)))
                for name, digest, roles, instances in rows]

    def authenticate(self, token: str) -> MCPUser | None:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for user in self._records():
            if hmac.compare_digest(user.token_digest, digest):
                return user
        return None

    def list_users(self) -> list[dict[str, object]]:
        if not self.db_path:
            return [{"username": u.username, "roles": sorted(u.roles), "active": True,
                     "instances": sorted(u.instances)} for u in self._records()]
        with self._connect() as db:
            rows = db.execute(
                "SELECT username, roles_json, active, instances_json FROM mcp_users ORDER BY username"
            ).fetchall()
        return [
            {"username": name, "roles": json.loads(roles), "active": bool(active),
             "instances": json.loads(instances)}
            for name, roles, active, instances in rows
        ]

    def add_user(self, username: str, roles: object, instances: object) -> str:
        normalized_name = validate_username(username)
        validated_roles = validate_roles(roles)
        validated_instances = validate_instances(instances, self.instance_names)
        token = secrets.token_hex(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute(
                "INSERT INTO mcp_users(username, token_digest, roles_json, active, instances_json) "
                "VALUES (?, ?, ?, 1, ?)",
                (normalized_name, digest, json.dumps(sorted(validated_roles)),
                 json.dumps(sorted(validated_instances))),
            )
        return token

    def update_instances(self, username: str, instances: object) -> None:
        validated_instances = validate_instances(instances, self.instance_names)
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE mcp_users SET instances_json=? WHERE username=? AND active=1",
                (json.dumps(sorted(validated_instances)), username),
            )
            if cursor.rowcount == 0:
                raise KeyError("User not found")

    def update_roles(self, username: str, roles: object) -> None:
        validated_roles = validate_roles(roles)
        with self._connect() as db:
            row = db.execute("SELECT roles_json FROM mcp_users WHERE username=? AND active=1", (username,)).fetchone()
            if row is None:
                raise KeyError("User not found")
            was_admin = "administrator" in json.loads(row[0])
            will_be_admin = "administrator" in validated_roles
            if was_admin and not will_be_admin:
                self._ensure_another_admin(db, username)
            db.execute(
                "UPDATE mcp_users SET roles_json=? WHERE username=?",
                (json.dumps(sorted(validated_roles)), username),
            )

    def rotate_token(self, username: str) -> str:
        token = secrets.token_hex(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE mcp_users SET token_digest=? WHERE username=? AND active=1",
                (digest, username),
            )
            if cursor.rowcount == 0:
                raise KeyError("User not found")
        return token

    def revoke_user(self, username: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT roles_json FROM mcp_users WHERE username=? AND active=1", (username,)).fetchone()
            if row is None:
                raise KeyError("User not found")
            if "administrator" in json.loads(row[0]):
                self._ensure_another_admin(db, username)
            db.execute("UPDATE mcp_users SET active=0 WHERE username=?", (username,))

    @staticmethod
    def _ensure_another_admin(db: sqlite3.Connection, username: str) -> None:
        rows = db.execute("SELECT username, roles_json FROM mcp_users WHERE active=1 AND username<>?", (username,)).fetchall()
        if not any("administrator" in json.loads(roles) for _, roles in rows):
            raise ValueError("Cannot remove or demote the last active administrator")


def validate_username(username: object) -> str:
    if not isinstance(username, str):
        raise ValueError("username must be a string")
    normalized = username.strip()
    if not normalized or len(normalized) > 64 or any(ord(char) < 33 for char in normalized):
        raise ValueError("username must be 1 to 64 visible characters")
    return normalized


def validate_roles(roles: object) -> frozenset[str]:
    if not isinstance(roles, list) or not roles or any(not isinstance(role, str) for role in roles):
        raise ValueError("roles must be a non-empty list")
    unknown_roles = set(roles) - ROLE_TOOLS.keys()
    if unknown_roles:
        raise ValueError(f"Unknown roles: {', '.join(sorted(unknown_roles))}")
    return frozenset(roles)


def validate_instances(instances: object, available: Iterable[str] | None = None) -> frozenset[str]:
    if not isinstance(instances, list) or not instances or any(not isinstance(name, str) for name in instances):
        raise ValueError("instances must be a non-empty list of configured instance names")
    values = {name.strip() for name in instances}
    if "" in values:
        raise ValueError("instance names must not be empty")
    if "*" in values and values != {"*"}:
        raise ValueError("'*' must be selected alone to allow all configured instances")
    if available is not None and "*" not in values:
        unknown_instances = values - set(available)
        if unknown_instances:
            raise ValueError(f"Unknown Odoo instances: {', '.join(sorted(unknown_instances))}")
    return frozenset(values)


def bearer_token(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    value = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
    if not value:
        return None
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def require_tool_access(headers: Mapping[str, str] | None, directory: UserDirectory,
                        tool_name: str, instance: str | None = None) -> MCPUser:
    token = bearer_token(headers)
    user = directory.authenticate(token) if token else None
    if user is None:
        raise PermissionError("Authentication required")
    if not user.can_use(tool_name):
        raise PermissionError(f"User {user.username!r} is not allowed to use tool {tool_name!r}")
    if instance is not None and not user.can_access_instance(instance):
        raise PermissionError(f"User {user.username!r} is not allowed to access Odoo instance {instance!r}")
    return user


class BearerAuthMiddleware:
    """Require a configured bearer token on every HTTP request to the MCP route."""

    def __init__(self, app, directory: UserDirectory, mcp_path: str):
        self.app = app
        self.directory = directory
        self.mcp_path = mcp_path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == self.mcp_path:
            headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
            token = bearer_token(headers)
            if not token or self.directory.authenticate(token) is None:
                body = b"Unauthorized"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                    (b"www-authenticate", b"Bearer"),
                                    (b"content-length", str(len(body)).encode("ascii"))],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
