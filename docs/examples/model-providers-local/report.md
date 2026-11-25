# Vendor exit evidence

Suite 0.2.0b1 | Rubric 2
Manifest SHA-256: `956a229eb756ddfbc83b9fd0a32cc6a97c12d13100b3df4b6aac7d4485db2366`

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
| model_providers | 25 | attested | Worst of 1 provider: local_ollama scores 25: the scanner derives its endpoint as local, so the workload already runs off any vendor. Attested, not re-run: models in use, features in use (tool calling, streaming), vendor-only features in use, or that none are, jurisdiction, that the local model is open or self-hostable, producer. | `provider-inventory.json` (`72067d3e4e6463046d1f8deac85275d527e132c771cae5f73aecb845a7bc342c`) |

## Model providers

Each provider is scored on its own; the vector takes the worst. Hosting is
derived from each endpoint by this scanner. Models, features, fallbacks,
jurisdiction and every benchmark number are operator- or producer-attested.
0 is not reachable in rubric 2: the suite never runs a model.

| Provider | Kind | Hosting | Jurisdiction (attested) | Models covered | Lock-in | Verification |
|---|---|---|---|---:|---:|---|
| local_ollama | ollama | local | not_applicable | 0 of 1 | 25 | attested |

- local_ollama scores 25: the scanner derives its endpoint as local, so the workload already runs off any vendor.

## Installed components

- agentforge-oss: 0.5.6 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b2 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: 0.1.0b5 (ok)
- openontologylite: 0.2.0b3 (ok)
- privateaistack: absent (optional)
