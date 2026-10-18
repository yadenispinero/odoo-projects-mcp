import hashlib

import pytest

from odoo_mcp.auth import (
    MCPUser,
    UserDirectory,
    current_mcp_user,
    current_user_directory,
    require_tool_access,
)


def test_tool_access_uses_roles_from_database_on_every_call(tmp_path):
    token = "a-test-token-that-is-never-needed-by-the-request"
    user = MCPUser(
        "editor", hashlib.sha256(token.encode()).hexdigest(), frozenset({"blog_editor"}),
    )
    directory = UserDirectory([user], db_path=str(tmp_path / "users.sqlite"))
    user_context = current_mcp_user.set(user)
    directory_context = current_user_directory.set(directory)
    try:
        assert require_tool_access("create_blog_post").roles == frozenset({"blog_editor"})

        directory.update_roles("editor", ["viewer"])

        with pytest.raises(PermissionError, match="not allowed to use tool"):
            require_tool_access("create_blog_post")
        assert require_tool_access("get_project").roles == frozenset({"viewer"})
    finally:
        current_user_directory.reset(directory_context)
        current_mcp_user.reset(user_context)
