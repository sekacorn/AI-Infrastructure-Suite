"""The ``re-embed-record.v1`` format: what it accepts, and what it refuses.

Every fixture is built in memory or under ``tmp_path``. Nothing here embeds
text, loads weights or opens a socket, because the reader cannot.
"""

from __future__ import annotations

import copy
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from ai_infrastructure_suite.re_embed_record import (
    ATTESTED_CLAIMS,
    DEFAULT_BATCH_SIZE,
    KNOWN_SCHEMES,
    SCHEMA_VERSION,
    ReEmbedRecordError,
    load_re_embed_record,
    parse_re_embed_record,
)

EXAMPLE = Path(__file__).parents[1] / "docs/examples/re-embed-record/re-embed-record.json"

SOURCE_DIGEST = "a" * 64
TARGET_DIGEST = "b" * 64


def record(**overrides: Any) -> dict[str, Any]:
    """A minimal valid record, before any override."""
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_store": {"store_id": "support-memory", "store_evidence_sha256": SOURCE_DIGEST},
        "target_store": {"store_id": "support-rebuilt", "store_evidence_sha256": TARGET_DIGEST},
        "target_model": {
            "id": "privateaistack.hashed-bow.sha256-signed",
            "revision": "1",
            "dimensions": 64,
            "availability": "open_weight_self_hostable",
            "availability_verification": "attested",
        },
        "batch_log": {
            "batch_size": DEFAULT_BATCH_SIZE,
            "batches_completed": 1,
            "records_embedded": 2,
            "resumed": False,
        },
        "coverage": {
            "total_records": 3,
            "retained_rebuilt": 2,
            "missing_skipped": 0,
            "deleted_by_policy_skipped": 1,
            "failed": 0,
        },
        "egress": {
            "remote_provider_used": False,
            "records_transmitted": 0,
            "raw_source_text_transmitted": False,
        },
    }
    base.update(copy.deepcopy(overrides))
    return base


def parse(document: dict[str, Any]):
    return parse_re_embed_record(json.dumps(document).encode())


def sampled_quality(**overrides: Any) -> dict[str, Any]:
    """A passing quality block of the shape a seeded record-anchored run writes."""
    quality: dict[str, Any] = {
        "status": "sampled",
        "query_set_size": 10,
        "top_k": 5,
        "sufficient_evidence": True,
        "mode": "record_anchored",
        "metric_basis": "neighborhood_agreement",
        "anchor_selection": "seeded_random",
        "anchor_seed": "re-embed-quality",
        "similarity": "cosine",
        "candidate_pool_size": 64,
        "thresholds": {"minimum_top_k_overlap": 0.7},
        "metrics": {"top_k_overlap": 0.8, "mrr": 0.7, "mrr_before": 0.72},
        "checks": [
            {"name": "minimum_top_k_overlap", "passed": True, "actual": 0.8, "threshold": 0.7}
        ],
    }
    quality.update(overrides)
    return quality


# --- Accepted ---------------------------------------------------------------


def test_the_shipped_example_parses_and_is_internally_consistent() -> None:
    result = load_re_embed_record(EXAMPLE)

    assert result.target_model.triple == ("privateaistack.hashed-bow.sha256-signed", "1", 64)
    assert result.target_model.is_known_scheme
    assert result.coverage.total_records == (
        result.coverage.retained_rebuilt
        + result.coverage.missing_skipped
        + result.coverage.deleted_by_policy_skipped
        + result.coverage.failed
    )
    assert result.quality is not None and result.quality.passed
    assert not result.egress.remote_provider_used


def test_a_minimal_record_without_quality_parses() -> None:
    result = parse(record())

    assert result.quality is None
    assert result.quality_status == "not_measured"


