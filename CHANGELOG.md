# Changelog

All notable changes to AI Infrastructure Suite are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/) with PEP 440 pre-release identifiers.

---

## [Unreleased]

### Added
- **`provider-inventory.v1`**, a new evidence format listing the model
  providers a system calls, with a packaged JSON Schema, four example
  documents, and a bounded reader in
  `ai_infrastructure_suite.provider_inventory`. It is the evidence for the
  `model_providers` lock-in vector, which a later release adds; nothing reads
  it into an exit report yet, and no score changes.
- Hosting in an inventory is **derived** from each endpoint origin with the
  same no-DNS rule AgentForge and ModelSwapBench use, and a declaration may
  only add hosted-ness. Endpoint origins carrying credentials, a path or a
  query are refused, and the reader's errors never repeat a document value.
- **`model-swap-evidence.v1`**, the canonical schema and a bounded reader in
  `ai_infrastructure_suite.model_swap_evidence`, for benchmark evidence that a
  tested alternative exists for a model provider. ModelSwapBench writes it;
  this package's schema is the authority, and a producer's own may be
  narrower, never wider. The reader recomputes the decision from the numbers
  and refuses a document whose stated verdict disagrees. `assess` applies the
  suite's stricter rule: at least 20 samples, a quality score, no test
  fixture on either side, and cost ignored. `bind_to_inventory` names the one
  inventory provider and model a document tested, refusing no match or two.
  Ten example documents ship in `docs/examples/model-swap-evidence/`. Nothing
  reads this into an exit report yet, and no score changes.
- A **`producer-agreement`** CI workflow installs ModelSwapBench from its main
  branch and checks, over a grid of its real output, that both decision rules
  agree.

## [0.1.0b4] - 2026-11-24

A patch release. Exit reports that carry re-embed records now match the
packaged report schema, and the ModelSwapBench floor moves past a security
fix. No scoring, label or report content changes.

### Changed
- **Raised the `modelswapbench` floor to `>=0.1.0b4,<0.2.0`** in the
  `[benchmarking]` and `[full]` extras. 0.1.0b3 and earlier wrote credentials
  placed in a model's `base_url` to every run artefact and to the logs; 0.1.0b4
  refuses such a URL.

### Fixed
- **Exit reports with re-embed records failed the packaged report schema.**
  0.1.0b3 writes a top-level `re_embed_records` section whenever a manifest
  declares re-embed records, but `exit-report.schema.json` did not declare it
  and forbids undeclared fields, so any consumer validating such a report
  against the shipped schema rejected it. The section is now declared, closed,
  with each row's fields and values pinned. Reports without re-embed records
  were unaffected.
- The test suite now validates **every** exit report it builds against the
  packaged schema, not one example. 0.1.0b3's only schema test used an example
  with no re-embed records, which is how the gap shipped. `jsonschema` joins
  the `dev` extra for this; it is not a runtime dependency.

## [0.1.0b3] - 2026-11-24

Re-embedded stores in exit reports. A store whose embeddings were rebuilt
with a different model can now carry a `re-embed-record.v1` document, and the
scanner checks part of that rebuild itself before it lets the record move a
score. Nothing in 0.1.0b2 read such a record, so no published behaviour
changes except the dependency floors under Changed.

### The diagnostics-only invariant, amended

0.1.0b2 said the scanner "never embeds text". That sentence no longer holds
and is superseded by this one:

**The suite never runs a model or embeds text with one.** It loads no weights,
opens no socket and imports no provider SDK. It *may* recompute the two
built-in hash-based embedding schemes, with the standard library only and over
a bounded sample, to verify a rebuild that claims to use one. That is the only
embedding computation it performs, and it is how a rebuild earns a verified
level instead of an attested one. Reproducing a rebuild with any other model
stays the job of the tool that produced it.

