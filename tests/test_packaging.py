"""Packaging coherence: metadata, resources, and manifest/pyproject agreement."""

from __future__ import annotations

import importlib
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from ai_infrastructure_suite import __version__
from ai_infrastructure_suite.components import ecosystem_components, load_manifest

DISTRIBUTION = "ai-infrastructure-suite"
_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# Names that must never appear in packaging metadata or the manifest.
FORBIDDEN_METADATA_PATTERNS = ("@gmail", "@outlook", "@hotmail", "@yahoo", "Co-authored-by")


def _pyproject() -> dict[str, object]:
    if not _PYPROJECT.is_file():
        pytest.skip("pyproject.toml is not present (running against an installed wheel)")
    with _PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _requirements() -> list[str]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    base = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    assert isinstance(optional, dict)
    for group in optional.values():
        base.extend(group)
    return base


class TestDistributionMetadata:
    """What ends up in the wheel and sdist."""

    def test_distribution_is_installed(self) -> None:
        assert metadata.version(DISTRIBUTION) == __version__

    def test_version_is_the_expected_alpha(self) -> None:
        assert __version__ == "0.1.0a1"

    def test_author_is_the_public_identity(self) -> None:
        raw = metadata.metadata(DISTRIBUTION)
        author = raw.get("Author") or ""
        assert "sekacorn" in author

    def test_license_is_apache_2(self) -> None:
        raw = metadata.metadata(DISTRIBUTION)
        expression = raw.get("License-Expression") or raw.get("License") or ""
        assert "Apache-2.0" in expression

    def test_requires_python_matches_manifest(self) -> None:
        declared = metadata.metadata(DISTRIBUTION).get("Requires-Python") or ""
        assert "3.11" in declared
        assert "3.14" in declared
        assert load_manifest().python_requires == ">=3.11,<3.14"

    def test_console_script_is_registered(self) -> None:
        names = {
            item.name
            for item in metadata.distribution(DISTRIBUTION).entry_points
            if item.group == "console_scripts"
        }
        assert "ai-suite" in names

    def test_metadata_carries_no_personal_identifiers(self) -> None:
        rendered = str(metadata.metadata(DISTRIBUTION))
        for pattern in FORBIDDEN_METADATA_PATTERNS:
            assert pattern not in rendered

    def test_classifiers_cover_supported_pythons(self) -> None:
        rendered = str(metadata.metadata(DISTRIBUTION))
        for version in ("3.11", "3.12", "3.13"):
            assert f"Programming Language :: Python :: {version}" in rendered


class TestPackagedResources:
    """The manifest must survive packaging as a real resource."""

    def test_manifest_resource_is_importable_from_the_package(self) -> None:
        assert len(ecosystem_components()) == 7

    def test_manifest_loads_without_the_source_tree(self) -> None:
        # importlib.resources reads the installed package, not a relative path.
        assert load_manifest().suite_distribution == DISTRIBUTION


class TestDeclaredApiSymbols:
    """`api_symbols` must hold for the versions the ranges actually admit.

    Declaring a symbol that only exists in a component's unreleased source tree
    makes the doctor report a correct installation as ``incompatible``. Whichever
    components are present in this environment get checked; the rest are skipped,
    so the suite stays runnable without all seven installed.
    """

    def test_declared_symbols_exist_on_installed_components(self) -> None:
        checked = 0
        for component in ecosystem_components():
            try:
                module = importlib.import_module(component.import_package)
            except ImportError:
                continue
            checked += 1
            missing = [name for name in component.api_symbols if not hasattr(module, name)]
            assert not missing, (
                f"{component.distribution} "
                f"{getattr(module, '__version__', 'unknown')} does not expose "
                f"{missing}; api_symbols must hold across the whole supported range"
            )
        assert checked, "expected at least one ecosystem component to be installed"

    def test_default_components_are_importable(self) -> None:
        for component in ecosystem_components():
            if not component.installed_by_default:
                continue
            importlib.import_module(component.import_package)


class TestManifestAgreesWithPyproject:
    """The manifest and the dependency declarations must not drift apart."""

    def test_every_component_range_matches_a_declared_requirement(self) -> None:
        declared = _requirements()
        for component in ecosystem_components():
            expected = f"{component.distribution}{component.version_specifier}"
            matches = [item for item in declared if item.replace(" ", "") == expected]
            assert matches, (
                f"manifest range for {component.distribution} "
                f"({component.version_specifier}) has no matching requirement"
            )

    def test_default_components_are_base_dependencies(self) -> None:
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        base = "".join(str(item) for item in project.get("dependencies", []))
        for component in ecosystem_components():
            if component.installed_by_default:
                assert component.distribution in base

    def test_optional_components_are_not_base_dependencies(self) -> None:
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        base = "".join(str(item) for item in project.get("dependencies", []))
        for component in ecosystem_components():
            if not component.installed_by_default:
                assert component.distribution not in base

    def test_every_manifest_extra_exists_in_pyproject(self) -> None:
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        optional = project.get("optional-dependencies", {})
        assert isinstance(optional, dict)
        for group in load_manifest().extras:
            assert group.name in optional

    def test_full_extra_covers_all_seven(self) -> None:
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        optional = project.get("optional-dependencies", {})
        assert isinstance(optional, dict)
        full = "".join(str(item) for item in optional.get("full", []))
        for component in ecosystem_components():
            assert component.distribution in full

    def test_no_heavy_infrastructure_sdk_in_base_dependencies(self) -> None:
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        base = "".join(str(item) for item in project.get("dependencies", [])).lower()
        for unwanted in (
            "anthropic",
            "openai",
            "boto3",
            "psycopg",
            "pgvector",
            "opentelemetry",
            "fastapi",
            "uvicorn",
            "docker",
        ):
            assert unwanted not in base, f"{unwanted} must not be a default dependency"