def test_the_digest_binds_the_record_to_its_bytes(tmp_path: Path) -> None:
    data = json.dumps(record()).encode()
    path = tmp_path / "r.json"
    path.write_bytes(data)

    assert load_re_embed_record(path, expected_sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(ReEmbedRecordError, match="differs from its declared SHA-256"):
        load_re_embed_record(path, expected_sha256="c" * 64)


def test_model_identity_is_the_triple_not_the_identifier() -> None:
    sixty_four = parse(record()).target_model
    five_twelve = parse(
        record(
            target_model={
                "id": "privateaistack.hashed-bow.sha256-signed",
                "revision": "1",
                "dimensions": 512,
                "availability": "open_weight_self_hostable",
                "availability_verification": "attested",
            }
        )
    ).target_model

    # Same scheme, same revision, different dimensions: two different models.
    assert sixty_four.id == five_twelve.id
    assert sixty_four.triple != five_twelve.triple


def test_both_shipped_schemes_are_recognised() -> None:
    assert set(KNOWN_SCHEMES) == {
        "agentforge.hashed-bow.blake2b",
        "privateaistack.hashed-bow.sha256-signed",
    }
    for scheme in KNOWN_SCHEMES:
        result = parse(
            record(
                target_model={
                    "id": scheme,
                    "revision": "1",
                    "dimensions": 64,
                    "availability": "open_weight_self_hostable",
                    "availability_verification": "attested",
                }
            )
        )
        assert result.target_model.is_known_scheme


def test_an_unknown_model_is_valid_but_not_a_known_scheme() -> None:
    result = parse(
        record(
            target_model={
                "id": "some-vendor-embed",
                "revision": "2026-01-01",
                "dimensions": 1536,
                "availability": "vendor_only",
                "availability_verification": "attested",
            }
        )
    )

    assert not result.target_model.is_known_scheme
    assert not result.target_model.self_hostable


def test_the_default_batch_size_is_bounded_for_a_small_machine() -> None:
    # 64 records at the 1 MiB per-record source ceiling is 64 MiB of text.
    assert DEFAULT_BATCH_SIZE == 64
    assert DEFAULT_BATCH_SIZE * 1024 * 1024 <= 64 * 1024 * 1024


def test_the_attested_claims_name_what_the_record_cannot_establish() -> None:
    assert "query set representativeness" in ATTESTED_CLAIMS
    assert "model availability" in ATTESTED_CLAIMS
    assert any("threshold" in claim for claim in ATTESTED_CLAIMS)


def test_the_declared_thresholds_are_listed_among_the_attested_claims() -> None:
    result = parse(record(quality=sampled_quality()))

    claims = result.attested_claims()
    assert set(ATTESTED_CLAIMS) <= set(claims)
    # A reader who is shown "quality sampled" must also be shown the bar.
    assert "operator-declared thresholds: minimum_top_k_overlap 0.7" in claims


def test_a_record_without_thresholds_lists_only_the_fixed_claims() -> None:
    bare = {"status": "not_measured", "query_set_size": 0, "top_k": 5}

    assert parse(record(quality=bare)).attested_claims() == ATTESTED_CLAIMS
    assert parse(record()).attested_claims() == ATTESTED_CLAIMS


# --- Labels and scoring -----------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        ("sampled", "verified (re-embedded, quality sampled)"),
        ("below_threshold", "verified (re-embedded, quality below threshold)"),
        ("not_measured", "verified (re-embedded, quality not measured)"),
    ),
)
def test_each_quality_status_maps_to_its_label(status: str, expected: str) -> None:
    if status == "sampled":
        quality = sampled_quality()
    elif status == "below_threshold":
        quality = sampled_quality(
            status="below_threshold",
            checks=[
                {"name": "minimum_top_k_overlap", "passed": False, "actual": 0.4, "threshold": 0.7}
            ],
        )
    else:
        quality = {"status": "not_measured", "query_set_size": 0, "top_k": 5}

    result = parse(record(quality=quality))

    assert result.label(rebuild_reproduced=True) == expected


def test_a_rebuild_nobody_reproduced_is_attested_however_good_the_numbers() -> None:
    result = parse(record(quality=sampled_quality()))

    # This is what keeps a live-model run out of the verified levels.
    assert result.label(rebuild_reproduced=False) == "attested"
    assert not result.improves_score(rebuild_reproduced=False)


