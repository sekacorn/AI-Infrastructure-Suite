# Contributing

Contributions are welcome.

## The one architectural rule

**This package must not duplicate the business logic of the seven components.**

It installs them, describes them, and diagnoses them. Nothing else. If a change would make the suite reimplement something a component already does, it belongs in that component instead.

Concretely, the suite does not: run or wrap component functionality, start or contact any service, cache or mirror any package, reimplement dependency resolution, or claim live runtime integration.

## Useful contributions

- **Version-range updates** when a component publishes a release that crosses an upper bound
- **Additional diagnostic checks** that stay offline, bounded, and deterministic
- **Documentation improvements**, especially anywhere a claim is stronger than the evidence
- **Tests** across Python 3.11–3.13
- **Bug reports** with `ai-suite doctor --json` output attached — it is designed to be safe to paste

## Development setup

```bash
git clone https://github.com/sekacorn/AI-Infrastructure-Suite.git
cd AI-Infrastructure-Suite
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check .
ruff format --check .
mypy
pytest
bandit -c pyproject.toml -r src
pip-audit
```

All must pass. CI runs the same checks on Python 3.11, 3.12, and 3.13.

## Standards

- **Typed.** `mypy --strict` passes. Result types are frozen `slots` dataclasses with a deterministic, JSON-compatible `to_dict()`.
- **Offline.** No command may open a socket, spawn a subprocess, or execute a shell. Tests enforce this.
- **Bounded.** Any string that can originate outside this package goes through `redact_text` before reaching output.
- **Deterministic.** Same environment, same output. Collections are sorted at load time, not at display time.
- **No eager imports.** The package must import cleanly with zero components installed.

## Updating a version range

Ranges live in **two places that must agree**:

1. `pyproject.toml` — `dependencies` and `optional-dependencies`
2. `src/ai_infrastructure_suite/data/ecosystem-manifest.json` — `version_specifier`, `minimum_version`, `latest_published_version`

`tests/test_packaging.py` fails if they drift.

Two rules when choosing a bound:

- The lower bound must be a version **actually published on PyPI**. A range that selects an unreleased version makes the metapackage uninstallable.
- Keep the alpha lower bound on alpha components. Under PEP 440 it is what makes pip resolve them without `--pre`.

Update `version_sources_verified_on` when you re-verify against PyPI. See [docs/release-policy.md](docs/release-policy.md).

## Choosing `api_symbols`

The doctor's API check asserts these names exist on a component's public package. They must be present across the **entire supported range**, so verify them against the range's **published lower bound**, not against the component's newest source tree.

Getting this wrong reports a healthy install as `incompatible`. It happened during initial development: ModelSwapBench's `evaluate_gate` and `GateThresholds` exist in its source tree but arrived after the published `0.1.0a6` that the range pins to, so a correct `[full]` install was flagged as broken.

Prefer long-lived names — error classes, primary entry points — over recently added helpers:

```bash
pip install "ai-infrastructure-suite[full]"
ai-suite doctor --json | python -c "
import json,sys
for c in json.load(sys.stdin)['components']:
    if c['api_status'] not in {'ok','not_checked'}:
        print(c['distribution'], c['diagnostics'])
"
```

## Adding a diagnostic check

1. Add a `DiagnosticCode` member with the next free number.
2. Emit it from `doctor.py` with a `message` and an **actionable** `action` — a command the user can run.
3. Decide deliberately whether it changes the component's overall status. Informational findings (`AIS1006`, `AIS1007`, `AIS1008`) do not.
4. Test the new category in `tests/test_doctor.py` using the `fake_environment` fixture.
5. Document it in `docs/doctor.md`.

## Tests

Tests must **not** require the seven repositories to be checked out, installed, or reachable. Use the `component_factory`, `manifest_factory`, and `fake_environment` fixtures in `tests/conftest.py` to build synthetic components and fake installed metadata.

Anything asserting a promise made in the README — no network, no shell, no leakage — belongs in `tests/test_security.py`.

## Commit and PR conventions

- Present-tense, imperative subject lines: `Add pgvector detection to doctor`
- No AI attribution and no `Co-authored-by` trailers
- No personal names, personal email addresses, local paths, or secrets in code, tests, fixtures, docs, metadata, or commit messages
- The public maintainer identity is `sekacorn`

## Reporting security issues

Open a private security advisory rather than a public issue. See [docs/security.md](docs/security.md).

## License

Contributions are licensed under the [Apache License 2.0](LICENSE).
