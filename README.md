# Odoo Projects and Website MCP

Initial MCP server for a named Odoo 19 target. The Docker Compose service requires a bearer token for every MCP request and binds to `127.0.0.1:8000`. Do not expose it publicly until HTTPS and a suitable remote identity provider or authenticated reverse proxy are configured.

## Current tools

- `check_odoo_connection`: read-only Odoo version check; does not use an API key.
- Project and task search/read tools.
- Task create/update tools.
- Website page search/read, content update, publish, and unpublish tools.

All tools require a configured MCP user token and Odoo instance name (`testing` in the starter configuration). JSON-2 requests require the Odoo API key from a user with appropriate limited permissions. MCP roles restrict tools before Odoo is called; Odoo permissions and record rules remain a second authorization layer.

### MCP users and roles

The local administration page is `http://127.0.0.1:8000/admin`. Sign in with the initial `owner` token from `.env`. It lets an administrator add users, assign roles, change roles, rotate a token, and revoke access. A newly created or rotated token is shown once; copy it to that person's MCP client. The admin page keeps the owner token in the current browser tab only.

On first startup, copy `mcp-users.example.json` to the ignored local file `mcp-users.json` and set distinct random values of at least 32 characters in `.env` for `MCP_TOKEN_OWNER` and `MCP_TOKEN_N8N`. The initial users are `owner` with all current tools and `n8n` with read access plus task create/update. The service seeds these identities into its persistent SQLite volume. After that, manage users in the webpage; changes take effect immediately and user tokens are stored as SHA-256 digests.

Available roles:

- `viewer`: connection check and project, task, and page search/read.
- `task_editor`: create and update tasks.
- `project_manager`: search, read, create, and update projects.
- `website_editor`: edit page content.
- `publisher`: publish and unpublish pages.
- `administrator`: all currently registered tools.

The database stores only SHA-256 digests of MCP bearer tokens, not the tokens themselves. Initial tokens are read from environment variables at first startup and must never be committed. The administration page and MCP service are currently local-only; this setup uses static bearer tokens, not an OAuth login flow. The Odoo API key is still shared by requests to the configured instance, so Odoo audit entries identify the technical Odoo account.

## Configure and run

1. Copy `odoo-instances.example.json` to the ignored local file `odoo-instances.json` and set the URL/database for your instance. The local copy in this workspace already points at `https://odoo.example.com`.
2. Copy `.env.example` to the ignored local file `.env` and set `ODOO_TESTING_API_KEY` plus distinct random bearer tokens for the MCP users. Keep all keys out of source files and chat messages.
3. Copy `mcp-users.example.json` to the ignored local file `mcp-users.json`.
4. Start or refresh the service:

   ```sh
   docker compose up -d --build
   ```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Every client must send `Authorization: Bearer <its MCP token>` on MCP requests. Odoo credentials remain server-side.

## Run tests

Build and run the unit tests plus a read-only live Odoo version probe:

```sh
docker build -f Dockerfile.test -t mcp-odoo-test .
docker run --rm -e ODOO_TEST_BASE_URL=https://odoo.example.com mcp-odoo-test
```

The live probe checks `GET /web/version`; it does not read or change Odoo records. Tests for authenticated reads and writes are still pending an API key and an agreed disposable test record.

## Odoo API

The connector uses Odoo 19's JSON-2 endpoint (`/json/2/<model>/<method>`) with Bearer API-key authentication and optional `X-Odoo-Database`. See [Odoo 19 External JSON-2 API documentation](https://www.odoo.com/documentation/19.0/developer/misc/api/external_api.html). Odoo's hosted service documents external API access as plan-restricted; this test target was confirmed reachable and reported Odoo 19.0. The authenticated model calls still need verification against the target's `/doc` page and configured user permissions.
