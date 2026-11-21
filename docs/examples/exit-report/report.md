# Vendor exit evidence

Suite 0.1.0b2 | Rubric 1
Manifest SHA-256: `e838cf47f03e0c0225b82da50c3a46683aa44d91a4477fc7a162a9ec12c4f714`

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
| ontology | 25 | verified | OpenOntologyLite loaded and round-tripped the JSON-LD without changing its digest. | `ontology.jsonld` (`2566d8677a8f0cbca68f2e94f534baaaac6c0f85ed622f90b6b8de038cf90e78`) |
| contract_terms | 100 | attested | Operator checklist: data_export_rights=confirmed, fine_tune_adapter_ownership=unknown, egress_fees=limited, termination_assistance=confirmed. Not legal advice. | `contract-terms.json` (`0214654c8f2b9c90eef14e1e89bd953334239c3334ebeb4964e5bee3334c942f`) |

## Installed components

- agentforge-oss: 0.5.4 (ok)
- agentpolicypack: 0.2.0b1 (ok)
- aiauditlog: 0.2.0b1 (ok)
- aimeter-oss: 0.2.0b1 (ok)
- modelswapbench: absent (optional)
- openontologylite: 0.2.0b2 (ok)
- privateaistack: absent (optional)
