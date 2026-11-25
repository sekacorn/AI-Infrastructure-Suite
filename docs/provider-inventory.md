# Reading a `provider-inventory.v1`

A provider inventory lists the model providers a system calls: which provider,
where its endpoint is, which models, whether an OpenAI-compatible fallback is
declared, and which features are in use. It is the evidence behind the
`model_providers` lock-in vector, scored in rubric 2: see
[exit-report.md](exit-report.md#model-providers-rubric-2).

```python
from ai_infrastructure_suite.provider_inventory import load_provider_inventory

inventory = load_provider_inventory(path, expected_sha256=digest)
for provider in inventory.providers:
    print(provider.provider_id, provider.hosting, provider.jurisdiction)
inventory.attested_claims()
```

The JSON Schema ships as `ai_infrastructure_suite/data/provider-inventory.schema.json`.
Examples are in `docs/examples/provider-inventory/`.

## What is declared and what is derived

Everything in an inventory is the operator's declaration, with one exception.

**Hosting is derived.** The reader classifies each `endpoint_origin` itself,
with the same rule AgentForge and ModelSwapBench apply: loopback, the
unspecified address, link-local or a private range is local, and everything
else is hosted. A name is never resolved, because a name that resolves
privately today may not tomorrow.

- `hosting_declared` is optional, and may only **add** hosted-ness.
- Declaring `local` for an endpoint that is not local is **refused**: whoever
  owns the server, the content still leaves the machine.
- A null `endpoint_origin` is allowed only for kinds whose SDK supplies its own
  hosted default (`openai`, `anthropic`, `bedrock`), which are then hosted, and
  for the offline `echo` kind, which is local. That the SDK default is the
  vendor's own endpoint is listed among the attested claims.

**Jurisdiction is always attested.** It is an ISO 3166-1 alpha-2 code,
`unknown`, or `not_applicable`, which only a local provider may use. It says
nothing about where a request is routed or stored, and it is never scored.

Models in use, features in use, vendor-only features, reconstruction paths,
whether a fallback works, and the `generated_by` producer strings are all
attested.

## No credentials, ever

`endpoint_origin` is a scheme, a host and an optional port, in canonical
lowercase form. Userinfo, a path, a query, a fragment or a trailing slash is
refused. The reader's error messages never repeat a value from the document,
because an endpoint field is exactly where a misplaced credential would be.

## Bounds

| Limit | Value |
|---|---|
| File size | 256 KiB |
| Providers | 64 |
| Models per provider | 64 |
| Vendor-only features per provider | 16 |
| Any text field | 256 characters |

Duplicate JSON keys, NaN and Infinity, invalid UTF-8, symbolic links and
non-regular files are refused. An inventory must list at least one provider;
a system that calls no model declares the vector `inapplicable` instead.

## Schema and reader

Every object in the schema is closed. Some rules a JSON Schema cannot express,
and the reader enforces those alone:

- `provider_id` and model uniqueness;
- a fallback must name another provider in the same inventory, never itself;
- `endpoint_origin` must be canonical, and port 0 is refused;
- hosting derivation, and refusing `local` for a public endpoint;
- which kinds may omit `endpoint_origin`;
- `not_applicable` jurisdiction only for a local provider.

The test suite pins this division: every negative case is refused by the
reader, and each records whether the schema rejects it too.
