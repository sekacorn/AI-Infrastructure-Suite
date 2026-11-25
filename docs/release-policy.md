# Release policy

## Versioning

The suite follows [semantic versioning](https://semver.org/) with PEP 440 pre-release identifiers. Current version: `0.1.0b4` (Beta release).

While the suite is `0.x`, the manifest schema, JSON report schemas, and Python API may change between minor versions. Both schemas carry their own `schema_version` so consumers can detect a change rather than discover it.

## Version ranges

Every component is pinned to a **compatible range**, never an exact version:

| Component | Range | Minimum supported | Latest published |
|---|---|---|---|
| `agentforge-oss` | `>=0.5.5,<0.6.0` | 0.5.5 | 0.5.5 |
| `agentpolicypack` | `>=0.2.0b1,<0.3.0` | 0.2.0b1 | 0.2.0b1 |
| `aiauditlog` | `>=0.2.0b1,<0.3.0` | 0.2.0b1 | 0.2.0b2 |
| `openontologylite` | `>=0.2.0b3,<0.3.0` | 0.2.0b3 | 0.2.0b3 |
| `aimeter-oss` | `>=0.2.0b1,<0.3.0` | 0.2.0b1 | 0.2.0b1 |
| `modelswapbench` | `>=0.1.0b4,<0.2.0` | 0.1.0b4 | 0.1.0b4 |
| `privateaistack` | `>=0.2.0b1,<0.3.0` | 0.2.0b1 | 0.2.0b2 |

Verified against PyPI on the date in the manifest's `version_sources_verified_on`.

AIAuditLog shows the two columns doing their job: the range was set at `0.2.0b1`, `0.2.0b2` was published afterwards, resolves automatically under the existing upper bound, and needed **no suite release**. The minimum stays at `0.2.0b1` because that version is still supported.

A minimum is raised only when an older release should no longer be installed. `agentforge-oss` moved to `0.5.5` because earlier versions could leak a credential through provider errors, and `modelswapbench` moved to `0.1.0b4` because earlier versions reported an unknown cost as zero for some hosted endpoints (fixed in `0.1.0b3`) and wrote credentials placed in a model's `base_url` to run artefacts and logs (fixed in `0.1.0b4`).

### Three rules

**1. The lower bound is the latest version actually published.**

Not the latest version in a source tree, and not a version that is planned. A range that selects an unreleased version makes the metapackage uninstallable for everyone.

This is a live concern, not a hypothetical. While this package was being built, ModelSwapBench's source tree carried `0.1.0a7` while PyPI's latest was `0.1.0a6`. The suite pinned `>=0.1.0a6` — the published one. `0.1.0a7` was published a few hours later, satisfied the existing range immediately, and required **no suite release**. Had the range been written against the source tree, every install would have failed until that upload happened.

**2. The upper bound is the next minor.**

`<0.6.0` and `<0.2.0` let patch and minor releases flow through without a suite release. For a `0.2.0bN` beta line the next-minor bound is `<0.3.0`, which admits `0.2.0b1`, the `0.2.0` final, and every `0.2.x`. Note `<0.2.0` would exclude `0.2.0b1`: under PEP 440 an exclusive `<V` never matches a pre-release of `V` itself.

This bounds blast radius without freezing users out of fixes. It does not guarantee that a release inside the range is safe; it limits how far a surprise can travel.

**3. Pre-release lower bounds are deliberate.**

Under PEP 440, pip ignores pre-releases unless the specifier itself contains one. Because each current Beta component is pinned with a beta lower bound, `pip install ai-infrastructure-suite` resolves them correctly with **no `--pre` flag** — and without opting the rest of the environment into pre-releases.

### Recording the calibration

The manifest carries both, per component:

- `minimum_version` — the range's lower bound
- `latest_published_version` — what was on PyPI when the range was set

When they are equal, the range was calibrated against the newest release. When they diverge — as they now do for AIAuditLog and PrivateAIStack — the component has published something newer that the range still admits, so no suite change is needed.

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

Publishing runs through `.github/workflows/release.yml` using **PyPI Trusted Publishing (OIDC)**. No PyPI API token exists, is referenced, or is needed.

The workflow is built so a release is always deliberate:

- **Manual trigger only** (`workflow_dispatch` with a tag input). A tag push cannot start a publish.
- **A dispatch rehearses by default.** The `dry_run` input defaults to `true`: every check and the build run, and the upload and everything after it are skipped. Only `-f dry_run=false` publishes.
- **Tag/version agreement is enforced.** The build job refuses to continue unless the tag resolves to the version in `_version.py`, so the tag and the artifact can never disagree.
- **The publish job is gated on the `pypi` GitHub environment**, which should carry required reviewers so a human approves the upload.
- **`id-token: write` is granted only to the publish job**; the rest of the workflow runs read-only.
- **Exactly one distribution at one version** may reach the upload step; anything else fails the run.
- **Post-publish verification** installs the exact version from PyPI in clean environments — wheel and sdist separately — runs the CLI, confirms the default install pulls nothing heavy, confirms `[full]` resolves all seven, and confirms the import resolves from `site-packages`.

### One-time PyPI setup

Trusted Publishing for a project that does not yet exist requires a **pending publisher**, created by the PyPI account owner at <https://pypi.org/manage/account/publishing/>:

| Field | Value |
|---|---|
| PyPI project name | `ai-infrastructure-suite` |
| Owner | `sekacorn` |
| Repository name | `AI-Infrastructure-Suite` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Without it the publish step fails with `invalid-publisher`. This step cannot be automated: it requires an authenticated PyPI session.

### Running a release

```bash
git tag -a v0.1.0b4 <verified-commit> -m "AI Infrastructure Suite 0.1.0b4"
git push origin refs/tags/v0.1.0b4:refs/tags/v0.1.0b4
gh workflow run release.yml -f tag=v0.1.0b4                 # rehearsal
gh workflow run release.yml -f tag=v0.1.0b4 -f dry_run=false  # real release
```

Push the tag by explicit refspec, never with `--tags`. Then approve the `pypi` environment deployment when prompted. A published version can never be re-uploaded, so rehearse first.

## Support

- **Python:** 3.11–3.13. The upper bound is the intersection across the ecosystem: four components declare `<3.14`. It will lift when they do.
- **Platforms:** Linux, macOS, and Windows. The suite is pure Python; the only compiled wheels in the default resolution are `pydantic-core` and `cryptography`.
- **Maturity caveat:** the seven components and this suite (`0.1.0b4` Beta release) have reached Beta. APIs and schemas may still change before 1.0.
