"""Manifest loading, validation, and determinism."""

from __future__ import annotations

import json

import pytest
from packaging.version import Version

from ai_infrastructure_suite.components import (
    Component,
    ExtraGroup,
    available_extras,
    component_ids,
    ecosystem_components,
    load_manifest,
    manifest_text,
)
from ai_infrastructure_suite.errors import ComponentNotFoundError, ManifestError

EXPECTED_IDS = (
    "agentforge",
    "agentpolicypack",
    "aiauditlog",
    "aimeter",
    "modelswapbench",
    "openontologylite",
    "privateaistack",
)


class TestPackagedManifest:
    """The manifest that ships inside the wheel."""

    def test_loads_from_package_resource(self) -> None:
        raw = manifest_text()
        assert json.loads(raw)["manifest_schema_version"] == "1.0"

    def test_describes_exactly_seven_components(self) -> None:
        assert len(ecosystem_components()) == 7

    def test_component_ids_are_expected_and_sorted(self) -> None:
        assert component_ids() == EXPECTED_IDS
        assert list(component_ids()) == sorted(component_ids())

    def test_ordering_is_deterministic_across_calls(self) -> None:
        load_manifest.cache_clear()
        first = [item.id for item in ecosystem_components()]
        load_manifest.cache_clear()
        second = [item.id for item in ecosystem_components()]
        assert first == second

    def test_is_cached(self) -> None:
        assert load_manifest() is load_manifest()

    def test_every_version_range_parses(self) -> None:
        for component in ecosystem_components():
            assert len(component.specifier_set) == 2

    def test_every_range_admits_its_latest_published_version(self) -> None:
        # A range that excludes what is actually on PyPI makes the suite uninstallable.
        for component in ecosystem_components():
            assert component.specifier_set.contains(
                component.latest_published_version, prereleases=True
            ), f"{component.distribution} range excludes its published version"

    def test_minimum_never_exceeds_latest_published(self) -> None:
        # The two diverge once a component publishes a release inside the range:
        # the minimum is what the suite supports, the latest is what exists. The
        # minimum must never overtake it, which would pin to something unreleased.
        for component in ecosystem_components():
            assert Version(component.minimum_version) <= Version(
                component.latest_published_version
            ), f"{component.distribution} pins above its latest published release"

    def test_range_admits_the_declared_minimum(self) -> None:
        for component in ecosystem_components():
            assert component.specifier_set.contains(component.minimum_version, prereleases=True)

    def test_five_components_install_by_default(self) -> None:
        default = [item.id for item in ecosystem_components() if item.installed_by_default]
        assert default == [
            "agentforge",
            "agentpolicypack",
            "aiauditlog",
            "aimeter",
            "openontologylite",
        ]

    def test_heavy_components_are_not_installed_by_default(self) -> None:
        for component in ecosystem_components():
            if component.requires_heavy_infrastructure:
                assert not component.installed_by_default

    def test_lookup_by_id(self) -> None:
        assert load_manifest().component("agentforge").distribution == "agentforge-oss"

    def test_unknown_id_raises(self) -> None:
        with pytest.raises(ComponentNotFoundError, match="nope"):
            load_manifest().component("nope")

    def test_extras_are_sorted_and_reference_known_components(self) -> None:
        extras = available_extras()
        assert [item.name for item in extras] == sorted(item.name for item in extras)
        known = set(component_ids())
        for group in extras:
            assert set(group.components) <= known

    def test_components_for_extra(self) -> None:
        found = load_manifest().components_for_extra("benchmarking")
        assert [item.id for item in found] == ["modelswapbench"]

    def test_components_for_unknown_extra_is_empty(self) -> None:
        assert load_manifest().components_for_extra("nope") == ()

    def test_to_dict_is_json_serialisable_and_stable(self) -> None:
        payload = load_manifest().to_dict()
        assert json.dumps(payload, sort_keys=True) == json.dumps(
            load_manifest().to_dict(), sort_keys=True
        )


class TestComponentModel:
    """Construction and validation of individual entries."""

    def test_from_mapping_round_trips(self, component_factory: object) -> None:
        original = ecosystem_components()[0]
        assert Component.from_mapping(original.to_dict()) == original

    def test_missing_field_raises(self) -> None:
        payload = ecosystem_components()[0].to_dict()
        del payload["purpose"]
        with pytest.raises(ManifestError, match="missing required field 'purpose'"):
            Component.from_mapping(payload)

    def test_wrong_type_raises(self) -> None:
        payload = ecosystem_components()[0].to_dict()
        payload["offline_support"] = "yes"
        with pytest.raises(ManifestError, match="must be bool"):
            Component.from_mapping(payload)

    def test_non_string_list_item_raises(self) -> None:
        payload = ecosystem_components()[0].to_dict()
        payload["extras"] = ["full", 7]
        with pytest.raises(ManifestError, match="only strings"):
            Component.from_mapping(payload)

    def test_non_string_cli_raises(self) -> None:
        payload = ecosystem_components()[0].to_dict()
        payload["cli"] = 5
        with pytest.raises(ManifestError, match="'cli' must be a string or null"):
            Component.from_mapping(payload)

    def test_null_cli_is_allowed(self) -> None:
        payload = ecosystem_components()[0].to_dict()
        payload["cli"] = None
        assert Component.from_mapping(payload).cli is None

    def test_unparseable_specifier_raises(self, component_factory: object) -> None:
        broken = Component.from_mapping(
            {**ecosystem_components()[0].to_dict(), "version_specifier": ">>> nope"}
        )
        with pytest.raises(ManifestError, match="invalid version specifier"):
            _ = broken.specifier_set

    def test_extra_group_round_trips(self) -> None:
        group = ExtraGroup(name="x", purpose="p", components=("a",))
        assert ExtraGroup.from_mapping(group.to_dict()) == group
