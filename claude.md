# Odoo Projects MCP: project plan and status

## Purpose

Build a standalone MCP service that gives AI clients and workflow automation a constrained, auditable interface to Odoo project, task, and website operations. The service must select a configured Odoo target explicitly and must not expose generic model access or arbitrary SQL.

This file tracks engineering scope and decisions. It is not a compliance statement.

## Product scope

- Read and search Odoo projects, tasks, and website pages.
- Create and update projects and tasks through named tools and field allowlists.
- Edit page content separately from explicit publish and unpublish actions.
- Authenticate individual MCP users and assign tool roles.
- Run independently in Docker using Streamable HTTP.

## Current implementation

- Python MCP server using Odoo JSON-2 over HTTPS.
- Configurable named Odoo instances; a client names the target on each call.
- Project, task, and website-page tools with input and field validation.
- Blog translation writes use the selected active website language; public image covers are restricted to PNG, JPEG, or WebP up to 5 MB.
- The `blog_scheduler` role is reserved; its tool remains pending anonymous checks on list, post URL, feed, and sitemap in Odoo 19 testing.
- Bearer-token users, role-to-tool access control, token rotation/revocation, and a local admin page.
- Add, edit, credential-test, deactivate, and reactivate Odoo connections from the local admin page; create/update re-test credentials server-side before persisting, and keys persist outside the source tree.
- Per-user allowlists for configured Odoo instances, editable in the local admin page.
- Existing SQLite users retain wildcard instance access during migration to preserve current behavior; wildcard access includes instances added later, so administrators should review scopes as targets are added.
- SQLite persistence for user/token-digest records.
- Docker Compose local deployment, bound to loopback by default.
- Unit tests for configuration, Odoo API behavior, and server input constraints.

## Confirmed design decisions

- Tools remain narrow and domain-specific; no generic SQL/model/method execution.
- Odoo access rights and record rules remain in force as a second authorization layer.
- Project content editing and website publication use separate permissions and operations.
- Local secrets and SQLite state live outside the Git repository in the developer's private configuration directory.
- The public repository uses generic templates and a portable `./local` default; developers may override it with `MCP_CONNECTIONS_DIR`.
- Code is licensed under LGPL-3.0 by project choice, aligning with Odoo 19 Community's license. The connector does not include Odoo source code.

## Gaps before production use

1. Add and run an authorization matrix for allowed/denied user-instance-tool combinations before relying on instance isolation.
2. Define structured audit events, privacy-safe fields, access controls, and retention.
3. Decide on remote identity (OAuth/OIDC or a protected gateway), token lifecycle, rate limits, and admin-page protections.
4. Harden container runtime and secret injection; evaluate non-root/read-only operation and restrict capabilities.
5. Add automated CI and security checks.
6. Verify authenticated reads and writes against disposable test data in a repeatable staging procedure.
7. Review each deployment's personal-data flows, downstream AI provider, retention, subprocessors, and legal obligations.

## Portfolio preparation

- Keep samples free of real domains, users, credentials, and company data.
- Publish architecture, data-flow, threat-model, and data-protection notes with deployment-specific limitations stated clearly.
- Do not claim GDPR, AI Act, NIS2, or certification compliance based only on this code.
- Include screenshots or a short demo using synthetic records after the access boundaries are improved.

## Next work sequence

1. Review old Git history for local paths/test-host references before making the repository public; current working files are sanitized.
2. Add repeatable tests for instance lifecycle, credentials, and per-user scope updates.
3. Design and implement privacy-safe audit events and retention configuration.
4. Harden runtime, secret handling, and admin access; evaluate encrypted credential storage.
5. Add CI and security scanning.
6. Prepare a repeatable staging demo and portfolio case study.
