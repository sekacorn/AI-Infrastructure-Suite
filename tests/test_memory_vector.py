"""The ``memory`` vector scored from ``memory_inventory`` (Phase 4a, checkpoint 2).

Builds manifests on disk from the shipped worked example, so every report here
also passes through the schema net in conftest.py.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from ai_infrastructure_suite.agent_memory import (
    AgentMemoryError,
    load_agent_memory,
    parse_memory_import_report,
)
from ai_infrastructure_suite.exit_report import VECTORS, build_exit_report, render_markdown

DOCS = Path(__file__).resolve().parents[1] / "docs" / "examples"
WORKED = DOCS / "memory-inventory"
AGENT_MEMORY = DOCS / "agent-memory"
SNAPSHOT_0_2_0B2 = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "exit-report.v2.schema.0.2.0b2.json").read_text(
        "utf-8"
    )
)
REPORT_SCHEMA = json.loads(
    files("ai_infrastructure_suite").joinpath("data/exit-report.v2.schema.json").read_text("utf-8")
)
MANIFEST_SCHEMA = json.loads(
    files("ai_infrastructure_suite").joinpath("data/exit-manifest.schema.json").read_text("utf-8")
)
IMPORT_SCHEMA = json.loads(
    files("ai_infrastructure_suite")
    .joinpath("data/agent-memory-import.schema.json")
    .read_text("utf-8")
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, document: object) -> str:
    data = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return _sha(data)


class Scenario:
    """A writable copy of the worked example; helpers keep every digest consistent."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "memory-inventory"
        shutil.copytree(WORKED, self.root)
        self.export_path = self.root / "export" / "agent-memory.json"
        self.export: dict[str, Any] = json.loads(self.export_path.read_text("utf-8"))
        self.report: dict[str, Any] | None = json.loads(
            (self.root / "import-report.json").read_text("utf-8")
        )
        self.entry_extra: dict[str, Any] = {}

    def run(self, vector: str = "memory", *, schema_valid: bool | None = None) -> dict[str, Any]:
        export_sha = _write_json(self.export_path, self.export)
        entry: dict[str, Any] = {
            "state": "memory_inventory",
            "path": "export/agent-memory.json",
            "sha256": export_sha,
        }
        if self.report is not None:
            if self.report["source_export"]["sha256"] == "BIND":
                self.report["source_export"]["sha256"] = export_sha
            report_sha = _write_json(self.root / "import-report.json", self.report)
            entry["import_report"] = {"path": "import-report.json", "sha256": report_sha}
        entry.update(self.entry_extra)
        manifest = {"schema_version": "exit-manifest.v1", "vectors": {vector: entry}}
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        if schema_valid is None:
            schema_valid = vector == "memory" and set(entry) <= {
                "state",
                "path",
                "sha256",
                "import_report",
            }
        assert jsonschema.Draft202012Validator(MANIFEST_SCHEMA).is_valid(manifest) is schema_valid
        return build_exit_report(path)

    def rebind(self) -> None:
        """Point the import report at whatever the export's digest will be."""
        assert self.report is not None
        self.report["source_export"]["sha256"] = "BIND"


