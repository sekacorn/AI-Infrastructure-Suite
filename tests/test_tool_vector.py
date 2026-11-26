"""The ``tool_connectors`` vector scored from ``tool_inventory`` (Phase 4b, suite checkpoint).

Builds manifests from the shipped worked example, so every report here also
passes through the schema net in conftest.py.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import shutil
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from ai_infrastructure_suite.exit_report import VECTORS, build_exit_report, render_markdown
from ai_infrastructure_suite.tool_inventory import (
    ToolInventoryError,
    parse_tool_approvals,
    parse_tool_inventory,
)
from ai_infrastructure_suite.tool_vector import score_tool

DOCS = Path(__file__).resolve().parents[1] / "docs" / "examples"
WORKED = DOCS / "tool-inventory"
FORMATS = DOCS / "mcp-tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _schema(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(
        files("ai_infrastructure_suite").joinpath(f"data/{name}").read_text("utf-8")
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    return schema


INVENTORY_SCHEMA = _schema("tool-inventory.schema.json")
APPROVALS_SCHEMA = _schema("tool-approvals.schema.json")
MANIFEST_SCHEMA = _schema("exit-manifest.schema.json")
SNAPSHOT_0_2_0B3 = json.loads((FIXTURES / "exit-report.v2.schema.0.2.0b3.json").read_text("utf-8"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, document: object) -> str:
    data = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return _sha(data)


class Scenario:
    """A writable copy of the worked example; every digest is recomputed on run."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "tool-inventory"
        shutil.copytree(WORKED, self.root)
        self.inventory: dict[str, Any] = json.loads(
            (self.root / "tool-inventory.json").read_text("utf-8")
        )
        self.approvals: dict[str, Any] | None = json.loads(
            (self.root / "tool-approvals.json").read_text("utf-8")
        )
        self.entry_extra: dict[str, Any] = {}

    def tool(self, tool_id: str) -> dict[str, Any]:
        return next(t for t in self.inventory["tools"] if t["tool_id"] == tool_id)

    def only(self, *tool_ids: str) -> None:
        self.inventory["tools"] = [t for t in self.inventory["tools"] if t["tool_id"] in tool_ids]

    def run(
        self, vector: str = "tool_connectors", *, schema_valid: bool | None = None
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "state": "tool_inventory",
            "path": "tool-inventory.json",
            "sha256": _write(self.root / "tool-inventory.json", self.inventory),
        }
        if self.approvals is not None:
            entry["approvals"] = {
                "path": "tool-approvals.json",
                "sha256": _write(self.root / "tool-approvals.json", self.approvals),
            }
        entry.update(self.entry_extra)
        manifest = {"schema_version": "exit-manifest.v1", "vectors": {vector: entry}}
        if schema_valid is None:
            schema_valid = vector == "tool_connectors" and set(entry) <= {
                "state",
                "path",
                "sha256",
                "approvals",
            }
        assert jsonschema.Draft202012Validator(MANIFEST_SCHEMA).is_valid(manifest) is schema_valid
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return build_exit_report(path)


