"""Layered compatibility reporting for the Linux of AI ecosystem.

Compatibility is reported as five distinct layers, because conflating them is how
metapackages end up overstating what they prove:

``installation``
    The right distributions are present at versions inside the supported range.
``imports``
    Each component's public package imports and exposes its expected API.
``cli``
    Each component registers its expected console script.
``offline_contract``
    The portable data conventions the ecosystem exchanges — canonical JSON,
    SHA-256 digests, hash chains, JSONL, exact decimal money — behave
    deterministically here. This runs on the standard library alone.
``live_runtime``
    Seven products actually running together against real models and services.
    This layer is always reported as ``not_checked``: it needs Ollama,
    PostgreSQL, Docker, or hosted credentials, and this package starts none of
    them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Final

from ai_infrastructure_suite._version import __version__
from ai_infrastructure_suite.components import EcosystemManifest, load_manifest
from ai_infrastructure_suite.doctor import (
    CheckStatus,
    InstallationReport,
    inspect_installation,
)

REPORT_SCHEMA_VERSION: Final = "1.0"

# Anchors the offline fixture against silent drift. Recomputed by the test suite;
# a mismatch means the fixture or the canonical form changed.
CONTRACT_FIXTURE_DIGEST: Final = "75f823dc69bfda9bb38dad5cfcc7152cea83722f89ada98bcafaa789480f8162"


class CompatibilityLayer(StrEnum):
    """The five layers reported independently."""

    INSTALLATION = "installation"
    IMPORTS = "imports"
    CLI = "cli"
    OFFLINE_CONTRACT = "offline_contract"
    LIVE_RUNTIME = "live_runtime"


def canonical_json(value: Any) -> str:
    """Serialise ``value`` to the ecosystem's canonical JSON form.

    Sorted keys, no incidental whitespace, ASCII-escaped, and no NaN or Infinity.
    This mirrors the convention the ecosystem's portable artifacts already use.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def digest_value(value: Any) -> str:
    """Return the SHA-256 hex digest of a value's canonical JSON form."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _contract_fixture() -> dict[str, Any]:
    """Build the deterministic in-memory fixture used by the offline check.

    The shape deliberately mirrors what the ecosystem exchanges across its file
    boundaries: an AI System Map style task, a policy decision, a benchmark
    comparison, and a cost record. The values are fictional and fixed.
    """
    return {
        "schema_version": "1.0",
        "workload": "example-support-triage",
        "task": {
            "name": "PasswordReset",
            "risk_level": "low",
            "allowed_routes": ["candidate_model", "baseline_model"],
            "review_required": False,
        },
        "policy_decision": {
            "decision": "allow",
            "effective_decision": "allow",
            "conflict_strategy": "deny_overrides",
            "obligations": ["audit", "attach_policy_context"],
        },
        "benchmark": {
            "baseline_model": "baseline-fixture",
            "candidate_model": "candidate-fixture",
            "quality_retained_percent": "97.50",
            "success_rate": "0.94",
        },
        "cost": {
            "currency": "USD",
            "baseline_cost_per_success": "0.041000",
            "candidate_cost_per_success": "0.006000",
            "basis": "estimated",
        },
    }


@dataclass(frozen=True, slots=True)
class ContractCheck:
    """One deterministic offline conformance check."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def offline_contract_check() -> tuple[ContractCheck, ...]:
    """Verify the ecosystem's portable data conventions, offline and in memory.

    Runs on the standard library alone, touches no file, opens no socket, and
    imports no ecosystem component. It establishes that this environment
    reproduces the serialisation, digest, chaining, and decimal behaviour the
    portable formats depend on — not that any product works.
    """
    checks: list[ContractCheck] = []
    fixture = _contract_fixture()

    first = canonical_json(fixture)
    second = canonical_json(json.loads(first))
    checks.append(
        ContractCheck(
            name="canonical_json_deterministic",
            passed=first == second,
            detail=(
                "Canonical JSON is byte-stable across a serialise/parse round trip."
                if first == second
                else "Canonical JSON changed across a round trip."
            ),
        )
    )

    actual_digest = digest_value(fixture)
    anchored = actual_digest == CONTRACT_FIXTURE_DIGEST
    checks.append(
        ContractCheck(
            name="fixture_digest_stable",
            passed=anchored,
            detail=(
                f"Fixture SHA-256 matches the anchored value {CONTRACT_FIXTURE_DIGEST[:12]}..."
                if anchored
                else (
                    f"Fixture SHA-256 {actual_digest[:12]}... does not match the anchored "
                    f"value {CONTRACT_FIXTURE_DIGEST[:12]}..."
                )
            ),
        )
    )

    # Build a three-link hash chain in the shape the ecosystem's audit streams use,
    # then re-verify every link from scratch.
    events: list[dict[str, Any]] = []
    previous = "0" * 64
    for index, event_type in enumerate(
        ("route.selected", "model.invocation.completed", "outcome.recorded")
    ):
        event = {
            "sequence": index,
            "event_type": event_type,
            "previous_digest": previous,
            "payload": {"workload": fixture["workload"], "index": index},
        }
        previous = digest_value(event)
        event["event_digest"] = previous
        events.append(event)

    chain_ok = True
    expected_previous = "0" * 64
    for index, event in enumerate(events):
        recorded = event["event_digest"]
        recomputed = digest_value({k: v for k, v in event.items() if k != "event_digest"})
        if (
            event["sequence"] != index
            or event["previous_digest"] != expected_previous
            or recorded != recomputed
        ):
            chain_ok = False
            break
        expected_previous = recorded
    checks.append(
        ContractCheck(
            name="hash_chain_verifies",
            passed=chain_ok,
            detail=(
                f"Recomputed a {len(events)}-link SHA-256 chain with contiguous sequence "
                "numbers and matching previous-digest links."
                if chain_ok
                else "Hash chain failed recomputation."
            ),
        )
    )

    # JSONL is the ecosystem's canonical local stream format.
    lines = "\n".join(canonical_json(event) for event in events)
    parsed = [json.loads(line) for line in lines.splitlines() if line.strip()]
    jsonl_ok = parsed == events
    checks.append(
        ContractCheck(
            name="jsonl_round_trip",
            passed=jsonl_ok,
            detail=(
                f"{len(parsed)} JSONL records round-tripped without loss."
                if jsonl_ok
                else "JSONL round trip lost or altered records."
            ),
        )
    )

    # Money is exchanged as decimal strings precisely so float drift cannot occur.
    baseline = Decimal(str(fixture["cost"]["baseline_cost_per_success"]))
    candidate = Decimal(str(fixture["cost"]["candidate_cost_per_success"]))
    saving = (baseline - candidate).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    decimal_ok = str(saving) == "0.035000" and saving == Decimal("0.035")
    checks.append(
        ContractCheck(
            name="decimal_money_exact",
            passed=decimal_ok,
            detail=(
                "Decimal money arithmetic preserved six-place precision without float drift."
                if decimal_ok
                else f"Decimal money arithmetic produced unexpected value {saving}."
            ),
        )
    )

    manifest = load_manifest()
    ranges_ok = True
    for component in manifest.components:
        try:
            _ = component.specifier_set
        except Exception:  # reported, never raised
            ranges_ok = False
            break
    checks.append(
        ContractCheck(
            name="manifest_ranges_parseable",
            passed=ranges_ok,
            detail=(
                f"All {len(manifest.components)} component version ranges parse as PEP 440."
                if ranges_ok
                else "At least one component version range is not valid PEP 440."
            ),
        )
    )

    return tuple(checks)