def _row(report: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = report["vectors"]["memory"]
    return row


# --- the worked example --------------------------------------------------------------------


def test_the_worked_example_reproduces_its_committed_report() -> None:
    report = build_exit_report(WORKED / "manifest.json")
    committed = json.loads((WORKED / "report.json").read_text("utf-8"))
    for document in (report, committed):
        document.pop("components")
        document.pop("suite_version")
    assert report == committed
    assert _row(report)["score"] == 25
    assert _row(report)["verification_level"] == "verified (memory export)"


def test_the_worked_example_renders_the_memory_section() -> None:
    markdown = render_markdown(build_exit_report(WORKED / "manifest.json"))
    assert "## Memory stores" in markdown
    assert "| kb-memory | retrieval | sharded_jsonl (2) | 5 | 0 | 0 | 5 of 5 | 0 | 25 |" in markdown


def test_the_worked_example_digests_match_the_checked_out_bytes() -> None:
    manifest = json.loads((WORKED / "manifest.json").read_text("utf-8"))["vectors"]["memory"]
    assert _sha((WORKED / manifest["path"]).read_bytes()) == manifest["sha256"]
    report = manifest["import_report"]
    assert _sha((WORKED / report["path"]).read_bytes()) == report["sha256"]


def test_the_example_import_report_matches_its_schema() -> None:
    document = json.loads((WORKED / "import-report.json").read_text("utf-8"))
    jsonschema.Draft202012Validator.check_schema(IMPORT_SCHEMA)
    assert list(jsonschema.Draft202012Validator(IMPORT_SCHEMA).iter_errors(document)) == []


# --- compatibility with 0.2.0b2 (decision D1) ---------------------------------------------


@pytest.mark.parametrize(
    "example", ["exit-report", "embedding-portability", "model-providers", "model-providers-local"]
)
def test_a_report_without_memory_inventory_still_validates_against_0_2_0b2(example: str) -> None:
    report = build_exit_report(DOCS / example / "manifest.json")
    assert "memory_stores" not in report
    assert list(jsonschema.Draft202012Validator(SNAPSHOT_0_2_0B2).iter_errors(report)) == []


def test_a_report_with_memory_inventory_needs_this_schema() -> None:
    report = build_exit_report(WORKED / "manifest.json")
    assert not jsonschema.Draft202012Validator(SNAPSHOT_0_2_0B2).is_valid(report)
    assert jsonschema.Draft202012Validator(REPORT_SCHEMA).is_valid(report)


def test_the_vector_list_is_unchanged() -> None:
    assert len(VECTORS) == 12 and "memory" in VECTORS


# --- the ladder ------------------------------------------------------------------------


def test_without_an_import_report_the_store_is_50_and_says_why(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.report = None
    report = scenario.run()
    assert _row(report)["score"] == 50
    assert "no import report is bound to this export" in _row(report)["reason"]
    assert "that the import ran at the destination" not in _row(report)["attested_claims"]


def test_an_import_that_refused_items_is_50(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    assert scenario.report is not None
    scenario.report["stores"][0].update(imported=3, refused=2)
    report = scenario.run()
    assert _row(report)["score"] == 50
    assert "the destination refused 2 item(s)" in _row(report)["reason"]


def test_an_item_without_a_vector_is_50(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    shard = scenario.root / "export" / "items" / "0001.jsonl"
    extra = {
        "item_id": "kb-0006",
        "kind": "document",
        "text": "A sixth fact with no vector.",
        "content_sha256": hashlib.sha256(b"A sixth fact with no vector.").hexdigest(),
        "metadata": {},
        "created_at": None,
        "status": "active",
        "provenance": None,
    }
    data = shard.read_bytes() + (json.dumps(extra, separators=(",", ":")) + "\n").encode()
    shard.write_bytes(data)
    store = scenario.export["stores"][0]
    store["items"]["shards"][1].update(sha256=_sha(data), record_count=3)
    store["counts"]["active"] = 6
    assert scenario.report is not None
    scenario.report["stores"][0].update(received=6, imported=6)
    scenario.rebind()
    report = scenario.run()
    assert _row(report)["score"] == 50
    assert "1 item(s) have no vector in the linked inventory" in _row(report)["reason"]
    assert report["memory_stores"][0]["embeddings"]["items_without_vector"] == 1


def test_a_store_without_vectors_can_reach_25(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.export["stores"][0]["embeddings"] = None
    scenario.rebind()
    report = scenario.run()
    assert _row(report)["score"] == 25
    assert report["memory_stores"][0]["embeddings"] is None


def test_the_worst_store_governs(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    single = json.loads((AGENT_MEMORY / "minimal" / "agent-memory.json").read_text("utf-8"))
    scenario.export["stores"].append(single["stores"][0])
    assert scenario.report is not None
    scenario.report["stores"].append(
        {
            "source_store_id": "notes",
            "destination_store_id": "notes",
            "received": 1,
            "imported": 0,
            "unchanged": 0,
            "refused": 1,
            "tombstones_applied": 0,
        }
    )
    scenario.rebind()
    report = scenario.run()
    scores = {store["store_id"]: store["score"] for store in report["memory_stores"]}
    assert scores == {"kb-memory": 25, "notes": 50}
    assert _row(report)["score"] == 50
    assert "notes scores 50" in _row(report)["reason"]


def test_nothing_ever_scores_0(tmp_path: Path) -> None:
    for variant in range(3):
        scenario = Scenario(tmp_path / str(variant))
        if variant == 1:
            scenario.report = None
        if variant == 2:
            scenario.export["stores"][0]["embeddings"] = None
            scenario.rebind()
        report = scenario.run()
        assert _row(report)["score"] in (25, 50)
        assert all(store["score"] in (25, 50) for store in report["memory_stores"])


# --- attested claims ---------------------------------------------------------------------


def test_attested_claims_follow_what_the_export_contains(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.export["export"]["redaction_profile"] = None
    scenario.export["stores"][0]["tombstones"] = [
        {"item_id": "kb-0099", "deleted_at": "2026-11-30T08:12:00Z"}
    ]
    scenario.export["stores"][0]["counts"]["deleted_by_policy"] = 1
    assert scenario.report is not None
    scenario.report["stores"][0]["tombstones_applied"] = 1
    scenario.rebind()
    row = _row(scenario.run())
    assert "that policy deletions happened" in row["attested_claims"]
    assert "that the redaction profile was adequate" not in row["attested_claims"]
    assert "No redaction profile was applied at export." in row["reason"]
    for claim in row["attested_claims"]:
        assert claim in row["reason"]


def test_a_vector_kept_for_a_deleted_item_is_reported(tmp_path: Path) -> None:
    # Remove an item but keep its vector: tombstone it, and the inventory still has it.
    scenario = Scenario(tmp_path)
    shard = scenario.root / "export" / "items" / "0001.jsonl"
    lines = shard.read_text("utf-8").splitlines()
    kept = [line for line in lines if '"kb-0005"' not in line]
    data = ("\n".join(kept) + "\n").encode()
    shard.write_bytes(data)
    store = scenario.export["stores"][0]
    store["items"]["shards"][1].update(sha256=_sha(data), record_count=len(kept))
    store["counts"].update(active=4, deleted_by_policy=1)
    store["tombstones"] = [{"item_id": "kb-0005", "deleted_at": "2026-11-30T08:12:00Z"}]
    assert scenario.report is not None
    scenario.report["stores"][0].update(received=4, imported=4, tombstones_applied=1)
    scenario.rebind()
    report = scenario.run()
    findings = report["memory_stores"][0]["embeddings"]["findings"]
    assert findings == [{"finding": "vector retained after item deletion", "count": 1}]
    assert _row(report)["score"] == 25


# --- refused, never scored ------------------------------------------------------------------


def test_an_import_report_for_another_export_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    assert scenario.report is not None
    scenario.report["source_export"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="different export digest"):
        scenario.run()


def test_an_import_report_naming_another_export_id_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    assert scenario.report is not None
    scenario.report["source_export"]["export_id"] = "exp-other"
    scenario.rebind()
    with pytest.raises(ValueError, match="different export"):
        scenario.run()


@pytest.mark.parametrize(
    "change",
    [{"received": 4, "imported": 4}, {"tombstones_applied": 1}],
    ids=["item count", "tombstone count"],
)
def test_an_import_report_whose_counts_differ_is_refused(tmp_path: Path, change: Any) -> None:
    scenario = Scenario(tmp_path)
    assert scenario.report is not None
    scenario.report["stores"][0].update(change)
    scenario.rebind()
    with pytest.raises(ValueError, match="counts do not match"):
        scenario.run()


def test_an_import_report_covering_other_stores_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    assert scenario.report is not None
    scenario.report["stores"][0]["source_store_id"] = "elsewhere"
    scenario.rebind()
    with pytest.raises(ValueError, match="exactly this export's stores"):
        scenario.run()


def test_an_item_whose_vector_was_built_from_other_text_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    inventory = scenario.root / "export" / "embeddings" / "embedding-portability.json"
    source = scenario.root / "export" / "embeddings" / "sources" / "kb-0001.txt"
    vectors = scenario.root / "export" / "embeddings" / "vectors" / "0000.jsonl"
    text = "A different sentence than the memory item holds."
    source.write_bytes(text.encode())
    rows = [json.loads(line) for line in vectors.read_text("utf-8").splitlines()]
    rows[0]["source_text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    vdata = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()
    vectors.write_bytes(vdata)
    doc = json.loads(inventory.read_text("utf-8"))
    vstore = doc["stores"][0]["vectors"]
    vstore["shards"][0]["sha256"] = _sha(vdata)
    vstore["source_text"][0]["source_text"]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
    scenario.export["stores"][0]["embeddings"]["inventory"]["sha256"] = _write_json(inventory, doc)
    scenario.rebind()
    with pytest.raises(ValueError, match="text its vector was built from"):
        scenario.run()


def test_an_inventory_naming_no_such_store_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.export["stores"][0]["embeddings"]["inventory_store_id"] = "not-there"
    scenario.rebind()
    with pytest.raises(ValueError, match="names no such store"):
        scenario.run()


def test_a_wrong_inventory_digest_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.export["stores"][0]["embeddings"]["inventory"]["sha256"] = "0" * 64
    scenario.rebind()
    with pytest.raises(ValueError):
        scenario.run()


def test_memory_inventory_on_another_vector_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    with pytest.raises(ValueError, match="Only the memory vector"):
        scenario.run(vector="prompts")


def test_an_unknown_entry_field_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.entry_extra = {"notes": "x"}
    with pytest.raises(ValueError, match="takes state, path, sha256"):
        scenario.run()


def test_a_bad_import_reference_is_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.report = None
    scenario.entry_extra = {"import_report": {"path": "../import-report.json", "sha256": "0" * 64}}
    with pytest.raises(ValueError):
        scenario.run(schema_valid=False)


# --- the import report reader ------------------------------------------------------------


def _import_report() -> dict[str, Any]:
    document: dict[str, Any] = json.loads((WORKED / "import-report.json").read_text("utf-8"))
    return document


@pytest.mark.parametrize(
    ("mutate", "schema_rejects"),
    [
        (lambda d: d.__setitem__("schema_version", "agent-memory-import.v2"), True),
        (lambda d: d.__setitem__("extra", 1), True),
        (lambda d: d["stores"][0].update(imported=4), False),
        (lambda d: d["stores"].append(dict(d["stores"][0])), False),
        (lambda d: d["stores"][0].update(refused=-1, received=4), True),
        (lambda d: d["import"]["destination"].update(dsn="postgresql://u:p@h/db"), True),
        (lambda d: d["import"].update(imported_at="later"), True),
        (lambda d: d.__setitem__("stores", []), True),
    ],
    ids=[
        "wrong version",
        "unknown key",
        "counts do not add up",
        "store listed twice",
        "negative count",
        "locator in destination",
        "bad time",
        "no stores",
    ],
)
def test_the_import_report_reader_refuses(mutate: Any, schema_rejects: bool) -> None:
    document = _import_report()
    mutate(document)
    with pytest.raises(AgentMemoryError):
        parse_memory_import_report(json.dumps(document).encode())
    assert jsonschema.Draft202012Validator(IMPORT_SCHEMA).is_valid(document) is not schema_rejects


# --- the canonical digest ------------------------------------------------------------------


def test_the_canonical_digest_is_independent_of_layout(tmp_path: Path) -> None:
    sharded = load_agent_memory(AGENT_MEMORY / "retrieval-sharded" / "agent-memory.json")
    root = AGENT_MEMORY / "retrieval-sharded"
    items = [
        json.loads(line)
        for shard in sorted((root / "items").glob("*.jsonl"))
        for line in shard.read_text("utf-8").splitlines()
    ]
    document = json.loads((root / "agent-memory.json").read_text("utf-8"))
    document["stores"][0]["items"] = {"layout": "single_file", "records": items}
    document["stores"][0]["embeddings"] = None
    path = tmp_path / "agent-memory.json"
    path.write_text(json.dumps(document, indent=4), encoding="utf-8")
    single = load_agent_memory(path)
    assert single.stores[0].layout == "single_file"
    assert single.stores[0].canonical_sha256 == sharded.stores[0].canonical_sha256