def test_the_score_moves_only_when_all_five_conditions_hold() -> None:
    passing = record(quality=sampled_quality())
    assert parse(passing).improves_score(rebuild_reproduced=True)

    # 1. not reproduced by the scanner
    assert not parse(passing).improves_score(rebuild_reproduced=False)

    # 2. quality not sampled
    below = record(
        quality=sampled_quality(
            status="below_threshold",
            thresholds={"m": 0.7},
            checks=[{"name": "m", "passed": False, "actual": 0.1, "threshold": 0.7}],
        )
    )
    assert not parse(below).improves_score(rebuild_reproduced=True)
    unmeasured = record(quality={"status": "not_measured", "query_set_size": 0, "top_k": 5})
    assert not parse(unmeasured).improves_score(rebuild_reproduced=True)

    # 3. target not self-hostable
    vendor = record(
        quality=sampled_quality(),
        target_model={
            "id": "some-vendor-embed",
            "revision": "1",
            "dimensions": 64,
            "availability": "vendor_only",
            "availability_verification": "attested",
        },
    )
    assert not parse(vendor).improves_score(rebuild_reproduced=True)

    # 4. a retained record failed to rebuild
    incomplete = record(
        quality=sampled_quality(),
        batch_log={
            "batch_size": DEFAULT_BATCH_SIZE,
            "batches_completed": 1,
            "records_embedded": 2,
            "resumed": False,
        },
        coverage={
            "total_records": 4,
            "retained_rebuilt": 2,
            "missing_skipped": 0,
            "deleted_by_policy_skipped": 1,
            "failed": 1,
            "failures": [{"record_id": "r003", "reason": "embedder returned no vector"}],
        },
    )
    assert not parse(incomplete).improves_score(rebuild_reproduced=True)

    # 5. the operator chose the query set
    chosen = record(quality=sampled_quality(anchor_selection="operator", anchor_seed=None))
    del chosen["quality"]["anchor_seed"]
    assert not parse(chosen).improves_score(rebuild_reproduced=True)


def test_an_operator_chosen_query_set_still_reports_its_full_label_and_metrics() -> None:
    quality = sampled_quality(anchor_selection="operator")
    del quality["anchor_seed"]

    result = parse(record(quality=quality))

    # It reports normally; only the score is withheld.
    assert result.label(rebuild_reproduced=True) == "verified (re-embedded, quality sampled)"
    assert result.quality is not None
    assert result.quality.metrics["top_k_overlap"] == 0.8
    assert not result.quality.reproducible_query_set
    assert not result.improves_score(rebuild_reproduced=True)


def test_a_record_predating_anchor_selection_moves_no_score() -> None:
    # Silence about how the query set was chosen is read conservatively, not
    # as a seeded draw.
    quality = sampled_quality()
    del quality["anchor_selection"]
    del quality["anchor_seed"]

    result = parse(record(quality=quality))

    assert result.label(rebuild_reproduced=True) == "verified (re-embedded, quality sampled)"
    assert not result.improves_score(rebuild_reproduced=True)


def test_record_anchored_agreement_is_never_user_search_quality() -> None:
    result = parse(record(quality=sampled_quality()))

    assert result.quality is not None
    assert result.quality.metric_basis == "neighborhood_agreement"
    assert not result.quality.measures_user_search_quality


def test_a_below_threshold_record_is_still_a_verified_label() -> None:
    # The rebuild was reproduced; only the quality claim failed. Reporting that
    # is more useful than discarding the record.
    result = parse(
        record(
            quality=sampled_quality(
                status="below_threshold",
                thresholds={"m": 0.7},
                checks=[{"name": "m", "passed": False, "actual": 0.2, "threshold": 0.7}],
            )
        )
    )

    assert result.label(rebuild_reproduced=True).startswith("verified (re-embedded")
    assert not result.improves_score(rebuild_reproduced=True)


# --- Refused ----------------------------------------------------------------


def test_another_schema_version_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match=r"re-embed-record\.v1"):
        parse(record(schema_version="re-embed-record.v2"))


def test_unexpected_top_level_fields_are_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="Unexpected fields in re-embed record"):
        parse(record(note="extra"))


def test_a_missing_required_block_is_refused() -> None:
    document = record()
    del document["egress"]
    with pytest.raises(ReEmbedRecordError, match="Unexpected fields in re-embed record"):
        parse(document)


def test_duplicate_json_fields_are_refused() -> None:
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        parse_re_embed_record(b'{"schema_version":"re-embed-record.v1","egress":{},"egress":{}}')


def test_a_record_whose_source_and_target_are_the_same_document_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="must be different documents"):
        parse(
            record(
                target_store={"store_id": "support-rebuilt", "store_evidence_sha256": SOURCE_DIGEST}
            )
        )


@pytest.mark.parametrize("digest", ("", "A" * 64, "a" * 63, "z" * 64, 7))
def test_a_malformed_store_digest_is_refused(digest: object) -> None:
    with pytest.raises(ReEmbedRecordError, match="store evidence digest"):
        parse(record(source_store={"store_id": "s", "store_evidence_sha256": digest}))


