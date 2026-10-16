# Threat model

This is a working threat model for the prototype, not a security assessment or certification. It should be revisited when the deployment, clients, data, or trust boundaries change.

## Assets

- Odoo API keys and MCP bearer tokens.
- Project, task, customer, and website content returned by tools.
- Integrity of project records and website pages, including publication state.
- User-to-role assignments and instance configuration.
- Availability and integrity of the service and its SQLite user database.

## Entry points and trust boundaries

- Streamable HTTP MCP endpoint.
- Local web administration page and its authenticated API.
- Configuration files, environment variables, Docker mounts, and SQLite state.
- Outbound HTTPS calls to each configured Odoo instance.
- Tool output returned to the MCP client and potentially onward to an AI provider.

## Threats and current controls

| Threat | Current control | Remaining work |
| --- | --- | --- |
| Stolen MCP token | Per-user tokens, role checks, token rotation and revocation | Expiry, rate limits, monitoring, and remote identity integration |
| Excessive tool access | Named tools, role-to-tool mapping, and per-user instance allowlists | Add automated authorization-matrix checks and verify revocation/role changes under load |
| Arbitrary database access | No generic SQL or model-call tool; model/method/fields are set by code | Keep allowlists covered by security tests during future tool additions |
| Unauthorized Odoo access | Server-side API key, HTTPS-only configured URL, Odoo ACLs/record rules, authenticated credential test on instance setup | Per-customer technical identities and credential rotation strategy |
| Accidental page publication or overwrite | Separate editor and publisher roles; publish/unpublish are explicit tools | Add change preview, confirmation workflow, and recovery/versioning guidance |
| Secret exposure in repository or image | Example templates; local files are ignored and excluded from Docker context | Run full-history secret scanning before publication and automate secret scanning in CI |
| Odoo API key exposure from host storage | Instance store is outside Git and written with owner-only file permissions | Application-level encryption and automated secure backup/restore are not implemented; protect the host directory and its backups |
| Sensitive data in logs | Odoo error response bodies are not returned to callers | Define structured audit events and retention; verify logs never contain tokens or page bodies |
| Cross-customer data exposure | Explicit instance name required and checked against the caller's allowlist | Migrated users retain wildcard access for compatibility until an administrator narrows it; admin management is global and allowlist behavior needs automated isolation checks |
| Compromised service host | Localhost-only Compose port binding by default | Non-root container, restricted filesystem/capabilities, host hardening, backup and incident procedures |

## Deployment assumptions

- Local Compose binding is not exposed outside the host.
- Remote deployments terminate TLS and authenticate clients before the service is reachable.
- Odoo technical users have only the rights needed for the enabled tools.
- Demo environments use synthetic or approved data.

These assumptions must be revalidated for every deployment. A control in this table is not evidence that a customer's environment is compliant with a law or standard.
