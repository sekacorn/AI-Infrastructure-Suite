#!/usr/bin/env python3
"""Verify built-artifact metadata semantically.

Metadata fields are compared by meaning, not by literal string. Build backends
normalise several fields — hatchling emits ``Requires-Python: <3.14,>=3.11`` for
a declared ``>=3.11,<3.14`` — so a literal comparison fails on correct metadata.

Checks the wheel's METADATA and the sdist's PKG-INFO for:

* distribution name and version,
* author, license expression, and Python requirement,
* the ``ai-suite`` console-script entry point,
* the packaged manifest resource,
* the ``py.typed`` marker.

Usage:
    python scripts/check_metadata.py [dist_dir] [--version 0.1.0a1]
"""

from __future__ import annotations

import argparse
import pathlib
import tarfile
import zipfile
from email.parser import Parser

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

EXPECTED_NAME = "ai-infrastructure-suite"
EXPECTED_AUTHOR = "sekacorn"
EXPECTED_LICENSE = "Apache-2.0"
EXPECTED_PYTHON = SpecifierSet(">=3.11,<3.14")
EXPECTED_SCRIPT = "ai-suite"
EXPECTED_CLASSIFIER_PYTHONS = ("3.11", "3.12", "3.13")


def _fail(problems: list[str], message: str) -> None:
    problems.append(f"  {message}")


def check_core_metadata(raw: str, source: str, version: str, problems: list[str]) -> None:
    """Validate the RFC 822 core-metadata block shared by wheel and sdist."""
    meta = Parser().parsestr(raw)

    name = meta.get("Name") or ""
    if canonicalize_name(name) != canonicalize_name(EXPECTED_NAME):
        _fail(problems, f"{source}: Name is {name!r}, expected {EXPECTED_NAME!r}")

    got_version = meta.get("Version") or ""
    if Version(got_version) != Version(version):
        _fail(problems, f"{source}: Version is {got_version!r}, expected {version!r}")

    author = meta.get("Author") or ""
    if EXPECTED_AUTHOR not in author:
        _fail(problems, f"{source}: Author is {author!r}, expected to contain {EXPECTED_AUTHOR!r}")

    if meta.get("Author-email") or meta.get("Maintainer-email"):
        _fail(problems, f"{source}: an email field is present and must not be")

    license_value = meta.get("License-Expression") or meta.get("License") or ""
    if EXPECTED_LICENSE not in license_value:
        _fail(problems, f"{source}: license is {license_value!r}, expected {EXPECTED_LICENSE!r}")

    requires_python = meta.get("Requires-Python") or ""
    try:
        declared = SpecifierSet(requires_python)
    except Exception:  # reported, never raised
        _fail(problems, f"{source}: Requires-Python {requires_python!r} does not parse")
    else:
        # Compare by meaning: the field is normalised and reordered by the backend.
        if {str(s) for s in declared} != {str(s) for s in EXPECTED_PYTHON}:
            _fail(
                problems,
                f"{source}: Requires-Python is {requires_python!r}, "
                f"expected the equivalent of {EXPECTED_PYTHON}",
            )
        for supported in ("3.11.0", "3.12.0", "3.13.0"):
            if not declared.contains(supported):
                _fail(problems, f"{source}: Requires-Python excludes Python {supported}")
        for unsupported in ("3.10.0", "3.14.0"):
            if declared.contains(unsupported):
                _fail(problems, f"{source}: Requires-Python wrongly admits Python {unsupported}")

    classifiers = meta.get_all("Classifier") or []
    for wanted in EXPECTED_CLASSIFIER_PYTHONS:
        if f"Programming Language :: Python :: {wanted}" not in classifiers:
            _fail(problems, f"{source}: missing classifier for Python {wanted}")


def check_wheel(wheel: pathlib.Path, version: str, problems: list[str]) -> None:
    archive = zipfile.ZipFile(wheel)
    names = archive.namelist()

    metadata_entry = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
    if metadata_entry is None:
        _fail(problems, f"{wheel.name}: no METADATA found")
        return
    check_core_metadata(archive.read(metadata_entry).decode("utf-8"), wheel.name, version, problems)

    entry_points = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
    if entry_points is None:
        _fail(problems, f"{wheel.name}: no entry_points.txt found")
    else:
        body = archive.read(entry_points).decode("utf-8")
        if EXPECTED_SCRIPT not in body:
            _fail(problems, f"{wheel.name}: console script {EXPECTED_SCRIPT!r} not registered")

    if not any(n.endswith("data/ecosystem-manifest.json") for n in names):
        _fail(problems, f"{wheel.name}: ecosystem-manifest.json is missing")
    if not any(n.endswith("py.typed") for n in names):
        _fail(problems, f"{wheel.name}: py.typed marker is missing")

    for junk in (".pyc", ".pyo", "__pycache__", ".env", ".pem", ".key", ".db", ".log"):
        offenders = [n for n in names if junk in n]
        if offenders:
            _fail(problems, f"{wheel.name}: build debris present: {offenders[:3]}")


def check_sdist(sdist: pathlib.Path, version: str, problems: list[str]) -> None:
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        pkg_info = next((n for n in names if n.endswith("PKG-INFO")), None)
        if pkg_info is None:
            _fail(problems, f"{sdist.name}: no PKG-INFO found")
        else:
            handle = archive.extractfile(pkg_info)
            if handle is not None:
                check_core_metadata(handle.read().decode("utf-8"), sdist.name, version, problems)

        if not any(n.endswith("data/ecosystem-manifest.json") for n in names):
            _fail(problems, f"{sdist.name}: ecosystem-manifest.json is missing")
        if not any(n.endswith("pyproject.toml") for n in names):
            _fail(problems, f"{sdist.name}: pyproject.toml is missing")
        for wanted in ("README.md", "LICENSE", "CHANGELOG.md"):
            if not any(n.endswith(wanted) for n in names):
                _fail(problems, f"{sdist.name}: {wanted} is missing")
        for junk in ("__pycache__", ".pyc", "/dist/", "/.venv/", ".mypy_cache", ".ruff_cache"):
            offenders = [n for n in names if junk in n]
            if offenders:
                _fail(problems, f"{sdist.name}: build debris present: {offenders[:3]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", default="dist")
    parser.add_argument("--version", default="0.1.0a1")
    args = parser.parse_args()

    dist = pathlib.Path(args.dist)
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))

    if not wheels:
        print("no wheel found")
        return 2
    if not sdists:
        print("no sdist found")
        return 2

    problems: list[str] = []
    for wheel in wheels:
        check_wheel(wheel, args.version, problems)
    for sdist in sdists:
        check_sdist(sdist, args.version, problems)

    print(f"checked {len(wheels)} wheel(s) and {len(sdists)} sdist(s) for version {args.version}")
    if problems:
        print("\n".join(problems))
        print("METADATA CHECK FAILED")
        return 1
    print(
        f"OK: name={EXPECTED_NAME} version={args.version} author={EXPECTED_AUTHOR} "
        f"license={EXPECTED_LICENSE} python={EXPECTED_PYTHON} script={EXPECTED_SCRIPT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
