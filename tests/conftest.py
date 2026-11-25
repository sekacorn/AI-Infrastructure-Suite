"""Shared fixtures.

Tests never require the seven ecosystem projects to be checked out, installed, or
reachable. Component presence, versions, imports, and console scripts are all
faked, so the suite is deterministic on a bare interpreter and offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from importlib.resources import files
from typing import Any

import jsonschema
import pytest

from ai_infrastructure_suite import exit_report as _exit_report
from ai_infrastructure_suite._inspection import ImportProbe
from ai_infrastructure_suite.components import Component, EcosystemManifest, ExtraGroup

# --- every exit report built anywhere in the test suite meets its schema -----
#
# ``build_exit_report`` is replaced here, before any test module imports it, by
# a wrapper that validates the report it returns against the packaged
# exit-report.schema.json and fails the test on any error. The CLI imports the
# function when the command runs, so it is covered too. 0.1.0b3 shipped a
# report shape its own schema rejected, because the one schema test looked at
# a single example; this net looks at every report the tests produce.
#
# The wrapper carries what it needs as attributes (``__wrapped__`` and
# ``schema_errors``), and wrapping is idempotent, so a second import of this
# file cannot stack wrappers. test_report_schema_net.py proves the net is
# installed and that it bites.


def _install_report_schema_net() -> None:
    current = _exit_report.build_exit_report
    if getattr(current, "schema_checked", False):
        return
    schema = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/exit-report.schema.json")
        .read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    def schema_errors(report: object) -> list[str]:
        """Every way ``report`` departs from the packaged exit-report schema."""
        found = sorted(validator.iter_errors(report), key=lambda error: list(error.path))
        return [
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message[:300]}"
            for error in found
        ]

    def checked(*args: Any, **kwargs: Any) -> dict[str, Any]:
        report: dict[str, Any] = checked.__wrapped__(*args, **kwargs)  # type: ignore[attr-defined]
        errors = schema_errors(report)
        if errors:
            raise AssertionError(
                "exit report does not match the packaged schema: " + "; ".join(errors[:10])
            )
        return report

    checked.__wrapped__ = current  # type: ignore[attr-defined]
    checked.schema_checked = True  # type: ignore[attr-defined]
    checked.schema_errors = schema_errors  # type: ignore[attr-defined]
    _exit_report.build_exit_report = checked  # type: ignore[assignment]


_install_report_schema_net()

_COMPONENT_DEFAULTS: dict[str, Any] = {
    "id": "example",
    "name": "Example",
    "distribution": "example-dist",
    "import_package": "example_pkg",
    "cli": "example-cli",
    "layer": "core",
    "installed_by_default": True,
    "extras": ("full",),
    "purpose": "An example component used only by the test suite.",
    "minimum_version": "1.0.0",
    "maximum_version_exclusive": "2.0.0",
    "version_specifier": ">=1.0.0,<2.0.0",
    "latest_published_version": "1.0.0",
    "repository_url": "https://example.invalid/repo",
    "pypi_url": "https://example.invalid/pypi",
    "api_symbols": ("thing",),
    "integration_status": "portable-contract",
    "offline_support": True,
    "heavy_infrastructure": (),
    "optional_infrastructure": (),
    "notes": "Fixture component.",
}


def make_component(**overrides: Any) -> Component:
    """Build a valid :class:`Component` with test-friendly defaults."""
    return Component(**{**_COMPONENT_DEFAULTS, **overrides})


def make_manifest(*components: Component, **overrides: Any) -> EcosystemManifest:
    """Build a manifest around the supplied components, sorted by id."""
    items = components or (make_component(),)
    defaults: dict[str, Any] = {
        "manifest_schema_version": "1.0",
        "ecosystem": "Test Ecosystem",
        "suite_distribution": "ai-infrastructure-suite",
        "suite_import_package": "ai_infrastructure_suite",
        "suite_cli": "ai-suite",
        "python_requires": ">=3.11,<3.14",
        "version_sources_verified_on": "2026-08-08",
        "affiliation_notice": "Independent project.",
        "components": tuple(sorted(items, key=lambda item: item.id)),
        "extras": (ExtraGroup(name="full", purpose="Everything.", components=()),),
    }
    return EcosystemManifest(**{**defaults, **overrides})


@pytest.fixture
def component_factory() -> Callable[..., Component]:
    """Expose :func:`make_component` as a fixture."""
    return make_component


@pytest.fixture
def manifest_factory() -> Callable[..., EcosystemManifest]:
    """Expose :func:`make_manifest` as a fixture."""
    return make_manifest


@pytest.fixture
def fake_environment(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Return a helper that fakes installed versions, scripts, and imports.

    Patches the names as bound inside :mod:`ai_infrastructure_suite.doctor`,
    which is where they are actually looked up.
    """

    def apply(
        *,
        versions: dict[str, str] | None = None,
        scripts: dict[str, tuple[str, ...]] | None = None,
        probes: dict[str, ImportProbe] | None = None,
    ) -> None:
        installed = versions or {}
        entry_points = scripts or {}
        imports = probes or {}

        monkeypatch.setattr(
            "ai_infrastructure_suite.doctor.installed_version",
            lambda distribution: installed.get(distribution),
        )
        monkeypatch.setattr(
            "ai_infrastructure_suite.doctor.console_scripts",
            lambda distribution: entry_points.get(distribution, ()),
        )
        monkeypatch.setattr(
            "ai_infrastructure_suite.doctor.probe_import",
            lambda package, symbols=(): imports.get(
                package,
                ImportProbe(imported=True, error=None, missing_symbols=(), module_version=None),
            ),
        )

    return apply


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make every socket operation fail loudly.

    Any code path that tries to reach the network under this fixture raises
    instead of silently succeeding on a connected machine.
    """
    import socket

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    yield
