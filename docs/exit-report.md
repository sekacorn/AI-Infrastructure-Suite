# Project exit report

From a development installation and the suite repository root, run:

```console
ai-suite exit-report docs/examples/exit-report/manifest.json
ai-suite exit-report docs/examples/exit-report/manifest.json --json
ai-suite exit-report path/to/restore-manifest.json --verify-restores --json
```

The [example manifest](examples/exit-report/manifest.json) declares a prompt export, a round-trip OpenOntologyLite JSON-LD export, and an operator-attested contract checklist. The [Markdown](examples/exit-report/report.md) and [JSON](examples/exit-report/report.json) examples capture one installation. Repeated runs with the same manifest bytes, evidence bytes, suite version and installed component metadata produce the same output.

Supply a local JSON manifest with `schema_version` set to `exit-manifest.v1` and a `vectors` object. Each entry contains the operator's evidence state, a file path relative to the manifest and its lowercase SHA-256. The command checks the digest over the exact file bytes. Installation checks use local distribution metadata and do not affect scores.

A `restore_tested` entry may reference a Linux of AI `restore-summary.v1`. Without `--verify-restores`, the scanner checks only the supplied record digest and labels the row `attested`. With the flag, it validates all six profile-record digests, rebuilds the three fan-out and three chained fake profiles in scanner-owned temporary directories, reruns the content and behavior checks, and compares the restored and re-exported assets. Successful rows are labeled `verified (fake profiles)`. The scanner never executes code named by a record and does not treat the machine sign-off as a person, certificate, or external identity.

Behavioral disagreement with matching asset digests is reported separately in
`restore_profiles` as `unattributed behavior drift`. It fails the affected
profile run but does not guess which individual lock-in vector caused the
drift. Asset vectors are downgraded only when their own content evidence fails.
Every vector checked by the failed profile retains its score and includes a
`profile failure: unattributed drift in <profile>` annotation. The command
still emits the complete report and exits with status 1 when any profile fails.

## Embedding inventories

The `embeddings` vector may instead declare `state: embedding_inventory`, referencing an `embedding-portability.v1` document. The [example](examples/embedding-portability/manifest.json) declares one store with complete source coverage and a self-hostable model.

The scanner answers two questions without calling an embedding API: can every stored vector be traced to retained source text whose content still matches the vector record, and is the declared model portable enough to reproduce locally. It hashes retained source text, streams `single_file` or `sharded_jsonl` vectors, and computes source coverage as retained records over the total minus attested policy deletions. A retained record whose file no longer matches the digest recorded when the vector was produced is invalid evidence, not a lower score. One shard that fails its digest invalidates its whole store rather than leaving a partial inventory. The scanner never runs a model or embeds text with one. It may recompute the two built-in hash-based schemes, using the standard library only and over a bounded sample, to verify a claim; it opens no socket and loads no weights, and reports never print source text, vector values, or shard lines.

| Source coverage | Model availability | Verified restore link | Score |
|---:|---|---|---:|
| 0% | either | any | 100 |
| Between 0% and 100% | either | any | 75 |
| 100% | `vendor_only` | any | 50 |
| 100% | `open_weight_self_hostable` | absent or unverified | 25 |
| 100% | `open_weight_self_hostable` | verified, matching store and digest | 0 |
| Every record deleted under policy | either | any | N/A |

A store reaches 0 only when `--verify-restores` re-ran a restore whose own embeddings asset passed with no unattributed drift, whose summary names that `store_id` in `embedding_stores`, and whose `store_evidence_sha256` equals the digest of this evidence document. A matching `store_id` with a different digest stays at 25 and says so. The worst store governs the vector score, so one portable store cannot hide a locked one.

Model availability, any `weights_sha256`, any `license`, and every `deleted_by_policy` status are operator declarations the scanner cannot re-run. They are listed in the row's `attested_claims` and labeled attested in the per-store section. When a self-hostable model has no `weights_sha256`, the reason states that the weights are not pinned and re-embedding may not reproduce identical vectors. When policy deletions are a majority of a store, a warning states that coverage depends on attested deletions. Every deleted record whose vector still exists produces the reported, unscored finding `vector retained after source deletion`.

