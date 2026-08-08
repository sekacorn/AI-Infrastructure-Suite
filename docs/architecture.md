# Architecture

## What this package is

A **metapackage plus a diagnostics layer**. It installs seven independent components and answers questions about them. It contains none of their business logic, and that constraint is load-bearing: the moment the suite reimplements something a component already does, it becomes a fork with a version-skew problem.

The rule for contributions: *if a change would make the suite duplicate a component's behaviour, it belongs in that component.*

## Module layout

```
src/ai_infrastructure_suite/
    __init__.py          public API re-exports
    __main__.py          python -m ai_infrastructure_suite
    _version.py          single source of truth for the version
    errors.py            exception hierarchy
    components.py        manifest models and deterministic loading
    _inspection.py       private probes: redaction, environment, metadata, imports
    doctor.py            per-component diagnosis  -> inspect_installation()
    compatibility.py     five-layer report        -> compatibility_report()
    cli.py               the ai-suite command
    data/
        ecosystem-manifest.json
```

Dependencies flow one way, with no cycles:

```
errors  <-  components  <-  doctor  <-  compatibility  <-  cli
              ^               |              |              |
              +-- _inspection +--------------+--------------+
```

`_inspection` is private because its probes are implementation detail. The stable surface is what `__init__` re-exports.

## Design decisions

### The manifest is data, not code

The seven components are described in `data/ecosystem-manifest.json`, loaded through `importlib.resources`. Consequences:

- CI can read it without importing Python: `ai-suite manifest > ecosystem.json`.
- Updating a version range is a data edit, not a code change.
- It is validated on load — missing fields, wrong types, duplicate ids, unparseable ranges, and extras referencing unknown components all raise `ManifestError` rather than failing later during diagnosis.

Ordering is normalised at load time: components sorted by `id`, extras by `name`. Consumers get the same order regardless of how the JSON happens to be written, which keeps JSON output diffable.

A test asserts the manifest agrees with `pyproject.toml` — every component's range must match a declared requirement, default components must be base dependencies, and optional ones must not be. Drift between the two would make the doctor report ranges the installer does not use.

### Nothing is imported eagerly

`import ai_infrastructure_suite` imports the standard library, `packaging`, `typer`, and `rich`. It imports **no ecosystem component**.

This is what makes the failure modes work. `ai-suite --help`, `ai-suite version`, and `ai-suite components` all function with zero components installed, and `ai-suite doctor` reports a broken component instead of inheriting its crash. Component imports happen only inside `probe_import`, one at a time, each wrapped so any exception becomes a redacted diagnostic.

### Checks are classified, not collapsed

Each component carries five independently classified results — `install_status`, `import_status`, `api_status`, `cli_status`, and a folded `status`. Keeping them separate means "installed but won't import" and "not installed" are distinguishable, and a consumer can gate on whichever one it actually cares about.

The folding rules are in [doctor.md](doctor.md). Two matter: an absent distribution is reported as such rather than as `not_checked`, and `not_checked` never outvotes a real result — so `--no-imports` reports what it verified instead of degrading everything.

### Compatibility is layered

Five layers, reported separately, with `live_runtime` permanently `not_checked`. See [compatibility.md](compatibility.md). The alternative — one boolean — would have to be either dishonest or useless.

### Typed, frozen, serialisable

Every result is a frozen `slots` dataclass with a `to_dict()` returning JSON-compatible primitives in deterministic key order. Enums use a `str` mixin so they serialise as their values. `mypy --strict` passes.

Frozen matters because reports get passed around and cached; nothing downstream can mutate a diagnosis.

## Security posture

The threat model is in [security.md](security.md). Architecturally:

- **`_inspection.redact_text` is a chokepoint.** Every diagnostic string — including third-party exception text — passes through it before reaching output. It strips control characters, scrubs Windows and POSIX paths, credential-bearing URLs, and secret-looking assignments, then bounds length.
- **`environment_info` is an allowlist**, not a filter. It returns exactly four fields. There is no code path that can add a hostname or a path to it.
- **The console disables Rich markup.** Manifest text and third-party strings can never be interpreted as terminal formatting.
- **Table borders degrade to ASCII** when the active encoding cannot carry box-drawing characters, and all internal strings are ASCII, so output survives a legacy cp1252 console.
- **JSON uses `ensure_ascii`**, so it stays parseable and encodable everywhere.

## Dependency design

The suite's own dependencies are `packaging`, `rich`, and `typer` — all already present in the core ecosystem resolution, so the metapackage adds no meaningful weight of its own.

The component layering is in [extras.md](extras.md). The principle: **a component is in the default install only if it is lightweight and implies no external infrastructure.** Measured, that is 30 packages by default versus 55 for `[full]`.

## Testing approach

162 tests, none of which require the seven repositories to be checked out, installed, or reachable.

Component presence, versions, console scripts, and imports are faked by patching the names as bound inside `doctor`, and synthetic manifests are built by fixtures. So every status category — including `import_error`, `incompatible`, and `unknown` — is exercised deterministically on a bare interpreter, offline.

Tests that encode a promise made in the README (no network, no shell, no secret or path leakage, `--no-imports` imports nothing) live in `test_security.py`. A regression there is a documentation lie, not just a bug.

## What this package deliberately does not do

- Run, wrap, or proxy any component's functionality
- Start or contact any service
- Cache, vendor, or mirror any component
- Reimplement version resolution — `pip` does that; the suite reports on the result
- Claim live runtime integration
