# Odoo Projects and Website MCP

Initial MCP server for a named Odoo 19 target. The current Docker Compose setup binds to `127.0.0.1:8000`; it is intended for local testing and must not be exposed publicly until inbound MCP authentication and HTTPS deployment are configured.

## Current tools

- `check_odoo_connection`: read-only Odoo version check; does not use an API key.
- Project and task search/read tools.
- Task create/update tools.
- Website page search/read, content update, publish, and unpublish tools.

All tools require the configured instance name (`testing` in the starter configuration). JSON-2 requests require an Odoo API key from a user with appropriate limited permissions.

## Configure and run

1. Copy `odoo-instances.example.json` to the ignored local file `odoo-instances.json` and set the URL/database for your instance. The local copy in this workspace already points at `https://odoo.example.com`.
2. Copy `.env.example` to the ignored local file `.env` and put the API key in `ODOO_TESTING_API_KEY`. Keep the key out of source files and chat messages.
3. Start or refresh the service:

   ```sh
   docker compose up -d --build
   ```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. The connection-check tool can confirm the public Odoo version without credentials. Other tools require the configured API key.

## Run tests

Build and run the unit tests plus a read-only live Odoo version probe:

```sh
docker build -f Dockerfile.test -t mcp-odoo-test .
docker run --rm -e ODOO_TEST_BASE_URL=https://odoo.example.com mcp-odoo-test
```

The live probe checks `GET /web/version`; it does not read or change Odoo records. Tests for authenticated reads and writes are still pending an API key and an agreed disposable test record.

## Odoo API

The connector uses Odoo 19's JSON-2 endpoint (`/json/2/<model>/<method>`) with Bearer API-key authentication and optional `X-Odoo-Database`. See [Odoo 19 External JSON-2 API documentation](https://www.odoo.com/documentation/19.0/developer/misc/api/external_api.html). Odoo's hosted service documents external API access as plan-restricted; this test target was confirmed reachable and reported Odoo 19.0. The authenticated model calls still need verification against the target's `/doc` page and configured user permissions.