def test_coverage_counts_that_do_not_reconcile_are_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="do not reconcile"):
        parse(
            record(
                coverage={
                    "total_records": 10,
                    "retained_rebuilt": 2,
                    "missing_skipped": 0,
                    "deleted_by_policy_skipped": 1,
                    "failed": 0,
                }
            )
        )


def test_a_batch_log_that_disagrees_with_coverage_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="differ from the rebuilt coverage count"):
        parse(
            record(
                batch_log={
                    "batch_size": 64,
                    "batches_completed": 1,
                    "records_embedded": 99,
                    "resumed": False,
                }
            )
        )


def test_more_failures_listed_than_counted_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="More failures listed than counted"):
        parse(
            record(
                coverage={
                    "total_records": 3,
                    "retained_rebuilt": 2,
                    "missing_skipped": 0,
                    "deleted_by_policy_skipped": 1,
                    "failed": 0,
                    "failures": [{"record_id": "r1", "reason": "boom"}],
                }
            )
        )


def test_unlisted_failures_without_a_truncation_flag_are_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="without a truncation flag"):
        parse(
            record(
                batch_log={
                    "batch_size": 64,
                    "batches_completed": 1,
                    "records_embedded": 2,
                    "resumed": False,
                },
                coverage={
                    "total_records": 5,
                    "retained_rebuilt": 2,
                    "missing_skipped": 0,
                    "deleted_by_policy_skipped": 1,
                    "failed": 2,
                },
            )
        )


def test_a_local_run_claiming_transmitted_content_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="local run cannot report transmitted content"):
        parse(
            record(
                egress={
                    "remote_provider_used": False,
                    "records_transmitted": 2,
                    "raw_source_text_transmitted": True,
                }
            )
        )


def test_a_remote_run_without_a_named_provider_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="must name its provider"):
        parse(
            record(
                egress={
                    "remote_provider_used": True,
                    "records_transmitted": 2,
                    "raw_source_text_transmitted": True,
                }
            )
        )


def test_transmitting_more_records_than_were_rebuilt_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="More records transmitted than were rebuilt"):
        parse(
            record(
                egress={
                    "remote_provider_used": True,
                    "provider": "example-embeddings",
                    "records_transmitted": 99,
                    "raw_source_text_transmitted": True,
                }
            )
        )


def test_model_availability_claimed_as_verified_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="must be labeled attested"):
        parse(
            record(
                target_model={
                    "id": "m",
                    "revision": "1",
                    "dimensions": 64,
                    "availability": "open_weight_self_hostable",
                    "availability_verification": "verified",
                }
            )
        )


@pytest.mark.parametrize("dimensions", (0, -1, 8193, True, 1.5, "64"))
def test_invalid_dimensions_are_refused(dimensions: object) -> None:
    with pytest.raises(ReEmbedRecordError, match="dimensions"):
        parse(
            record(
                target_model={
                    "id": "m",
                    "revision": "1",
                    "dimensions": dimensions,
                    "availability": "vendor_only",
                    "availability_verification": "attested",
                }
            )
        )


def test_a_sampled_result_with_a_failed_check_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="cannot contain a failed check"):
        parse(
            record(
                quality=sampled_quality(
                    checks=[{"name": "m", "passed": False, "actual": 0.1, "threshold": 0.7}]
                )
            )
        )


def test_a_sampled_result_without_checks_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="requires its threshold checks"):
        parse(record(quality=sampled_quality(checks=[])))


def test_a_sampled_result_on_an_empty_query_set_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="non-empty query set"):
        parse(record(quality=sampled_quality(query_set_size=0)))


def test_a_sampled_result_declaring_insufficient_evidence_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="insufficient evidence"):
        parse(record(quality=sampled_quality(sufficient_evidence=False)))


def test_a_below_threshold_result_with_no_failed_check_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="requires at least one failed check"):
        parse(record(quality=sampled_quality(status="below_threshold")))


def test_an_unmeasured_result_carrying_metrics_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="cannot carry metrics or checks"):
        parse(
            record(
                quality={
                    "status": "not_measured",
                    "query_set_size": 0,
                    "top_k": 5,
                    "metrics": {"mrr": 0.5},
                }
            )
        )


@pytest.mark.parametrize(
    ("name", "value"),
    (("top_k_overlap", 1.5), ("rank_correlation", -2.0), ("hit_rate", -0.1), ("mrr", 2.0)),
)
def test_out_of_range_metrics_are_refused(name: str, value: float) -> None:
    with pytest.raises(ReEmbedRecordError, match=f"metric {name}"):
        parse(record(quality=sampled_quality(metrics={name: value})))


