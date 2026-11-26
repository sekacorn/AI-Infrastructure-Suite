# Vendor exit evidence

Suite 0.2.0b3 | Rubric 2
Manifest SHA-256: `d56a104792ef7702377083051fb485026c070ded849ad41ddc4e14a945b918c5`

Contract terms are operator-attested. This report is not legal advice.

| Vector | Lock-in (0-100) | Verification | Evidence | Reference |
|---|---:|---|---|---|
| policies | 100 | unverified | No evidence supplied. | - |
| prompts | 100 | unverified | No evidence supplied. | - |
| embeddings | 100 | unverified | No evidence supplied. | - |
| fine_tunes | 100 | unverified | No evidence supplied. | - |
| memory | 25 | verified (memory export) | Worst of 1 store(s): kb-memory scores 25. Verified export with every item bound to its vector, and an import report bound to it by digest accounts for every item (the import itself is attested). Attested, not re-run: that the export holds every item, that the redaction profile was adequate, created_at values, that the import ran at the destination, the linked embedding model's declarations. | `export/agent-memory.json` (`7e04cc8c89bcda52dcaecdec0b5a6139f11994ab500ed5b2c88c5f28ea0ef69a`) |
| audit_history | 100 | unverified | No evidence supplied. | - |
| cost_history | 100 | unverified | No evidence supplied. | - |
| tool_connectors | 100 | unverified | No evidence supplied. | - |
| identities | 100 | unverified | No evidence supplied. | - |
| ontology | 100 | unverified | No evidence supplied. | - |
| contract_terms | 100 | unverified | No evidence supplied. | - |
| model_providers | 100 | unverified | No evidence supplied. | - |

## Memory stores

Digests, counts, tombstones and the item-to-vector binding are
scanner-verified. That the export is complete, that deletions happened, and
that any import ran are attested. 0 is not reachable: the scanner never
restores memory.

| Store | Kind | Layout | Active | Quarantined | Deleted (tombstones) | Vectors bound | Import refused | Lock-in |
|---|---|---|---:|---:|---:|---|---|---:|
| kb-memory | retrieval | sharded_jsonl (2) | 5 | 0 | 0 | 5 of 5 | 0 | 25 |

- kb-memory: Verified export with every item bound to its vector, and an import report bound to it by digest accounts for every item (the import itself is attested).

## Installed components

- agentforge-oss: 0.5.8 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b2 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: 0.1.0b6 (ok)
- openontologylite: 0.2.0b3 (ok)
- privateaistack: absent (optional)
