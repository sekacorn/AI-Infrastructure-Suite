# Changelog

All notable changes to AI Infrastructure Suite are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/) with PEP 440 pre-release identifiers.

---

## [Unreleased]

## [0.1.0a1] - 2026-08-08

Initial alpha. Not published to PyPI.

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
- No workflow in this repository can publish to PyPI.

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
- 162 tests, none of which require the seven repositories to be checked out,
  installed, or reachable.

[Unreleased]: https://github.com/sekacorn/AI-Infrastructure-Suite/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/sekacorn/AI-Infrastructure-Suite/releases/tag/v0.1.0a1