def test_a_non_finite_metric_is_refused() -> None:
    with pytest.raises(ValueError, match="Non-finite JSON number"):
        parse_re_embed_record(
            json.dumps(record(quality=sampled_quality())).encode().replace(b"0.8", b"NaN")
        )


def test_a_seeded_query_set_without_its_seed_is_refused() -> None:
    quality = sampled_quality()
    del quality["anchor_seed"]

    with pytest.raises(ReEmbedRecordError, match="must record its seed"):
        parse(record(quality=quality))


def test_a_seed_on_an_operator_chosen_query_set_is_refused() -> None:
    # A seed that did not draw anything is decoration that looks like evidence.
    with pytest.raises(ReEmbedRecordError, match="Only a seeded_random"):
        parse(record(quality=sampled_quality(anchor_selection="operator")))


def test_record_anchored_quality_claiming_relevance_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="neighborhood_agreement"):
        parse(record(quality=sampled_quality(metric_basis="retrieval_relevance")))


def test_thresholds_that_do_not_match_the_checks_are_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="do not match the checks"):
        parse(record(quality=sampled_quality(thresholds={"minimum_rank_correlation": 0.5})))


def test_an_unmeasured_result_carrying_thresholds_is_refused() -> None:
    quality = {
        "status": "not_measured",
        "query_set_size": 0,
        "top_k": 5,
        "thresholds": {"minimum_top_k_overlap": 0.7},
    }

    with pytest.raises(ReEmbedRecordError, match="cannot carry thresholds"):
        parse(record(quality=quality))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("mode", "vibes"),
        ("metric_basis", "accuracy"),
        ("anchor_selection", "handpicked"),
        ("similarity", "euclidean"),
    ),
)
def test_an_unknown_enum_value_is_refused(field: str, value: str) -> None:
    quality = sampled_quality()
    quality[field] = value
    if field == "metric_basis":
        quality["mode"] = "text"

    with pytest.raises(ReEmbedRecordError, match=f"quality {field}"):
        parse(record(quality=quality))


def test_too_many_thresholds_are_refused() -> None:
    quality = sampled_quality(thresholds={f"check_{index}": 0.5 for index in range(17)})

    with pytest.raises(ReEmbedRecordError, match="Too many quality thresholds"):
        parse(record(quality=quality))


def test_a_non_numeric_threshold_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="threshold"):
        parse(record(quality=sampled_quality(thresholds={"minimum_top_k_overlap": "high"})))


def test_a_candidate_pool_of_zero_is_refused() -> None:
    # Every query was ranked against something, or no query was ranked.
    with pytest.raises(ReEmbedRecordError, match="candidate_pool_size"):
        parse(record(quality=sampled_quality(candidate_pool_size=0)))


def test_the_candidate_pool_size_is_read_back() -> None:
    result = parse(record(quality=sampled_quality()))

    assert result.quality is not None
    assert result.quality.candidate_pool_size == 64


def test_a_negative_boundary_tie_count_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="boundary_ties"):
        parse(record(quality=sampled_quality(boundary_ties=-1)))


def test_an_unknown_quality_field_is_still_refused() -> None:
    # The additive fields widened the block; they did not open it.
    with pytest.raises(ReEmbedRecordError, match="Unexpected fields in quality"):
        parse(record(quality=sampled_quality(precision_at_1=0.9)))


def test_an_unknown_quality_status_is_refused() -> None:
    with pytest.raises(ReEmbedRecordError, match="Invalid quality status"):
        parse(record(quality=sampled_quality(status="great")))


def test_an_oversized_record_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    document = record()
    document["pad"] = "x" * (1024 * 1024)
    path.write_bytes(json.dumps(document).encode())

    with pytest.raises(ValueError, match="bounded regular file"):
        load_re_embed_record(path)


def test_the_packaged_schema_matches_the_reader() -> None:
    schema = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/re-embed-record.schema.json")
        .read_text(encoding="utf-8")
    )
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert set(schema["required"]) <= set(document)
    assert not set(document) - set(schema["properties"])
    assert schema["$defs"]["quality"]["properties"]["status"]["enum"] == [
        "sampled",
        "below_threshold",
        "not_measured",
    ]
    assert schema["$defs"]["model_identity"]["properties"]["availability_verification"] == {
        "const": "attested"
    }
