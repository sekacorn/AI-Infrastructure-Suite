"""Shared fixtures.

Tests never require the seven ecosystem projects to be checked out, installed, or
reachable. Component presence, versions, imports, and console scripts are all
faked, so the suite is deterministic on a bare interpreter and offline.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from ai_infrastructure_suite._inspection import ImportProbe
from ai_infrastructure_suite.components import Component, EcosystemManifest, ExtraGroup

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
