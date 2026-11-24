"""3b-6: what a re-embed record does to an exit report, and what it must not.

Every fixture is built under ``tmp_path`` from deterministic text. The stores
are written by hand rather than by PrivateAIStack, because the suite must not
depend on the tool it audits -- the cross-repo agreement test in
PrivateAIStack's nightly contract job is what keeps the two arithmetics in
step.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

import pytest

from ai_infrastructure_suite.embedding_portability import (
    UNPINNED_WEIGHTS_NOTE,
    scan_embedding_portability,
)
from ai_infrastructure_suite.exit_report import build_exit_report, render_markdown
from ai_infrastructure_suite.re_embed_scan import (
    derive_anchor_seed,
    scan_re_embed_record,
)
from ai_infrastructure_suite.reproduction import embed, sample_seed, select_sample

SCHEME: Final = "privateaistack.hashed-bow.sha256-signed"
DIMS: Final = 16
TEXTS: Final = {
    "r0": "escalate to the on-call engineer",
    "r1": "reset the password through the portal",
    "r2": "refunds over 500 need approval",
    "r3": "rotate the signing key every quarter",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_store(
    directory: Path,
    *,
    store_id: str,
    model_id: str,
    revision: str = "1",
    dimensions: int = DIMS,
    availability: str = "open_weight_self_hostable",
    corrupt_vectors: bool = False,
) -> Path:
    """A complete, internally consistent ``embedding-portability.v1`` store."""
    (directory / "sources").mkdir(parents=True, exist_ok=True)
    (directory / "vectors").mkdir(parents=True, exist_ok=True)
    entries, lines = [], []
    for record_id, text in TEXTS.items():
        relative = f"sources/{record_id}.txt"
        (directory / relative).write_bytes(text.encode("utf-8"))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        entries.append(
            {
                "record_id": record_id,
                "source_text": {"status": "retained", "path": relative, "sha256": digest},
            }
        )
        values = embed(text, model_id=SCHEME, revision="1", dimensions=dimensions)
        if corrupt_vectors:
            values = [value + 0.5 for value in values]
        lines.append(
            json.dumps({"record_id": record_id, "source_text_sha256": digest, "values": values})
        )
    shard = ("\n".join(lines) + "\n").encode("utf-8")
    (directory / "vectors/shard-0000.jsonl").write_bytes(shard)
    document = {
        "schema_version": "embedding-portability.v1",
        "stores": [
            {
                "store_id": store_id,
                "embedding_model": {
                    "id": model_id,
                    "revision": revision,
                    "dimensions": dimensions,
                    "availability": availability,
                    "availability_verification": "attested",
                },
                "vectors": {
                    "layout": "sharded_jsonl",
                    "shards": [
                        {
                            "path": "vectors/shard-0000.jsonl",
                            "sha256": hashlib.sha256(shard).hexdigest(),
                            "record_count": len(TEXTS),
                        }
                    ],
                    "source_text": entries,
                },
            }
        ],
    }
    path = directory / "embedding-portability.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def quality_block(*, seed: str, **overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "status": "sampled",
        "query_set_size": 24,
        "top_k": 5,
        "sufficient_evidence": True,
        "mode": "record_anchored",
        "metric_basis": "neighborhood_agreement",
        "anchor_selection": "seeded_random",
        "anchor_seed": seed,
        "similarity": "cosine",
        "thresholds": {"minimum_top_k_overlap": 0.7},
        "metrics": {"top_k_overlap": 0.81},
        "checks": [
            {"name": "minimum_top_k_overlap", "passed": True, "actual": 0.81, "threshold": 0.7}
        ],
        "notes": "Neighborhood agreement between the two stores.",
    }
    block.update(overrides)
    return block


@pytest.fixture
def scene(tmp_path: Path) -> dict[str, Any]:
    """A vendor-locked source store at 50, rebuilt onto a self-hostable scheme."""
    source = write_store(
        tmp_path / "evidence",
        store_id="support-memory",
        model_id="some-vendor-embed",
        availability="vendor_only",
    )
    target = write_store(
        tmp_path / "evidence/rebuilt", store_id="support-memory-rebuilt", model_id=SCHEME
    )
    source_sha, target_sha = _digest(source), _digest(target)
    return {
        "root": tmp_path,
        "source": source,
        "target": target,
        "source_sha": source_sha,
        "target_sha": target_sha,
        "derived_seed": derive_anchor_seed(source_sha, target_sha),
    }


def write_record(scene: dict[str, Any], **overrides: Any) -> tuple[Path, str]:
    document: dict[str, Any] = {
        "schema_version": "re-embed-record.v1",
        "source_store": {
            "store_id": "support-memory",
            "store_evidence_sha256": scene["source_sha"],
        },
        "target_store": {
            "store_id": "support-memory-rebuilt",
            "store_evidence_sha256": scene["target_sha"],
        },
        "target_model": {
            "id": SCHEME,
            "revision": "1",
            "dimensions": DIMS,
            "availability": "open_weight_self_hostable",
            "availability_verification": "attested",
        },
        "batch_log": {
            "batch_size": 64,
            "batches_completed": 1,
            "records_embedded": len(TEXTS),
            "resumed": False,
        },
        "coverage": {
            "total_records": len(TEXTS),
            "retained_rebuilt": len(TEXTS),
            "missing_skipped": 0,
            "deleted_by_policy_skipped": 0,
            "failed": 0,
        },
        "egress": {
            "remote_provider_used": False,
            "records_transmitted": 0,
            "raw_source_text_transmitted": False,
            "executor_kind": "local_builtin",
            "execution_verification": "registry_owned",
            "transmission_basis": "no_transmission",
            "hosting_mode": "local",
        },
        "quality": quality_block(seed=scene["derived_seed"]),
    }
    document.update(overrides)
    path = scene["target"].parent / "re-embed-record.json"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path, _digest(path)


def write_manifest(scene: dict[str, Any], records: list[dict[str, str]] | None) -> Path:
    entry: dict[str, Any] = {
        "state": "embedding_inventory",
        "path": "evidence/embedding-portability.json",
        "sha256": scene["source_sha"],
    }
    if records is not None:
        entry["re_embed_records"] = records
    manifest = {"schema_version": "exit-manifest.v1", "vectors": {"embeddings": entry}}
    path = scene["root"] / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _assess(scene: dict[str, Any], **overrides: Any) -> Any:
    path, digest = write_record(scene, **overrides)
    return scan_re_embed_record(
        path,
        declared_sha256=digest,
        source_store_sha256=scene["source_sha"],
        source_store_ids=frozenset({"support-memory"}),
    )


# --- the happy path --------------------------------------------------------


def test_a_reproduced_record_moves_the_store_from_50_to_25(scene: dict[str, Any]) -> None:
    path, digest = write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)

    assert report["vectors"]["embeddings"]["score"] == 25
    assert report["embedding_stores"][0]["score"] == 25
    row = report["re_embed_records"][0]
    assert row["verification_level"] == "verified (re-embedded, quality sampled)"
    assert row["rebuild_reproduced"]
    assert row["score_effect"] == "50_to_25"
    assert row["reproduction_sample_size"] >= 1
    assert path.exists()


def test_the_same_evidence_without_the_record_stays_at_50(scene: dict[str, Any]) -> None:
    manifest = write_manifest(scene, None)

    report = build_exit_report(manifest)

    assert report["vectors"]["embeddings"]["score"] == 50
    assert "re_embed_records" not in report


def test_the_label_claims_a_sample_not_a_whole_store(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    markdown = render_markdown(build_exit_report(manifest))

    assert "**sample**" in markdown
    assert "a sample is not a whole-store digest" in markdown
    assert "vector(s) recomputed" in markdown


# --- seeds are derived, never accepted ------------------------------------


def test_a_ground_seed_is_read_as_operator_chosen_and_moves_nothing(scene: dict[str, Any]) -> None:
    # An operator who can choose the seed can grind it: draw set after set
    # until the numbers clear the bar, then publish the one that passed.
    assessment = _assess(scene, quality=quality_block(seed="operator-picked-seed"))

    assert assessment.seed_was_ground
    assert assessment.anchor_selection == "operator"
    assert not assessment.improves_score
    # Still reported in full, label and all.
    assert assessment.label == "verified (re-embedded, quality sampled)"
    assert assessment.reproduced


def test_a_ground_seed_leaves_the_report_at_50(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene, quality=quality_block(seed="operator-picked-seed"))
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)

    assert report["vectors"]["embeddings"]["score"] == 50
    row = report["re_embed_records"][0]
    assert row["anchor_selection"] == "operator"
    assert row["anchor_seed_was_declared_not_derived"]
    assert row["score_effect"] == "none"
    assert row["metrics"]["top_k_overlap"] == 0.81


def test_the_derived_seed_is_a_function_of_both_digests(scene: dict[str, Any]) -> None:
    first = derive_anchor_seed("a" * 64, "b" * 64)

    assert first == derive_anchor_seed("a" * 64, "b" * 64)
    assert first != derive_anchor_seed("b" * 64, "a" * 64)
    assert first != derive_anchor_seed("a" * 64, "c" * 64)
    assert len(first) == 64


def test_the_reproduction_sample_ignores_any_seed_in_the_record(scene: dict[str, Any]) -> None:
    # The scanner's sample is derived from the target digest alone, so a seed
    # written into a record cannot steer which vectors get checked.
    derived = _assess(scene)
    ground = _assess(scene, quality=quality_block(seed="operator-picked-seed"))

    assert derived.reproduction.records_checked == ground.reproduction.records_checked
    expected = select_sample(
        tuple(sorted(TEXTS)), seed=sample_seed(scene["target_sha"]), size=len(TEXTS)
    )
    assert set(derived.reproduction.records_checked) <= set(expected)


def test_an_operator_declared_selection_is_honoured_without_a_seed(scene: dict[str, Any]) -> None:
    block = quality_block(seed=scene["derived_seed"], anchor_selection="operator")
    del block["anchor_seed"]

    assessment = _assess(scene, quality=block)

    assert assessment.anchor_selection == "operator"
    assert not assessment.seed_was_ground
    assert not assessment.improves_score


# --- binding is refused, not ignored --------------------------------------


def test_a_record_bound_to_another_store_digest_is_refused(scene: dict[str, Any]) -> None:
    _, digest = write_record(
        scene, source_store={"store_id": "support-memory", "store_evidence_sha256": "c" * 64}
    )
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    with pytest.raises(ValueError, match="does not match the embeddings evidence"):
        build_exit_report(manifest)


def test_a_record_naming_an_unknown_store_is_refused(scene: dict[str, Any]) -> None:
    _, digest = write_record(
        scene,
        source_store={"store_id": "nosuchstore", "store_evidence_sha256": scene["source_sha"]},
    )
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    with pytest.raises(ValueError, match="which the embeddings evidence does not contain"):
        build_exit_report(manifest)


def test_a_record_whose_bytes_differ_from_the_manifest_digest_is_refused(
    scene: dict[str, Any],
) -> None:
    write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": "d" * 64}]
    )

    with pytest.raises(ValueError, match="Invalid re-embed record"):
        build_exit_report(manifest)


@pytest.mark.parametrize(
    "records",
    (
        [{"path": "evidence/rebuilt/re-embed-record.json"}],
        [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": "nothex"}],
        [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": "e" * 64, "extra": 1}],
    ),
)
def test_a_malformed_declaration_is_refused(
    scene: dict[str, Any], records: list[dict[str, str]]
) -> None:
    write_record(scene)
    manifest = write_manifest(scene, records)

    with pytest.raises(ValueError):
        build_exit_report(manifest)


def test_too_many_records_are_refused(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene)
    manifest = write_manifest(
        scene,
        [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest} for _ in range(65)],
    )

    with pytest.raises(ValueError, match="Invalid re_embed_records list"):
        build_exit_report(manifest)


# --- reproduction ---------------------------------------------------------


def test_a_target_the_scanner_cannot_recompute_stays_attested(scene: dict[str, Any]) -> None:
    assessment = _assess(
        scene,
        target_model={
            "id": "some-vendor-embed",
            "revision": "1",
            "dimensions": DIMS,
            "availability": "open_weight_self_hostable",
            "availability_verification": "attested",
        },
    )

    assert not assessment.reproduced
    assert assessment.label == "attested"
    assert not assessment.improves_score
    assert "does not implement" in assessment.reason


def test_the_same_scheme_at_another_dimension_is_not_recognised(scene: dict[str, Any]) -> None:
    # Model identity is the triple: the bucket a token lands in is
    # hash(token) % dimensions, so the same scheme at two widths is two models.
    assessment = _assess(
        scene,
        target_model={
            "id": SCHEME,
            "revision": "1",
            "dimensions": 32,
            "availability": "open_weight_self_hostable",
            "availability_verification": "attested",
        },
    )

    assert not assessment.reproduced
    assert assessment.label == "attested"


def test_an_unknown_revision_is_not_recognised(scene: dict[str, Any]) -> None:
    assessment = _assess(
        scene,
        target_model={
            "id": SCHEME,
            "revision": "99",
            "dimensions": DIMS,
            "availability": "open_weight_self_hostable",
            "availability_verification": "attested",
        },
    )

    assert not assessment.reproduced
    assert "does not implement" in assessment.reason


def test_vectors_that_do_not_match_the_recomputation_are_reported(tmp_path: Path) -> None:
    source = write_store(
        tmp_path / "evidence",
        store_id="support-memory",
        model_id="some-vendor-embed",
        availability="vendor_only",
    )
    target = write_store(
        tmp_path / "evidence/rebuilt",
        store_id="support-memory-rebuilt",
        model_id=SCHEME,
        corrupt_vectors=True,
    )
    scene = {
        "root": tmp_path,
        "source": source,
        "target": target,
        "source_sha": _digest(source),
        "target_sha": _digest(target),
        "derived_seed": derive_anchor_seed(_digest(source), _digest(target)),
    }

    assessment = _assess(scene)

    assert assessment.reproduction.attempted
    assert not assessment.reproduction.matched
    assert assessment.label == "attested"
    assert not assessment.improves_score
    assert "did not match" in assessment.reason


def test_a_missing_target_document_leaves_the_record_attested(scene: dict[str, Any]) -> None:
    scene["target"].unlink()

    assessment = _assess(scene)

    assert not assessment.reproduced
    assert assessment.label == "attested"
    assert "No readable" in assessment.reason


def test_a_target_document_that_does_not_match_its_digest_is_not_reproduced(
    scene: dict[str, Any],
) -> None:
    scene["target"].write_text(
        scene["target"].read_text(encoding="utf-8").replace("support-memory-rebuilt", "renamed"),
        encoding="utf-8",
    )

    assessment = _assess(scene)

    assert not assessment.reproduced
    assert "does not match the target" in assessment.reason


# --- which rungs can move -------------------------------------------------


def test_a_record_never_moves_a_store_that_is_not_at_50(tmp_path: Path) -> None:
    # The target is self-hostable, so 3a already scores this store 25. A record
    # must leave it there rather than "improve" it to 0.
    source = write_store(tmp_path / "evidence", store_id="support-memory", model_id=SCHEME)
    target = write_store(
        tmp_path / "evidence/rebuilt", store_id="support-memory-rebuilt", model_id=SCHEME
    )
    scene = {
        "root": tmp_path,
        "source": source,
        "target": target,
        "source_sha": _digest(source),
        "target_sha": _digest(target),
        "derived_seed": derive_anchor_seed(_digest(source), _digest(target)),
    }
    _, digest = write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)

    assert report["embedding_stores"][0]["score"] == 25


def test_a_vendor_only_target_moves_nothing(scene: dict[str, Any]) -> None:
    assessment = _assess(
        scene,
        target_model={
            "id": SCHEME,
            "revision": "1",
            "dimensions": DIMS,
            "availability": "vendor_only",
            "availability_verification": "attested",
        },
    )

    assert assessment.reproduced
    assert not assessment.improves_score


def test_an_incomplete_rebuild_moves_nothing(scene: dict[str, Any]) -> None:
    assessment = _assess(
        scene,
        batch_log={
            "batch_size": 64,
            "batches_completed": 1,
            "records_embedded": 3,
            "resumed": False,
        },
        coverage={
            "total_records": 4,
            "retained_rebuilt": 3,
            "missing_skipped": 0,
            "deleted_by_policy_skipped": 0,
            "failed": 1,
            "failures": [{"record_id": "r3", "reason": "executor failed"}],
        },
    )

    assert not assessment.improves_score


@pytest.mark.parametrize("status", ("below_threshold", "unjudged", "not_measured"))
def test_only_a_sampled_quality_status_moves_a_score(scene: dict[str, Any], status: str) -> None:
    if status == "below_threshold":
        block = quality_block(
            seed=scene["derived_seed"],
            status=status,
            checks=[
                {"name": "minimum_top_k_overlap", "passed": False, "actual": 0.1, "threshold": 0.7}
            ],
        )
    elif status == "unjudged":
        block = quality_block(
            seed=scene["derived_seed"],
            status=status,
            thresholds={"minimum_sample_size": 20},
            checks=[
                {"name": "minimum_sample_size", "passed": True, "actual": 24.0, "threshold": 20.0}
            ],
            notes="No agreement floor was declared. Declare minimum_top_k_overlap for a verdict.",
        )
    else:
        block = {"status": "not_measured", "query_set_size": 0, "top_k": 5}

    assessment = _assess(scene, quality=block)

    assert assessment.reproduced
    assert assessment.label == f"verified (re-embedded, quality {status.replace('_', ' ')})"
    assert not assessment.improves_score


def test_a_record_with_no_quality_block_moves_nothing(scene: dict[str, Any]) -> None:
    path, digest = write_record(scene)
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["quality"]
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    assessment = scan_re_embed_record(
        path,
        declared_sha256=_digest(path),
        source_store_sha256=scene["source_sha"],
        source_store_ids=frozenset({"support-memory"}),
    )

    assert assessment.label == "verified (re-embedded, quality not measured)"
    assert not assessment.improves_score
    assert digest != _digest(path)


# --- the deterministic wording rule ---------------------------------------


def test_softer_wording_needs_both_the_triple_and_a_reproduction(tmp_path: Path) -> None:
    path = write_store(tmp_path / "ok", store_id="s", model_id=SCHEME)
    result = scan_embedding_portability(path, evidence_sha256=_digest(path))

    assert UNPINNED_WEIGHTS_NOTE not in result.stores[0].reason
    assert "deterministic scheme this scanner re-ran" in result.stores[0].reason


def test_an_unrecognised_revision_keeps_the_generic_sentence(tmp_path: Path) -> None:
    path = write_store(tmp_path / "rev", store_id="s", model_id=SCHEME, revision="99")
    result = scan_embedding_portability(path, evidence_sha256=_digest(path))

    assert UNPINNED_WEIGHTS_NOTE in result.stores[0].reason


def test_an_unrecognised_model_keeps_the_generic_sentence(tmp_path: Path) -> None:
    path = write_store(tmp_path / "vendor", store_id="s", model_id="some-other-embed")
    result = scan_embedding_portability(path, evidence_sha256=_digest(path))

    assert UNPINNED_WEIGHTS_NOTE in result.stores[0].reason


def test_vectors_that_do_not_reproduce_keep_the_generic_sentence(tmp_path: Path) -> None:
    path = write_store(tmp_path / "bad", store_id="s", model_id=SCHEME, corrupt_vectors=True)
    result = scan_embedding_portability(path, evidence_sha256=_digest(path))

    assert UNPINNED_WEIGHTS_NOTE in result.stores[0].reason


# --- rendering ------------------------------------------------------------


def _hosted_egress() -> dict[str, Any]:
    return {
        "remote_provider_used": True,
        "provider": "example-hosted",
        "records_transmitted": len(TEXTS),
        "raw_source_text_transmitted": True,
        "executor_kind": "external_unverified",
        "execution_verification": "attested",
        "external_execution_confirmed": True,
        "hosted_execution_confirmed": True,
        "executor_id": "example-hosted",
        "endpoint_origin": "https://embeddings.example",
        "network_behavior_verification": "attested",
        "records_submitted_to_external_executor": len(TEXTS),
        "transmission_basis": "handed_to_executor",
        "transmission_accounting": "distinct_records_in_completed_batches",
        "transmission_attempts_may_exceed_count": True,
        "delivery_verification": "unverified",
        "hosting_mode": "hosted",
        "dataset_privacy_classification": "public",
        "redaction_applied": False,
        "telemetry_enabled": False,
        "warnings": [
            "records handed to an external executor are not confirmed delivered to any provider"
        ],
    }


def test_external_execution_is_impossible_to_miss(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene, egress=_hosted_egress())
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)
    markdown = render_markdown(report)

    assert "Operator-supplied code ran this rebuild" in markdown
    assert "Source text left the machine" in markdown
    assert "example-hosted" in markdown
    assert "`unverified`" in markdown
    # And in the summary row's reason, for a reader who only reads the table.
    reason = report["vectors"]["embeddings"]["reason"]
    assert "Operator-supplied code ran the rebuild" in reason
    assert "Source text left the machine" in reason


def test_a_registry_owned_rebuild_gets_one_quiet_line(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)
    markdown = render_markdown(report)

    assert "Registry-owned embedder; nothing was handed to external code." in markdown
    assert "Operator-supplied code" not in markdown
    assert "Operator-supplied code ran the rebuild" not in report["vectors"]["embeddings"]["reason"]


def test_an_absent_executor_kind_is_not_rendered_as_local(scene: dict[str, Any]) -> None:
    # A record written before the field must not be shown as a verified local
    # run. Absence is conservative for scoring and never a default for display.
    _, digest = write_record(
        scene,
        egress={
            "remote_provider_used": False,
            "records_transmitted": 0,
            "raw_source_text_transmitted": False,
        },
    )
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)
    markdown = render_markdown(report)

    assert report["re_embed_records"][0]["egress"]["executor_kind"] == "not stated"
    assert "does not state which executor ran" in markdown
    assert "Registry-owned embedder" not in markdown


def test_the_declared_thresholds_render_beside_the_verdict(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    markdown = render_markdown(build_exit_report(manifest))

    verdict = next(line for line in markdown.splitlines() if "quality sampled)**" in line)
    index = markdown.splitlines().index(verdict)
    following = "\n".join(markdown.splitlines()[index : index + 6])
    assert "operator-declared thresholds: minimum_top_k_overlap 0.7" in following
    assert "passing means the bar they set was cleared" in following


def test_agreement_is_never_described_as_search_quality(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)
    markdown = render_markdown(report)

    assert "not a measure of search quality" in markdown
    assert report["re_embed_records"][0]["measures_user_search_quality"] is False
    for forbidden in ("confirmed delivery", "delivered to", "verified delivery"):
        assert forbidden not in markdown


def test_the_report_prints_no_source_text(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene)
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    body = json.dumps(build_exit_report(manifest)) + render_markdown(build_exit_report(manifest))

    for text in TEXTS.values():
        assert text not in body


def test_the_attested_claims_reach_the_report(scene: dict[str, Any]) -> None:
    _, digest = write_record(scene, egress=_hosted_egress())
    manifest = write_manifest(
        scene, [{"path": "evidence/rebuilt/re-embed-record.json", "sha256": digest}]
    )

    report = build_exit_report(manifest)
    claims = report["re_embed_records"][0]["attested_claims"]

    assert "injected executor network behaviour" in claims
    assert "whether any provider received a submitted record" in claims
    assert "attested, not re-run:" in render_markdown(report)
