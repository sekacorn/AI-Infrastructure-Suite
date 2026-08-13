# Changelog

All notable changes to AI Infrastructure Suite are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/) with PEP 440 pre-release identifiers.

---

## [Unreleased]

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

[Unreleased]: https://github.com/sekacorn/AI-Infrastructure-Suite/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/sekacorn/AI-Infrastructure-Suite/compare/v0.1.0a1...v0.1.0b1
[0.1.0a1]: https://github.com/sekacorn/AI-Infrastructure-Suite/releases/tag/v0.1.0a1
