"""The ``model_providers`` lock-in vector: rubric 2.

Scores whether a system could leave the model providers it calls, from a
``provider-inventory.v1`` and any number of ``model-swap-evidence.v1``
documents showing tested alternatives. Each provider is scored on the ladder
below and **the vector takes the worst provider's score**.

- **0**: reserved for scanner-verified evidence. Nothing reaches it in rubric 2.
- **25**: every model has an accepted tested alternative that is local, every
  feature in use was exercised by one, and no vendor-only feature is in use.
- **50**: an accepted alternative for every model, but none is local for some
  model; or a feature in use was not exercised; or not every model has an
  accepted alternative, no vendor-only feature is in use, and an
  OpenAI-compatible fallback is declared.
- **25** also: the scanner derives the provider's own endpoint as local. The
  workload already runs off any vendor; that the model is open or
  self-hostable is attested.
- **75**: a vendor-only feature is in use, with a documented reconstruction path.
- **100**: a vendor-only feature with no reconstruction path; or not every model
  has an accepted alternative and no fallback is declared.

A 50 always says which case applied, and names each when several do.

0 is unreachable by design: the suite never runs a model, so it cannot itself
show that an alternative works. Every number in a benchmark document is the
producer's attestation; the suite checks only that the numbers are consistent
and that the decision follows from them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import portable_relative_path, read_bounded_file
from ai_infrastructure_suite.model_swap_evidence import (
    MAX_EVIDENCE_BYTES,
    Assessment,
    ModelSwapEvidence,
    ModelSwapEvidenceError,
    assess,
    bind_to_inventory,
    parse_model_swap_evidence,
)
from ai_infrastructure_suite.provider_inventory import (
    CLAIM_ENDPOINT_OVERRIDE,
    CLAIM_REGISTRY_MODELS,
    MAX_INVENTORY_BYTES,
    InventoryProvider,
    ProviderInventory,
    ProviderInventoryError,
    parse_provider_inventory,
)

VECTOR: Final = "model_providers"
STATES: Final = frozenset({"provider_inventory", "inapplicable"})
MAX_TESTED_ALTERNATIVES: Final = 64
BENCHMARK_LEVEL: Final = "verified (benchmark arithmetic)"
ATTESTED_LEVEL: Final = "attested"

#: Each case that can put a provider at 50, by fixed code and fixed sentence.
CASE_HOSTED_ALTERNATIVE: Final = "hosted_alternative"
CASE_UNTESTED_FALLBACK: Final = "untested_fallback"
CASE_FEATURE_NOT_EXERCISED: Final = "feature_not_exercised"
_CASE_TEXT: Final = {
    CASE_HOSTED_ALTERNATIVE: (
        "a tested alternative is hosted, which moves the workload between vendors, not off them"
    ),
    CASE_UNTESTED_FALLBACK: "the declared OpenAI-compatible fallback was not tested",
}

_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class AlternativeRow:
    """One tested alternative, as the report shows it."""

    path: str
    sha256: str
    provider_id: str
    model: str
    evidence: ModelSwapEvidence
    assessment: Assessment


@dataclass(frozen=True)
class ProviderScore:
    """One provider's place on the ladder, and why."""

    provider: InventoryProvider
    score: int
    verification_level: str
    cases: tuple[str, ...]
    features_not_exercised: tuple[str, ...]
    models_covered: int
    reason: str
    attested_claims: tuple[str, ...]


@dataclass(frozen=True)
class VectorResult:
    """The vector row and its detail section."""

    row: dict[str, Any]
    section: dict[str, Any]


# --- manifest entry -------------------------------------------------------------