def _row(report: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = report["vectors"]["tool_connectors"]
    return row


def _tools(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["tool_id"]: tool for tool in report["tools"]}


# --- the worked example and the formats ----------------------------------------------------


def test_the_worked_example_reproduces_its_committed_report() -> None:
    report = build_exit_report(WORKED / "manifest.json")
    committed = json.loads((WORKED / "report.json").read_text("utf-8"))
    for document in (report, committed):
        document.pop("components")
        document.pop("suite_version")
    assert report == committed
    row = _row(report)
    assert (row["score"], row["verification_level"], row["stale_approvals"]) == (
        75,
        "verified (tool inventory)",
        1,
    )
    assert {t: r["score"] for t, r in _tools(report).items()} == {
        "files__search": 25,
        "files__count": 25,
        "crm__lookup": 75,
        "billing_export": 75,
    }


def test_the_worked_example_renders_the_tools_section() -> None:
    markdown = render_markdown(build_exit_report(WORKED / "manifest.json"))
    assert "## Tools" in markdown
    assert (
        "| crm__lookup | crm | mcp_streamable_http | hosted | mcp "
        "| crm-vendor-api (vendor_only, documented) | approved | 75 |"
    ) in markdown


def test_the_worked_example_digests_match_the_checked_out_bytes() -> None:
    entry = json.loads((WORKED / "manifest.json").read_text("utf-8"))["vectors"]["tool_connectors"]
    assert _sha((WORKED / entry["path"]).read_bytes()) == entry["sha256"]
    assert _sha((WORKED / entry["approvals"]["path"]).read_bytes()) == entry["approvals"]["sha256"]


def test_shipped_documents_meet_their_schemas() -> None:
    jsonschema.validate(
        json.loads((WORKED / "tool-inventory.json").read_text("utf-8")), INVENTORY_SCHEMA
    )
    for path in (WORKED / "tool-approvals.json", FORMATS / "tool-approvals.json"):
        jsonschema.validate(json.loads(path.read_text("utf-8")), APPROVALS_SCHEMA)
        parse_tool_approvals(path.read_bytes())
    servers = tomllib.loads((FORMATS / "mcp-servers.toml").read_text("utf-8"))
    jsonschema.validate(servers, _schema("mcp-servers.schema.json"))
    definition_schema = _schema("tool-definition.schema.json")
    for path in sorted((FORMATS / "tool-definitions").glob("*.json")):
        document = json.loads(path.read_text("utf-8"))
        jsonschema.validate(document, definition_schema)
        assert path.stem == document["definition_sha256"]


def test_a_report_without_tool_inventory_still_meets_the_0_2_0b3_schema(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {}}), encoding="utf-8"
    )
    report = build_exit_report(manifest)
    assert "tools" not in report
    jsonschema.validate(report, SNAPSHOT_0_2_0B3)


def test_a_tool_inventory_report_needs_the_new_schema() -> None:
    report = build_exit_report(WORKED / "manifest.json")
    assert not jsonschema.Draft202012Validator(SNAPSHOT_0_2_0B3).is_valid(report)


# --- scoring -------------------------------------------------------------------------------


