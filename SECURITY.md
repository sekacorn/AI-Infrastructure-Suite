# Security policy

## Supported versions

AI Infrastructure Suite is alpha software. Security fixes are applied to the latest release only.

| Version | Supported |
|---|---|
| `0.1.0a1` | yes |

## Reporting a vulnerability

Open a **private security advisory** on the [repository](https://github.com/sekacorn/AI-Infrastructure-Suite/security/advisories). Please do not open a public issue for a suspected vulnerability.

Include the affected version, reproduction steps, and the impact you observed. `ai-suite doctor --json` output is designed to be safe to attach — it contains no paths, credentials, or environment variables.

If the issue is in one of the seven ecosystem components rather than in this suite, report it to that project directly. This package contains none of their logic.

## Security properties

Enforced by tests in `tests/test_security.py`:

- **No network access** in any command.
- **No shell execution**, subprocess spawning, or service startup.
- **No secrets, absolute paths, hostnames, or environment variables** in any output. All diagnostic strings pass through a redaction filter and are length-bounded.
- **No unrequested file access.** The only file written is one named explicitly via `--output`.
- **Terminal-safe output.** Rich markup is disabled and control characters are stripped, so no third-party string can drive the terminal.
- **No publishing workflow** exists in this repository, so no credential can be exfiltrated from one.

## Known tradeoff

Verifying that a component is usable rather than merely installed requires importing it, which executes that package's import-time code. This is the same exposure as `import forge` in a Python shell; the suite decides only *when* it happens.

Use `--no-imports` (or `check_imports=False`) to restrict checks to installed metadata.

Full threat model: [docs/security.md](docs/security.md).