def _alternative_declarations(entry: dict[str, Any]) -> list[dict[str, str]]:
    declared = entry.get("tested_alternatives", [])
    if not isinstance(declared, list) or len(declared) > MAX_TESTED_ALTERNATIVES:
        raise ValueError("Invalid tested_alternatives list.")
    references: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in declared:
        if not isinstance(item, dict) or item.keys() != {"path", "sha256"}:
            raise ValueError("Each tested_alternatives entry requires path and sha256.")
        digest = item["sha256"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("Invalid tested alternative SHA-256 digest.")
        if digest in seen:
            raise ValueError("The same tested alternative is declared twice.")
        seen.add(digest)
        references.append({"path": portable_relative_path(item["path"]), "sha256": digest})
    return references


def _read_bound(root: Path, name: str, digest: str, limit: int) -> bytes:
    data = read_bounded_file(root / name, limit)
    if not data or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Evidence is empty or differs from its declared SHA-256.")
    return data


def _alternatives(
    root: Path, entry: dict[str, Any], inventory: ProviderInventory
) -> list[AlternativeRow]:
    rows: list[AlternativeRow] = []
    for reference in _alternative_declarations(entry):
        data = _read_bound(root, reference["path"], reference["sha256"], MAX_EVIDENCE_BYTES)
        try:
            evidence = parse_model_swap_evidence(data)
            provider_id, model = bind_to_inventory(evidence, inventory)
        except ModelSwapEvidenceError as exc:
            # Refused, not skipped: evidence that binds to nothing, or that
            # contradicts itself, would otherwise go unmentioned.
            raise ValueError(f"Invalid tested alternative {reference['path']}: {exc}") from None
        rows.append(
            AlternativeRow(
                path=reference["path"],
                sha256=reference["sha256"],
                provider_id=provider_id,
                model=model,
                evidence=evidence,
                assessment=assess(evidence),
            )
        )
    return rows


# --- the ladder -----------------------------------------------------------------


def _model_cases(
    alternatives: list[AlternativeRow], *, tool_calling: bool
) -> tuple[str, ...] | None:
    """Which 50-cases the accepted alternatives leave open for one model.

    ``None`` when the model has no accepted alternative at all. An empty tuple
    means a local alternative that exercised every feature the model needs.

    - hosted alternative: no accepted alternative is local.
    - feature not exercised (tool calling): no accepted *local* alternative
      exercised it. When there is no local alternative at all, this is named
      only if no accepted alternative exercised it either, because the hosted
      case already explains the rest.
    """
    accepted = [row.assessment for row in alternatives if row.assessment.accepted]
    if not accepted:
        return None
    local = [a for a in accepted if a.candidate_hosting == "local"]
    cases: list[str] = []
    if not local:
        cases.append(CASE_HOSTED_ALTERNATIVE)
        if tool_calling and not any(a.tool_calling_exercised for a in accepted):
            cases.append(CASE_FEATURE_NOT_EXERCISED)
    elif tool_calling and not any(a.tool_calling_exercised for a in local):
        cases.append(CASE_FEATURE_NOT_EXERCISED)
    return tuple(cases)


def _reason(
    provider: InventoryProvider,
    score: int,
    cases: tuple[str, ...],
    features: tuple[str, ...],
    *,
    local: bool = False,
) -> str:
    prefix = f"{provider.provider_id} scores {score}"
    if score == 100:
        if any(f.reconstruction == "none" for f in provider.features.vendor_only):
            return f"{prefix}: a vendor-only feature is in use with no reconstruction path."
        return (
            f"{prefix}: not every model in use has an accepted tested alternative, "
            "and no OpenAI-compatible fallback is declared."
        )
    if score == 75:
        return f"{prefix}: a vendor-only feature is in use; a reconstruction path is documented."
    if score == 25 and local:
        return (
            f"{prefix}: the scanner derives its endpoint as local, so the workload already "
            "runs off any vendor."
        )
    if score == 25:
        return (
            f"{prefix}: every model in use has an accepted local alternative that "
            "exercised every feature in use."
        )
    texts = [
        _CASE_TEXT[case]
        if case != CASE_FEATURE_NOT_EXERCISED
        else "a feature in use was not exercised by an accepted local alternative: "
        + ", ".join(features)
        for case in cases
    ]
    return f"{prefix} because " + "; and ".join(texts) + "."


def score_provider(
    provider: InventoryProvider, alternatives: list[AlternativeRow]
) -> ProviderScore:
    """Place one provider on the ladder."""
    vendor_only = provider.features.vendor_only
    tool_calling = provider.features.tool_calling
    per_model = {
        model: _model_cases(
            [row for row in alternatives if row.model == model], tool_calling=tool_calling
        )
        for model in provider.models
    }
    covered = sum(1 for cases in per_model.values() if cases is not None)
    all_covered = covered == len(provider.models)

    features: list[str] = []
    if tool_calling and not (
        all_covered and all(CASE_FEATURE_NOT_EXERCISED not in c for c in per_model.values() if c)
    ):
        features.append("tool_calling")
    if provider.features.streaming:
        # The benchmark does not exercise streaming, so it is never covered.
        features.append("streaming")

    benchmark_cases: list[str] = []
    if all_covered:
        if any(c and CASE_HOSTED_ALTERNATIVE in c for c in per_model.values()):
            benchmark_cases.append(CASE_HOSTED_ALTERNATIVE)
        if features:
            benchmark_cases.append(CASE_FEATURE_NOT_EXERCISED)
    # Only an endpoint the scanner classified itself counts: not an SDK
    # default, and not a declaration.
    local = provider.hosting == "local" and provider.hosting_verification == "derived"

    cases: list[str] = []
    if any(f.reconstruction == "none" for f in vendor_only):
        score, level = 100, ATTESTED_LEVEL
    elif vendor_only:
        score, level = 75, ATTESTED_LEVEL
    elif all_covered and not benchmark_cases:
        score, level = 25, BENCHMARK_LEVEL
    elif local:
        # The provider is already off any vendor. Never 0: the suite cannot
        # show the model is open, self-hostable or still available.
        score, level = 25, ATTESTED_LEVEL
    elif all_covered:
        score, level = 50, BENCHMARK_LEVEL
        cases = benchmark_cases
    elif provider.fallback.available:
        score, level = 50, ATTESTED_LEVEL
        cases.append(CASE_UNTESTED_FALLBACK)
        if features:
            cases.append(CASE_FEATURE_NOT_EXERCISED)
    else:
        score, level = 100, ATTESTED_LEVEL
    named = tuple(features) if CASE_FEATURE_NOT_EXERCISED in cases else ()
    local_rule = local and score == 25 and level == ATTESTED_LEVEL
    return ProviderScore(
        provider=provider,
        score=score,
        verification_level=level,
        cases=tuple(cases),
        features_not_exercised=named,
        models_covered=covered,
        reason=_reason(provider, score, tuple(cases), named, local=local_rule),
        attested_claims=_provider_claims(provider, alternatives, local_rule=local_rule),
    )


def _provider_claims(
    provider: InventoryProvider, alternatives: list[AlternativeRow], *, local_rule: bool
) -> tuple[str, ...]:
    """Every operator- or producer-declared input this provider's score rests on."""
    claims = [
        "models in use",
        "features in use (tool calling, streaming)",
        "vendor-only features in use, or that none are",
    ]
    if provider.features.vendor_only:
        claims.append("reconstruction paths for vendor-only features")
    claims.append("jurisdiction")
    if provider.fallback.available:
        claims.append("that the declared fallback works")
    if provider.hosting_verification == "sdk_default":
        claims.append("that an SDK default endpoint is the vendor's")
    if provider.endpoint_override_present:
        claims.append(CLAIM_ENDPOINT_OVERRIDE)
    if provider.registry_models:
        claims.append(CLAIM_REGISTRY_MODELS)
    if local_rule:
        claims.append("that the local model is open or self-hostable")
    if alternatives:
        claims.extend(["that any benchmarked model was run", "every measured number"])
    return tuple(claims)


def _governing(scores: list[ProviderScore]) -> ProviderScore:
    """The worst provider; on a tie, the one with the weaker level."""
    worst = max(item.score for item in scores)
    tied = [item for item in scores if item.score == worst]
    attested = [item for item in tied if item.verification_level == ATTESTED_LEVEL]
    return (attested or tied)[0]


# --- report rows ----------------------------------------------------------------


def _provider_row(item: ProviderScore, alternatives: list[AlternativeRow]) -> dict[str, Any]:
    provider = item.provider
    return {
        "provider_id": provider.provider_id,
        "kind": provider.kind,
        "endpoint_origin": provider.endpoint_origin,
        "hosting": provider.hosting,
        "hosting_verification": provider.hosting_verification,
        "jurisdiction": provider.jurisdiction,
        "jurisdiction_verification": "attested",
        "models": list(provider.models),
        "models_with_accepted_alternative": item.models_covered,
        "tested_alternatives": sum(
            1 for row in alternatives if row.provider_id == provider.provider_id
        ),
        "openai_compatible_fallback": provider.fallback.available,
        "tool_calling": provider.features.tool_calling,
        "streaming": provider.features.streaming,
        "vendor_only_features": [
            {"feature": f.feature, "reconstruction": f.reconstruction}
            for f in provider.features.vendor_only
        ],
        "score": item.score,
        "verification_level": item.verification_level,
        "cases": list(item.cases),
        "features_not_exercised": list(item.features_not_exercised),
        "reason": item.reason,
        "attested_claims": list(item.attested_claims),
    }


def _alternative_row(row: AlternativeRow) -> dict[str, Any]:
    evidence = row.evidence
    candidate = evidence.candidate
    summary = candidate.summary
    return {
        "path": row.path,
        "sha256": row.sha256,
        "provider_id": row.provider_id,
        "model": row.model,
        "candidate_provider_kind": candidate.provider_kind,
        "candidate_model": candidate.model,
        "candidate_endpoint_origin": candidate.endpoint_origin,
        "candidate_hosting": row.assessment.candidate_hosting,
        "candidate_declaration_contradicted": candidate.declaration_contradicted,
        "producer_recommendation": evidence.verdict.recommendation,
        "accepted": row.assessment.accepted,
        "reasons": list(row.assessment.reasons),
        "sample_count": summary.sample_count,
        "tool_calling_exercised": row.assessment.tool_calling_exercised,
        "verification_level": BENCHMARK_LEVEL,
        "attested_claims": ["that any model was run", "every measured number", "producer"],
    }


def build_vector(root: Path, entry: object) -> VectorResult:
    """Read an inventory and its tested alternatives, and score the vector."""
    if not isinstance(entry, dict) or not {"state", "path", "sha256"} <= entry.keys():
        raise ValueError("Each entry requires state, path and sha256.")
    if entry.keys() - {"state", "path", "sha256", "tested_alternatives"}:
        raise ValueError("Unexpected field in the model_providers entry.")
    if entry["state"] != "provider_inventory":
        raise ValueError("model_providers accepts only provider_inventory or inapplicable.")
    name = portable_relative_path(entry["path"])
    digest = entry["sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("Invalid SHA-256 digest.")
    data = _read_bound(root, name, digest, MAX_INVENTORY_BYTES)
    try:
        inventory = parse_provider_inventory(data)
    except ProviderInventoryError as exc:
        raise ValueError(f"Invalid provider inventory: {exc}") from None
    alternatives = _alternatives(root, entry, inventory)
    scores = [
        score_provider(
            provider, [row for row in alternatives if row.provider_id == provider.provider_id]
        )
        for provider in inventory.providers
    ]
    governing = _governing(scores)
    # Every declared input any provider's score rests on, once each, in order.
    attested = list(dict.fromkeys(claim for item in scores for claim in item.attested_claims))
    attested.append("producer")
    count = len(scores)
    row = {
        "state": "provider_inventory",
        "score": governing.score,
        "verification_level": governing.verification_level,
        "reason": (
            f"Worst of {count} provider{'s' if count != 1 else ''}: {governing.reason} "
            f"Attested, not re-run: {', '.join(attested)}."
        ),
        "path": name,
        "sha256": digest,
    }
    section = {
        "inventory": {
            "path": name,
            "sha256": digest,
            "generated_by_tool": inventory.generated_by_tool,
            "generated_by_version": inventory.generated_by_version,
            "generated_by_verification": "attested",
        },
        "governing_provider": governing.provider.provider_id,
        "providers": [_provider_row(item, alternatives) for item in scores],
        "tested_alternatives": [_alternative_row(row) for row in alternatives],
        "attested_claims": attested,
    }
    return VectorResult(row=row, section=section)