def test_approved_tools_on_local_servers_score_25(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search", "files__count")
    row = _row(scenario.run())
    assert row["score"] == 25 and row["stale_approvals"] == 2
    assert "2 approval(s) name tools the inventory does not list" in row["reason"]


def test_without_an_approvals_file_mcp_tools_score_50(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search")
    scenario.approvals = None
    report = scenario.run()
    assert _row(report)["score"] == 50 and "approvals" not in _row(report)
    tool = _tools(report)["files__search"]
    assert (
        tool["approval"] == "no_approvals_file"
        and "no approvals file was supplied" in tool["reason"]
    )


def test_a_changed_hash_is_not_approved(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search")
    scenario.tool("files__search")["definition_sha256"] = "b" * 64
    tool = _tools(scenario.run())["files__search"]
    assert (tool["approval"], tool["score"]) == ("not_approved", 50)
    assert "definition hash is not approved" in tool["reason"]


def test_an_approval_for_another_origin_does_not_apply(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search")
    scenario.tool("files__search")["server_origin"] = "http://127.0.0.1:9999"
    assert _tools(scenario.run())["files__search"]["approval"] == "not_approved"


def test_hosted_and_unapproved_reasons_name_both(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("crm__lookup")
    scenario.tool("crm__lookup")["backend"] = None  # isolate the MCP rules
    scenario.tool("crm__lookup")["definition_sha256"] = "c" * 64
    tool = _tools(scenario.run())["crm__lookup"]
    assert tool["score"] == 50 and tool["hosting"] == "hosted"
    assert "hosted" in tool["reason"] and "not approved" in tool["reason"]


def test_hosting_is_derived_never_declared(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("crm__lookup")
    scenario.tool("crm__lookup")["backend"] = None  # isolate the MCP rules
    scenario.tool("crm__lookup")["server_origin"] = "http://10.1.2.3:8080"
    scenario.approvals["approvals"][0]["server_origin"] = "http://10.1.2.3:8080"  # type: ignore[index]
    tool = _tools(scenario.run())["crm__lookup"]
    assert (tool["hosting"], tool["score"]) == ("local", 25)


def test_stdio_and_in_process_mcp_servers_are_local(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search")
    scenario.tool("files__search").update(transport="mcp_stdio", server_origin=None)
    scenario.approvals["approvals"][3]["server_origin"] = None  # type: ignore[index]
    assert _tools(scenario.run())["files__search"]["score"] == 25


def test_vendor_only_tools_score_75_or_100(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("billing_export")
    scenario.tool("billing_export")["backend"] = None  # the tool's own reconstruction only
    assert _row(scenario.run())["score"] == 75
    scenario.tool("billing_export")["reconstruction"] = "none"
    row = _row(scenario.run())
    assert row["score"] == 100 and "no reconstruction path" in row["reason"]
    assert "that a documented reconstruction path works" not in row["attested_claims"]


def test_nothing_ever_scores_zero() -> None:
    """Every combination of the inputs a score depends on stays at 25 or above."""
    approvals = parse_tool_approvals((WORKED / "tool-approvals.json").read_bytes())
    inventory = parse_tool_inventory((WORKED / "tool-inventory.json").read_bytes())
    seen = set()
    for tool, supplied in itertools.product(inventory.tools, (True, False)):
        row = score_tool(tool, approvals if supplied else None)
        seen.add(row["score"])
        assert row["score"] in (25, 50, 75, 100)
    assert 0 not in seen


def test_tool_inventory_is_refused_on_other_vectors(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    for vector in VECTORS:
        if vector == "tool_connectors":
            continue
        # model_providers is checked first and refuses with its own message.
        with pytest.raises(
            ValueError, match=r"Only the tool_connectors vector|model_providers accepts only"
        ):
            scenario.run(vector, schema_valid=False)


def test_unknown_entry_keys_are_refused(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.entry_extra = {"score": 0}
    with pytest.raises(ValueError, match="takes state, path, sha256 and approvals"):
        scenario.run(schema_valid=False)


@pytest.mark.parametrize("target", ["inventory", "approvals"])
def test_a_digest_mismatch_is_refused(tmp_path: Path, target: str) -> None:
    scenario = Scenario(tmp_path)
    original = scenario.run
    manifest_path = scenario.root / "manifest.json"

    def tampered() -> None:
        original()
        name = "tool-inventory.json" if target == "inventory" else "tool-approvals.json"
        with (scenario.root / name).open("a", encoding="utf-8") as stream:
            stream.write(" ")
        build_exit_report(manifest_path)

    with pytest.raises(ToolInventoryError, match="declared SHA-256"):
        tampered()


# --- the readers ---------------------------------------------------------------------------


def _inventory() -> dict[str, Any]:
    document: dict[str, Any] = json.loads((WORKED / "tool-inventory.json").read_text("utf-8"))
    return document


def _mutated(change: Any) -> dict[str, Any]:
    document = copy.deepcopy(_inventory())
    change(document)
    return document


INVALID_INVENTORIES: list[tuple[str, Any, str]] = [
    (
        "version",
        lambda d: d.update(schema_version="tool-inventory.v2"),
        "Expected a tool-inventory.v1",
    ),
    ("extra key", lambda d: d.update(extra=1), "exactly the keys"),
    ("empty", lambda d: d.update(tools=[]), "inapplicable"),
    ("tool key", lambda d: d["tools"][0].update(note="x"), "exactly the keys"),
    ("tool_id", lambda d: d["tools"][0].update(tool_id="a.b"), "tool_id"),
    ("dup tool_id", lambda d: d["tools"][1].update(tool_id="files__search"), "share a tool_id"),
    ("dup name", lambda d: d["tools"][1].update(name="search"), "share a server and name"),
    (
        "dup hash",
        lambda d: d["tools"][1].update(definition_sha256=d["tools"][0]["definition_sha256"]),
        "share a definition_sha256",
    ),
    (
        "origin path",
        lambda d: d["tools"][0].update(server_origin="http://127.0.0.1:8765/mcp"),
        "origin",
    ),
    (
        "origin creds",
        lambda d: d["tools"][0].update(server_origin="http://u:p@127.0.0.1:8765"),
        "credentials",
    ),
    (
        "origin case",
        lambda d: d["tools"][0].update(server_origin="http://LOCALHOST:8765"),
        "canonical",
    ),
    (
        "stdio origin",
        lambda d: d["tools"][0].update(transport="mcp_stdio"),
        "only for, mcp_streamable_http",
    ),
    ("http no origin", lambda d: d["tools"][0].update(server_origin=None), "required for"),
    ("mcp no hash", lambda d: d["tools"][0].update(definition_sha256=None), "definition_sha256"),
    (
        "mcp reconstruction",
        lambda d: d["tools"][0].update(reconstruction="documented"),
        "no reconstruction",
    ),
    (
        "vendor transport",
        lambda d: d["tools"][3].update(transport="mcp_stdio"),
        "vendor-only tool is in_process",
    ),
    (
        "vendor hash",
        lambda d: d["tools"][3].update(definition_sha256="d" * 64),
        "no definition hash",
    ),
    (
        "vendor reconstruction",
        lambda d: d["tools"][3].update(reconstruction=None),
        "documented or none",
    ),
    ("transport", lambda d: d["tools"][0].update(transport="sse"), "unknown transport"),
    ("portability", lambda d: d["tools"][0].update(portability="open"), "unknown portability"),
    ("backend missing", lambda d: d["tools"][0].pop("backend"), "exactly the keys"),
    (
        "backend key",
        lambda d: d["tools"][0].update(
            backend={"system": "x", "portability": "mcp", "reconstruction": None, "url": "u"}
        ),
        "exactly the keys",
    ),
    (
        "backend system",
        lambda d: d["tools"][0].update(
            backend={
                "system": "https://api.example.com",
                "portability": "mcp",
                "reconstruction": None,
            }
        ),
        "backend system",
    ),
    (
        "backend vendor reconstruction",
        lambda d: d["tools"][0].update(
            backend={"system": "x", "portability": "vendor_only", "reconstruction": None}
        ),
        "documented or none",
    ),
    (
        "backend mcp reconstruction",
        lambda d: d["tools"][0].update(
            backend={"system": "x", "portability": "mcp", "reconstruction": "none"}
        ),
        "no reconstruction",
    ),
    (
        "backend portability",
        lambda d: d["tools"][0].update(
            backend={"system": "x", "portability": "open", "reconstruction": None}
        ),
        "unknown portability",
    ),
]


@pytest.mark.parametrize(
    ("label", "change", "match"), INVALID_INVENTORIES, ids=[c[0] for c in INVALID_INVENTORIES]
)
def test_invalid_inventories_are_refused(label: str, change: Any, match: str) -> None:
    with pytest.raises(ToolInventoryError, match=match):
        parse_tool_inventory(json.dumps(_mutated(change)).encode())


SCHEMA_CATCHES = {
    "backend missing",
    "backend key",
    "backend system",
    "backend vendor reconstruction",
    "backend mcp reconstruction",
    "backend portability",
    "version",
    "extra key",
    "empty",
    "tool key",
    "tool_id",
    "origin path",
    "origin creds",
    "stdio origin",
    "http no origin",
    "mcp no hash",
    "mcp reconstruction",
    "vendor transport",
    "vendor hash",
    "vendor reconstruction",
    "transport",
    "portability",
}


@pytest.mark.parametrize(
    ("label", "change", "match"),
    [c for c in INVALID_INVENTORIES if c[0] in SCHEMA_CATCHES],
    ids=[c[0] for c in INVALID_INVENTORIES if c[0] in SCHEMA_CATCHES],
)
def test_the_schema_agrees_where_it_can(label: str, change: Any, match: str) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_mutated(change), INVENTORY_SCHEMA)


def test_duplicate_keys_and_nan_are_refused() -> None:
    with pytest.raises(ToolInventoryError, match="Duplicate"):
        parse_tool_inventory(b'{"schema_version": "tool-inventory.v1", "schema_version": "x"}')
    with pytest.raises(ToolInventoryError):
        parse_tool_inventory(b'{"schema_version": NaN}')


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda d: d["approvals"].append(copy.deepcopy(d["approvals"][0])), "same server and tool"),
        (lambda d: d["approvals"][0].update(definition_sha256="X" * 64), "definition_sha256"),
        (lambda d: d["approvals"][0].update(approved_at="yesterday"), "approved_at"),
        (lambda d: d["approvals"][0].update(accepted_flags=["a", "a"]), "accepted_flags"),
        (
            lambda d: d["approvals"][0].update(server_origin="https://mcp.crm.example.com/x"),
            "origin",
        ),
        (lambda d: d.update(schema_version="tool-approvals.v2"), "Expected a tool-approvals.v1"),
    ],
)
def test_invalid_approvals_are_refused(change: Any, match: str) -> None:
    document = json.loads((WORKED / "tool-approvals.json").read_text("utf-8"))
    change(document)
    with pytest.raises(ToolInventoryError, match=match):
        parse_tool_approvals(json.dumps(document).encode())


# --- backends ------------------------------------------------------------------------------


def test_a_vendor_only_backend_governs_an_approved_local_mcp_tool(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search")
    scenario.tool("files__search")["backend"] = {
        "system": "search-saas",
        "portability": "vendor_only",
        "reconstruction": "none",
    }
    tool = _tools(scenario.run())["files__search"]
    assert (tool["approval"], tool["hosting"], tool["score"]) == ("approved", "local", 100)
    assert "search-saas" in tool["reason"] and "do not change this" in tool["reason"]
    scenario.tool("files__search")["backend"]["reconstruction"] = "documented"
    assert _tools(scenario.run())["files__search"]["score"] == 75


def test_a_portable_backend_keeps_the_mcp_score(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search")
    scenario.tool("files__search")["backend"] = {
        "system": "postgres",
        "portability": "mcp",
        "reconstruction": None,
    }
    tool = _tools(scenario.run())["files__search"]
    assert tool["score"] == 25 and tool["backend"]["system"] == "postgres"


def test_the_worse_reconstruction_governs_a_vendor_tool_with_a_vendor_backend(
    tmp_path: Path,
) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("billing_export")
    scenario.tool("billing_export")["backend"]["reconstruction"] = "none"
    assert _tools(scenario.run())["billing_export"]["score"] == 100
    scenario.tool("billing_export")["backend"] = None
    assert _tools(scenario.run())["billing_export"]["score"] == 75


def test_the_backend_is_always_an_attested_claim(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.only("files__search")
    row = _row(scenario.run())
    assert _tools(scenario.run())["files__search"]["backend"] is None
    assert "that each tool's declared backend is accurate" in row["attested_claims"]


def test_no_backend_combination_reaches_zero_or_25_when_vendor_only() -> None:
    inventory = parse_tool_inventory((WORKED / "tool-inventory.json").read_bytes())
    approvals = parse_tool_approvals((WORKED / "tool-approvals.json").read_bytes())
    for tool in inventory.tools:
        row = score_tool(tool, approvals)
        if tool.backend is not None and tool.backend.portability == "vendor_only":
            assert row["score"] in (75, 100)
        assert row["score"] != 0
