"""Ecosystem component manifest: typed models and deterministic loading.

The manifest is a packaged JSON resource, not code, so it can be read by tools
that never import this package. Everything here is pure data handling: no
component is imported and no distribution is inspected at this layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any, Final

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from ai_infrastructure_suite.errors import ComponentNotFoundError, ManifestError

MANIFEST_RESOURCE: Final = "ecosystem-manifest.json"
_DATA_PACKAGE: Final = "ai_infrastructure_suite.data"


def _require(mapping: dict[str, Any], key: str, kind: type[Any], context: str) -> Any:
    """Fetch ``key`` from ``mapping`` enforcing presence and type."""
    if key not in mapping:
        raise ManifestError(f"{context}: missing required field {key!r}")
    value = mapping[key]
    if not isinstance(value, kind):
        actual = type(value).__name__
        raise ManifestError(f"{context}: field {key!r} must be {kind.__name__}, got {actual}")
    return value


def _string_tuple(mapping: dict[str, Any], key: str, context: str) -> tuple[str, ...]:
    """Read a list-of-strings field as an immutable tuple."""
    raw = _require(mapping, key, list, context)
    for item in raw:
        if not isinstance(item, str):
            raise ManifestError(f"{context}: field {key!r} must contain only strings")
    return tuple(raw)


@dataclass(frozen=True, slots=True)
class Component:
    """One Linux of AI ecosystem component.

    Attributes describe the component statically. Nothing here reflects what is
    installed in the current environment; see :mod:`ai_infrastructure_suite.doctor`
    for that.
    """

    id: str
    name: str
    distribution: str
    import_package: str
    cli: str | None
    layer: str
    installed_by_default: bool
    extras: tuple[str, ...]
    purpose: str
    minimum_version: str
    maximum_version_exclusive: str
    version_specifier: str
    latest_published_version: str
    repository_url: str
    pypi_url: str
    api_symbols: tuple[str, ...]
    integration_status: str
    offline_support: bool
    heavy_infrastructure: tuple[str, ...]
    optional_infrastructure: tuple[str, ...]
    notes: str

    @property
    def specifier_set(self) -> SpecifierSet:
        """Parsed version range for this component.

        Raises:
            ManifestError: if the manifest carries an unparseable specifier.
        """
        try:
            return SpecifierSet(self.version_specifier)
        except InvalidSpecifier as exc:
            raise ManifestError(
                f"component {self.id!r}: invalid version specifier {self.version_specifier!r}"
            ) from exc

    @property
    def requires_heavy_infrastructure(self) -> bool:
        """True when the component expects external services to be useful."""
        return bool(self.heavy_infrastructure)

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "id": self.id,
            "name": self.name,
            "distribution": self.distribution,
            "import_package": self.import_package,
            "cli": self.cli,
            "layer": self.layer,
            "installed_by_default": self.installed_by_default,
            "extras": list(self.extras),
            "purpose": self.purpose,
            "minimum_version": self.minimum_version,
            "maximum_version_exclusive": self.maximum_version_exclusive,
            "version_specifier": self.version_specifier,
            "latest_published_version": self.latest_published_version,
            "repository_url": self.repository_url,
            "pypi_url": self.pypi_url,
            "api_symbols": list(self.api_symbols),
            "integration_status": self.integration_status,
            "offline_support": self.offline_support,
            "heavy_infrastructure": list(self.heavy_infrastructure),
            "optional_infrastructure": list(self.optional_infrastructure),
            "notes": self.notes,
        }

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> Component:
        """Build a component from one manifest entry, validating as we go."""
        identifier = _require(raw, "id", str, "component entry")
        context = f"component {identifier!r}"
        cli_raw = raw.get("cli")
        if cli_raw is not None and not isinstance(cli_raw, str):
            raise ManifestError(f"{context}: field 'cli' must be a string or null")
        return cls(
            id=identifier,
            name=_require(raw, "name", str, context),
            distribution=_require(raw, "distribution", str, context),
            import_package=_require(raw, "import_package", str, context),
            cli=cli_raw,
            layer=_require(raw, "layer", str, context),
            installed_by_default=_require(raw, "installed_by_default", bool, context),
            extras=_string_tuple(raw, "extras", context),
            purpose=_require(raw, "purpose", str, context),
            minimum_version=_require(raw, "minimum_version", str, context),
            maximum_version_exclusive=_require(raw, "maximum_version_exclusive", str, context),
            version_specifier=_require(raw, "version_specifier", str, context),
            latest_published_version=_require(raw, "latest_published_version", str, context),
            repository_url=_require(raw, "repository_url", str, context),
            pypi_url=_require(raw, "pypi_url", str, context),
            api_symbols=_string_tuple(raw, "api_symbols", context),
            integration_status=_require(raw, "integration_status", str, context),
            offline_support=_require(raw, "offline_support", bool, context),
            heavy_infrastructure=_string_tuple(raw, "heavy_infrastructure", context),
            optional_infrastructure=_string_tuple(raw, "optional_infrastructure", context),
            notes=_require(raw, "notes", str, context),
        )


@dataclass(frozen=True, slots=True)
class ExtraGroup:
    """An installable optional dependency group."""

    name: str
    purpose: str
    components: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "name": self.name,
            "purpose": self.purpose,
            "components": list(self.components),
        }

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> ExtraGroup:
        """Build an extra group from one manifest entry."""
        name = _require(raw, "name", str, "extra entry")
        context = f"extra {name!r}"
        return cls(
            name=name,
            purpose=_require(raw, "purpose", str, context),
            components=_string_tuple(raw, "components", context),
        )


@dataclass(frozen=True, slots=True)
class EcosystemManifest:
    """The full, deterministically ordered ecosystem description."""

    manifest_schema_version: str
    ecosystem: str
    suite_distribution: str
    suite_import_package: str
    suite_cli: str
    python_requires: str
    version_sources_verified_on: str
    affiliation_notice: str
    components: tuple[Component, ...]
    extras: tuple[ExtraGroup, ...]

    def component(self, component_id: str) -> Component:
        """Look up one component by identifier.

        Raises:
            ComponentNotFoundError: if no component carries that id.
        """
        for candidate in self.components:
            if candidate.id == component_id:
                return candidate
        known = ", ".join(item.id for item in self.components)
        raise ComponentNotFoundError(f"unknown component {component_id!r}; known ids: {known}")

    def components_for_extra(self, extra: str) -> tuple[Component, ...]:
        """Return the components an extra installs, in manifest order."""
        for group in self.extras:
            if group.name == extra:
                return tuple(item for item in self.components if item.id in group.components)
        return ()

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "manifest_schema_version": self.manifest_schema_version,
            "ecosystem": self.ecosystem,
            "suite_distribution": self.suite_distribution,
            "suite_import_package": self.suite_import_package,
            "suite_cli": self.suite_cli,
            "python_requires": self.python_requires,
            "version_sources_verified_on": self.version_sources_verified_on,
            "affiliation_notice": self.affiliation_notice,
            "extras": [group.to_dict() for group in self.extras],
            "components": [item.to_dict() for item in self.components],
        }


def _manifest_resource() -> Traversable:
    """Locate the packaged manifest resource."""
    return files(_DATA_PACKAGE).joinpath(MANIFEST_RESOURCE)


def manifest_text() -> str:
    """Return the raw packaged manifest JSON.

    Raises:
        ManifestError: if the resource is missing or not readable as UTF-8.
    """
    try:
        return _manifest_resource().read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ManifestError(f"packaged manifest {MANIFEST_RESOURCE!r} was not found") from exc
    except UnicodeDecodeError as exc:
        raise ManifestError(f"packaged manifest {MANIFEST_RESOURCE!r} is not valid UTF-8") from exc


@lru_cache(maxsize=1)
def load_manifest() -> EcosystemManifest:
    """Load, validate, and cache the packaged ecosystem manifest.

    Components and extras are sorted by identifier so every consumer sees the
    same order regardless of how the JSON file happens to be written.

    Raises:
        ManifestError: if the manifest is missing, malformed, or inconsistent.
    """
    try:
        raw: Any = json.loads(manifest_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f"packaged manifest is not valid JSON: {exc.msg}") from exc

    if not isinstance(raw, dict):
        raise ManifestError("packaged manifest must be a JSON object")

    context = "manifest"
    component_entries = _require(raw, "components", list, context)
    extra_entries = _require(raw, "extras", list, context)

    components: list[Component] = []
    seen: set[str] = set()
    for entry in component_entries:
        if not isinstance(entry, dict):
            raise ManifestError("manifest: every component entry must be a JSON object")
        component = Component.from_mapping(entry)
        if component.id in seen:
            raise ManifestError(f"manifest: duplicate component id {component.id!r}")
        seen.add(component.id)
        # Fail fast on an unparseable range rather than at diagnosis time.
        _ = component.specifier_set
        components.append(component)

    extras: list[ExtraGroup] = []
    extra_names: set[str] = set()
    for entry in extra_entries:
        if not isinstance(entry, dict):
            raise ManifestError("manifest: every extra entry must be a JSON object")
        group = ExtraGroup.from_mapping(entry)
        if group.name in extra_names:
            raise ManifestError(f"manifest: duplicate extra name {group.name!r}")
        extra_names.add(group.name)
        for referenced in group.components:
            if referenced not in seen:
                raise ManifestError(
                    f"extra {group.name!r} references unknown component {referenced!r}"
                )
        extras.append(group)

    return EcosystemManifest(
        manifest_schema_version=_require(raw, "manifest_schema_version", str, context),
        ecosystem=_require(raw, "ecosystem", str, context),
        suite_distribution=_require(raw, "suite_distribution", str, context),
        suite_import_package=_require(raw, "suite_import_package", str, context),
        suite_cli=_require(raw, "suite_cli", str, context),
        python_requires=_require(raw, "python_requires", str, context),
        version_sources_verified_on=_require(raw, "version_sources_verified_on", str, context),
        affiliation_notice=_require(raw, "affiliation_notice", str, context),
        components=tuple(sorted(components, key=lambda item: item.id)),
        extras=tuple(sorted(extras, key=lambda item: item.name)),
    )


def ecosystem_components() -> tuple[Component, ...]:
    """Return every known ecosystem component in deterministic id order."""
    return load_manifest().components


def component_ids() -> tuple[str, ...]:
    """Return every known component identifier in deterministic order."""
    return tuple(item.id for item in ecosystem_components())


def available_extras() -> tuple[ExtraGroup, ...]:
    """Return every documented optional dependency group, sorted by name."""
    return load_manifest().extras


__all__ = [
    "MANIFEST_RESOURCE",
    "Component",
    "EcosystemManifest",
    "ExtraGroup",
    "available_extras",
    "component_ids",
    "ecosystem_components",
    "load_manifest",
    "manifest_text",
]