@dataclass(frozen=True, slots=True)
class LayerResult:
    """Outcome for one compatibility layer."""

    layer: CompatibilityLayer
    status: CheckStatus
    checked: bool
    passed: int
    total: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "layer": self.layer.value,
            "status": self.status.value,
            "checked": self.checked,
            "passed": self.passed,
            "total": self.total,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """The five-layer compatibility answer for this environment."""

    schema_version: str
    suite_version: str
    manifest_schema_version: str
    layers: tuple[LayerResult, ...]
    contract_checks: tuple[ContractCheck, ...]
    installation: InstallationReport

    @property
    def ok(self) -> bool:
        """True when every checked layer passed."""
        return all(
            layer.status in (CheckStatus.OK, CheckStatus.NOT_CHECKED, CheckStatus.OPTIONAL)
            for layer in self.layers
        )

    def layer(self, name: CompatibilityLayer) -> LayerResult:
        """Return one layer result by name."""
        for item in self.layers:
            if item.layer is name:
                return item
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "schema_version": self.schema_version,
            "suite_version": self.suite_version,
            "manifest_schema_version": self.manifest_schema_version,
            "ok": self.ok,
            "layers": [item.to_dict() for item in self.layers],
            "contract_checks": [item.to_dict() for item in self.contract_checks],
            "installation": self.installation.to_dict(),
        }


