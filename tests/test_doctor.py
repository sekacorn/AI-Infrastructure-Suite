"""Diagnostic classification: every status category and diagnostic code."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from ai_infrastructure_suite._inspection import ImportProbe
from ai_infrastructure_suite.components import Component, EcosystemManifest
from ai_infrastructure_suite.doctor import (
    CheckStatus,
    DiagnosticCode,
    inspect_installation,
    python_supported,
)


def codes(component: Any) -> set[str]:
    """Return the diagnostic codes attached to a component status."""
    return {item.code.value for item in component.diagnostics}


class TestStatusCategories:
    """One test per documented status value."""

    def test_ok(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "1.2.0"},
            scripts={"example-dist": ("example-cli",)},
        )
        report = inspect_installation(manifest=manifest_factory(component_factory()))
        component = report.components[0]
        assert component.status is CheckStatus.OK
        assert component.install_status is CheckStatus.OK
        assert component.import_status is CheckStatus.OK
        assert component.api_status is CheckStatus.OK
        assert component.cli_status is CheckStatus.OK
        assert report.ok is True
        assert report.status is CheckStatus.OK

    def test_missing(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={})
        report = inspect_installation(
            manifest=manifest_factory(component_factory(installed_by_default=True))
        )
        component = report.components[0]
        assert component.status is CheckStatus.MISSING
        assert component.installed_version is None
        assert DiagnosticCode.COMPONENT_MISSING.value in codes(component)
        assert report.ok is False
        assert report.status is CheckStatus.MISSING

    def test_optional(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={})
        report = inspect_installation(
            manifest=manifest_factory(
                component_factory(installed_by_default=False, extras=("benchmarking",))
            )
        )
        component = report.components[0]
        assert component.status is CheckStatus.OPTIONAL
        assert component.is_healthy is True
        assert DiagnosticCode.OPTIONAL_COMPONENT_ABSENT.value in codes(component)
        # An absent optional component must not make the whole report fail.
        assert report.ok is True
        assert report.status is CheckStatus.OK

    def test_optional_action_points_at_its_extra(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={})
        report = inspect_installation(
            manifest=manifest_factory(
                component_factory(installed_by_default=False, extras=("full", "local"))
            )
        )
        actions = [item.action for item in report.components[0].diagnostics]
        assert any("[local]" in action for action in actions)

    def test_incompatible(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "3.0.0"},
            scripts={"example-dist": ("example-cli",)},
        )
        report = inspect_installation(manifest=manifest_factory(component_factory()))
        component = report.components[0]
        assert component.status is CheckStatus.INCOMPATIBLE
        assert component.install_status is CheckStatus.INCOMPATIBLE
        assert DiagnosticCode.VERSION_INCOMPATIBLE.value in codes(component)
        assert report.ok is False

    def test_import_error(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "1.0.0"},
            scripts={"example-dist": ("example-cli",)},
            probes={
                "example_pkg": ImportProbe(
                    imported=False,
                    error="ModuleNotFoundError: No module named 'thing'",
                    missing_symbols=("thing",),
                    module_version=None,
                )
            },
        )
        report = inspect_installation(manifest=manifest_factory(component_factory()))
        component = report.components[0]
        assert component.status is CheckStatus.IMPORT_ERROR
        assert component.import_status is CheckStatus.IMPORT_ERROR
        assert DiagnosticCode.IMPORT_FAILED.value in codes(component)
        assert report.ok is False

    def test_not_checked_when_imports_disabled(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "1.0.0"},
            scripts={"example-dist": ("example-cli",)},
        )
        report = inspect_installation(
            check_imports=False, manifest=manifest_factory(component_factory())
        )
        component = report.components[0]
        assert component.import_status is CheckStatus.NOT_CHECKED
        assert component.api_status is CheckStatus.NOT_CHECKED
        # Skipping a check must not be reported as a failure.
        assert component.status is CheckStatus.OK
        assert report.ok is True
        assert report.imports_checked is False
        assert DiagnosticCode.IMPORT_CHECKS_SKIPPED.value in {
            item.code.value for item in report.diagnostics
        }

    def test_unknown_for_unparseable_installed_version(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "not-a-version"},
            scripts={"example-dist": ("example-cli",)},
        )
        report = inspect_installation(manifest=manifest_factory(component_factory()))
        component = report.components[0]
        assert component.install_status is CheckStatus.UNKNOWN
        assert DiagnosticCode.VERSION_INCOMPATIBLE.value in codes(component)

    def test_unknown_for_unparseable_manifest_range(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={"example-dist": "1.0.0"})
        report = inspect_installation(
            manifest=manifest_factory(component_factory(version_specifier=">>> broken"))
        )
        component = report.components[0]
        assert component.status is CheckStatus.UNKNOWN
        assert DiagnosticCode.MANIFEST_RANGE_UNPARSEABLE.value in codes(component)


class TestSecondaryChecks:
    """Checks that inform without dominating the overall status."""

    def test_missing_console_script(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={"example-dist": "1.0.0"}, scripts={"example-dist": ()})
        component = inspect_installation(manifest=manifest_factory(component_factory())).components[
            0
        ]
        assert component.cli_status is CheckStatus.INCOMPATIBLE
        assert DiagnosticCode.CLI_ENTRY_POINT_MISSING.value in codes(component)
        # CLI packaging quirks are reported, but do not fail the component.
        assert component.status is CheckStatus.OK

    def test_cli_not_checked_when_component_declares_none(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={"example-dist": "1.0.0"})
        component = inspect_installation(
            manifest=manifest_factory(component_factory(cli=None))
        ).components[0]
        assert component.cli_status is CheckStatus.NOT_CHECKED

    def test_missing_api_symbols(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "1.0.0"},
            scripts={"example-dist": ("example-cli",)},
            probes={
                "example_pkg": ImportProbe(
                    imported=True,
                    error=None,
                    missing_symbols=("thing",),
                    module_version=None,
                )
            },
        )
        component = inspect_installation(manifest=manifest_factory(component_factory())).components[
            0
        ]
        assert component.api_status is CheckStatus.INCOMPATIBLE
        assert component.status is CheckStatus.INCOMPATIBLE
        assert DiagnosticCode.API_SYMBOLS_MISSING.value in codes(component)

    def test_version_metadata_mismatch(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "1.0.0"},
            scripts={"example-dist": ("example-cli",)},
            probes={
                "example_pkg": ImportProbe(
                    imported=True,
                    error=None,
                    missing_symbols=(),
                    module_version="1.4.0",
                )
            },
        )
        component = inspect_installation(manifest=manifest_factory(component_factory())).components[
            0
        ]
        assert DiagnosticCode.VERSION_METADATA_MISMATCH.value in codes(component)
        # Informational only.
        assert component.status is CheckStatus.OK

    def test_heavy_infrastructure_is_informational(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "1.0.0"},
            scripts={"example-dist": ("example-cli",)},
        )
        component = inspect_installation(
            manifest=manifest_factory(
                component_factory(heavy_infrastructure=("Ollama", "PostgreSQL"))
            )
        ).components[0]
        assert DiagnosticCode.HEAVY_INFRASTRUCTURE_REQUIRED.value in codes(component)
        assert component.status is CheckStatus.OK

    def test_prerelease_versions_satisfy_alpha_ranges(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        # The ecosystem ships alphas; a range must accept them.
        fake_environment(
            versions={"example-dist": "0.1.0a6"},
            scripts={"example-dist": ("example-cli",)},
        )
        component = inspect_installation(
            manifest=manifest_factory(component_factory(version_specifier=">=0.1.0a6,<0.2.0"))
        ).components[0]
        assert component.install_status is CheckStatus.OK


class TestPythonSupport:
    """Interpreter range handling."""

    def test_current_interpreter_is_supported(self) -> None:
        assert python_supported() is True

    def test_unsupported_interpreter_flagged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        monkeypatch.setattr(
            "ai_infrastructure_suite.doctor.python_version_string", lambda: "3.10.4"
        )
        fake_environment(versions={"example-dist": "1.0.0"})
        report = inspect_installation(manifest=manifest_factory(component_factory()))
        assert report.python_supported is False
        assert report.ok is False
        assert report.status is CheckStatus.INCOMPATIBLE
        assert DiagnosticCode.PYTHON_UNSUPPORTED.value in {
            item.code.value for item in report.diagnostics
        }

    def test_future_interpreter_flagged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        manifest_factory: Callable[..., EcosystemManifest],
    ) -> None:
        monkeypatch.setattr(
            "ai_infrastructure_suite.doctor.python_version_string", lambda: "3.14.0"
        )
        assert python_supported(manifest_factory()) is False


class TestReportShape:
    """The serialised report contract."""

    def test_summary_covers_every_status(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={"example-dist": "1.0.0"})
        summary = inspect_installation(manifest=manifest_factory(component_factory())).summary()
        assert set(summary) == {status.value for status in CheckStatus}

    def test_to_dict_is_json_serialisable(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={"example-dist": "1.0.0"})
        payload = inspect_installation(manifest=manifest_factory(component_factory())).to_dict()
        assert json.loads(json.dumps(payload)) == payload
        assert payload["schema_version"] == "1.0"

    def test_components_follow_manifest_order(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={})
        manifest = manifest_factory(
            component_factory(id="zulu", distribution="z"),
            component_factory(id="alpha", distribution="a"),
        )
        report = inspect_installation(manifest=manifest)
        assert [item.component_id for item in report.components] == ["alpha", "zulu"]

    def test_runs_without_network(
        self,
        no_network: None,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={"example-dist": "1.0.0"})
        report = inspect_installation(manifest=manifest_factory(component_factory()))
        assert report.ok is True

    def test_real_environment_inspection_succeeds(self, no_network: None) -> None:
        # Against the packaged manifest and whatever is actually installed.
        report = inspect_installation(check_imports=False)
        assert len(report.components) == 7
        assert report.schema_version == "1.0"