| Declared evidence | Score | Meaning |
|---|---:|---|
| restore_tested | 0 | A restore and functional check were recorded. It remains attested unless `--verify-restores` reruns the fake profiles. |
| embedding_inventory | 0-100 or N/A | An `embedding-portability.v1` inventory, scored as above. |
| importer_validated | 25 | Export and importer validation were recorded. |
| exported | 50 | A portable export exists. |
| reconstruction | 75 | A provider-specific reconstruction procedure exists. |
| non_exportable | 100 | The operator documented a required asset that cannot be exported. |
| Omitted entry | 100 | Unknown. The score is conservative and the report identifies missing evidence. |
| inapplicable | N/A | The referenced record explains why the vector does not apply. |

## Model providers (rubric 2)

Rubric 2 adds a twelfth vector, `model_providers`: which model providers the
system calls, and whether anything else has been shown to take their place.
The first eleven vectors and their ladders are unchanged. Reports are now
`exit-report.v2` with `rubric_version: "2"`; the
[v1 schema](../src/ai_infrastructure_suite/data/exit-report.schema.json) stays
packaged so earlier reports still validate. **A rubric-1 report did not assess
model providers.** Read its absence as *not assessed*, never as 0 or 100.

The vector accepts two states only:

- `provider_inventory`, referencing a [`provider-inventory.v1`](provider-inventory.md),
  with an optional `tested_alternatives` list of up to 64
  [`model-swap-evidence.v1`](model-swap-evidence.md) documents, each by path
  and SHA-256;
- `inapplicable`, referencing a record of why the system calls no model.

Omitted, it is `unknown`, 100, `unverified`, like every other vector. Suite
0.1.0b4 and earlier refuse a manifest that declares it.

Each tested alternative must bind to exactly one inventory provider and model
and agree with its own numbers, or the whole report is refused: evidence that
fits nothing is a mistake to surface, not skip. A side declared `local` at an
endpoint that is not local is refused, as the inventory refuses it.

Each provider is scored and **the vector takes the worst provider's score**.

| Score | Condition, per provider |
|---:|---|
| 0 | Reserved for scanner-verified evidence. **Nothing reaches it in rubric 2**: the suite never runs a model. |
| 25 | Every model in use has an accepted tested alternative that is local, every feature in use was exercised by one, and no vendor-only feature is in use. **Or** the scanner derives the provider's own endpoint as local: the workload already runs off any vendor, and that the model is open or self-hostable is listed as attested. An SDK default endpoint or a declaration never qualifies. |
| 50 | One or more of: an accepted alternative for every model, but none is local for some model; a feature in use not exercised by an accepted local alternative (streaming, always); or not every model covered, no vendor-only feature, and an OpenAI-compatible fallback declared. |
| 75 | A vendor-only feature is in use, with a documented reconstruction path. |
| 100 | A vendor-only feature with no reconstruction path; or not every model covered and no fallback declared. |

**A 50 always says why.** The provider's `cases` lists `hosted_alternative`,
`untested_fallback` or `feature_not_exercised`, each that applies, and the
reason names them in words, including which features.

**Accepted** is the suite's own rule, recomputed from the benchmark's numbers:
every gate passed, at least 20 samples and the document's own recommended
minimum, a quality score, and neither side a test fixture. Cost is ignored: a
dearer alternative still shows the workload can move. Evidence that is not
accepted is listed with its reasons and moves nothing.

Jurisdiction is shown, always as attested, and never changes a score.

