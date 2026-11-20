# Project exit report

From a development installation and the suite repository root, run:

```console
ai-suite exit-report docs/examples/exit-report/manifest.json
ai-suite exit-report docs/examples/exit-report/manifest.json --json
```

The [example manifest](examples/exit-report/manifest.json) declares a prompt export, a round-trip OpenOntologyLite JSON-LD export, and an operator-attested contract checklist. The [Markdown](examples/exit-report/report.md) and [JSON](examples/exit-report/report.json) examples capture one installation. Repeated runs with the same manifest bytes, evidence bytes, suite version and installed component metadata produce the same output.

Supply a local JSON manifest with `schema_version` set to `exit-manifest.v1` and a `vectors` object. Each entry contains the operator's evidence state, a file path relative to the manifest and its lowercase SHA-256. The command checks the digest over the exact file bytes. Use a report from a trusted independent restore test to support a `restore_tested` claim. The scanner cannot authenticate its author or repeat the test. Installation checks use local distribution metadata and do not affect scores.

| Declared evidence | Score | Meaning |
|---|---:|---|
| restore_tested | 0 | A restore and functional check were recorded. This remains attested until Phase 2 reruns it. |
| importer_validated | 25 | Export and importer validation were recorded. |
| exported | 50 | A portable export exists. |
| reconstruction | 75 | A provider-specific reconstruction procedure exists. |
| non_exportable | 100 | The operator documented a required asset that cannot be exported. |
| Omitted entry | 100 | Unknown. The score is conservative and the report identifies missing evidence. |
| inapplicable | N/A | The referenced record explains why the vector does not apply. |

`verification_level` is `verified` when the scanner checked the evidence content and `attested` when it checked only the operator record and digest. Missing evidence is `unverified`. The ontology vector is verified by loading and round-tripping OpenOntologyLite JSON-LD and comparing canonical ontology digests.

The contract-terms vector uses a `contract-terms-checklist.v1` record covering data export rights, fine-tune or adapter ownership, egress fees, and termination assistance. Values are `confirmed`, `limited`, `absent`, or `unknown`; the most restrictive answer supplies the score. This record is always operator-attested. The report is not legal advice.

Use local, reviewed evidence files under the manifest directory. Keep that directory stable while scanning. The manifest limit is 64 KiB and each referenced file is limited to 4 MiB. Reference a small receipt for a large artifact. Use logical filenames and keep private content, credentials and weights outside Git. Review privacy, license and export permissions before moving data or models.

Exit code 0 means a report was generated, including when evidence is missing. Invalid input, a changed digest or an unreadable file gives exit code 2. Both [input](../src/ai_infrastructure_suite/data/exit-manifest.schema.json) and [output](../src/ai_infrastructure_suite/data/exit-report.schema.json) schemas ship with the package.
