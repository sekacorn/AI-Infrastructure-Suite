"""Embedding lock-in scanning: scoring outcomes and rejected evidence.

Every fixture is built on disk inside ``tmp_path``. No provider SDK, embedding
call, or socket is involved, and no test depends on a model being available.
"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from ai_infrastructure_suite.embedding_portability import (
    ATTESTED_DELETION_WARNING,
    FINDING_RECORD_CAP,
    MAX_SHARD_LINE_BYTES,
    RETAINED_VECTOR_FINDING,
    SCHEMA_VERSION,
    UNPINNED_WEIGHTS_NOTE,
    RestoreEvidence,
    scan_embedding_portability,
)

DIMENSIONS = 4
WEIGHTS = "b" * 64
Document = dict[str, Any]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _digest(data)


def _model(
    *,
    availability: str = "open_weight_self_hostable",
    weights: str | None = WEIGHTS,
    license_text: str | None = None,
) -> Document:
    model: Document = {
        "id": "example-embed",
        "revision": "2026-01-01",
        "dimensions": DIMENSIONS,
        "availability": availability,
        "availability_verification": "attested",
    }
    if weights is not None:
        model["weights_sha256"] = weights
    if license_text is not None:
        model["license"] = license_text
    return model


def _source_entry(root: Path, store_id: str, record_id: str, status: str) -> tuple[Document, str]:
    """Return the source_text block plus the digest recorded with the vector."""
    text = f"source text for {store_id}/{record_id}\n".encode()
    recorded = _digest(text)
    if status != "retained":
        return ({"status": status}, recorded)
    name = f"sources/{store_id}/{record_id}.txt"
    return ({"status": "retained", "path": name, "sha256": _write(root / name, text)}, recorded)


def _values(index: int) -> list[float]:
    return [round(0.1 * (index + offset), 4) for offset in range(DIMENSIONS)]


def single_file_store(
    root: Path,
    *,
    store_id: str = "support-memory",
    statuses: tuple[str, ...] = ("retained", "retained"),
    model: Document | None = None,
) -> Document:
    """Build a ``single_file`` store whose vectors and sources exist on disk."""
    records: list[Document] = []
    for index, status in enumerate(statuses):
        record_id = f"r{index:03d}"
        name = f"vectors/{store_id}/{record_id}.json"
        data = json.dumps({"values": _values(index)}).encode()
        source, recorded = _source_entry(root, store_id, record_id, status)
        records.append(
            {
                "record_id": record_id,
                "vector": {"path": name, "sha256": _write(root / name, data)},
                "source_text_sha256_in_vector_record": recorded,
                "source_text": source,
            }
        )
    return {
        "store_id": store_id,
        "embedding_model": model or _model(),
        "vectors": {"layout": "single_file", "records": records},
    }


def sharded_store(
    root: Path,
    *,
    store_id: str = "support-memory",
    statuses: tuple[str, ...] = ("retained", "retained", "retained"),
    per_shard: int = 2,
    model: Document | None = None,
) -> Document:
    """Build a ``sharded_jsonl`` store split across digest-pinned shards."""
    sources: list[Document] = []
    lines: list[bytes] = []
    for index, status in enumerate(statuses):
        record_id = f"r{index:03d}"
        source, recorded = _source_entry(root, store_id, record_id, status)
        sources.append({"record_id": record_id, "source_text": source})
        lines.append(
            json.dumps(
                {
                    "record_id": record_id,
                    "source_text_sha256": recorded,
                    "values": _values(index),
                }
            ).encode()
        )
    shards: list[Document] = []
    for shard_index in range(0, len(lines), per_shard):
        batch = lines[shard_index : shard_index + per_shard]
        name = f"vectors/{store_id}/shard-{shard_index // per_shard:03d}.jsonl"
        data = b"\n".join(batch) + b"\n"
        shards.append(
            {
                "path": name,
                "sha256": _write(root / name, data),
                "record_count": len(batch),
            }
        )
    return {
        "store_id": store_id,
        "embedding_model": model or _model(),
        "vectors": {"layout": "sharded_jsonl", "shards": shards, "source_text": sources},
    }


def write_evidence(
    root: Path, *stores: Document, name: str = "embeddings.json"
) -> tuple[Path, str]:
    """Write an evidence document and return its path and SHA-256."""
    path = root / name
    document = {"schema_version": SCHEMA_VERSION, "stores": list(stores)}
    return path, _write(path, (json.dumps(document, indent=2) + "\n").encode())


def scan(root: Path, *stores: Document, restore: RestoreEvidence | None = None) -> Any:
    path, sha256 = write_evidence(root, *stores)
    return scan_embedding_portability(path, evidence_sha256=sha256, restore=restore)


def verified_restore(
    root: Path, *stores: Document, store_id: str = "support-memory"
) -> tuple[Path, str, RestoreEvidence]:
    """Write evidence and a restore record that commits to its real digest."""
    path, sha256 = write_evidence(root, *stores)
    return path, sha256, RestoreEvidence(supplied=True, verified=True, stores={store_id: sha256})


# --- Scoring outcomes ------------------------------------------------------


def test_no_retained_source_text_is_fully_locked(tmp_path: Path) -> None:
    result = scan(tmp_path, single_file_store(tmp_path, statuses=("missing", "missing")))

    assert (result.score, result.re_embeddable) == (100, "no")
    store = result.stores[0]
    assert (store.retained, store.missing, store.coverage) == (0, 2, 0.0)
    assert "retains no source text" in store.reason


def test_partial_source_coverage_scores_seventy_five(tmp_path: Path) -> None:
    result = scan(
        tmp_path,
        single_file_store(tmp_path, statuses=("retained", "missing", "missing", "missing")),
    )

    assert (result.score, result.re_embeddable) == (75, "partial")
    assert result.stores[0].coverage == 25.0
    assert "25.00%" in result.stores[0].reason


def test_vendor_only_model_caps_at_fifty_despite_full_coverage(tmp_path: Path) -> None:
    result = scan(
        tmp_path,
        single_file_store(tmp_path, model=_model(availability="vendor_only")),
    )

    assert (result.score, result.re_embeddable) == (50, "yes")
    assert "vendor-only" in result.stores[0].reason
    assert UNPINNED_WEIGHTS_NOTE not in result.stores[0].reason


def test_full_coverage_without_restore_evidence_is_capped_at_twenty_five(tmp_path: Path) -> None:
    result = scan(tmp_path, single_file_store(tmp_path))

    assert (result.score, result.re_embeddable) == (25, "yes")
    store = result.stores[0]
    assert (store.coverage, store.restore_link) == (100.0, "absent")
    assert "No verified restore names this store." in store.reason
    assert "validated export with a compatible importer" in store.reason


def test_verified_restore_naming_the_store_and_digest_scores_zero(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    path, sha256, restore = verified_restore(tmp_path, store)

    result = scan_embedding_portability(path, evidence_sha256=sha256, restore=restore)

    assert (result.score, result.re_embeddable) == (0, "yes")
    assert result.stores[0].restore_link == "verified"
    assert "scanner-verified restore names this store" in result.stores[0].reason


def test_unverified_restore_record_does_not_earn_zero(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    path, sha256 = write_evidence(tmp_path, store)
    restore = RestoreEvidence(supplied=True, verified=False, stores={"support-memory": sha256})

    result = scan_embedding_portability(path, evidence_sha256=sha256, restore=restore)

    assert result.score == 25
    assert result.stores[0].restore_link == "unverified"
    assert "supplied but not verified" in result.stores[0].reason


def test_matching_store_id_with_a_different_digest_never_earns_zero(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    path, sha256 = write_evidence(tmp_path, store)
    restore = RestoreEvidence(supplied=True, verified=True, stores={"support-memory": "c" * 64})

    result = scan_embedding_portability(path, evidence_sha256=sha256, restore=restore)

    assert result.score == 25
    assert result.stores[0].restore_link == "store_id_matched_digest_mismatch"
    assert "commits to a different evidence digest" in result.stores[0].reason


def test_verified_restore_for_another_store_leaves_this_store_unlinked(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    path, sha256, restore = verified_restore(tmp_path, store, store_id="other-store")

    result = scan_embedding_portability(path, evidence_sha256=sha256, restore=restore)

    assert result.score == 25
    assert result.stores[0].restore_link == "absent"


def test_open_weight_model_without_a_weights_digest_is_noted_in_the_reason(
    tmp_path: Path,
) -> None:
    unpinned = scan(tmp_path, single_file_store(tmp_path, model=_model(weights=None)))
    pinned = scan(tmp_path, single_file_store(tmp_path))

    assert unpinned.score == pinned.score == 25
    assert unpinned.stores[0].model.weights_sha256 is None
    assert UNPINNED_WEIGHTS_NOTE in unpinned.stores[0].reason
    assert UNPINNED_WEIGHTS_NOTE not in pinned.stores[0].reason


def test_attested_license_is_reported_and_never_scored(tmp_path: Path) -> None:
    licensed = scan(
        tmp_path,
        single_file_store(tmp_path, model=_model(license_text="Apache-2.0")),
    )
    unlicensed = scan(tmp_path, single_file_store(tmp_path))

    assert licensed.stores[0].model.license == "Apache-2.0"
    assert unlicensed.stores[0].model.license is None
    assert licensed.score == unlicensed.score


def test_sharded_layout_scores_like_a_single_file_store(tmp_path: Path) -> None:
    sharded = scan(tmp_path, sharded_store(tmp_path, per_shard=2))

    assert (sharded.score, sharded.re_embeddable) == (25, "yes")
    store = sharded.stores[0]
    assert (store.layout, store.shard_count, store.vector_count) == ("sharded_jsonl", 2, 3)
    assert store.coverage == 100.0


def test_the_worst_store_governs_the_aggregate_score(tmp_path: Path) -> None:
    result = scan(
        tmp_path,
        single_file_store(tmp_path, store_id="portable"),
        single_file_store(tmp_path, store_id="locked", statuses=("missing",)),
    )

    assert (result.score, result.re_embeddable) == (100, "partial")
    assert "'locked'" in result.reason
    assert {store.score for store in result.stores} == {25, 100}


# --- Attested policy deletions ---------------------------------------------


def test_policy_deletions_are_excluded_from_coverage_and_counted_separately(
    tmp_path: Path,
) -> None:
    result = scan(
        tmp_path,
        single_file_store(
            tmp_path,
            statuses=("retained", "retained", "retained", "deleted_by_policy"),
        ),
    )

    store = result.stores[0]
    assert (store.retained, store.missing, store.deleted_by_policy) == (3, 0, 1)
    assert store.retained + store.missing + store.deleted_by_policy == store.vector_count
    # The deleted record leaves the denominator, so coverage stays complete.
    assert store.coverage == 100.0
    assert (result.score, result.re_embeddable) == (25, "yes")
    assert "1 of 4 records are excluded as attested policy deletions." in store.reason


def test_a_fully_deleted_store_is_not_applicable_and_leaves_aggregation(
    tmp_path: Path,
) -> None:
    result = scan(
        tmp_path,
        single_file_store(tmp_path, statuses=("deleted_by_policy", "deleted_by_policy")),
    )

    store = result.stores[0]
    assert (store.coverage, store.re_embeddable, store.score) == (None, "not_applicable", None)
    assert store.scored is False
    assert (result.score, result.re_embeddable) == (None, "not_applicable")
    assert "no embedding lock-in score applies" in result.reason


def test_a_fully_deleted_store_cannot_hide_a_locked_store(tmp_path: Path) -> None:
    result = scan(
        tmp_path,
        single_file_store(tmp_path, store_id="purged", statuses=("deleted_by_policy",)),
        single_file_store(tmp_path, store_id="locked", statuses=("missing",)),
    )

    assert result.score == 100
    assert "'locked'" in result.reason


def test_majority_deletion_warns_that_coverage_rests_on_attested_deletions(
    tmp_path: Path,
) -> None:
    majority = scan(
        tmp_path,
        single_file_store(
            tmp_path,
            statuses=("retained", "deleted_by_policy", "deleted_by_policy"),
        ),
    )
    half = scan(
        tmp_path,
        single_file_store(
            tmp_path,
            statuses=("retained", "retained", "deleted_by_policy", "deleted_by_policy"),
        ),
    )

    assert ATTESTED_DELETION_WARNING in majority.stores[0].warnings
    # Exactly half is not a majority, so no warning is raised.
    assert half.stores[0].warnings == ()
    # The warning is advisory: both stores still have complete coverage.
    assert majority.stores[0].score == half.stores[0].score == 25


def test_no_warning_when_nothing_was_deleted(tmp_path: Path) -> None:
    result = scan(tmp_path, single_file_store(tmp_path))

    assert result.stores[0].warnings == ()
    assert result.stores[0].findings == ()


def test_vectors_outliving_deleted_sources_are_reported_but_not_scored(
    tmp_path: Path,
) -> None:
    with_deletions = scan(
        tmp_path,
        single_file_store(
            tmp_path,
            statuses=("retained", "retained", "deleted_by_policy", "deleted_by_policy"),
        ),
    )
    without = scan(tmp_path, single_file_store(tmp_path, statuses=("retained", "retained")))

    (finding,) = with_deletions.stores[0].findings
    assert finding.code == RETAINED_VECTOR_FINDING
    assert (finding.store_id, finding.record_count) == ("support-memory", 2)
    assert finding.record_ids == ("r002", "r003")
    assert finding.truncated is False
    # Reporting the finding must not move the score.
    assert with_deletions.score == without.score == 25


def test_the_retained_vector_finding_caps_the_record_ids_it_lists(tmp_path: Path) -> None:
    count = FINDING_RECORD_CAP + 5
    result = scan(
        tmp_path,
        single_file_store(tmp_path, statuses=("retained",) + ("deleted_by_policy",) * count),
    )

    (finding,) = result.stores[0].findings
    assert finding.record_count == count
    assert len(finding.record_ids) == FINDING_RECORD_CAP
    assert finding.truncated is True


# --- Rejected evidence -----------------------------------------------------


def test_evidence_differing_from_its_declared_digest_is_rejected(tmp_path: Path) -> None:
    path, _ = write_evidence(tmp_path, single_file_store(tmp_path))

    with pytest.raises(ValueError, match="differs from its declared SHA-256"):
        scan_embedding_portability(path, evidence_sha256="a" * 64)


def test_another_schema_version_is_rejected(tmp_path: Path) -> None:
    document = {"schema_version": "embedding-portability.v2", "stores": []}
    path = tmp_path / "embeddings.json"
    sha256 = _write(path, json.dumps(document).encode())

    with pytest.raises(ValueError, match=r"embedding-portability\.v1"):
        scan_embedding_portability(path, evidence_sha256=sha256)


def test_unexpected_top_level_fields_are_rejected(tmp_path: Path) -> None:
    document = {
        "schema_version": SCHEMA_VERSION,
        "stores": [single_file_store(tmp_path)],
        "note": "extra",
    }
    path = tmp_path / "embeddings.json"
    sha256 = _write(path, json.dumps(document).encode())

    with pytest.raises(ValueError, match="Unexpected fields in embedding evidence"):
        scan_embedding_portability(path, evidence_sha256=sha256)


def test_duplicate_store_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Duplicate store_id"):
        scan(tmp_path, single_file_store(tmp_path), single_file_store(tmp_path))


def test_duplicate_json_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "embeddings.json"
    data = b'{"schema_version":"embedding-portability.v1","stores":[],"stores":[]}'
    sha256 = _write(path, data)

    with pytest.raises(ValueError, match="Duplicate JSON field"):
        scan_embedding_portability(path, evidence_sha256=sha256)


def test_an_empty_store_list_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        scan(tmp_path)


def test_a_corrupted_shard_rejects_the_whole_store(tmp_path: Path) -> None:
    store = sharded_store(tmp_path, statuses=("retained",) * 4, per_shard=2)
    shard = tmp_path / str(store["vectors"]["shards"][1]["path"])
    shard.write_bytes(shard.read_bytes().replace(b"r003", b"r009"))

    with pytest.raises(ValueError, match="Shard file differs from its declared SHA-256"):
        scan(tmp_path, store)


def test_a_shard_record_count_that_does_not_match_is_rejected(tmp_path: Path) -> None:
    store = sharded_store(tmp_path, statuses=("retained", "retained"), per_shard=2)
    store["vectors"]["shards"][0]["record_count"] = 1

    with pytest.raises(ValueError, match="more records than it declares"):
        scan(tmp_path, store)


def test_an_oversized_shard_line_is_rejected(tmp_path: Path) -> None:
    store = sharded_store(tmp_path, statuses=("retained",), per_shard=1)
    shard = tmp_path / str(store["vectors"]["shards"][0]["path"])
    data = shard.read_bytes().rstrip(b"\n") + b" " * (MAX_SHARD_LINE_BYTES + 1) + b"\n"
    store["vectors"]["shards"][0]["sha256"] = _write(shard, data)

    with pytest.raises(ValueError, match="exceeds the line size limit"):
        scan(tmp_path, store)


def test_retained_source_text_differing_from_its_declared_digest_is_rejected(
    tmp_path: Path,
) -> None:
    store = single_file_store(tmp_path)
    source = tmp_path / str(store["vectors"]["records"][0]["source_text"]["path"])
    source.write_bytes(b"rewritten after the digest was taken\n")

    with pytest.raises(ValueError, match="source text differs from its declared SHA-256"):
        scan(tmp_path, store)


def test_retained_source_text_that_no_longer_matches_the_vector_record_is_rejected(
    tmp_path: Path,
) -> None:
    store = single_file_store(tmp_path)
    record = store["vectors"]["records"][0]
    source = tmp_path / str(record["source_text"]["path"])
    data = b"a different source text\n"
    # The evidence is internally consistent about the file, but the text no
    # longer matches what produced the vector, so coverage would be a fiction.
    record["source_text"]["sha256"] = _write(source, data)

    with pytest.raises(ValueError, match="digest recorded with the vector"):
        scan(tmp_path, store)


def test_a_vector_file_differing_from_its_declared_digest_is_rejected(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    vector = tmp_path / str(store["vectors"]["records"][0]["vector"]["path"])
    vector.write_bytes(b'{"values":[9.0,9.0,9.0,9.0]}')

    with pytest.raises(ValueError, match="Vector file differs from its declared SHA-256"):
        scan(tmp_path, store)


def test_a_vector_whose_dimension_differs_from_the_declaration_is_rejected(
    tmp_path: Path,
) -> None:
    store = single_file_store(tmp_path)
    record = store["vectors"]["records"][0]
    vector = tmp_path / str(record["vector"]["path"])
    record["vector"]["sha256"] = _write(vector, b'{"values":[0.1,0.2]}')

    with pytest.raises(ValueError, match="dimension differs from the store declaration"):
        scan(tmp_path, store)


def test_a_non_finite_vector_value_is_rejected(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    record = store["vectors"]["records"][0]
    vector = tmp_path / str(record["vector"]["path"])
    record["vector"]["sha256"] = _write(vector, b'{"values":[NaN,0.1,0.2,0.3]}')

    with pytest.raises(ValueError, match="Non-finite JSON number"):
        scan(tmp_path, store)


@pytest.mark.parametrize("availability", ("self_hostable", "", "OPEN_WEIGHT_SELF_HOSTABLE"))
def test_an_unrecognized_model_availability_is_rejected(tmp_path: Path, availability: str) -> None:
    store = single_file_store(tmp_path)
    store["embedding_model"]["availability"] = availability

    with pytest.raises(ValueError, match="availability"):
        scan(tmp_path, store)


def test_model_availability_claimed_as_verified_is_rejected(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    store["embedding_model"]["availability_verification"] = "verified"

    with pytest.raises(ValueError, match="must be labeled attested"):
        scan(tmp_path, store)


def test_an_unknown_source_text_status_is_rejected(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    store["vectors"]["records"][0]["source_text"] = {"status": "archived"}

    with pytest.raises(ValueError, match="Invalid source_text status"):
        scan(tmp_path, store)


def test_a_source_text_entry_without_a_vector_record_is_rejected(tmp_path: Path) -> None:
    store = sharded_store(tmp_path, statuses=("retained",), per_shard=1)
    store["vectors"]["source_text"].append(
        {"record_id": "ghost", "source_text": {"status": "missing"}}
    )

    with pytest.raises(ValueError, match="no matching vector record"):
        scan(tmp_path, store)


def test_a_vector_record_without_a_source_text_entry_is_rejected(tmp_path: Path) -> None:
    store = sharded_store(tmp_path, statuses=("retained", "retained"), per_shard=2)
    store["vectors"]["source_text"].pop()

    with pytest.raises(ValueError, match="requires one source_text entry"):
        scan(tmp_path, store)


def test_duplicate_record_ids_are_rejected(tmp_path: Path) -> None:
    store = single_file_store(tmp_path)
    store["vectors"]["records"][1]["record_id"] = store["vectors"]["records"][0]["record_id"]

    with pytest.raises(ValueError, match="Duplicate record_id"):
        scan(tmp_path, store)


@pytest.mark.parametrize("name", ("../outside.json", "/etc/passwd", "a//b.json", "NUL.json"))
def test_non_portable_evidence_paths_are_rejected(tmp_path: Path, name: str) -> None:
    store = single_file_store(tmp_path)
    store["vectors"]["records"][0]["vector"]["path"] = name

    with pytest.raises(ValueError, match="portable project-relative evidence path"):
        scan(tmp_path, store)


def test_a_linked_evidence_document_is_rejected(tmp_path: Path) -> None:
    path, sha256 = write_evidence(tmp_path, single_file_store(tmp_path))
    alias = tmp_path / "alias.json"
    try:
        alias.symlink_to(path)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(ValueError, match="Linked"):
        scan_embedding_portability(alias, evidence_sha256=sha256)


# --- Boundaries ------------------------------------------------------------


def test_scanning_never_touches_the_network(tmp_path: Path, no_network: None) -> None:
    result = scan(tmp_path, single_file_store(tmp_path), restore=None)

    assert result.score == 25


def test_the_report_carries_no_source_text_or_vector_values(tmp_path: Path) -> None:
    sentinel = 0.86421357
    store = single_file_store(tmp_path, statuses=("retained", "deleted_by_policy"))
    record = store["vectors"]["records"][0]
    vector = tmp_path / str(record["vector"]["path"])
    record["vector"]["sha256"] = _write(
        vector, json.dumps({"values": [sentinel] * DIMENSIONS}).encode()
    )

    result = scan(tmp_path, store)

    rendered = repr(result)
    assert "source text for" not in rendered
    assert str(sentinel) not in rendered


def test_the_packaged_schema_matches_the_scanner_contract(tmp_path: Path) -> None:
    schema = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/embedding-portability.schema.json")
        .read_text(encoding="utf-8")
    )
    path, _ = write_evidence(tmp_path, single_file_store(tmp_path), sharded_store(tmp_path))
    document = json.loads(path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert set(schema["required"]) == set(document)
    assert schema["$defs"]["embedding_model"]["properties"]["availability_verification"] == {
        "description": ("Availability cannot be re-run offline, so this must say attested."),
        "const": "attested",
    }
    assert set(schema["$defs"]["store"]["required"]) == set(document["stores"][0])