### Added
- **`re-embed-record.v1`**, a new evidence format with a packaged JSON Schema,
  read by `ai_infrastructure_suite.re_embed_record`. A record describes one
  store rebuilt with a different model: source and target stores by digest,
  coverage, an egress block saying which code ran and what it was handed, and
  an optional quality comparison.
- **`exit-report` reads re-embed records** declared as `re_embed_records` on
  the `embeddings` vector. A record must bind to that vector's evidence by
  both digest and `store_id`, or it is **refused**, not skipped, so a record
  produced from a different store cannot be attached and quietly ignored. The
  Markdown report gains a `## Re-embedding` section.
- **The scanner reproduces a bounded sample of the rebuild.** For a target
  model that is one of the two built-in hash-based schemes, it recomputes a
  sample of the target store's vectors from that store's own retained source
  text, with its own implementation. A verified level means *that sample
  matched*. **It is not a whole-store digest**, and the report prints the
  sample size, worded "a sample of N", so it cannot be read as one. Any other
  model, a mismatch, or a target document whose digest does not match leaves
  the record `attested`.
- **Four verified levels:** `verified (re-embedded, quality sampled)`,
  `verified (re-embedded, quality below threshold)`,
  `verified (re-embedded, quality unjudged)` and
  `verified (re-embedded, quality not measured)`. `attested` covers every
  rebuild the scanner did not reproduce, including every real-model run.
- **Quality statuses** `sampled`, `below_threshold`, `unjudged` and
  `not_measured`. `unjudged` means the comparison ran and produced metrics but
  no agreement floor was declared, so there is no verdict; it is never read as
  a pass. Declared thresholds are attested and are always shown.
- **Seeds are derived, never accepted.** The anchor seed is `sha256` over the
  source and target store digests, and the reproduction sample seed is
  `sha256` over the target store digest. A record whose anchor seed is
  anything else is read as `anchor_selection: operator`: reported in full,
  moving no score. A seed written into a record cannot influence which vectors
  the scanner checks.
- **Egress fields.** The record's egress block states the executor kind,
  whether content was transmitted and on what basis, and whether hosted
  execution was confirmed. Delivery is always recorded as `unverified`:
  submission is not delivery. The reader refuses a record that reports
  transmitting content without confirmed hosted execution.
- **The one score move.** A qualifying record moves a store **from exactly 50
  to 25** and nothing else. It can never lower a score and can never produce
  0, which still requires a verified restore. Worst-store aggregation is
  unchanged.

### Changed
- **Raised the `modelswapbench` floor to `>=0.1.0b3,<0.2.0`** in the
  `[benchmarking]` and `[full]` extras. In 0.1.0b1 and 0.1.0b2 a hosted
  `openai_compatible` endpoint with no declared pricing was classified as
  local and its cost reported as `0.0` instead of unknown.
- **Raised the `agentforge-oss` floor to `>=0.5.5,<0.6.0`** for the core
  install and the `[observability]` and `[full]` extras. 0.5.5 is a security
  release: provider errors in 0.5.4 and earlier could repeat upstream text,
  including an API key reflected by a proxy or third-party endpoint, and a
  `base_url` containing credentials was logged. The floor keeps an install of
  this suite from resolving to an affected version. The upper bound is
  unchanged.

### Compatibility
- **An exit manifest that declares `re_embed_records` requires this release or
  later to read.** 0.1.0b2's manifest schema forbids unknown fields on a vector
  entry and rejects it as invalid input. Manifests without the field are
  unaffected.
- **Reading a re-embed record written by PrivateAIStack 0.2.0b2 or later
  requires this release.** This is a compatibility statement, not a
  dependency: PrivateAIStack does not depend on this suite, and this suite
  does not import PrivateAIStack.

## [0.1.0b2] - 2026-11-21