def _fold(statuses: list[CheckStatus]) -> CheckStatus:
    """Reduce per-component statuses to one layer status."""
    if not statuses:
        return CheckStatus.NOT_CHECKED
    if all(status is CheckStatus.NOT_CHECKED for status in statuses):
        return CheckStatus.NOT_CHECKED
    for candidate in (
        CheckStatus.IMPORT_ERROR,
        CheckStatus.INCOMPATIBLE,
        CheckStatus.MISSING,
        CheckStatus.UNKNOWN,
    ):
        if candidate in statuses:
            return candidate
    return CheckStatus.OK


def compatibility_report(
    *,
    check_imports: bool = True,
    manifest: EcosystemManifest | None = None,
) -> CompatibilityReport:
    """Produce the layered compatibility report for this environment.

    Args:
        check_imports: When ``False``, the import layer is reported as
            ``not_checked`` and no third-party package is imported.
        manifest: Manifest override, mainly for testing.

    Returns:
        A :class:`CompatibilityReport`. The ``live_runtime`` layer is always
        ``not_checked``; this function never proves seven products run together.
    """
    resolved = manifest or load_manifest()
    installation = inspect_installation(check_imports=check_imports, manifest=resolved)

    present = [item for item in installation.components if item.installed_version is not None]

    install_statuses = [item.install_status for item in installation.components]
    install_passed = sum(
        1 for status in install_statuses if status in (CheckStatus.OK, CheckStatus.OPTIONAL)
    )
    installation_layer = LayerResult(
        layer=CompatibilityLayer.INSTALLATION,
        status=_fold(install_statuses),
        checked=True,
        passed=install_passed,
        total=len(install_statuses),
        detail=(
            f"{len(present)} of {len(installation.components)} components installed; "
            f"{install_passed} of {len(install_statuses)} satisfy their supported range "
            "or are optional and absent."
        ),
    )

    import_statuses = [item.import_status for item in present]
    import_passed = sum(1 for status in import_statuses if status is CheckStatus.OK)
    imports_layer = LayerResult(
        layer=CompatibilityLayer.IMPORTS,
        status=CheckStatus.NOT_CHECKED if not check_imports else _fold(import_statuses),
        checked=check_imports,
        passed=import_passed,
        total=len(import_statuses),
        detail=(
            "Import checks were disabled; no third-party package was imported."
            if not check_imports
            else f"{import_passed} of {len(import_statuses)} installed components import "
            "and expose their expected public API."
        ),
    )

    cli_statuses = [item.cli_status for item in present if item.cli is not None]
    cli_passed = sum(1 for status in cli_statuses if status is CheckStatus.OK)
    cli_layer = LayerResult(
        layer=CompatibilityLayer.CLI,
        status=_fold(cli_statuses),
        checked=bool(cli_statuses),
        passed=cli_passed,
        total=len(cli_statuses),
        detail=(
            f"{cli_passed} of {len(cli_statuses)} installed components register their "
            "expected console script. Entry-point metadata is read; no script is executed."
        ),
    )

    contract_checks = offline_contract_check()
    contract_passed = sum(1 for check in contract_checks if check.passed)
    contract_layer = LayerResult(
        layer=CompatibilityLayer.OFFLINE_CONTRACT,
        status=(
            CheckStatus.OK if contract_passed == len(contract_checks) else CheckStatus.INCOMPATIBLE
        ),
        checked=True,
        passed=contract_passed,
        total=len(contract_checks),
        detail=(
            f"{contract_passed} of {len(contract_checks)} portable-format conventions "
            "verified deterministically in memory using the standard library only."
        ),
    )

    live_layer = LayerResult(
        layer=CompatibilityLayer.LIVE_RUNTIME,
        status=CheckStatus.NOT_CHECKED,
        checked=False,
        passed=0,
        total=0,
        detail=(
            "Not tested. Running the seven components together requires local or hosted "
            "services such as Ollama, PostgreSQL, Docker, or provider credentials. This "
            "suite never starts or contacts them, so no live integration is claimed."
        ),
    )

    return CompatibilityReport(
        schema_version=REPORT_SCHEMA_VERSION,
        suite_version=__version__,
        manifest_schema_version=resolved.manifest_schema_version,
        layers=(installation_layer, imports_layer, cli_layer, contract_layer, live_layer),
        contract_checks=contract_checks,
        installation=installation,
    )


__all__ = [
    "CONTRACT_FIXTURE_DIGEST",
    "REPORT_SCHEMA_VERSION",
    "CompatibilityLayer",
    "CompatibilityReport",
    "ContractCheck",
    "LayerResult",
    "canonical_json",
    "compatibility_report",
    "digest_value",
    "offline_contract_check",
]
