# Vendor exit evidence

Suite 0.2.0b3 | Rubric 2
Manifest SHA-256: `1a1a8d653eadc249b13b88508584c93f782281412140dff14ae6ab2cc11c24e0`

Contract terms are operator-attested. This report is not legal advice.

| Vector | Lock-in (0-100) | Verification | Evidence | Reference |
|---|---:|---|---|---|
| policies | 100 | unverified | No evidence supplied. | - |
| prompts | 100 | unverified | No evidence supplied. | - |
| embeddings | 100 | unverified | No evidence supplied. | - |
| fine_tunes | 100 | unverified | No evidence supplied. | - |
| memory | 100 | unverified | No evidence supplied. | - |
| audit_history | 100 | unverified | No evidence supplied. | - |
| cost_history | 100 | unverified | No evidence supplied. | - |
| tool_connectors | 75 | verified (tool inventory) | Worst of 4 tool(s): crm__lookup scores 75. The tool depends on vendor-only service crm-vendor-api, with a documented reconstruction path (attested). Its MCP interface, approval and hosting do not change this. 1 approval(s) name tools the inventory does not list. Attested, not re-run: that the inventory lists every tool, that any tool works, that an MCP server is self-hostable, that a documented reconstruction path works, that tool descriptions are benign, that each tool's declared backend is accurate. | `tool-inventory.json` (`2bcf3a80ef4b55119b9b7259dd7bcb3e0d83a40f4b05c8e85b7a4de3f3c4240e`) |
| identities | 100 | unverified | No evidence supplied. | - |
| ontology | 100 | unverified | No evidence supplied. | - |
| contract_terms | 100 | unverified | No evidence supplied. | - |
| model_providers | 100 | unverified | No evidence supplied. | - |

## Tools

Hosting is derived from each MCP server's origin; approval is matched by
server, origin, name and definition hash. A vendor-only backend scores the
tool 75 or 100 whatever its MCP interface, approval or hosting. That any tool
works, that a server is self-hostable, that descriptions are benign and that
each declared backend is accurate are attested.
0 is not reachable: the scanner runs no tool.

| Tool | Server | Transport | Hosting | Portability | Backend | Approval | Lock-in |
|---|---|---|---|---|---|---|---:|
| files__search | files | mcp_streamable_http | local | mcp | self-contained | approved | 25 |
| files__count | files | mcp_streamable_http | local | mcp | self-contained | approved | 25 |
| crm__lookup | crm | mcp_streamable_http | hosted | mcp | crm-vendor-api (vendor_only, documented) | approved | 75 |
| billing_export | billing | in_process | local | vendor_only | billing-saas (vendor_only, documented) | not_applicable | 75 |

- files__search: MCP tool with an approved definition hash on a local mcp_streamable_http server.
- files__count: MCP tool with an approved definition hash on a local mcp_streamable_http server.
- crm__lookup: The tool depends on vendor-only service crm-vendor-api, with a documented reconstruction path (attested). Its MCP interface, approval and hosting do not change this.
- billing_export: The tool depends on vendor-only service billing-saas, with a documented reconstruction path (attested).

## Installed components

- agentforge-oss: 0.5.9 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b2 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: absent (optional)
- openontologylite: 0.2.0b3 (ok)
- privateaistack: absent (optional)