- Added an offline `ai-suite exit-report` command with a versioned project manifest, eleven evidence vectors, verification levels, installed component versions, Markdown and JSON output.
- Added explicit `--verify-restores` handling for `restore-summary.v1` evidence. The scanner rebuilds and reruns all deterministic fan-out and chained fake profiles; unchecked records remain attested.
- Added offline embedding lock-in scanning for a new `embedding-portability.v1` evidence document, with a packaged JSON Schema. The scanner hashes retained source text, streams single-file or sharded JSONL vectors, computes source coverage, and scores migration lock-in. Model availability, weight digests, licenses, and policy deletions stay labeled attested; the scanner never embeds text, loads weights, or opens a socket.
- Fixed an unhandled `ImportError` when the installed `openontologylite` predates the JSON-LD transfer API. Ontology verification now exits 2 with a message naming `openontologylite>=0.2.0b3`, instead of printing a traceback (`exit-report`) or recording the missing API as unattributed behavior drift (`--verify-restores`).
- Added an optional `embedding_stores` block to `restore-summary.v1`, binding a `store_id` to the SHA-256 of one embedding-portability evidence document. **A restore summary containing `embedding_stores` requires this release or later to read**, because the schema forbids unknown fields and `0.1.0b1` rejects it as invalid input. Reading an exit report that contains an embedding inventory also requires `0.1.0b2`, for the same reason. Summaries without the block are unaffected.

## [0.1.0b1] - 2026-08-13

First Beta candidate for the suite itself. The seven components it installs are
all Beta, and the suite now resolves those published Beta releases correctly. The
default install stays lightweight (five packages); `[full]` installs all seven.
Python support is `>=3.11,<3.14`.

"Compatible" here means the suite verifies **installation**, **import**, **CLI
presence**, and **offline-contract** conventions. It does **not** mean the seven
packages have been proven to operate together as one live runtime — the
`live_runtime` compatibility layer remains `not_checked`.

### Changed

- **Track the Beta ecosystem.** All seven components have reached Beta, so the
  dependency ranges and the packaged manifest now target the published Beta
  versions: agentforge-oss `>=0.5.4,<0.6.0`, agentpolicypack / aiauditlog /
  aimeter-oss / privateaistack `>=0.2.0b1,<0.3.0`, openontologylite
  `>=0.2.0b2,<0.3.0`, and modelswapbench `>=0.1.0b1,<0.2.0`.
- **Fix pre-release upper bounds.** The previous `<0.2.0` upper bounds silently
  **excluded** the `0.2.0bN` betas — under PEP 440 an exclusive `<V` does not
  match a pre-release of `V` itself, so `pip install ai-infrastructure-suite`
  would have resolved the old alphas instead of the current Beta releases. The
  upper bound now sits at the next minor above the whole pre-release line
  (`<0.3.0`), which admits the beta, the eventual final, and every patch.
- Refresh the `latest_published_version` and `version_sources_verified_on`
  manifest fields, and add `evaluate_gate` / `GateThresholds` to the ModelSwapBench
  API-symbol checks now that its floor is `0.1.0b1`.
- Pin the PyPI Trusted Publishing action to `pypa/gh-action-pypi-publish` v1.14.2
  (`dc37677b2e1c63e2034f94d8a5b11f265b73ba33`) for Twine 7 / Core Metadata 2.5
  compatibility. OIDC configuration is unchanged; no API token is used.

This raises the suite's own Development Status classifier to `4 - Beta`.

## [0.1.0a1] - 2026-08-08

Initial alpha release.

### Added

- **Metapackage** for the seven Linux of AI components, with a layered dependency
  design. The default install is the lightweight core — AgentForge,
  AgentPolicyPack, AIAuditLog, OpenOntologyLite, and AIMeter — resolving to about
  30 packages with no external infrastructure implied.
- **Optional extras**: `governance`, `benchmarking`, `local`, `observability`,
  `full`, and `dev`. ModelSwapBench, PrivateAIStack, and the OpenTelemetry export
  chain are opt-in, so a default install pulls no FastAPI, Uvicorn, grpcio, or
  protobuf.