Every operator- or producer-declared input a provider's score rests on is
listed in that provider's `attested_claims` and in the vector's reason: models
and features in use, vendor-only features (or that none are), reconstruction
paths, jurisdiction, a declared fallback, an SDK default endpoint, a local
model's openness, and every benchmark number. This holds for a `verified
(benchmark arithmetic)` provider too: the level names what the scanner
recomputed, and the claims name what it took on trust.

A provider scored from accepted benchmark evidence gets `verified (benchmark
arithmetic)`; one scored from declarations alone gets `attested`. The vector
takes the level of the provider that sets its score, and the weaker level on a
tie. Per-provider and per-document detail is in the report's top-level
`model_providers` section; the vector table keeps one row per vector.

The [worked example](examples/model-providers/manifest.json) has one provider
at 25 with a local and a hosted alternative, and one at 75 with a documented
vendor-only feature, so the vector is 75 and attested. The
[Ollama-only example](examples/model-providers-local/manifest.json) scores 25.

## Memory inventories

The `memory` vector may declare `state: memory_inventory`, referencing an
[`agent-memory.v1`](agent-memory.md) export by path and SHA-256, and optionally
an `import_report`: the `agent-memory-import.v1` a destination wrote when it
imported that export. Suite 0.2.0b2 and earlier refuse this state; a report
that does not use it still validates against 0.2.0b2's schema.

**The scanner re-runs:** the export's digest and every shard's; every item's
text against its `content_sha256`; ordering, uniqueness, counts and tombstones;
a lossless canonical round trip of every item; for a store that links an
`embedding-portability.v1` inventory, that the inventory verifies at its own
digest, names the store, and recorded for each item a vector built from exactly
that item's text; and, when an import report is supplied, that it is bound to
this export's digest and ID, covers exactly its stores, and accounts for every
item and tombstone.

**Attested, listed in `attested_claims`:** that the export holds every item;
that policy deletions happened; that the redaction profile was adequate (when
one was applied; otherwise the reason says none was); `created_at` values;
provenance of imported items; that the import ran at the destination; and the
linked embedding model's declarations.

| Per store | Score |
|---|---:|
| Verified export, every item bound to its vector (when vectors are linked), and a bound import report that refused nothing | 25 |
| Verified export otherwise; the reason names what is missing | 50 |
| Invalid evidence (a digest, count or binding fails) | refused, exit code 2 |

The worst store sets the vector. **Nothing reaches 0**: the scanner never
restores memory, and 0 is never given on attested evidence. An item whose
vector was built from different text is invalid evidence, not a lower score.
A vector kept for a tombstoned item, or with no item at all, is reported as an
unscored finding. Per-store detail is in the report's `memory_stores` section.
The [worked example](examples/memory-inventory/manifest.json) scores 25.

## Tool inventories

The `tool_connectors` vector may declare `state: tool_inventory`, referencing a
[`tool-inventory.v1`](tool-inventory.md) document by path and SHA-256, and
optionally `approvals`: the `tool-approvals.v1` file the operator committed.
Suite 0.2.0b3 and earlier refuse this state. A report that does not use it
still validates against 0.2.0b3's schema.

**The scanner re-runs:**
- the inventory's digest, schema rules and bounds;
- that every origin is a credential-free canonical origin;
- each MCP server's hosting, *derived from its origin* by the shared locality
  rule (stdio and in-process servers are local), never taken from a
  declaration;
- hash format and uniqueness;
- when approvals are supplied, their digest, and whether each MCP tool's
  server, origin, name and definition hash match an approval. Approvals naming
  tools the inventory does not list are counted as `stale_approvals`.

**Attested, listed in `attested_claims`:**
- that the inventory lists every tool;
- that any tool works;
- that an MCP server is self-hostable;
- that a documented reconstruction path works;
- that tool descriptions are benign;
- that each tool's declared `backend` is accurate (always listed).

The scanner never sees a tool definition, so it cannot recompute a hash, and
it cannot see behind a tool to check what service it depends on.

| Per tool | Score |
|---|---:|
| An MCP tool whose hash is approved, on a local server | 25 |
| An MCP tool whose server is hosted, or whose hash is not approved (or no approvals file was supplied); the reason names which | 50 |
| A vendor-only tool with a documented reconstruction path | 75 |
| A vendor-only tool with no reconstruction path | 100 |
| **Any tool whose `backend` is vendor-only**, whatever its MCP interface, approval or hosting | 75 (documented) or 100 (none) |
| Invalid evidence | refused, exit code 2 |

Every tool declares a `backend`: `null` when it is self-contained, or the
external service it depends on (including a vendor API behind a third-party
MCP server). A vendor-only backend governs: wrapping it in MCP, approving it
or running it locally does not make it portable. When a vendor-only tool also
has a vendor-only backend, the worse reconstruction governs.

The worst tool sets the vector. **Nothing reaches 0**: the scanner runs no
tool, and 0 is never given on attested evidence. Per-tool detail is in the
report's `tools` section. The
[worked example](examples/tool-inventory/manifest.json) scores 75: its
approved MCP tool `crm__lookup` wraps a vendor-only CRM API and so scores 75,
not 25 or 50; its vendor-only billing tool also scores 75; and one approval is
stale.

## Verification vocabulary

`verification_level` says **which checks the scanner re-ran itself**. No level means the evidence is authentic, that its provenance was established, or that an operator's claim about a vendor is true. A level is a statement about reproducibility and internal consistency, nothing more.

| Level | The scanner re-ran | It did not establish | Appears on |
|---|---|---|---|
| `verified` | Loaded the evidence and re-ran a content check over its bytes: for the ontology vector, an OpenOntologyLite JSON-LD round trip whose canonical digest matched. | That the ontology describes the real system, or who produced it. | `ontology` |
| `verified (fake profiles)` | Rebuilt all three fan-out and three chained deterministic fake profiles in scanner-owned temporary directories, re-ran the content and behavior gates, and compared restored and re-exported assets. | That any real vendor restore ever happened. The profiles are fakes by design. | Restore-tested vectors |
| `verified (local evidence)` | Hashed every retained source text, checked it against both its declared digest and the digest recorded when the vector was produced, parsed every vector or shard against its own digest, and computed coverage and counts. | The model's availability, weight digest or license, and whether a `deleted_by_policy` record was really deleted under policy. See `attested_claims`. | `embeddings`, as an inventory |
| `verified (local evidence + fake profiles)` | Both of the two rows above, and the restore it re-ran names this store and commits to this evidence document's exact digest. | The same attested model claims. A drifted profile withholds this level. | `embeddings`, as an inventory |
| `verified (benchmark arithmetic)` | Derived each endpoint's hosting itself, bound each benchmark document to an inventory provider and model by digest, and recomputed acceptance and feature coverage from the document's numbers. | That any model was run, that a single measurement is real, or that the alternative works in production. | `model_providers` |
| `verified (memory export)` | Read the agent-memory export and every shard against their digests, re-hashed every item, round-tripped every item canonically, checked counts and tombstones, bound items to linked vectors by digest, and bound any import report to this export. | That the export is complete, that deletions happened, that redaction was adequate, or that any import ran. See `attested_claims`. | `memory`, as an inventory |
| `verified (tool inventory)` | Read the tool inventory against its digest, checked its schema rules, bounds and canonical origins, derived each MCP server's hosting from its origin, and matched each MCP tool to the approvals file by server, origin, name and hash. | That the inventory is complete, that any tool works, that a server is self-hostable, that a reconstruction path works, that descriptions are benign, or that each declared backend is accurate. See `attested_claims`. | `tool_connectors`, as an inventory |
| `attested` | Only that an operator-supplied record exists and its bytes match the declared SHA-256. | Everything the record asserts. A producer string inside a record is not trusted, and contract terms are never more than attested. | Any vector, and `contract_terms` always |
| `unverified` | Nothing. No evidence was supplied. | Everything. The score is the conservative 100. | Any omitted vector |

Never read an `attested` row as verified. The distinction is the point of the report: `attested` says an operator wrote something down and the bytes have not changed since, and `verified (...)` says the scanner reproduced a specific check and names which one. The ontology vector is verified by loading and round-tripping OpenOntologyLite JSON-LD and comparing canonical ontology digests. That needs `openontologylite>=0.2.0b3`, which publishes the JSON-LD transfer API. With an older install the command exits 2 and says which version to install; it does not report the operator's evidence as invalid or as behavior drift, because the check could not run at all.

The contract-terms vector uses a `contract-terms-checklist.v1` record covering data export rights, fine-tune or adapter ownership, egress fees, and termination assistance. Values are `confirmed`, `limited`, `absent`, or `unknown`; the most restrictive answer supplies the score. This record is always operator-attested. The report is not legal advice.

Use local, reviewed evidence files under the manifest directory. Keep that directory stable while scanning. The manifest limit is 64 KiB and each referenced file is limited to 4 MiB. Reference a small receipt for a large artifact. Use logical filenames and keep private content, credentials and weights outside Git. Review privacy, license and export permissions before moving data or models.

Exit code 0 means a report was generated, including when evidence is missing. Invalid input, a changed digest or an unreadable file gives exit code 2. The [input](../src/ai_infrastructure_suite/data/exit-manifest.schema.json), [output](../src/ai_infrastructure_suite/data/exit-report.v2.schema.json) (rubric 1: [v1](../src/ai_infrastructure_suite/data/exit-report.schema.json)), [restore summary](../src/ai_infrastructure_suite/data/restore-summary-v1.schema.json), [profile record](../src/ai_infrastructure_suite/data/restore-profile-record-v1.schema.json), and [embedding portability](../src/ai_infrastructure_suite/data/embedding-portability.schema.json) schemas ship with the package.
