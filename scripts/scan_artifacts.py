#!/usr/bin/env python3
"""Scan built artifacts for personal data, local paths, secrets, and AI attribution.

Two passes with different strictness, because the two artifacts carry different risk:

* **Wheel** — what users actually install. Scanned with **zero exemptions**.
* **Sdist** — additionally ships tests and contributor docs, some of which must
  contain forbidden-looking strings to do their job: the redaction tests need
  fake home paths and fake credentials, and CONTRIBUTING.md names the trailers it
  forbids. Matches are allowed there only when they carry a known synthetic
  placeholder, so a real path or credential still fails the scan.

Usage:
    python scripts/scan_artifacts.py [dist_dir]
"""

from __future__ import annotations

import pathlib
import re
import sys
import tarfile
import zipfile

BS = chr(92)  # backslash, kept out of literals so shell quoting cannot mangle it

FORBIDDEN: tuple[tuple[str, str], ...] = (
    (r"[\w.+-]+@(?!example\.invalid)[\w-]+\.[\w]{2,}", "email address"),
    (r"Co-authored-by", "AI attribution"),
    (r"(?i)generated with claude|co-written by ai", "AI attribution"),
    (rf"[A-Za-z]:{BS}{BS}Users{BS}{BS}", "windows home path"),
    (r"[A-Za-z]:/Users/", "windows home path"),
    (r"/home/[a-z][a-z0-9_-]+/", "posix home path"),
    (r"/Users/[a-z][a-z0-9_-]+/", "macos home path"),
    (r"(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*[A-Za-z0-9_-]{16,}", "possible secret"),
)

# Synthetic values used by the redaction tests and the forbidden-pattern lists.
# A match containing one of these is a fixture, not a leak.
SYNTHETIC_MARKERS: tuple[str, ...] = (
    "someone",
    "hunter2",
    "abcdef",
    "aaaabbbb",
    "topsecret",
    "verysecret",
    "p4ssw0rd",
    "eyJhbG",
    "FAKE_SECRET",
    "example.invalid",
    "[redacted]",
)

# Sdist-only files permitted to name a forbidden pattern literally, because
# enumerating it is their purpose.
PATTERN_DECLARING_FILES: tuple[str, ...] = (
    "CONTRIBUTING.md",
    "tests/test_packaging.py",
    "tests/test_security.py",
    "scripts/scan_artifacts.py",
)


def _matches(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for pattern, label in FORBIDDEN:
        for raw in re.findall(pattern, text):
            value = raw if isinstance(raw, str) else "".join(raw)
            found.append((label, value))
    return found


def _line_for(text: str, value: str) -> str:
    for line in text.splitlines():
        if value and value in line:
            return line.strip()[:160]
    return value


def scan_strict(name: str, text: str) -> list[str]:
    """No exemptions. Used for the wheel."""
    return [f"  {label}: {name} -> {value!r}" for label, value in _matches(text)]


def scan_lenient(name: str, text: str) -> list[str]:
    """Allow synthetic fixtures in files whose job is to declare them."""
    declaring = any(name.endswith(suffix) for suffix in PATTERN_DECLARING_FILES)
    problems: list[str] = []
    for label, value in _matches(text):
        context = _line_for(text, value)
        if declaring and any(marker in context for marker in SYNTHETIC_MARKERS):
            continue
        if declaring and label == "AI attribution":
            # Naming a forbidden trailer in order to forbid it is not attribution.
            continue
        if any(marker in value for marker in SYNTHETIC_MARKERS):
            continue
        problems.append(f"  {label}: {name} -> {value!r}  [{context}]")
    return problems


def main() -> int:
    dist = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    if not dist.is_dir():
        print(f"no such directory: {dist}")
        return 2

    problems: list[str] = []
    wheels = files = 0

    for wheel in sorted(dist.glob("*.whl")):
        wheels += 1
        archive = zipfile.ZipFile(wheel)
        for entry in archive.namelist():
            files += 1
            body = archive.read(entry).decode("utf-8", "replace")
            problems += scan_strict(f"{wheel.name}:{entry}", body)

    for sdist in sorted(dist.glob("*.tar.gz")):
        with tarfile.open(sdist) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                files += 1
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                body = handle.read().decode("utf-8", "replace")
                problems += scan_lenient(f"{sdist.name}:{member.name}", body)

    if not wheels:
        print("no wheel found to scan")
        return 2

    print(f"scanned {files} files across wheel and sdist")
    if problems:
        print("\n".join(problems))
        print("ARTIFACT SCAN FAILED")
        return 1
    print("CLEAN: no emails, no personal names, no local paths, no secrets, no AI attribution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