- **Packaged ecosystem manifest** (`ecosystem-manifest.json`, schema version 1.0)
  describing all seven components: distribution, import package, CLI, purpose,
  layer, extras, version range, latest published version, repository and PyPI
  URLs, expected API symbols, integration status, offline support, heavy
  infrastructure, and limitations. Validated and deterministically ordered on
  load, and readable without importing Python.
- **`ai-suite` CLI** with `version`, `components`, `doctor`, `compatibility`,
  `info`, `extras`, and `manifest`. All report commands support `--json`;
  `doctor` and `compatibility` support `--no-imports`.
- **`inspect_installation()`** — per-component diagnosis classifying install,
  import, API, and CLI checks as `ok`, `missing`, `incompatible`, `import_error`,
  `optional`, `not_checked`, or `unknown`, with stable diagnostic codes
  (`AIS1001`–`AIS2003`) and a suggested action for every finding.
- **`compatibility_report()`** — five independently reported layers:
  `installation`, `imports`, `cli`, `offline_contract`, and `live_runtime`. The
  live runtime layer is always `not_checked`; running the seven components
  together requires services this package never starts or contacts.
- **Offline contract check** — six deterministic in-memory checks over the
  portable conventions the ecosystem exchanges: canonical JSON stability, an
  anchored fixture digest, hash-chain recomputation, JSONL round trip, exact
  decimal money, and PEP 440 range parsing. Standard library only; no file, no
  socket, no component import.
- **Typed public API** with frozen dataclasses and deterministic,
  JSON-compatible `to_dict()` on every result type.
- **Documentation**: installation, extras, components, doctor, compatibility,
  architecture, release policy, and security.

### Security

- No network access in any command.
- No shell execution, subprocess spawning, or service startup.
- All diagnostic output passes through a redaction filter that strips control
  characters and scrubs Windows and POSIX paths, credential-bearing URLs, and
  secret-looking assignments, then bounds length to 240 characters.
- Environment reporting is an allowlist of four non-identifying fields; no
  hostname, interpreter path, prefix, working directory, or environment variable
  is ever read or emitted.
- Rich markup is disabled on the console so no manifest or third-party string can
  be interpreted as terminal formatting; table borders degrade to ASCII on
  encodings that cannot carry box-drawing characters.
- `--no-imports` for environments where executing third-party import-time code is
  not acceptable.
- The only file written is one the caller names explicitly via `--output`.
- Publishing uses PyPI Trusted Publishing (OIDC) with no API token, triggers
  only by manual dispatch, is gated on the `pypi` GitHub environment, and
  grants `id-token: write` to the publish job alone.

### Notes

- Version ranges were calibrated against versions actually published on PyPI,
  verified 2026-08-08. ModelSwapBench is pinned to `>=0.1.0a6` — its published
  release at calibration time — rather than the then-unreleased `0.1.0a7` in its
  source tree. `0.1.0a7` was published the same day, satisfied the existing range
  immediately, and required no suite change; the manifest records it as
  `latest_published_version` while `minimum_version` stays at `0.1.0a6`.
- The doctor's `api_symbols` for ModelSwapBench are limited to names present
  across the whole supported range. `evaluate_gate` and `GateThresholds` exist
  only from `0.1.0a7`, so checking them would have reported a correct `0.1.0a6`
  install as `incompatible`.
- Python 3.11–3.13. The upper bound is the intersection across the ecosystem:
  four components declare `<3.14`.
- 165 tests, none of which require the seven repositories to be checked out,
  installed, or reachable.

[Unreleased]: https://github.com/sekacorn/AI-Infrastructure-Suite/compare/v0.1.0b2...HEAD
[0.1.0b2]: https://github.com/sekacorn/AI-Infrastructure-Suite/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/sekacorn/AI-Infrastructure-Suite/compare/v0.1.0a1...v0.1.0b1
[0.1.0a1]: https://github.com/sekacorn/AI-Infrastructure-Suite/releases/tag/v0.1.0a1
