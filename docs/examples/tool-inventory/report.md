# Vendor exit evidence

Suite 0.2.0b3 | Rubric 2
Manifest SHA-256: `fd70decdaff77a75fa9f9e1a1f7a7dd513e3c52306f491cfa0078df88603a014`

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
| tool_connectors | 75 | verified (tool inventory) | Worst of 4 tool(s): billing_export scores 75. Vendor-only tool with a documented reconstruction path (attested). 1 approval(s) name tools the inventory does not list. Attested, not re-run: that the inventory lists every tool, that any tool works, that an MCP server is self-hostable, that a documented reconstruction path works, that tool descriptions are benign. | `tool-inventory.json` (`1b9d30300103043f7337fcb80e6a2c9b0912ff2fbf05181f5700405d1d091bbb`) |
| identities | 100 | unverified | No evidence supplied. | - |
| ontology | 100 | unverified | No evidence supplied. | - |
| contract_terms | 100 | unverified | No evidence supplied. | - |
| model_providers | 100 | unverified | No evidence supplied. | - |

## Tools

Hosting is derived from each MCP server's origin; approval is matched by
server, origin, name and definition hash. That any tool works, that a
server is self-hostable and that descriptions are benign are attested.
0 is not reachable: the scanner runs no tool.

| Tool | Server | Transport | Hosting | Portability | Approval | Lock-in |
|---|---|---|---|---|---|---:|
| files__search | files | mcp_streamable_http | local | mcp | approved | 25 |
| files__count | files | mcp_streamable_http | local | mcp | approved | 25 |
| crm__lookup | crm | mcp_streamable_http | hosted | mcp | approved | 50 |
| billing_export | billing | in_process | local | vendor_only | not_applicable | 75 |

- files__search: MCP tool with an approved definition hash on a local mcp_streamable_http server.
- files__count: MCP tool with an approved definition hash on a local mcp_streamable_http server.
- crm__lookup: MCP tool; not 25 because its MCP server is hosted (derived from the origin).
- billing_export: Vendor-only tool with a documented reconstruction path (attested).

## Installed components

- agentforge-oss: 0.5.9 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b2 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: absent (optional)
- openontologylite: 0.2.0b3 (ok)
- privateaistack: absent (optional)