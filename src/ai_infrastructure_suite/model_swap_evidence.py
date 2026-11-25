"""Reading ``model-swap-evidence.v1`` and recomputing its decision.

A swap-evidence document reports one benchmark comparison: a baseline model
against one candidate, with the counts and rates measured, the thresholds the
run was judged against, and the producer's own verdict. ModelSwapBench writes
it. It is the evidence that a tested alternative exists for a model provider,
for the ``model_providers`` lock-in vector. This module reads, checks and
assesses a document; scoring the vector is a later checkpoint.

The schema packaged beside this module is the canonical definition of the
format. A producer's own schema may be narrower, never wider.

What this reader does itself:

- validates the shape, the bounds and the internal consistency of the counts;
- derives the candidate's hosting from its endpoint origin, with the same
  no-DNS rule used for a provider inventory. A declaration may only add
  hosted-ness;
- recomputes the decision from the numbers and **refuses** a document whose
  stated verdict disagrees;
- applies its own, stricter acceptance rule on top (see :func:`assess`).

What it cannot do: establish that any model was run, or that a single number
was measured. Every number is the producer's attestation. Agreement between
the numbers and the verdict shows the arithmetic is right, nothing more.

This module never imports ModelSwapBench, never opens a socket and never
resolves a name. Error messages never repeat a value from the document.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import load_json_document, read_bounded_file
from ai_infrastructure_suite.provider_inventory import ProviderInventory, origin_is_local

SCHEMA_VERSION: Final = "model-swap-evidence.v1"
MAX_EVIDENCE_BYTES: Final = 256 * 1024
MAX_TEXT_CHARS: Final = 256
MAX_CONSTRAINTS: Final = 16

#: The suite does not accept a comparison on fewer cases than this, whatever
#: minimum the document states for itself.
SUITE_MINIMUM_SAMPLES: Final = 20

PROVIDER_KINDS: Final = frozenset(
    {"deterministic", "ollama", "forge", "openai_compatible", "openai", "anthropic", "bedrock"}
)
#: Kinds that produce no model output at all: fixtures replayed for tests.
FAKE_KINDS: Final = frozenset({"deterministic"})
#: Kinds whose SDK supplies its own hosted default when no endpoint is stated.
SDK_DEFAULT_HOSTED_KINDS: Final = frozenset({"openai", "anthropic", "bedrock"})
DEPLOYMENTS: Final = frozenset({"local", "self_hosted", "hosted", "test"})

RECOMMENDED: Final = "recommended replacement"
RECOMMENDED_WITH_CONDITIONS: Final = "recommended with conditions"
FIRST_STAGE_WITH_ESCALATION: Final = "suitable as first-stage model with escalation"
NOT_RECOMMENDED: Final = "not recommended"
INSUFFICIENT_EVIDENCE: Final = "insufficient evidence"
RECOMMENDATIONS: Final = frozenset(
    {
        RECOMMENDED,
        RECOMMENDED_WITH_CONDITIONS,
        FIRST_STAGE_WITH_ESCALATION,
        NOT_RECOMMENDED,
        INSUFFICIENT_EVIDENCE,
    }
)

#: How a swap-evidence provider kind appears in a provider inventory. ``forge``
#: reaches an Ollama endpoint through AgentForge. A fake kind maps to nothing:
#: a fixture is never a provider in use.
_INVENTORY_KIND: Final = {
    "ollama": "ollama",
    "forge": "ollama",
    "openai_compatible": "openai_compatible",
    "openai": "openai",
    "anthropic": "anthropic",
    "bedrock": "bedrock",
}

_ORIGIN = re.compile(r"https?://(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:[0-9]{1,5})?")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CONSTRAINT = re.compile(r"[a-z0-9_]{1,64}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})")


class ModelSwapEvidenceError(ValueError):
    """A swap-evidence document is malformed, inconsistent, or contradicts itself."""


@dataclass(frozen=True)
class Summary:
    """The counts and rates reported for one model. All of it is attested."""

    sample_count: int
    success_count: int
    failure_count: int
    error_count: int
    timeout_count: int
    quality_score: float | None
    tool_accuracy: float | None
    policy_pass_rate: float | None
    p95_latency_ms: float
    evidence_sufficient: bool
    minimum_recommended_sample_size: int

    @property
    def success_rate(self) -> float:
        return self.success_count / self.sample_count if self.sample_count else 0.0


@dataclass(frozen=True)
class Side:
    """One model in the comparison."""

    alias: str
    provider_kind: str
    model: str
    endpoint_origin: str | None
    deployment_declared: str
    summary: Summary

    @property
    def is_fake(self) -> bool:
        """A fixture provider, or a model declared as a test double."""
        return self.provider_kind in FAKE_KINDS or self.deployment_declared == "test"

    @property
    def hosting(self) -> str:
        """``local`` or ``hosted``, derived here. A declaration only adds hosted-ness.

        Anything not positively local is hosted, including a real provider
        that states no endpoint.
        """
        if self.deployment_declared == "hosted":
            return "hosted"
        if self.endpoint_origin is not None:
            return "local" if origin_is_local(self.endpoint_origin) else "hosted"
        return "local" if self.provider_kind in FAKE_KINDS else "hosted"

    @property
    def declaration_contradicted(self) -> bool:
        """Declared local or self-hosted, at an endpoint that is not local."""
        return self.deployment_declared in {"local", "self_hosted"} and self.hosting == "hosted"


@dataclass(frozen=True)
class Thresholds:
    """The limits the producer judged the run against."""

    maximum_quality_drop: float
    minimum_reliability: float | None
    maximum_latency_ms: float | None
    minimum_cost_reduction: float
    hosted_candidates_permitted: bool


@dataclass(frozen=True)
class Verdict:
    """The producer's own decision, checked here against the numbers."""

    recommendation: str
    eligible: bool
    policy_pass: bool
    deployment_compatible: bool


