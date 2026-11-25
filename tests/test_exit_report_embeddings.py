"""The embeddings vector row and the per-store section it produces."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_infrastructure_suite.cli import app
from ai_infrastructure_suite.exit_report import build_exit_report, render_markdown

EXAMPLE = Path(__file__).parents[1] / "docs/examples/embedding-portability/manifest.json"


def _embedding_manifest(root: Path, statuses: tuple[str, ...]) -> Path:
    """Build an embedding inventory manifest with the given source statuses."""
    records = []
    for index, status in enumerate(statuses):
        record_id = f"r{index:03d}"
        text = f"source {record_id}\n".encode()
        source_digest = hashlib.sha256(text).hexdigest()
        vector = json.dumps({"values": [0.25, 0.5]}).encode()
        (root / f"{record_id}.json").write_bytes(vector)
        source: dict[str, object] = {"status": status}
        if status == "retained":
            (root / f"{record_id}.txt").write_bytes(text)
            source = {"status": "retained", "path": f"{record_id}.txt", "sha256": source_digest}
        records.append(
            {
                "record_id": record_id,
                "vector": {
                    "path": f"{record_id}.json",
                    "sha256": hashlib.sha256(vector).hexdigest(),
                },
                "source_text_sha256_in_vector_record": source_digest,
                "source_text": source,
            }
        )
    evidence = {
        "schema_version": "embedding-portability.v1",
        "stores": [
            {
                "store_id": "support-memory",
                "embedding_model": {
                    "id": "example-embed",
                    "revision": "1",
                    "dimensions": 2,
                    "availability": "open_weight_self_hostable",
                    "availability_verification": "attested",
                },
                "vectors": {"layout": "single_file", "records": records},
            }
        ],
    }
    raw = json.dumps(evidence).encode()
    (root / "embeddings.json").write_bytes(raw)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "exit-manifest.v1",
                "vectors": {
                    "embeddings": {
                        "state": "embedding_inventory",
                        "path": "embeddings.json",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_the_embedding_inventory_example_is_scanned_and_capped_at_twenty_five() -> None:
    report = build_exit_report(EXAMPLE)
    row = report["vectors"]["embeddings"]

    assert row["state"] == "embedding_inventory"
    assert (row["score"], row["re_embeddable"]) == (25, "yes")
    # Scanner-verified content and attested model claims must be distinguished.
    assert row["verification_level"] == "verified (local evidence)"
    assert row["attested_claims"] == [
        "model availability",
        "model weights digest",
        "model license",
    ]
    assert "Attested, not re-run:" in row["reason"]
    (store,) = report["embedding_stores"]
    assert (store["store_id"], store["coverage_pct"]) == ("support-memory", 100.0)
    assert (
        store["retained"] + store["missing"] + store["deleted_by_policy"] == (store["vector_count"])
    )
    assert store["availability_verification"] == "attested"
    assert store["deleted_by_policy_verification"] == "attested"
    assert store["restore_link"] == "absent"


def test_the_embedding_inventory_renders_an_attested_labelled_markdown_section() -> None:
    markdown = render_markdown(build_exit_report(EXAMPLE))

    assert "## Embedding stores" in markdown
    assert "| embeddings | 25 |" in markdown
    assert "open_weight_self_hostable (attested)" in markdown
    assert "Deleted (attested)" in markdown
    assert "operator-attested and" in markdown
    # Never print the evidence itself.
    assert "Verify identity" not in markdown
    assert "0.125" not in markdown


def test_warnings_and_findings_reach_both_outputs(tmp_path: Path) -> None:
    manifest = _embedding_manifest(tmp_path, ("retained", "deleted_by_policy", "deleted_by_policy"))

    report = build_exit_report(manifest)
    markdown = render_markdown(report)

    (store,) = report["embedding_stores"]
    assert store["coverage_pct"] == 100.0
    assert store["score"] == 25
    assert "attested deletions" in store["warnings"][0]
    (finding,) = store["findings"]
    assert finding["code"] == "vector retained after source deletion"
    assert finding["record_count"] == 2
    assert finding["record_ids_truncated"] is False
    assert "policy deletions" in report["vectors"]["embeddings"]["attested_claims"]
    assert "### support-memory" in markdown
    assert "warning: Coverage depends on attested deletions" in markdown
    assert "finding: vector retained after source deletion (2 record(s))" in markdown


def test_a_fully_deleted_store_reports_no_embedding_score(tmp_path: Path) -> None:
    manifest = _embedding_manifest(tmp_path, ("deleted_by_policy",))

    report = build_exit_report(manifest)
    markdown = render_markdown(report)

    row = report["vectors"]["embeddings"]
    assert (row["score"], row["re_embeddable"]) == (None, "not_applicable")
    assert "| embeddings | N/A |" in markdown
    assert report["embedding_stores"][0]["coverage_pct"] is None


def test_an_embedding_inventory_is_rejected_on_another_vector(tmp_path: Path) -> None:
    manifest = _embedding_manifest(tmp_path, ("retained",))
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["vectors"]["memory"] = document["vectors"].pop("embeddings")
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="Only the embeddings vector"):
        build_exit_report(manifest)


def test_the_embedding_report_matches_the_packaged_exit_report_schema() -> None:
    schema = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/exit-report.v2.schema.json")
        .read_text(encoding="utf-8")
    )
    report = build_exit_report(EXAMPLE)
    row = report["vectors"]["embeddings"]
    vector_schema = schema["$defs"]["vector"]["oneOf"][1]
    store_schema = schema["$defs"]["embedding_store"]

    assert "embedding_stores" in schema["properties"]
    assert "embedding_inventory" in vector_schema["properties"]["state"]["enum"]
    assert row["verification_level"] in vector_schema["properties"]["verification_level"]["enum"]
    # Every emitted field must be declared, and every required field emitted.
    assert not set(row) - set(vector_schema["properties"])
    assert set(vector_schema["required"]) <= set(row)
    for store in report["embedding_stores"]:
        assert not set(store) - set(store_schema["properties"])
        assert set(store_schema["required"]) <= set(store)


def test_the_manifest_schema_declares_the_embedding_inventory_state() -> None:
    schema = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/exit-manifest.schema.json")
        .read_text(encoding="utf-8")
    )
    states = schema["properties"]["vectors"]["additionalProperties"]["properties"]["state"]["enum"]

    assert "embedding_inventory" in states


def test_the_embedding_cli_output_is_deterministic_in_both_formats() -> None:
    runner = CliRunner()
    args = ["exit-report", str(EXAMPLE)]
    first = runner.invoke(app, [*args, "--json"])
    second = runner.invoke(app, [*args, "--json"])
    markdown = runner.invoke(app, args)

    assert first.exit_code == 0, first.exception
    assert first.stdout == second.stdout
    assert markdown.exit_code == 0, markdown.exception
    payload = json.loads(first.stdout)
    assert payload["vectors"]["embeddings"]["score"] == 25
    assert len(payload["embedding_stores"]) == 1
    assert "## Embedding stores" in markdown.stdout
