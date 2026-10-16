# Data protection notes

This document describes design questions for deployment owners. It is not legal advice, a GDPR assessment, a DPIA, or a compliance declaration.

## Data flow

The MCP service can read and modify project names, task descriptions, assignments, deadlines, customer references, website metadata, and page content. These fields may contain personal data depending on how the Odoo instance is used. Tool results leave Odoo and are returned to the configured MCP client; the client may send them to an AI provider or retain them in workflow history.

## Questions to answer for each deployment

- Who determines the purpose and means of processing, and what are the controller/processor roles?
- What personal data can appear in project, task, customer, and page fields?
- Which lawful basis and notices apply to each purpose?
- Which client and AI provider receive tool results, where are they hosted, and what are their retention and training settings?
- What data is necessary for each tool, and can fields or outputs be reduced further?
- How long are service, proxy, workflow, and AI-provider logs retained?
- How are access requests, correction, deletion, incidents, and data subject rights handled across Odoo and downstream clients?
- Are international transfers, subprocessors, or a DPIA relevant for the specific deployment?

## Existing design choices

- Tools return selected fields rather than arbitrary model records.
- MCP callers receive only the tools allowed by their assigned roles.
- Odoo permissions and record rules remain active.
- Tokens and Odoo API keys are intended to remain server-side.
- The prototype does not define a general-purpose audit retention policy or AI-provider policy.

The GDPR requires appropriate safeguards and demonstrable accountability; the actual obligations depend on the processing and deployment. The AI Act's relevance depends on the system's intended purpose and use case. Neither applicability nor compliance is established by this connector alone. See the [European Commission's GDPR principles](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/principles-gdpr_en) and [AI Act FAQ](https://digital-strategy.ec.europa.eu/en/faqs/navigating-ai-act).
