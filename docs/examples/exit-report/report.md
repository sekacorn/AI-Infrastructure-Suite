# Vendor exit evidence

Suite 0.2.0b1 | Rubric 2
Manifest SHA-256: `18d374bb936731ac7f4783d4c37ba3ba2670a3aaa1d6f9ac9d0dfdf92989b1ac`

Contract terms are operator-attested. This report is not legal advice.

| Vector | Lock-in (0-100) | Verification | Evidence | Reference |
|---|---:|---|---|---|
| policies | 100 | unverified | No evidence supplied. | - |
| prompts | 50 | attested | Operator supplied evidence of a portable export. | `prompt.txt` (`6b3e348bd817416829cea5f76243e36af89c468d839bd3ab171c684d9ebea739`) |
| embeddings | 100 | unverified | No evidence supplied. | - |
| fine_tunes | 100 | unverified | No evidence supplied. | - |
| memory | 100 | unverified | No evidence supplied. | - |
| audit_history | 100 | unverified | No evidence supplied. | - |
| cost_history | 100 | unverified | No evidence supplied. | - |
| tool_connectors | 100 | unverified | No evidence supplied. | - |
| identities | 100 | unverified | No evidence supplied. | - |
| ontology | 25 | verified | OpenOntologyLite loaded and round-tripped the JSON-LD without changing its digest. | `ontology.jsonld` (`5b54b0b5055d6cddaa0bd12e4e1c8e0cceba062ef4455b827a41e4544e92746b`) |
| contract_terms | 100 | attested | Operator checklist: data_export_rights=confirmed, fine_tune_adapter_ownership=unknown, egress_fees=limited, termination_assistance=confirmed. Not legal advice. | `contract-terms.json` (`0214654c8f2b9c90eef14e1e89bd953334239c3334ebeb4964e5bee3334c942f`) |
| model_providers | 100 | unverified | No evidence supplied. | - |

## Installed components

- agentforge-oss: 0.5.6 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b2 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: 0.1.0b5 (ok)
- openontologylite: 0.2.0b3 (ok)
- privateaistack: absent (optional)
