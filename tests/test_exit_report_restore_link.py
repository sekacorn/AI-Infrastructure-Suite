"""Linking a verified restore to an embedding inventory, end to end.

These tests drive `build_exit_report(..., verify_restores=True)` over a full
synthetic restore run plus an embedding inventory, which is the only path on
which an embeddings store can reach a lock-in score of 0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from tests.test_restore_verification import make_restore_evidence

from ai_infrastructure_suite.exit_report import build_exit_report, render_markdown

STORE_ID = "support-memory"
RESTORED_WITHOUT_EMBEDDINGS = (
    "policies",
    "prompts",
    "memory",
    "audit_history",
    "cost_history",
    "ontology",
)


def _write_json(path: Path, value: object) -> bytes:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _embedding_evidence(root: Path) -> str:
    """Write a fully covered, self-hostable inventory and return its digest."""
    text = b"Verify identity, then send a reset link.\n"
    source_digest = hashlib.sha256(text).hexdigest()
    (root / "evidence/sources").mkdir(parents=True, exist_ok=True)
    (root / "evidence/sources/r001.txt").write_bytes(text)
    vector = (json.dumps({"values": [1.0, 0.0, 0.25, 0.5]}) + "\n").encode()
    (root / "evidence/vectors").mkdir(parents=True, exist_ok=True)
    (root / "evidence/vectors/r001.json").write_bytes(vector)
    evidence = {
        "schema_version": "embedding-portability.v1",
        "stores": [
            {
                "store_id": STORE_ID,
                "embedding_model": {
                    "id": "example-embed",
                    "revision": "2026-02-01",
                    "dimensions": 4,
                    "availability": "open_weight_self_hostable",
                    "availability_verification": "attested",
                    "weights_sha256": "b" * 64,
                },
                "vectors": {
                    "layout": "single_file",
                    "records": [
                        {
                            "record_id": "r001",
                            "vector": {
                                "path": "vectors/r001.json",
                                "sha256": hashlib.sha256(vector).hexdigest(),
                            },
                            "source_text_sha256_in_vector_record": source_digest,
                            "source_text": {
                                "status": "retained",
                                "path": "sources/r001.txt",
                                "sha256": source_digest,
                            },
                        }
                    ],
                },
            }
        ],
    }
    data = _write_json(root / "evidence/embedding-portability.json", evidence)
    return hashlib.sha256(data).hexdigest()


def _linked_evidence(root: Path, *, committed_digest: str | None = None) -> Path:
    """Build a restore run plus an inventory the summary commits to.

    ``committed_digest`` overrides what the summary commits to, which is how
    the digest-mismatch case is built.
    """
    make_restore_evidence(root)
    evidence_digest = _embedding_evidence(root)
    summary_path = root / "restore-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["embedding_stores"] = [
        {
            "store_id": STORE_ID,
            "store_evidence_sha256": committed_digest or evidence_digest,
        }
    ]
    summary_data = _write_json(summary_path, summary)
    restore_entry = {
        "state": "restore_tested",
        "path": "restore-summary.json",
        "sha256": hashlib.sha256(summary_data).hexdigest(),
    }
    manifest_path = root / "exit-manifest.json"
    vectors: dict[str, object] = dict.fromkeys(RESTORED_WITHOUT_EMBEDDINGS, restore_entry)
    vectors["embeddings"] = {
        "state": "embedding_inventory",
        "path": "evidence/embedding-portability.json",
        "sha256": evidence_digest,
    }
    _write_json(manifest_path, {"schema_version": "exit-manifest.v1", "vectors": vectors})
    return manifest_path


def test_a_verified_restore_committing_to_the_digest_reaches_zero(tmp_path: Path) -> None:
    manifest = _linked_evidence(tmp_path)

    report = build_exit_report(manifest, verify_restores=True)
    markdown = render_markdown(report)

    row = report["vectors"]["embeddings"]
    assert (row["score"], row["re_embeddable"]) == (0, "yes")
    assert row["verification_level"] == "verified (local evidence + fake profiles)"
    (store,) = report["embedding_stores"]
    assert store["restore_link"] == "verified"
    assert "commits to this evidence digest" in store["reason"]
    assert "| embeddings | 0 |" in markdown


def test_without_verify_restores_the_same_evidence_stays_at_twenty_five(tmp_path: Path) -> None:
    manifest = _linked_evidence(tmp_path)

    report = build_exit_report(manifest)

    row = report["vectors"]["embeddings"]
    assert row["score"] == 25
    assert row["verification_level"] == "verified (local evidence)"
    # The restore record exists but the scanner was not asked to re-run it.
    assert report["embedding_stores"][0]["restore_link"] == "unverified"
    assert "supplied but not verified" in report["embedding_stores"][0]["reason"]


def test_a_committed_digest_that_does_not_match_never_reaches_zero(tmp_path: Path) -> None:
    manifest = _linked_evidence(tmp_path, committed_digest="c" * 64)

    report = build_exit_report(manifest, verify_restores=True)

    assert report["vectors"]["embeddings"]["score"] == 25
    (store,) = report["embedding_stores"]
    assert store["restore_link"] == "store_id_matched_digest_mismatch"
    assert "different evidence digest" in store["reason"]


def test_a_restore_naming_another_store_never_reaches_zero(tmp_path: Path) -> None:
    manifest = _linked_evidence(tmp_path)
    summary_path = tmp_path / "restore-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["embedding_stores"][0]["store_id"] = "some-other-store"
    summary_data = _write_json(summary_path, summary)
    manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
    digest = hashlib.sha256(summary_data).hexdigest()
    for vector in RESTORED_WITHOUT_EMBEDDINGS:
        manifest_document["vectors"][vector]["sha256"] = digest
    _write_json(manifest, manifest_document)

    report = build_exit_report(manifest, verify_restores=True)

    assert report["vectors"]["embeddings"]["score"] == 25
    assert report["embedding_stores"][0]["restore_link"] == "absent"


def test_unattributed_behavior_drift_withholds_the_zero(tmp_path: Path) -> None:
    manifest = _linked_evidence(tmp_path)
    # Break one profile's recorded behavior without touching any digest the
    # restore summary commits to.
    summary = json.loads((tmp_path / "restore-summary.json").read_text(encoding="utf-8"))
    record_path = tmp_path / summary["fanout"][0]["path"]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["migration"]["behavior_gate"]["actual_result_sha256"] = "f" * 64
    record_data = _write_json(record_path, record)
    summary["fanout"][0]["sha256"] = hashlib.sha256(record_data).hexdigest()
    summary_data = _write_json(tmp_path / "restore-summary.json", summary)
    manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
    digest = hashlib.sha256(summary_data).hexdigest()
    for vector in RESTORED_WITHOUT_EMBEDDINGS:
        manifest_document["vectors"][vector]["sha256"] = digest
    _write_json(manifest, manifest_document)

    report = build_exit_report(manifest, verify_restores=True)

    row = report["vectors"]["embeddings"]
    # A score of 0 claims a verified restore and a functional check; drift
    # puts the functional check in question, so the link is not counted. The
    # record still exists, so the link reads unverified rather than absent.
    assert row["score"] == 25
    (store,) = report["embedding_stores"]
    assert store["restore_link"] == "unverified"
    assert "supplied but not verified" in store["reason"]
    assert "unattributed drift in mock-azure" in row["reason"]


def test_disagreeing_restore_summaries_are_rejected(tmp_path: Path) -> None:
    manifest = _linked_evidence(tmp_path)
    # A second restore summary for the same store, committing elsewhere.
    second = tmp_path / "other-summary.json"
    summary = json.loads((tmp_path / "restore-summary.json").read_text(encoding="utf-8"))
    summary["embedding_stores"][0]["store_evidence_sha256"] = "d" * 64
    second_data = _write_json(second, summary)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["vectors"]["memory"] = {
        "state": "restore_tested",
        "path": "other-summary.json",
        "sha256": hashlib.sha256(second_data).hexdigest(),
    }
    _write_json(manifest, document)

    with pytest.raises(ValueError, match="disagree about an embedding store"):
        build_exit_report(manifest, verify_restores=True)
