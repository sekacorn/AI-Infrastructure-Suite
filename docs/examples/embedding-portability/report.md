# Vendor exit evidence

Suite 0.1.0b2 | Rubric 1
Manifest SHA-256: `2c76950a105ef79e3c97af9a90f9ab5e26642a7f154f194a8827c2caaefeaad8`

Contract terms are operator-attested. This report is not legal advice.

| Vector | Lock-in (0-100) | Verification | Evidence | Reference |
|---|---:|---|---|---|
| policies | 100 | unverified | No evidence supplied. | - |
| prompts | 100 | unverified | No evidence supplied. | - |
| embeddings | 25 | verified (local evidence) | Store 'support-memory' has complete source coverage and the attested model is self-hostable. No verified restore names this store. This is a validated export with a compatible importer. Attested, not re-run: model availability, model weights digest, model license. | `embedding-portability.json` (`3be289efb718dc4c9329adf8d71c79932e60454e94551e20fdf63cb437875ca0`) |
| fine_tunes | 100 | unverified | No evidence supplied. | - |
| memory | 100 | unverified | No evidence supplied. | - |
| audit_history | 100 | unverified | No evidence supplied. | - |
| cost_history | 100 | unverified | No evidence supplied. | - |
| tool_connectors | 100 | unverified | No evidence supplied. | - |
| identities | 100 | unverified | No evidence supplied. | - |
| ontology | 100 | unverified | No evidence supplied. | - |
| contract_terms | 100 | unverified | No evidence supplied. | - |

## Embedding stores

Counts and coverage are scanner-verified. Model availability, any weights
digest, any license, and every policy deletion are operator-attested and
were not re-run.

| Store | Model | Dim | Availability | Layout | Vectors | Retained | Missing | Deleted (attested) | Coverage | Restore link | Re-embeddable | Lock-in |
|---|---|---:|---|---|---:|---:|---:|---:|---:|---|---|---:|
| support-memory | example-embed-small@2026-02-01 | 8 | open_weight_self_hostable (attested) | single_file (0) | 3 | 3 | 0 | 0 | 100.00% | absent | yes | 25 |

## Installed components

- agentforge-oss: 0.5.4 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b1 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: 0.1.0b1 (ok)
- openontologylite: 0.2.0b3 (ok)
- privateaistack: absent (optional)
