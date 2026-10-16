# Case study: a controlled AI integration for Odoo

## Context

Project and website information already lives in Odoo. AI assistants and workflow automation can help teams find and update that information, but an integration needs to limit what each client can see and change.

## Objective

Build a standalone service that exposes a small set of useful Odoo operations to MCP clients, keeps Odoo credentials server-side, and allows an operator to assign different permissions to different people.

## Scope and constraints

- Odoo 19 Community with Project and Website available.
- Separate MCP service, deployable with Docker Compose.
- Streamable HTTP for remote-capable MCP clients; local deployment remains bound to loopback by default.
- Named Odoo targets, HTTPS calls, explicit tool parameters, and no generic database interface.
- Task/project changes and website publication need distinct permissions.
- Configuration and credentials live outside the tracked source tree.

## Solution decisions

| Decision | Reason | Trade-off / follow-up |
| --- | --- | --- |
| Use Odoo JSON-2 through a separate service | Keeps integration independent of Odoo add-ons and supports explicit model operations | Confirm API availability and permissions for each Odoo deployment |
| Define narrow MCP tools and allowlisted fields | Makes the client-facing contract understandable and constrains the operations available to an AI | New capabilities require deliberate tool and permission changes |
| Use MCP roles, per-user instance allowlists, and retain Odoo ACLs | Separates tool permissions from the Odoo targets a client may access while leaving Odoo authorization active | Validate isolation with automated tests; admin management remains global to this service |
| Split page editing from publish/unpublish | Makes publication a distinct capability that can be assigned separately | Add preview, confirmation, and recovery workflows before production use |
| Keep configuration outside Git and persist user digests in SQLite | Separates local credentials and user state from the application source | Production secret storage, backups, and retention still need deployment design |
| Add a local web access-management page | Lets an operator assign roles and rotate/revoke users without editing JSON after bootstrap | The page is local-only and does not replace an enterprise identity provider |

## Delivery so far

- Implemented project, task, and website-page read/write tools.
- Added role-based MCP users and a local administration page for user roles and Odoo connections.
- Added per-user allowlists for configured Odoo instances, including management through the local admin page.
- Added Docker Compose packaging and separated the developer's private connection files from the project directory.
- Documented architecture, threat assumptions, data-protection questions, and remaining work.
- Connected to an Odoo 19 Community test environment and verified the version endpoint.
- Verified the local admin page and permission behavior: a viewer could read the assigned instance, while access to an unassigned instance and task creation were denied. The temporary test user was revoked after the check.
- In the local test setup, restricted the task-management user to the configured test instance while retaining its viewer, task-editor, and project-manager roles.
- Ran the automated suite: 11 passed, 1 skipped. The skipped check requires a separately configured live Odoo test URL.

## Security and privacy posture

The service uses HTTPS to reach Odoo, keeps Odoo API keys on the service side, limits the tool surface, and checks both MCP roles and Odoo permissions. These are design controls, not proof of regulatory compliance. Deployment owners still need to assess personal-data categories, lawful basis, AI-provider data flows, logging, retention, hosting, subprocessors, and applicable requirements.

## Acceptance criteria for the next release

- Per-user instance isolation passes a repeatable authorization matrix, including denied cross-instance reads and writes.
- Read, create, update, page edit, publish, and unpublish flows pass a repeatable staging checklist.
- Authorization failures and successful mutations produce privacy-safe audit events with an agreed retention policy.
- CI runs automated checks, and secret/dependency scanning is part of the repository workflow.
- The remote deployment has HTTPS, a selected identity model, documented secret rotation, and a recovery procedure.
- A demo uses synthetic records and demonstrates least-privilege roles, including a user who cannot publish pages.

## Current status

This is a working prototype, not a production deployment or a claim of regulatory compliance. The project documents the implemented controls and observed checks separately from the acceptance criteria that remain open. Personal role, stakeholder decisions, delivery timeline, and business outcomes are omitted until they can be supported with project evidence.
