"""Layered compatibility reporting and the offline contract check."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from ai_infrastructure_suite.compatibility import (
    CONTRACT_FIXTURE_DIGEST,
    CompatibilityLayer,
    _contract_fixture,
    canonical_json,
    compatibility_report,
    digest_value,
    offline_contract_check,
)
from ai_infrastructure_suite.components import Component, EcosystemManifest
from ai_infrastructure_suite.doctor import CheckStatus


class TestCanonicalForm:
    """The serialisation conventions the ecosystem exchanges."""

    def test_canonical_json_sorts_keys(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_canonical_json_has_no_incidental_whitespace(self) -> None:
        assert " " not in canonical_json({"a": [1, 2], "b": {"c": 3}})

    def test_canonical_json_escapes_non_ascii(self) -> None:
        # ASCII-only output survives any console encoding.
        assert canonical_json({"k": "café"}) == '{"k":"caf\\u00e9"}'

    def test_canonical_json_is_order_independent(self) -> None:
        assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})

    def test_digest_matches_manual_sha256(self) -> None:
        value = {"x": 1}
        expected = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
        assert digest_value(value) == expected

    def test_fixture_digest_anchor_is_current(self) -> None:
        # Guards the anchored constant against silent fixture drift.
        assert digest_value(_contract_fixture()) == CONTRACT_FIXTURE_DIGEST


class TestOfflineContractCheck:
    """The deterministic, in-memory conformance check."""

    def test_all_checks_pass(self) -> None:
        checks = offline_contract_check()
        assert checks, "expected at least one contract check"
        assert all(check.passed for check in checks)

    def test_expected_checks_present(self) -> None:
        names = {check.name for check in offline_contract_check()}
        assert names == {
            "canonical_json_deterministic",
            "fixture_digest_stable",
            "hash_chain_verifies",
            "jsonl_round_trip",
            "decimal_money_exact",
            "manifest_ranges_parseable",
        }

    def test_is_deterministic_across_runs(self) -> None:
        first = [check.to_dict() for check in offline_contract_check()]
        second = [check.to_dict() for check in offline_contract_check()]
        assert first == second

    def test_runs_without_network(self, no_network: None) -> None:
        assert all(check.passed for check in offline_contract_check())

    def test_writes_no_files(self, tmp_path: object, monkeypatch: object) -> None:
        # The check is in-memory: open() is never called.
        import builtins

        def blocked(*args: object, **kwargs: object) -> object:
            raise AssertionError("file access attempted")

        monkeypatch.setattr(builtins, "open", blocked)  # type: ignore[attr-defined]
        assert all(check.passed for check in offline_contract_check())


class TestCompatibilityReport:
    """The five-layer report."""

    def test_reports_five_layers_in_order(self) -> None:
        report = compatibility_report(check_imports=False)
        assert [layer.layer for layer in report.layers] == [
            CompatibilityLayer.INSTALLATION,
            CompatibilityLayer.IMPORTS,
            CompatibilityLayer.CLI,
            CompatibilityLayer.OFFLINE_CONTRACT,
            CompatibilityLayer.LIVE_RUNTIME,
        ]

    def test_live_runtime_is_always_not_checked(self) -> None:
        for check_imports in (True, False):
            report = compatibility_report(check_imports=check_imports)
            live = report.layer(CompatibilityLayer.LIVE_RUNTIME)
            assert live.status is CheckStatus.NOT_CHECKED
            assert live.checked is False
            assert "not tested" in live.detail.lower()

    def test_live_runtime_never_claims_integration(self) -> None:
        detail = (
            compatibility_report(check_imports=False).layer(CompatibilityLayer.LIVE_RUNTIME).detail
        )
        assert "no live integration is claimed" in detail

    def test_imports_layer_not_checked_when_disabled(self) -> None:
        report = compatibility_report(check_imports=False)
        imports = report.layer(CompatibilityLayer.IMPORTS)
        assert imports.status is CheckStatus.NOT_CHECKED
        assert imports.checked is False

    def test_offline_contract_layer_passes(self) -> None:
        layer = compatibility_report(check_imports=False).layer(CompatibilityLayer.OFFLINE_CONTRACT)
        assert layer.status is CheckStatus.OK
        assert layer.passed == layer.total

    def test_not_checked_layers_do_not_fail_the_report(self) -> None:
        assert compatibility_report(check_imports=False).ok is True

    def test_missing_component_fails_installation_layer(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={})
        report = compatibility_report(
            check_imports=False,
            manifest=manifest_factory(component_factory(installed_by_default=True)),
        )
        assert report.layer(CompatibilityLayer.INSTALLATION).status is CheckStatus.MISSING
        assert report.ok is False

    def test_absent_optional_keeps_report_ok(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(versions={})
        report = compatibility_report(
            check_imports=False,
            manifest=manifest_factory(component_factory(installed_by_default=False)),
        )
        assert report.ok is True

    def test_to_dict_is_json_serialisable(self) -> None:
        payload = compatibility_report(check_imports=False).to_dict()
        assert json.loads(json.dumps(payload)) == payload
        assert payload["schema_version"] == "1.0"
        assert "installation" in payload

    def test_embeds_the_installation_report(self) -> None:
        report = compatibility_report(check_imports=False)
        assert len(report.installation.components) == 7

    def test_runs_without_network(self, no_network: None) -> None:
        assert compatibility_report(check_imports=False).schema_version == "1.0"
