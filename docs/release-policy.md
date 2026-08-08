# Release policy

## Versioning

The suite follows [semantic versioning](https://semver.org/) with PEP 440 pre-release identifiers. Current version: `0.1.0a1`.

While the suite is `0.x`, the manifest schema, JSON report schemas, and Python API may change between minor versions. Both schemas carry their own `schema_version` so consumers can detect a change rather than discover it.

## Version ranges

Every component is pinned to a **compatible range**, never an exact version:

| Component | Range | Minimum supported | Latest published |
|---|---|---|---|
| `agentforge-oss` | `>=0.5.3,<0.6.0` | 0.5.3 | 0.5.3 |
| `agentpolicypack` | `>=0.1.0a2,<0.2.0` | 0.1.0a2 | 0.1.0a2 |
| `aiauditlog` | `>=0.1.0a4,<0.2.0` | 0.1.0a4 | 0.1.0a4 |
| `openontologylite` | `>=0.1.0a4,<0.2.0` | 0.1.0a4 | 0.1.0a4 |
| `aimeter-oss` | `>=0.1.0a5,<0.2.0` | 0.1.0a5 | 0.1.0a5 |
| `modelswapbench` | `>=0.1.0a6,<0.2.0` | 0.1.0a6 | **0.1.0a7** |
| `privateaistack` | `>=0.1.0a3,<0.2.0` | 0.1.0a3 | 0.1.0a3 |

Verified against PyPI on the date in the manifest's `version_sources_verified_on`.

ModelSwapBench shows the two columns doing their job. The range was calibrated against `0.1.0a6`; `0.1.0a7` was published shortly afterwards, resolves automatically under the existing upper bound, and needed **no suite release**. The minimum stays at `0.1.0a6` because that version is still supported and verified — raising it would exclude a working release for no reason.

### Three rules

**1. The lower bound is the latest version actually published.**

Not the latest version in a source tree, and not a version that is planned. A range that selects an unreleased version makes the metapackage uninstallable for everyone.

This is a live concern, not a hypothetical. While this package was being built, ModelSwapBench's source tree carried `0.1.0a7` while PyPI's latest was `0.1.0a6`. The suite pinned `>=0.1.0a6` — the published one. `0.1.0a7` was published a few hours later, satisfied the existing range immediately, and required **no suite release**. Had the range been written against the source tree, every install would have failed until that upload happened.

**2. The upper bound is the next minor.**

`<0.6.0` and `<0.2.0` let patch and minor releases flow through without a suite release. On an alpha component, `<0.2.0` admits `0.1.0a7`, the `0.1.0` final, and every `0.1.x`.

This bounds blast radius without freezing users out of fixes. It does not guarantee that a release inside the range is safe; it limits how far a surprise can travel.

**3. Alpha lower bounds are deliberate.**

Under PEP 440, pip ignores pre-releases unless the specifier itself contains one. Because every alpha component is pinned with an alpha lower bound, `pip install ai-infrastructure-suite` resolves them correctly with **no `--pre` flag** — and without opting the rest of the environment into pre-releases.

### Recording the calibration

The manifest carries both, per component:

- `minimum_version` — the range's lower bound
- `latest_published_version` — what was on PyPI when the range was set

When they are equal, the range was calibrated against the newest release. When they diverge — as they now do for ModelSwapBench — the component has published something newer that the range still admits, so no suite change is needed.

```bash
ai-suite manifest | python -c "
import json,sys
for c in json.load(sys.stdin)['components']:
    print(c['distribution'], c['version_specifier'], 'calibrated:', c['latest_published_version'])
"
```

## When a component releases

| Release | Suite action |
|---|---|
| Patch or minor inside the range | **None.** It resolves automatically. |
| New minor crossing the upper bound | Update the range in `pyproject.toml` **and** the manifest, verify, release a suite patch. |
| Breaking change | Update the range, re-verify the API symbols the doctor checks, release a suite minor. |
| Distribution or import rename | Update the manifest, release a suite minor. Consider keeping the old entry with a note. |

`pyproject.toml` and the manifest must be updated together — a test enforces that they agree, and CI fails if they drift.

## Release checklist

Every release must pass, on Python 3.11, 3.12, and 3.13:

1. `ruff check .` and `ruff format --check .`
2. `mypy` (strict)
3. `pytest`
4. `bandit -c pyproject.toml -r src`
5. `pip-audit`
6. `python -m build` — wheel and sdist
7. `twine check dist/*`
8. Fresh install of the wheel **outside** the repository
9. Fresh install of the sdist **outside** the repository
10. CLI smoke: `--help`, `version`, `components`, `doctor --json`, `compatibility --json`
11. Metadata scan: no personal name, no personal email, no local paths, no secrets
12. Re-verify every component's latest published version against PyPI and refresh `version_sources_verified_on`

## Publishing

Not automated, and deliberately so. This repository has **no workflow that can publish to PyPI**, accidentally or otherwise. CI builds artifacts and runs `twine check`; it does not upload them.

A future release workflow, if added, must require:

- An explicit version tag
- A GitHub environment named `pypi` with required reviewers
- **Trusted Publishing (OIDC)** — no long-lived PyPI API token
- Manual approval before the publish step
- Post-publish verification that the version on PyPI matches the tag exactly

## Support

- **Python:** 3.11–3.13. The upper bound is the intersection across the ecosystem: four components declare `<3.14`. It will lift when they do.
- **Platforms:** Linux, macOS, and Windows. The suite is pure Python; the only compiled wheels in the default resolution are `pydantic-core` and `cryptography`.
- **Alpha caveat:** both the suite and the ecosystem are alpha. APIs and schemas may still change.
