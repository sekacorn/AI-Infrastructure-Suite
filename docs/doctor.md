# `ai-suite doctor`

Diagnoses the installed ecosystem. Every check is local, bounded, offline, and read-only.

```bash
ai-suite doctor
ai-suite doctor --json
ai-suite doctor --no-imports
```

## What it checks

Per component:

1. **Installed?** — `importlib.metadata.version(distribution)`. Metadata only; nothing is imported.
2. **Version in range?** — the installed version against the manifest specifier, with pre-releases accepted.
3. **Public import works?** — `import <package>`. Skippable with `--no-imports`.
4. **Expected API present?** — the component's documented public names exist on the imported module.
5. **Console script registered?** — entry-point metadata is read. **No script is executed.**
6. **Heavy infrastructure expected?** — informational, from the manifest.
7. **Version metadata coherent?** — distribution version against the module's `__version__`.

Globally:

- Is the running interpreter inside the supported range?
- Do the manifest's version ranges parse as PEP 440?

## What it never does

Start Docker · start Ollama · connect to PostgreSQL · call a hosted provider · access a cloud account · read credentials · send telemetry · execute a shell · modify a repository · write any file except one you named with `--output`.

## Status values

| Status | Meaning |
|---|---|
| `ok` | Installed, in range, imports, exposes its API |
| `missing` | Part of the default install but absent |
| `incompatible` | Installed but outside the supported range, or missing expected API names |
| `import_error` | Installed and in range, but importing it raised |
| `optional` | Not installed, and not part of the default install. **Not a problem.** |
| `not_checked` | The check did not run — usually `--no-imports` |
| `unknown` | Could not be determined, e.g. an unparseable version |

### How the overall status is derived

Two rules keep it honest:

- **An absent distribution *is* the answer.** When a component is `missing` or `optional`, downstream checks could not run, so their `not_checked` values are not folded in — burying "missing" under "not_checked" would be less informative.
- **`not_checked` never outvotes a real result.** `--no-imports` reports what it did verify rather than degrading every component to `not_checked`.

A healthy component is `ok` **or** `optional`. The report's overall status is `ok` when every component is healthy, otherwise the most severe finding. Severity ascends: `ok` → `optional` → `not_checked` → `unknown` → `missing` → `incompatible` → `import_error`.

**Absent optional components never cause a non-zero exit.** Neither does `--no-imports`.

### Secondary statuses

`cli_status` and the informational diagnostics do **not** change the overall status. A missing console script is reported (`AIS1005`) but reflects a packaging quirk rather than an unusable component, so it will not fail your CI on its own. Check the field directly if you need to gate on it.

## Diagnostic codes

| Code | Meaning | Severity |
|---|---|---|
| `AIS1001` | Component missing from the default install | error |
| `AIS1002` | Installed version outside the supported range, or unparseable | error |
| `AIS1003` | Import failed | error |
| `AIS1004` | Expected public API names absent | error |
| `AIS1005` | Expected console script not registered | warning |
| `AIS1006` | Optional component absent | informational |
| `AIS1007` | Component expects external services at runtime | informational |
| `AIS1008` | Distribution version differs from module `__version__` | warning |
| `AIS2001` | Python interpreter outside the supported range | error |
| `AIS2002` | Import checks skipped | informational |
| `AIS2003` | Manifest version range unparseable — a bug in this package | error |

Every diagnostic carries a `message` and a **suggested action**, and the action is a command you can run.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every check that ran passed |
| `1` | Completed with findings |
| `2` | The command could not complete |

## JSON output

```bash
ai-suite doctor --json
```

```json
{
  "schema_version": "1.0",
  "suite_version": "0.1.0b1",
  "manifest_schema_version": "1.0",
  "environment": {
    "python_version": "3.13.12",
    "python_implementation": "CPython",
    "platform_system": "Windows",
    "platform_machine": "AMD64"
  },
  "python_requires": ">=3.11,<3.14",
  "python_supported": true,
  "imports_checked": true,
  "status": "ok",
  "ok": true,
  "summary": {
    "ok": 5, "missing": 0, "incompatible": 0, "import_error": 0,
    "optional": 2, "not_checked": 0, "unknown": 0
  },
  "diagnostics": [],
  "components": [
    {
      "component_id": "agentforge",
      "name": "AgentForge",
      "distribution": "agentforge-oss",
      "import_package": "forge",
      "cli": "forge",
      "layer": "core",
      "installed_by_default": true,
      "extras": ["full", "observability"],
      "purpose": "Async multi-agent orchestration ...",
      "required_specifier": ">=0.5.4,<0.6.0",
      "minimum_version": "0.5.4",
      "latest_published_version": "0.5.4",
      "installed_version": "0.5.4",
      "status": "ok",
      "install_status": "ok",
      "import_status": "ok",
      "api_status": "ok",
      "cli_status": "ok",
      "diagnostics": []
    }
  ]
}
```

`summary` always contains all seven status keys, including zero counts, so consumers can index without guarding.

The `environment` object is deliberately four fields. There is no hostname, no interpreter path, no prefix, no working directory, and no environment variable — see [security.md](security.md).

### Using it in CI

```bash
ai-suite doctor --json > doctor.json || echo "findings present"
python - <<'PY'
import json, sys
report = json.load(open("doctor.json"))
broken = [c["component_id"] for c in report["components"]
          if c["status"] in {"missing", "incompatible", "import_error"}]
if broken:
    sys.exit(f"unusable components: {', '.join(broken)}")
PY
```

## `--no-imports`

Verifying that a component imports means running `import`, which executes that package's import-time code. That is the only way to distinguish "installed" from "actually usable", and it is a real tradeoff.

```bash
ai-suite doctor --no-imports
```

restricts every check to installed metadata. Import and API statuses become `not_checked`, `AIS2002` is recorded, and the exit code stays `0` when nothing else is wrong.

## Python API

```python
from ai_infrastructure_suite import inspect_installation

report = inspect_installation()  # or check_imports=False

print(report.status.value, report.ok)
print(report.summary())

for component in report.components:
    if not component.is_healthy:
        for finding in component.diagnostics:
            print(component.name, finding.code.value, finding.message)
            print("  ->", finding.action)

payload = report.to_dict()  # identical to --json
```
