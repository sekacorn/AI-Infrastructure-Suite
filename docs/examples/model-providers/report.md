# Vendor exit evidence

Suite 0.1.0b4 | Rubric 2
Manifest SHA-256: `c52d9f56e89c3ebb9d09d298604d843409ca4cf7296e339f7a72c23bc4f2e3ba`

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
| tool_connectors | 100 | unverified | No evidence supplied. | - |
| identities | 100 | unverified | No evidence supplied. | - |
| ontology | 100 | unverified | No evidence supplied. | - |
| contract_terms | 100 | unverified | No evidence supplied. | - |
| model_providers | 75 | attested | Worst of 2 providers: vendor_b scores 75: a vendor-only feature is in use; a reconstruction path is documented. Attested, not re-run: models in use, features in use (tool calling, streaming), vendor-only features in use, or that none are, jurisdiction, that the declared fallback works, that any benchmarked model was run, every measured number, reconstruction paths for vendor-only features, that an SDK default endpoint is the vendor's, producer. | `provider-inventory.json` (`259c88f4b9cbb3cef2443f5db08691442b8d1b932498713be99fbf84756afe41`) |

## Model providers

Each provider is scored on its own; the vector takes the worst. Hosting is
derived from each endpoint by this scanner. Models, features, fallbacks,
jurisdiction and every benchmark number are operator- or producer-attested.
0 is not reachable in rubric 2: the suite never runs a model.

| Provider | Kind | Hosting | Jurisdiction (attested) | Models covered | Lock-in | Verification |
|---|---|---|---|---:|---:|---|
| gateway_a | openai_compatible | hosted | XA | 1 of 1 | 25 | verified (benchmark arithmetic) |
| vendor_b | anthropic | hosted | XB | 0 of 1 | 75 | attested |

- gateway_a scores 25: every model in use has an accepted local alternative that exercised every feature in use.
- vendor_b scores 75: a vendor-only feature is in use; a reconstruction path is documented.

### Tested alternatives

Acceptance is recomputed here from the producer's numbers, with a floor
of 20 samples; cost is ignored. That any model ran is attested.

| Evidence | Provider | Model | Candidate | Hosting | Samples | Accepted |
|---|---|---|---|---|---:|---|
| `swap-local.json` | gateway_a | model-large | ollama:model-small | local | 24 | yes |
| `swap-hosted.json` | gateway_a | model-large | openai_compatible:model-medium | hosted | 24 | yes |

## Installed components

- agentforge-oss: 0.5.6 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b2 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: 0.1.0b5 (ok)
- openontologylite: 0.2.0b3 (ok)
- privateaistack: absent (optional)
