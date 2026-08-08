# Security

## Scope

This package installs distributions and inspects the local environment. It has a small attack surface by construction, and the guarantees below are enforced by tests in `tests/test_security.py` — a regression there is a documentation lie, not just a bug.

## Guarantees

### No network access

No command in this package opens a socket. Not for version checks, not for telemetry, not for update notifications. Version ranges are static data in the packaged manifest, calibrated at release time and recorded with the date they were verified.

Tests run every command with `socket.socket`, `socket.create_connection`, and `socket.getaddrinfo` patched to raise.

### No shell, no subprocess, no services

The suite never executes a shell, spawns a subprocess, starts a container, launches a model server, or connects to a database. Console-script checks read **entry-point metadata**; they never resolve or run a script.

Tests assert that `subprocess.run`, `subprocess.Popen`, and `os.system` are never called during any command.

### No secrets, paths, or identity in output

Every diagnostic string passes through `redact_text` before reaching the console or a JSON document. It:

- strips C0/C1 control characters, including ANSI escape introducers
- replaces Windows absolute paths, including UNC (`\\?\C:\...`)
- replaces POSIX home and system paths (`/home/...`, `/Users/...`, `/root/...`, `/var/...`, `/tmp/...`)
- replaces any other reasonably deep absolute POSIX path
- replaces credentials embedded in URLs (`scheme://user:password@host`)
- replaces secret-looking assignments — `api_key`, `secret`, `token`, `password`, `credential`, `authorization` — including `Bearer` values
- bounds the result to 240 characters

`environment_info()` is an **allowlist**, returning exactly four fields:

```json
{
  "python_version": "3.13.12",
  "python_implementation": "CPython",
  "platform_system": "Windows",
  "platform_machine": "AMD64"
}
```

There is no code path that adds a hostname, interpreter path, prefix, working directory, or environment variable. `platform.node()` and `sys.executable` are never read.

Tests invoke every command with fake `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` values set and assert that neither the value nor the home directory appears in any output.

**Output is safe to paste into a bug report.** That is the design goal.

### No unrequested file access

The suite reads its own packaged manifest through `importlib.resources`. It reads no user project file, no configuration file, no dotfile, and no credential store.

The only file it ever writes is one you name explicitly:

```bash
ai-suite manifest --output ./ecosystem.json
```

The offline contract check is entirely in memory; a test runs it with `open` patched to raise.

### Terminal-safe output

- The Rich console is created with `markup=False`, so no manifest text or third-party string can be interpreted as terminal formatting.
- Control characters are stripped by `redact_text`.
- Table borders fall back to ASCII when the active encoding cannot carry box-drawing characters.
- All internal strings are ASCII, and JSON is emitted with `ensure_ascii`, so output survives a legacy cp1252 console.

## The one real tradeoff: import checks

Verifying that a component is *usable* rather than merely *installed* means running `import`, which executes that package's import-time code.

**What that means.** A compromised or broken package in your environment could execute code during `ai-suite doctor`. This is exactly the same exposure as `import forge` in a Python shell — the suite adds no new risk beyond deciding *when* the import happens — but it is worth stating rather than burying.

**What the suite does about it.** Each import is isolated: exceptions are caught, redacted, and reported as an `AIS1003` diagnostic. One broken component cannot abort the run or crash the CLI.

**How to opt out.**

```bash
ai-suite doctor --no-imports
ai-suite compatibility --no-imports
```

```python
inspect_installation(check_imports=False)
compatibility_report(check_imports=False)
```

Checks are then restricted to installed metadata. Import and API statuses become `not_checked`, `AIS2002` is recorded, and — because a skipped check is not a failure — the exit code stays `0` when nothing else is wrong.

A test asserts that `--no-imports` never calls `importlib.import_module`.

## Supply chain

The suite's job is installing other people's code, so:

- **Its own dependencies are three:** `packaging`, `rich`, `typer` — all already in the core ecosystem resolution.
- **Ranges are bounded at both ends.** Every component has an upper bound at the next minor, so a future major release cannot be pulled in silently.
- **Nothing is vendored or mirrored.** Components come from PyPI over pip's own verified path. The suite adds no index, no mirror, and no download step.
- **No install-time code.** The build backend is `hatchling` with no custom build hooks, and the package has no `setup.py` and no post-install script.
- **CI runs `pip-audit` and `bandit`** on every push and pull request.
- **No publishing workflow exists**, so no credential can be exfiltrated from one and nothing can be published by accident. See [release-policy.md](release-policy.md) for the requirements any future workflow must meet.

## What this package does not protect you from

Stated plainly:

- **It does not audit the components.** It checks that they are present, in range, and importable. Their own security posture is theirs — each publishes a `SECURITY.md` and, in several cases, a threat model.
- **It does not verify integrity beyond pip's.** No signature checking, no hash pinning. Use a lockfile with hashes if you need that.
- **It does not sandbox imports.** `--no-imports` avoids them; it does not contain them.
- **It does not make the ecosystem's guarantees stronger.** Hash chains remain tamper-evident rather than immutable. Policy decisions remain evaluated rather than enforced. Estimated costs remain estimates.

## Reporting a vulnerability

Open a private security advisory on the [repository](https://github.com/sekacorn/AI-Infrastructure-Suite). Please do not open a public issue for a suspected vulnerability.

If the issue is in one of the seven components, report it to that project directly — this package contains none of their logic.
