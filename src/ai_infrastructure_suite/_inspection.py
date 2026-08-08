"""Internal probes for environment and distribution inspection.

Everything in this module is deliberately bounded, offline, and side-effect free:

* no shell execution,
* no network access,
* no reading of user project files,
* no environment-variable values in any output,
* no absolute paths in any output.

The one operation that runs third-party code is :func:`probe_import`, which
performs a real ``import``. That is opt-out via the caller. See ``docs/security.md``.

This module is private. Its names may change without a deprecation period.
"""

from __future__ import annotations

import importlib
import platform
import re
import sys
from dataclasses import dataclass
from importlib import metadata
from typing import Final

MAX_DIAGNOSTIC_CHARS: Final = 240
_REDACTED: Final = "[redacted]"

# Ordered most-specific first: a Windows path must be scrubbed before the generic
# POSIX rule gets a chance to partially match something inside it.
_REDACTION_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # scheme://user:password@host
    (re.compile(r"://[^/\s:]+:[^@/\s]+@"), f"://{_REDACTED}@"),
    # key = value / key: value for secret-looking key names. The optional "bearer"
    # prefix matters: without it the scheme word is eaten as the value and the
    # actual token survives into the output.
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential|authorization)\b"
            r"\s*[=:]\s*(?:bearer\s+)?\S+"
        ),
        rf"\1={_REDACTED}",
    ),
    # A bare "Bearer <token>" with no preceding key name.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), f"bearer {_REDACTED}"),
    # Windows absolute paths, with or without a UNC prefix
    (re.compile(r"(?:\\\\\?\\)?[A-Za-z]:[\\/][^\s'\"<>|]*"), _REDACTED),
    # POSIX home and root-owned locations
    (re.compile(r"/(?:home|Users|root|var|tmp|opt|srv)(?:/[^\s'\":<>|]*)?"), _REDACTED),
    # Any other reasonably deep absolute POSIX path
    (re.compile(r"/(?:[\w.\-]+/){2,}[\w.\-]*"), _REDACTED),
)

# C0/C1 control characters except tab. Strips ANSI escape introducers so that
# untrusted text from a third-party traceback cannot drive the terminal.
_CONTROL_CHARS: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def redact_text(value: str, *, max_chars: int = MAX_DIAGNOSTIC_CHARS) -> str:
    """Return ``value`` with paths and secret-like fragments removed and length bounded.

    Applied to every diagnostic string that reaches the console or a JSON
    document, including exception messages produced by third-party packages.
    """
    text = _CONTROL_CHARS.sub(" ", value)
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    text = " ".join(text.split())
    if len(text) > max_chars:
        # ASCII ellipsis only: diagnostics must survive a legacy cp1252 console.
        text = text[: max_chars - 3].rstrip() + "..."
    return text


@dataclass(frozen=True, slots=True)
class EnvironmentInfo:
    """Non-identifying description of the running interpreter."""

    python_version: str
    python_implementation: str
    platform_system: str
    platform_machine: str

    def to_dict(self) -> dict[str, str]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "python_version": self.python_version,
            "python_implementation": self.python_implementation,
            "platform_system": self.platform_system,
            "platform_machine": self.platform_machine,
        }


def environment_info() -> EnvironmentInfo:
    """Collect the environment facts needed for diagnosis, and nothing more.

    Deliberately excludes the hostname, the interpreter path, the prefix, the
    working directory, and every environment variable.
    """
    return EnvironmentInfo(
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        platform_system=platform.system(),
        platform_machine=platform.machine(),
    )


def installed_version(distribution: str) -> str | None:
    """Return the installed version of ``distribution``, or ``None`` if absent.

    Reads installed metadata only; the distribution is never imported.
    """
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # pragma: no cover - corrupt metadata on disk
        return None


def console_scripts(distribution: str) -> tuple[str, ...]:
    """Return the console-script names a distribution registers, sorted.

    Reads entry-point metadata. It does not resolve, load, or execute any script.
    """
    try:
        dist = metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        return ()
    except Exception:  # pragma: no cover - corrupt metadata on disk
        return ()
    try:
        entries = dist.entry_points
    except Exception:  # pragma: no cover - corrupt metadata on disk
        return ()
    return tuple(sorted(item.name for item in entries if item.group == "console_scripts"))


@dataclass(frozen=True, slots=True)
class ImportProbe:
    """Outcome of importing a component's public package."""

    imported: bool
    error: str | None
    missing_symbols: tuple[str, ...]
    module_version: str | None


def probe_import(import_package: str, expected_symbols: tuple[str, ...] = ()) -> ImportProbe:
    """Import ``import_package`` and check that ``expected_symbols`` are present.

    Importing a third-party package runs that package's import-time code. Callers
    that cannot accept this must skip the probe rather than sandbox it here.

    Any failure is captured and redacted rather than raised, so one broken
    component cannot abort a whole diagnostic run.
    """
    try:
        module = importlib.import_module(import_package)
    except Exception as exc:  # third-party imports can raise anything
        detail = f"{type(exc).__name__}: {exc}"
        return ImportProbe(
            imported=False,
            error=redact_text(detail),
            missing_symbols=expected_symbols,
            module_version=None,
        )

    missing = tuple(name for name in expected_symbols if not hasattr(module, name))
    raw_version = getattr(module, "__version__", None)
    module_version = raw_version if isinstance(raw_version, str) else None
    return ImportProbe(
        imported=True,
        error=None,
        missing_symbols=missing,
        module_version=module_version,
    )


def python_version_string() -> str:
    """Return the running interpreter version as ``major.minor.micro``."""
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


__all__ = [
    "MAX_DIAGNOSTIC_CHARS",
    "EnvironmentInfo",
    "ImportProbe",
    "console_scripts",
    "environment_info",
    "installed_version",
    "probe_import",
    "python_version_string",
    "redact_text",
]