@dataclass(frozen=True)
class ModelSwapEvidence:
    """A validated swap-evidence document whose verdict matches its numbers."""

    generated_by_tool: str
    generated_by_version: str
    run_id: str
    manifest_hash: str
    created_at: str
    mode: str
    suite_name: str
    suite_version: str
    baseline: Side
    candidate: Side
    thresholds: Thresholds
    candidate_constraints: tuple[tuple[str, bool], ...]
    verdict: Verdict
    failed_gates: tuple[str, ...]


@dataclass(frozen=True)
class Assessment:
    """What the suite concludes from a document. Scoring uses this later."""

    #: The candidate passed every gate, on enough real cases, with a quality
    #: score. Cost is ignored: a dearer alternative still shows the workload
    #: can move.
    accepted: bool
    #: ``local``, ``hosted`` or ``fake``.
    candidate_hosting: str
    #: Whether the benchmark exercised tool calling on the candidate.
    tool_calling_exercised: bool
    #: Never true: the benchmark does not exercise streaming.
    streaming_exercised: bool
    #: Why the candidate was not accepted. Fixed phrases, no document values.
    reasons: tuple[str, ...]


# --- field readers -----------------------------------------------------------


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelSwapEvidenceError(f"Expected an object for {label}.")
    return value


def _keys(document: dict[str, Any], required: set[str], label: str) -> None:
    if set(document) != required:
        raise ModelSwapEvidenceError(f"Unexpected or missing fields in {label}.")


