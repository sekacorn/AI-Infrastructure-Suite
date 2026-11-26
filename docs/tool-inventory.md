# Reading a `tool-inventory.v1`

A tool inventory lists the tools an agent system can call. The
`tool_connectors` vector scores it as `tool_inventory`: see
[exit-report.md](exit-report.md#tool-inventories). AgentForge writes it with
`forge tools inventory`.

```python
from ai_infrastructure_suite.tool_inventory import load_tool_approvals, load_tool_inventory

inventory = load_tool_inventory(path, expected_sha256=digest)
for tool in inventory.tools:
    print(tool.tool_id, tool.transport, tool.server_origin, tool.portability)
```

The JSON Schema ships as `ai_infrastructure_suite/data/tool-inventory.schema.json`.
The worked example is in `docs/examples/tool-inventory/`.

## Shape

Each tool has:
- `tool_id`: the name a model sees, unique in the inventory;
- `name`: the tool's name on its server;
- `server_id`;
- `transport`: `in_process`, `mcp_stdio` or `mcp_streamable_http`.

The remaining fields:
- **`server_origin`** (`scheme://host[:port]`, lowercase) is present for, and
  only for, `mcp_streamable_http`. It never has a path, query, fragment or
  credentials. Hosting is never declared: the scanner derives it.
- **`portability: mcp`** tools carry the `definition_sha256` that an approval
  pins, and `reconstruction: null`.
- **`portability: vendor_only`** tools are `in_process`, with
  `definition_sha256: null` and `reconstruction` set to `documented` or `none`.

The reader refuses:
- unknown or duplicate keys, and NaN;
- two tools sharing a `tool_id`, a server and name, or a definition hash;
- an empty `tools` list. A system with no tools declares the vector
  `inapplicable` instead.

## The MCP formats

The suite owns the canonical JSON Schemas for the files AgentForge reads and
writes around MCP tools. AgentForge's `suite-contract` job validates what it
writes against them.

| Format | Schema | What it is |
|---|---|---|
| `tool-approvals.v1` | `tool-approvals.schema.json` | Which definition hash of which tool a person approved. Read by `load_tool_approvals` for scoring. |
| `mcp-servers.v1` | `mcp-servers.schema.json` | The MCP servers an agent may contact (written as TOML). |
| `tool-definition.v1` | `tool-definition.schema.json` | A snapshot of one approved definition, named by its hash. |

The definition hash is the SHA-256 of the RFC 8785 canonical JSON of
`{server_id, name, description, inputSchema, outputSchema, annotations}`.
Examples of all three are in `docs/examples/mcp-tools/`.
