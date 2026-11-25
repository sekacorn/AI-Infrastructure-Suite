"""``model-swap-evidence.v1``: the reader, the canonical schema, and the fixtures.

The fixtures are the ten variants ModelSwapBench's writer emits, copied here
byte for byte. Every negative case is refused by the reader, and each is
marked with whether the JSON Schema can express it, so a schema change cannot
silently claim more than it checks.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import itertools
import json
import os
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from ai_infrastructure_suite.model_swap_evidence import (
    MAX_EVIDENCE_BYTES,
    SCHEMA_VERSION,
    SUITE_MINIMUM_SAMPLES,
    ModelSwapEvidenceError,
    assess,
    bind_to_inventory,
    load_model_swap_evidence,
    parse_model_swap_evidence,
)
from ai_infrastructure_suite.provider_inventory import parse_provider_inventory

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples" / "model-swap-evidence"
FIXTURES = sorted(p.name for p in EXAMPLES.glob("*.json"))
PLANTED = "swap-planted-91f3-do-not-log"
SCHEMA = json.loads(
    files("ai_infrastructure_suite")
    .joinpath("data/model-swap-evidence.schema.json")
    .read_text(encoding="utf-8")
)
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
Mutation = Callable[[dict[str, Any]], Any]

REQUIRE_PRODUCER_ENV = "AI_SUITE_REQUIRE_PRODUCER_AGREEMENT"
"""Set by the producer-agreement CI job. Turns a missing producer into a failure."""


def _fixture(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    return document


def _parse(document: object) -> Any:
    return parse_model_swap_evidence(json.dumps(document).encode("utf-8"))


def base() -> dict[str, Any]:
    """A local alternative that passed every gate, with tools and policy exercised."""
    return copy.deepcopy(_fixture("tools-and-policy.json"))


def _refused(mutate: Mutation, *, match: str | None = None) -> dict[str, Any]:
    document = base()
    mutate(document)
    with pytest.raises(ModelSwapEvidenceError, match=match) as excinfo:
        _parse(document)
    assert PLANTED not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    return document


def _inventory(*providers: dict[str, Any]) -> Any:
    document = {
        "schema_version": "provider-inventory.v1",
        "generated_by": {"tool": "operator", "version": "1"},
        "providers": list(providers),
    }
    return parse_provider_inventory(json.dumps(document).encode("utf-8"))


def _provider(provider_id: str, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "provider_id": provider_id,
        "kind": "openai_compatible",
        "preset": None,
        "preset_source": None,
        "endpoint_origin": "https://llm.vendor-a.example",
        "jurisdiction": "XA",
        "models": [{"model": "model-large"}],
        "openai_compatible_fallback": {"available": False},
        "features": {"tool_calling": True, "streaming": False, "vendor_only": []},
    }
    entry.update(overrides)
    return entry


# --- the shipped fixtures and the schema --------------------------------------


def test_the_schema_is_a_valid_closed_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["properties"]["schema_version"]["const"] == SCHEMA_VERSION

    def objects(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    found = list(objects(SCHEMA))
    assert len(found) >= 8
    assert all(node["additionalProperties"] is False for node in found)
    assert all(sorted(node["required"]) == sorted(node["properties"]) for node in found)


def test_the_expected_fixtures_ship() -> None:
    assert FIXTURES == [
        "default-endpoint.json",
        "fake-candidate.json",
        "hosted-accepted.json",
        "insufficient-samples.json",
        "local-accepted.json",
        "not-recommended.json",
        "nothing-executed.json",
        "policy-failed.json",
        "self-hosted-ipv6.json",
        "tools-and-policy.json",
    ]


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_parses_matches_the_schema_and_agrees_with_its_own_verdict(name: str) -> None:
    document = _fixture(name)

    assert list(VALIDATOR.iter_errors(document)) == []
    evidence = _parse(document)
    # Parsing recomputes the decision; a disagreement would have been refused.
    assert evidence.verdict.eligible is document["verdict"]["eligible"]
    assert evidence.verdict.eligible is (
        not evidence.failed_gates and evidence.verdict.recommendation != "insufficient evidence"
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_fixtures_use_placeholder_hosts_only(name: str) -> None:
    document = _fixture(name)
    for side in ("baseline", "candidate"):
        origin = document[side]["endpoint_origin"]
        if origin is not None:
            host = origin.split("://", 1)[1]
            assert ".example" in host or host.startswith(("127.0.0.1", "localhost", "[fd00::10]"))


# --- what the suite concludes -------------------------------------------------

EXPECTED = {
    "local-accepted.json": (True, "local", False),
    "tools-and-policy.json": (True, "local", True),
    "default-endpoint.json": (True, "local", False),
    "self-hosted-ipv6.json": (True, "local", False),
    "hosted-accepted.json": (True, "hosted", False),
    "fake-candidate.json": (False, "fake", False),
    "insufficient-samples.json": (False, "local", False),
    "not-recommended.json": (False, "local", False),
    "policy-failed.json": (False, "local", False),
    "nothing-executed.json": (False, "local", False),
}


@pytest.mark.parametrize("name", FIXTURES)
def test_assessment_of_every_fixture(name: str) -> None:
    accepted, hosting, tools = EXPECTED[name]
    assessment = assess(_parse(_fixture(name)))

    assert (assessment.accepted, assessment.candidate_hosting) == (accepted, hosting)
    assert assessment.tool_calling_exercised is tools
    assert assessment.streaming_exercised is False
    assert bool(assessment.reasons) is not accepted


def test_a_fake_candidate_is_reported_and_never_accepted() -> None:
    evidence = _parse(_fixture("fake-candidate.json"))
    # The producer's own verdict is eligible, and the arithmetic agrees.
    assert evidence.verdict.eligible is True
    assessment = assess(evidence)
    assert assessment.accepted is False
    assert assessment.reasons == ("the candidate is a test fixture, not a model",)


def test_a_real_provider_declared_as_a_test_double_is_fake_too() -> None:
    document = base()
    document["candidate"]["deployment_declared"] = "test"
    assert assess(_parse(document)).candidate_hosting == "fake"


def test_a_fake_baseline_is_never_accepted() -> None:
    document = base()
    document["baseline"].update(
        provider_kind="deterministic", endpoint_origin=None, deployment_declared="test"
    )
    assert "the baseline is a test fixture, not a model" in assess(_parse(document)).reasons


def test_the_reasons_name_each_failed_gate() -> None:
    assert assess(_parse(_fixture("not-recommended.json"))).reasons == (
        "failed gate: quality_drop",
        "failed gate: success_rate_drop",
        "failed gate: constraint",
    )
    assert assess(_parse(_fixture("policy-failed.json"))).reasons == ("failed gate: policy",)
    assert assess(_parse(_fixture("insufficient-samples.json"))).reasons == (
        "the producer marked the evidence insufficient",
        "too few samples",
    )
    assert "no case executed, so there is no quality score" in (
        assess(_parse(_fixture("nothing-executed.json"))).reasons
    )


def test_the_suite_floor_applies_even_when_the_document_states_a_lower_minimum() -> None:
    document = base()
    for side in ("baseline", "candidate"):
        document[side]["summary"].update(
            sample_count=5, success_count=5, minimum_recommended_sample_size=3
        )
    evidence = _parse(document)
    # Consistent, and eligible by the producer's rule on its own stated minimum.
    assert evidence.verdict.eligible is True
    assert SUITE_MINIMUM_SAMPLES == 20
    assert assess(evidence).reasons == ("too few samples",)


def test_a_higher_stated_minimum_is_honoured() -> None:
    document = base()
    document["candidate"]["summary"]["minimum_recommended_sample_size"] = 50
    document["candidate"]["summary"]["evidence_sufficient"] = False
    document["verdict"].update(recommendation="insufficient evidence", eligible=False)
    assert "too few samples" in assess(_parse(document)).reasons


def test_cost_plays_no_part_in_acceptance() -> None:
    cheap, dear = base(), base()
    cheap["thresholds"]["minimum_cost_reduction"] = 0.0
    dear["thresholds"]["minimum_cost_reduction"] = 0.99
    dear["verdict"]["recommendation"] = "recommended with conditions"
    cheap["verdict"]["recommendation"] = "recommended replacement"
    assert assess(_parse(cheap)).accepted is True
    assert assess(_parse(dear)).accepted is True


def test_tool_calling_counts_as_exercised_only_with_a_tool_accuracy() -> None:
    document = base()
    assert assess(_parse(document)).tool_calling_exercised is True
    document["candidate"]["summary"]["tool_accuracy"] = None
    assert assess(_parse(document)).tool_calling_exercised is False


# --- hosting is derived --------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "declared", "hosting", "contradicted"),
    [
        ("http://127.0.0.1:11434", "local", "local", False),
        ("http://[fd00::10]:8000", "self_hosted", "local", False),
        ("http://10.1.2.3", "local", "local", False),
        # Self-hosted at a public host: legitimate, hosted, and said to be so.
        ("https://api.vendor-b.example", "self_hosted", "hosted", True),
        # A name is never resolved, so it is never local.
        ("http://ollama.internal:11434", "self_hosted", "hosted", True),
        # A declaration may add hosted-ness.
        ("http://127.0.0.1:11434", "hosted", "hosted", False),
    ],
)
def test_candidate_hosting_is_derived_from_the_endpoint(
    origin: str, declared: str, hosting: str, contradicted: bool
) -> None:
    document = base()
    document["candidate"].update(
        provider_kind="openai_compatible", endpoint_origin=origin, deployment_declared=declared
    )
    candidate = _parse(document).candidate

    assert (candidate.hosting, candidate.declaration_contradicted) == (hosting, contradicted)
    assert assess(_parse(document)).candidate_hosting == hosting


def test_a_real_provider_with_no_endpoint_is_hosted() -> None:
    document = base()
    document["candidate"].update(provider_kind="anthropic", endpoint_origin=None)
    document["candidate"]["deployment_declared"] = "hosted"
    assert _parse(document).candidate.hosting == "hosted"
    document["candidate"]["deployment_declared"] = "self_hosted"
    assert _parse(document).candidate.hosting == "hosted"


@pytest.mark.parametrize("side", ["candidate", "baseline"])
@pytest.mark.parametrize(
    ("kind", "origin"),
    [
        ("openai_compatible", "https://api.vendor-b.example"),
        ("openai_compatible", "http://ollama.internal:11434"),
        # Shared address space (RFC 6598): not private, so not local.
        ("ollama", "http://100.64.0.1:11434"),
        ("anthropic", None),
        ("ollama", None),
    ],
)
def test_a_side_declared_local_at_an_endpoint_that_is_not_local_is_refused(
    side: str, kind: str, origin: str | None
) -> None:
    # The same rule a provider inventory applies to hosting_declared: local.
    document = _refused(
        lambda d: d[side].update(
            provider_kind=kind, endpoint_origin=origin, deployment_declared="local"
        ),
        match="declares local deployment",
    )
    # Cross-field: the schema cannot see it.
    assert VALIDATOR.is_valid(document)


def test_a_fixture_declared_local_is_not_refused() -> None:
    document = base()
    document["candidate"].update(
        provider_kind="deterministic", endpoint_origin=None, deployment_declared="local"
    )
    assert assess(_parse(document)).candidate_hosting == "fake"


def test_a_hosted_candidate_fails_the_deployment_gate_when_hosted_is_not_permitted() -> None:
    document = _fixture("hosted-accepted.json")
    document["thresholds"]["hosted_candidates_permitted"] = False
    document["verdict"].update(
        recommendation="not recommended", eligible=False, deployment_compatible=False
    )
    evidence = _parse(document)
    assert evidence.failed_gates == ("hosted_candidate_not_permitted",)
    assert assess(evidence).accepted is False


# --- a verdict that disagrees with its numbers is refused ----------------------


def _set(path: str, value: Any) -> Mutation:
    def mutate(document: dict[str, Any]) -> None:
        node: Any = document
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        # Claims eligibility the numbers do not support.
        _set("candidate.summary.quality_score", 0.5),
        _set("candidate.summary.success_count", 10),
        _set("candidate.summary.policy_pass_rate", 0.9),
        _set("candidate.summary.p95_latency_ms", 5000.0),
        _set("thresholds.maximum_quality_drop", 0.0),
        _set("candidate_constraints", [{"name": "minimum_tool_accuracy", "passed": False}]),
        _set("candidate.summary.evidence_sufficient", False),
        # Understates it.
        _set("verdict.eligible", False),
        _set("verdict.recommendation", "not recommended"),
        _set("verdict.recommendation", "insufficient evidence"),
        _set("verdict.recommendation", "suitable as first-stage model with escalation"),
        _set("verdict.policy_pass", False),
        _set("verdict.deployment_compatible", False),
    ],
)
def test_a_stated_verdict_that_disagrees_with_the_numbers_is_refused(mutate: Mutation) -> None:
    document = _refused(mutate, match="disagrees")
    # Each of these is well-formed: only the reader's recomputation catches it.
    assert VALIDATOR.is_valid(document)


def test_a_failed_candidate_may_be_a_first_stage_model_but_not_recommended() -> None:
    document = _fixture("not-recommended.json")
    document["verdict"]["recommendation"] = "suitable as first-stage model with escalation"
    assert _parse(document).verdict.eligible is False
    document["verdict"]["recommendation"] = "recommended with conditions"
    with pytest.raises(ModelSwapEvidenceError, match="disagrees"):
        _parse(document)


def test_a_candidate_with_all_timeouts_fails_on_quality_not_on_evidence() -> None:
    document = base()
    document["candidate"]["summary"].update(
        success_count=0, timeout_count=24, quality_score=None, tool_accuracy=None
    )
    document["candidate_constraints"] = []
    document["verdict"].update(recommendation="not recommended", eligible=False)
    evidence = _parse(document)
    assert evidence.failed_gates[:2] == ("quality_drop", "success_rate_drop")


# --- malformed documents -------------------------------------------------------

SCHEMA_CATCHES: list[Mutation] = [
    _set("schema_version", "model-swap-evidence.v2"),
    lambda d: d.pop("schema_version"),
    _set("extra", PLANTED),
    _set("candidate.notes", PLANTED),
    _set("candidate.summary.cost_usd", 1.0),
    _set("run.git_commit", PLANTED),
    _set("verdict.reasons", [PLANTED]),
    _set("thresholds.extra", 1),
    _set("generated_by.host", PLANTED),
    lambda d: d.pop("thresholds"),
    lambda d: d["candidate"]["summary"].pop("tool_accuracy"),
    _set("candidate.endpoint_origin", f"https://user:{PLANTED}@host.example"),
    _set("candidate.endpoint_origin", "https://host.example/v1"),
    _set("candidate.endpoint_origin", f"https://host.example?key={PLANTED}"),
    _set("candidate.endpoint_origin", "https://host.example#frag"),
    _set("candidate.endpoint_origin", "https://HOST.example"),
    _set("candidate.endpoint_origin", "ftp://host.example"),
    _set("candidate.endpoint_origin", "https://"),
    _set("candidate.endpoint_origin", ""),
    _set("candidate.provider_kind", "echo"),
    _set("candidate.deployment_declared", "cloud"),
    _set("candidate.model", ""),
    _set("candidate.model", "m" * 257),
    _set("candidate.alias", 7),
    _set("candidate.summary.sample_count", -1),
    _set("candidate.summary.sample_count", 24.5),
    _set("candidate.summary.sample_count", "24"),
    _set("candidate.summary.quality_score", 1.01),
    _set("candidate.summary.tool_accuracy", -0.01),
    _set("candidate.summary.policy_pass_rate", 2),
    _set("candidate.summary.p95_latency_ms", -1.0),
    _set("candidate.summary.evidence_sufficient", "yes"),
    _set("candidate.summary.minimum_recommended_sample_size", 0),
    _set("thresholds.maximum_quality_drop", -0.1),
    _set("thresholds.minimum_reliability", 1.5),
    _set("thresholds.maximum_latency_ms", -5),
    _set("thresholds.hosted_candidates_permitted", "true"),
    _set("run.manifest_hash", "abc"),
    _set("run.manifest_hash", "A" * 64),
    _set("run.created_at", "yesterday"),
    _set("run.created_at", "2026-11-25 12:00:00"),
    _set("run.run_id", ""),
    _set("verdict.recommendation", "ship it"),
    _set("verdict.eligible", 1),
    _set("candidate_constraints", [{"name": "Bad Name", "passed": True}]),
    _set("candidate_constraints", [{"name": "ok", "passed": True, "detail": PLANTED}]),
    _set("candidate_constraints", [{"name": f"c{i}", "passed": True} for i in range(17)]),
    _set("candidate_constraints", {}),
    _set("generated_by.version", "v" * 65),
    _set("baseline", []),
]

READER_ONLY: list[Mutation] = [
    # Outcomes exceed samples.
    _set("candidate.summary.error_count", 5),
    # A quality score with nothing executed, and the reverse.
    lambda d: d["candidate"]["summary"].update(success_count=0, failure_count=0, error_count=24),
    _set("baseline.summary.quality_score", None),
    # Sufficient evidence claimed below the stated minimum.
    _set("candidate.summary.minimum_recommended_sample_size", 25),
    # A fixture provider with an endpoint.
    lambda d: d["candidate"].update(provider_kind="deterministic"),
    # Both sides the same alias.
    _set("candidate.alias", "current"),
    # A port that matches the pattern and is not a port.
    _set("candidate.endpoint_origin", "http://127.0.0.1:99999"),
    _set("candidate.endpoint_origin", "http://127.0.0.1:0"),
    # A timestamp that matches the pattern and is not a date.
    _set("run.created_at", "2026-13-45T12:00:00Z"),
]


@pytest.mark.parametrize("mutate", SCHEMA_CATCHES)
def test_malformed_documents_are_refused_by_the_reader_and_the_schema(mutate: Mutation) -> None:
    document = _refused(mutate)
    assert not VALIDATOR.is_valid(document)


@pytest.mark.parametrize("mutate", READER_ONLY)
def test_inconsistent_documents_are_refused_by_the_reader_alone(mutate: Mutation) -> None:
    document = _refused(mutate)
    # Pinned: these are cross-field rules the schema does not claim to check.
    assert VALIDATOR.is_valid(document)


def test_booleans_are_not_numbers() -> None:
    _refused(_set("candidate.summary.sample_count", True))
    _refused(_set("candidate.summary.quality_score", True))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"[]",
        b"null",
        b"{",
        b"\xff\xfe",
        b'{"schema_version": "model-swap-evidence.v1", "schema_version": "model-swap-evidence.v1"}',
    ],
)
def test_bytes_that_are_not_one_well_formed_document_are_refused(data: bytes) -> None:
    with pytest.raises(ModelSwapEvidenceError):
        parse_model_swap_evidence(data)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_refused(literal: str) -> None:
    text = json.dumps(base()).replace('"quality_score": 0.9', f'"quality_score": {literal}', 1)
    assert literal in text
    with pytest.raises(ModelSwapEvidenceError):
        parse_model_swap_evidence(text.encode("utf-8"))


def test_duplicate_keys_are_refused_at_any_depth() -> None:
    text = json.dumps(base()).replace('"eligible": true', '"eligible": true, "eligible": true', 1)
    with pytest.raises(ModelSwapEvidenceError, match="Duplicate"):
        parse_model_swap_evidence(text.encode("utf-8"))


def test_an_oversized_document_is_refused_before_parsing() -> None:
    with pytest.raises(ModelSwapEvidenceError, match="size limit"):
        parse_model_swap_evidence(b" " * (MAX_EVIDENCE_BYTES + 1))


# --- loading from disk ---------------------------------------------------------


def test_loading_binds_the_file_to_a_declared_digest(tmp_path: Path) -> None:
    path = tmp_path / "swap.json"
    data = (EXAMPLES / "local-accepted.json").read_bytes()
    path.write_bytes(data)

    digest = hashlib.sha256(data).hexdigest()
    assert load_model_swap_evidence(path, expected_sha256=digest).run_id == "run-local-accepted"
    assert load_model_swap_evidence(path).candidate.alias == "local_alt"
    with pytest.raises(ModelSwapEvidenceError, match="differs from its declared SHA-256"):
        load_model_swap_evidence(path, expected_sha256="0" * 64)
    with pytest.raises(ModelSwapEvidenceError, match="Invalid expected digest"):
        load_model_swap_evidence(path, expected_sha256="nope")


def test_loading_refuses_an_empty_file_a_missing_file_and_traversal(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    with pytest.raises(ModelSwapEvidenceError):
        load_model_swap_evidence(empty)
    with pytest.raises((ValueError, OSError)):
        load_model_swap_evidence(tmp_path / "missing.json")
    with pytest.raises(ValueError):
        load_model_swap_evidence(tmp_path / ".." / "swap.json")


def test_loading_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_bytes((EXAMPLES / "local-accepted.json").read_bytes())
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are not available here")
    with pytest.raises(ValueError):
        load_model_swap_evidence(link)


# --- binding to a provider inventory -------------------------------------------


def test_a_document_binds_to_the_one_provider_and_model_its_baseline_tested() -> None:
    inventory = _inventory(
        _provider("gateway_a"),
        _provider("gateway_b", endpoint_origin="https://llm.vendor-b.example"),
    )
    assert bind_to_inventory(_parse(base()), inventory) == ("gateway_a", "model-large")


@pytest.mark.parametrize(
    "overrides",
    [
        {"endpoint_origin": "https://llm.vendor-z.example"},
        {"models": [{"model": "model-other"}]},
        {"kind": "anthropic"},
        {"kind": "openai", "endpoint_origin": None},
    ],
)
def test_a_baseline_that_matches_no_provider_and_model_is_refused(
    overrides: dict[str, Any],
) -> None:
    inventory = _inventory(_provider("gateway_a", **overrides))
    with pytest.raises(ModelSwapEvidenceError, match="matches no provider"):
        bind_to_inventory(_parse(base()), inventory)


def test_a_document_that_fits_two_providers_is_refused() -> None:
    inventory = _inventory(_provider("gateway_a"), _provider("gateway_a_again"))
    with pytest.raises(ModelSwapEvidenceError, match="more than one provider"):
        bind_to_inventory(_parse(base()), inventory)


def test_a_fixture_baseline_binds_to_nothing() -> None:
    document = base()
    document["baseline"].update(
        provider_kind="deterministic", endpoint_origin=None, deployment_declared="test"
    )
    inventory = _inventory(_provider("gateway_a", kind="openai", endpoint_origin=None))
    with pytest.raises(ModelSwapEvidenceError, match="matches no provider"):
        bind_to_inventory(_parse(document), inventory)


def test_a_forge_baseline_binds_to_the_ollama_provider_it_reaches() -> None:
    document = base()
    document["baseline"].update(
        provider_kind="forge", endpoint_origin="http://127.0.0.1:11434", deployment_declared="local"
    )
    inventory = _inventory(
        _provider(
            "local_ollama",
            kind="ollama",
            endpoint_origin="http://127.0.0.1:11434",
            jurisdiction="not_applicable",
        )
    )
    assert bind_to_inventory(_parse(document), inventory) == ("local_ollama", "model-large")


# --- independence ---------------------------------------------------------------


def test_the_reader_never_imports_the_producer() -> None:
    source = (
        files("ai_infrastructure_suite")
        .joinpath("model_swap_evidence.py")
        .read_text(encoding="utf-8")
    )
    assert "import model_swap_bench" not in source
    assert "from model_swap_bench" not in source


def test_agreement_with_the_producers_decision_engine_over_a_grid() -> None:
    """Both sides must reach the same decision from the same numbers.

    Runs only where a ModelSwapBench that has the writer is installed. The
    binding check is ModelSwapBench's own ``suite-contract`` job, which
    installs this package; this is the same comparison from this side.
    """
    try:
        swap = importlib.import_module("model_swap_bench.reports.swap_evidence")
    except ImportError:
        if os.environ.get(REQUIRE_PRODUCER_ENV) == "1":
            pytest.fail(
                f"ModelSwapBench with the swap-evidence writer is not installed and "
                f"{REQUIRE_PRODUCER_ENV}=1; a skip here would report a check that never ran."
            )
        pytest.skip("ModelSwapBench with the swap-evidence writer is not installed.")
    from model_swap_bench.config import build_suite
    from model_swap_bench.results import BenchmarkRun, ConstraintResult, ModelSummary
    from model_swap_bench.scoring.replacement import decide_replacement

    models = [
        {
            "alias": "current",
            "provider": "openai_compatible",
            "model": "model-large",
            "deployment": "hosted",
            "base_url": "https://llm.vendor-a.example/v1",
        },
        {
            "alias": "alt",
            "provider": "openai_compatible",
            "model": "model-small",
            "deployment": "local",
            "base_url": "http://127.0.0.1:8000/v1",
        },
        {
            "alias": "alt_hosted",
            "provider": "openai_compatible",
            "model": "model-medium",
            "deployment": "self_hosted",
            "base_url": "https://api.vendor-b.example/v1",
        },
    ]
    checked = 0
    grid = itertools.product(
        ("alt", "alt_hosted"),
        (True, False),  # hosted permitted
        (5, 20, 24),  # samples
        (0, 12, 22, 24),  # successes
        (0.0, 0.5, 0.88, 0.92),  # candidate quality
        (None, 0.9, 1.0),  # policy pass rate
        (None, 1000.0),  # latency limit
        (True, False),  # constraint passed
    )
    for alias, permitted, total, success, quality, policy, limit, constraint_ok in grid:
        if success > total:
            continue
        suite = build_suite(
            {
                "name": "grid",
                "version": "1",
                "baseline_model": "current",
                "models": models,
                "cases": [{"id": "c1", "input": {"message": "hi"}}],
                "privacy": {"allow_hosted_providers": True},
                "replacement": {
                    "baseline": "current",
                    "candidates": [alias],
                    "minimum_reliability": 0.8,
                    "maximum_latency_ms": limit,
                },
            }
        )
        suite.privacy.allow_hosted_providers = permitted
        candidate_model = next(m for m in suite.models if m.alias == alias)

        def summary(name: str, n: int, ok: int, q: float, **extra: Any) -> Any:
            return ModelSummary(
                model_alias=name,
                provider="openai_compatible",
                deployment="local",
                total_cases=n,
                successful_cases=ok,
                failed_cases=(n - ok) // 2,
                error_cases=(n - ok) - (n - ok) // 2,
                success_rate=ok / n,
                quality_score=q,
                evidence_sufficient=n >= 20,
                **extra,
            )

        baseline = summary("current", total, total, 0.92, p95_latency_ms=800.0)
        candidate = summary(
            alias, total, success, quality, p95_latency_ms=1200.0, policy_pass_rate=policy
        )
        constraints = [
            ConstraintResult(
                name="minimum_success_rate", passed=constraint_ok, threshold=0.8, actual=0.5
            )
        ]
        decision = decide_replacement(
            baseline,
            candidate,
            candidate_model,
            suite.replacement,
            candidate_constraints=constraints,
            privacy_allows_hosted=permitted,
        )
        run = BenchmarkRun(
            run_id="grid",
            suite_name="grid",
            suite_version="1",
            mode="compare",
            baseline_model="current",
            model_summaries=[baseline, candidate],
            constraint_results={alias: constraints},
            replacement_decisions=[decision],
            manifest_hash="0" * 64,
        )
        text = swap.render_swap_evidence_json(swap.build_swap_evidence(run, suite, alias))
        assert list(VALIDATOR.iter_errors(json.loads(text))) == []
        # Parsing refuses any document whose verdict it cannot reproduce.
        evidence = parse_model_swap_evidence(text.encode("utf-8"))
        assert evidence.verdict.eligible is decision.eligible
        assert bool(evidence.failed_gates) is bool(decision.failed_constraints)
        checked += 1
    assert checked > 1000