def _text(value: object, label: str, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    return value


def _flag(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    return value


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    return value


def _count(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    return value


def _number(
    value: object, label: str, *, upper: float | None = None, lower: float | None = 0.0
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    number = float(value)
    # NaN fails both comparisons' complements; infinity fails the bound check.
    if number != number or number in (float("inf"), float("-inf")):
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    if (lower is not None and number < lower) or (upper is not None and number > upper):
        raise ModelSwapEvidenceError(f"{label} is out of range.")
    return number


def _optional_rate(value: object, label: str) -> float | None:
    return None if value is None else _number(value, label, upper=1.0)


def _origin(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS or not _ORIGIN.fullmatch(value):
        raise ModelSwapEvidenceError(
            f"{label} must be a lowercase http(s) origin: no credentials, path, query or fragment."
        )
    port = value.rsplit(":", 1)[-1] if re.search(r":[0-9]{1,5}$", value) else None
    if port is not None and not 0 < int(port) <= 65535:
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    return value


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ModelSwapEvidenceError(f"Invalid {label}.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ModelSwapEvidenceError(f"Invalid {label}.") from None
    return value


def _summary(value: object, label: str) -> Summary:
    block = _mapping(value, label)
    _keys(
        block,
        {
            "sample_count",
            "success_count",
            "failure_count",
            "error_count",
            "timeout_count",
            "quality_score",
            "tool_accuracy",
            "policy_pass_rate",
            "p95_latency_ms",
            "evidence_sufficient",
            "minimum_recommended_sample_size",
        },
        label,
    )
    summary = Summary(
        sample_count=_count(block["sample_count"], f"{label} sample_count"),
        success_count=_count(block["success_count"], f"{label} success_count"),
        failure_count=_count(block["failure_count"], f"{label} failure_count"),
        error_count=_count(block["error_count"], f"{label} error_count"),
        timeout_count=_count(block["timeout_count"], f"{label} timeout_count"),
        quality_score=_optional_rate(block["quality_score"], f"{label} quality_score"),
        tool_accuracy=_optional_rate(block["tool_accuracy"], f"{label} tool_accuracy"),
        policy_pass_rate=_optional_rate(block["policy_pass_rate"], f"{label} policy_pass_rate"),
        p95_latency_ms=_number(block["p95_latency_ms"], f"{label} p95_latency_ms"),
        evidence_sufficient=_flag(block["evidence_sufficient"], f"{label} evidence_sufficient"),
        minimum_recommended_sample_size=_count(
            block["minimum_recommended_sample_size"],
            f"{label} minimum_recommended_sample_size",
            minimum=1,
        ),
    )
    outcomes = (
        summary.success_count + summary.failure_count + summary.error_count + summary.timeout_count
    )
    if outcomes > summary.sample_count:
        raise ModelSwapEvidenceError(f"{label} counts more outcomes than samples.")
    executed = summary.success_count + summary.failure_count
    if (summary.quality_score is None) != (executed == 0):
        raise ModelSwapEvidenceError(
            f"{label} quality_score must be null exactly when no case executed."
        )
    if (
        summary.evidence_sufficient
        and summary.sample_count < summary.minimum_recommended_sample_size
    ):
        raise ModelSwapEvidenceError(
            f"{label} claims sufficient evidence on fewer samples than its own minimum."
        )
    return summary


def _side(value: object, label: str) -> Side:
    block = _mapping(value, label)
    _keys(
        block,
        {"alias", "provider_kind", "model", "endpoint_origin", "deployment_declared", "summary"},
        label,
    )
    kind = _enum(block["provider_kind"], PROVIDER_KINDS, f"{label} provider_kind")
    origin = (
        None
        if block["endpoint_origin"] is None
        else _origin(block["endpoint_origin"], f"{label} endpoint_origin")
    )
    if origin is not None and kind in FAKE_KINDS:
        raise ModelSwapEvidenceError(f"{label} is a fixture provider and takes no endpoint_origin.")
    return Side(
        alias=_text(block["alias"], f"{label} alias"),
        provider_kind=kind,
        model=_text(block["model"], f"{label} model"),
        endpoint_origin=origin,
        deployment_declared=_enum(
            block["deployment_declared"], DEPLOYMENTS, f"{label} deployment_declared"
        ),
        summary=_summary(block["summary"], f"{label} summary"),
    )


def _thresholds(value: object) -> Thresholds:
    block = _mapping(value, "thresholds")
    _keys(
        block,
        {
            "maximum_quality_drop",
            "minimum_reliability",
            "maximum_latency_ms",
            "minimum_cost_reduction",
            "hosted_candidates_permitted",
        },
        "thresholds",
    )
    return Thresholds(
        maximum_quality_drop=_number(block["maximum_quality_drop"], "maximum_quality_drop"),
        minimum_reliability=_optional_rate(block["minimum_reliability"], "minimum_reliability"),
        maximum_latency_ms=(
            None
            if block["maximum_latency_ms"] is None
            else _number(block["maximum_latency_ms"], "maximum_latency_ms")
        ),
        minimum_cost_reduction=_number(
            block["minimum_cost_reduction"], "minimum_cost_reduction", lower=None
        ),
        hosted_candidates_permitted=_flag(
            block["hosted_candidates_permitted"], "hosted_candidates_permitted"
        ),
    )


def _constraints(value: object) -> tuple[tuple[str, bool], ...]:
    if not isinstance(value, list) or len(value) > MAX_CONSTRAINTS:
        raise ModelSwapEvidenceError("Invalid candidate_constraints.")
    outcomes: list[tuple[str, bool]] = []
    for item in value:
        entry = _mapping(item, "candidate_constraints")
        _keys(entry, {"name", "passed"}, "candidate_constraints")
        name = entry["name"]
        if not isinstance(name, str) or not _CONSTRAINT.fullmatch(name):
            raise ModelSwapEvidenceError("Invalid candidate_constraints name.")
        outcomes.append((name, _flag(entry["passed"], "candidate_constraints passed")))
    return tuple(outcomes)


def _verdict(value: object) -> Verdict:
    block = _mapping(value, "verdict")
    _keys(block, {"recommendation", "eligible", "policy_pass", "deployment_compatible"}, "verdict")
    return Verdict(
        recommendation=_enum(block["recommendation"], RECOMMENDATIONS, "verdict recommendation"),
        eligible=_flag(block["eligible"], "verdict eligible"),
        policy_pass=_flag(block["policy_pass"], "verdict policy_pass"),
        deployment_compatible=_flag(
            block["deployment_compatible"], "verdict deployment_compatible"
        ),
    )


# --- the decision, recomputed ------------------------------------------------


def _candidate_sends_content_away(candidate: Side) -> bool:
    """The producer's own hosted test, which the deployment gate uses."""
    if candidate.deployment_declared == "hosted":
        return True
    if candidate.endpoint_origin is not None:
        return not origin_is_local(candidate.endpoint_origin)
    return candidate.provider_kind in SDK_DEFAULT_HOSTED_KINDS


def failed_gates(
    baseline: Summary,
    candidate: Summary,
    thresholds: Thresholds,
    constraints: tuple[tuple[str, bool], ...],
    *,
    deployment_compatible: bool,
) -> tuple[str, ...]:
    """Which gates the candidate failed, by fixed name. Cost is not a gate."""
    failed: list[str] = []
    baseline_quality = baseline.quality_score or 0.0
    candidate_quality = candidate.quality_score or 0.0
    if max(0.0, baseline_quality - candidate_quality) > thresholds.maximum_quality_drop:
        failed.append("quality_drop")
    if -(candidate.success_rate - baseline.success_rate) > thresholds.maximum_quality_drop:
        failed.append("success_rate_drop")
    if (
        thresholds.minimum_reliability is not None
        and candidate.success_rate < thresholds.minimum_reliability
    ):
        failed.append("minimum_reliability")
    if (
        thresholds.maximum_latency_ms is not None
        and candidate.p95_latency_ms > thresholds.maximum_latency_ms
    ):
        failed.append("maximum_latency")
    if candidate.policy_pass_rate is not None and candidate.policy_pass_rate < 1.0:
        failed.append("policy")
    if not deployment_compatible:
        failed.append("hosted_candidate_not_permitted")
    if any(not passed for _name, passed in constraints):
        failed.append("constraint")
    return tuple(failed)


def _insufficient(candidate: Summary) -> bool:
    return (
        candidate.sample_count == 0
        or (candidate.success_count == 0 and candidate.error_count == candidate.sample_count)
        or not candidate.evidence_sufficient
    )


def _check_verdict(
    baseline: Side,
    candidate: Side,
    thresholds: Thresholds,
    constraints: tuple[tuple[str, bool], ...],
    verdict: Verdict,
) -> tuple[str, ...]:
    """Recompute the decision and refuse a verdict that disagrees with it."""
    compatible = not _candidate_sends_content_away(candidate) or (
        thresholds.hosted_candidates_permitted
    )
    if verdict.deployment_compatible != compatible:
        raise ModelSwapEvidenceError(
            "The stated deployment compatibility disagrees with the candidate's endpoint."
        )
    rate = candidate.summary.policy_pass_rate
    if verdict.policy_pass != (rate is not None and rate >= 1.0):
        raise ModelSwapEvidenceError(
            "The stated policy result disagrees with the policy pass rate."
        )
    failed = failed_gates(
        baseline.summary,
        candidate.summary,
        thresholds,
        constraints,
        deployment_compatible=compatible,
    )
    insufficient = _insufficient(candidate.summary)
    eligible = not insufficient and not failed
    if verdict.eligible != eligible:
        raise ModelSwapEvidenceError("The stated verdict disagrees with the reported numbers.")
    if insufficient:
        allowed = frozenset({INSUFFICIENT_EVIDENCE})
    elif failed:
        allowed = frozenset({NOT_RECOMMENDED, FIRST_STAGE_WITH_ESCALATION})
    else:
        allowed = frozenset({RECOMMENDED, RECOMMENDED_WITH_CONDITIONS})
    if verdict.recommendation not in allowed:
        raise ModelSwapEvidenceError(
            "The stated recommendation disagrees with the reported numbers."
        )
    return failed


def parse_model_swap_evidence(data: bytes) -> ModelSwapEvidence:
    """Validate ``model-swap-evidence.v1`` bytes and check the verdict against the numbers."""
    if len(data) > MAX_EVIDENCE_BYTES:
        raise ModelSwapEvidenceError("Swap evidence exceeds the size limit.")
    try:
        document = _mapping(load_json_document(data, "swap evidence"), "swap evidence")
    except ModelSwapEvidenceError:
        raise
    except ValueError as exc:
        raise ModelSwapEvidenceError(str(exc)) from None
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ModelSwapEvidenceError(f"Expected a {SCHEMA_VERSION} document.")
    _keys(
        document,
        {
            "schema_version",
            "generated_by",
            "run",
            "baseline",
            "candidate",
            "thresholds",
            "candidate_constraints",
            "verdict",
        },
        "swap evidence",
    )
    producer = _mapping(document["generated_by"], "generated_by")
    _keys(producer, {"tool", "version"}, "generated_by")
    run = _mapping(document["run"], "run")
    _keys(
        run, {"run_id", "manifest_hash", "created_at", "mode", "suite_name", "suite_version"}, "run"
    )
    manifest_hash = run["manifest_hash"]
    if not isinstance(manifest_hash, str) or not _SHA256.fullmatch(manifest_hash):
        raise ModelSwapEvidenceError("Invalid run manifest_hash.")
    baseline = _side(document["baseline"], "baseline")
    candidate = _side(document["candidate"], "candidate")
    if baseline.alias == candidate.alias:
        raise ModelSwapEvidenceError("The baseline and the candidate are the same model alias.")
    thresholds = _thresholds(document["thresholds"])
    constraints = _constraints(document["candidate_constraints"])
    verdict = _verdict(document["verdict"])
    failed = _check_verdict(baseline, candidate, thresholds, constraints, verdict)
    return ModelSwapEvidence(
        generated_by_tool=_text(producer["tool"], "generated_by tool"),
        generated_by_version=_text(producer["version"], "generated_by version", 64),
        run_id=_text(run["run_id"], "run run_id"),
        manifest_hash=manifest_hash,
        created_at=_timestamp(run["created_at"], "run created_at"),
        mode=_text(run["mode"], "run mode"),
        suite_name=_text(run["suite_name"], "run suite_name"),
        suite_version=_text(run["suite_version"], "run suite_version"),
        baseline=baseline,
        candidate=candidate,
        thresholds=thresholds,
        candidate_constraints=constraints,
        verdict=verdict,
        failed_gates=failed,
    )


def load_model_swap_evidence(
    path: Path, *, expected_sha256: str | None = None
) -> ModelSwapEvidence:
    """Read and validate swap evidence from a bounded local file.

    ``expected_sha256`` binds the document to the digest a manifest declared.
    """
    data = read_bounded_file(path, MAX_EVIDENCE_BYTES)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise ModelSwapEvidenceError("Invalid expected digest.")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ModelSwapEvidenceError("Swap evidence differs from its declared SHA-256.")
    return parse_model_swap_evidence(data)


# --- what the suite concludes ------------------------------------------------


def assess(evidence: ModelSwapEvidence) -> Assessment:
    """Apply the suite's own acceptance rule to a validated document.

    Stricter than the producer's verdict in three ways: the sample must reach
    both the document's own recommended minimum and
    :data:`SUITE_MINIMUM_SAMPLES`; there must be a quality score; and a fake
    candidate is reported but never accepted. Cost plays no part.
    """
    candidate = evidence.candidate
    summary = candidate.summary
    reasons: list[str] = []
    if candidate.is_fake:
        reasons.append("the candidate is a test fixture, not a model")
    if evidence.baseline.is_fake:
        reasons.append("the baseline is a test fixture, not a model")
    if not summary.evidence_sufficient:
        reasons.append("the producer marked the evidence insufficient")
    required = max(summary.minimum_recommended_sample_size, SUITE_MINIMUM_SAMPLES)
    if summary.sample_count < required:
        reasons.append("too few samples")
    if summary.quality_score is None:
        reasons.append("no case executed, so there is no quality score")
    if _insufficient(summary) and summary.evidence_sufficient:
        reasons.append("every case errored")
    reasons.extend(f"failed gate: {gate}" for gate in evidence.failed_gates)
    return Assessment(
        accepted=not reasons,
        candidate_hosting="fake" if candidate.is_fake else candidate.hosting,
        tool_calling_exercised=summary.tool_accuracy is not None,
        streaming_exercised=False,
        reasons=tuple(reasons),
    )


def bind_to_inventory(evidence: ModelSwapEvidence, inventory: ProviderInventory) -> tuple[str, str]:
    """The inventory provider and model this document's baseline tested.

    The baseline must be exactly one provider in the inventory: same kind,
    same endpoint origin, and a model that provider lists. No match and more
    than one match are both refused, never skipped: evidence that fits nothing
    is a mistake to surface, and evidence that fits two providers cannot say
    which one it tested.
    """
    baseline = evidence.baseline
    kind = _INVENTORY_KIND.get(baseline.provider_kind)
    matches = [
        provider.provider_id
        for provider in inventory.providers
        if provider.kind == kind
        and provider.endpoint_origin == baseline.endpoint_origin
        and baseline.model in provider.models
    ]
    if not matches:
        raise ModelSwapEvidenceError("The baseline matches no provider and model in the inventory.")
    if len(matches) > 1:
        raise ModelSwapEvidenceError(
            "The baseline matches more than one provider in the inventory."
        )
    return matches[0], baseline.model
