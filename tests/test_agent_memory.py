"""``agent-memory.v1``: the reader, the packaged schema, and the fixtures.

Every negative case is refused by the reader. Each also records whether the
JSON Schema can express it: ordering, uniqueness, counts, digests and metadata
depth are the reader's alone, and the test pins that division so a schema change
cannot silently claim more than it checks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from ai_infrastructure_suite.agent_memory import (
    MAX_DOCUMENT_BYTES,
    MAX_TEXT_BYTES,
    SCHEMA_VERSION,
    AgentMemoryError,
    load_agent_memory,
    parse_agent_memory,
)
from ai_infrastructure_suite.embedding_portability import scan_embedding_portability

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples" / "agent-memory"
FIXTURES = sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir())
PLANTED = "mem-planted-91ce-do-not-log"
SCHEMA = json.loads(
    files("ai_infrastructure_suite").joinpath("data/agent-memory.schema.json").read_text("utf-8")
)
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
ITEM_VALIDATOR = jsonschema.Draft202012Validator({"$ref": "#/$defs/item", "$defs": SCHEMA["$defs"]})


def _fixture(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        (EXAMPLES / name / "agent-memory.json").read_text("utf-8")
    )
    return document


def _parse(document: object, root: Path = EXAMPLES) -> Any:
    return parse_agent_memory(json.dumps(document).encode("utf-8"), root)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base() -> dict[str, Any]:
    return copy.deepcopy(_fixture("retrieval-single-file"))


def _items(document: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = document["stores"][0]["items"]["records"]
    return records


# --- the shipped fixtures ----------------------------------------------------------------


def test_the_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["properties"]["schema_version"]["const"] == SCHEMA_VERSION


def test_the_expected_fixtures_ship() -> None:
    assert FIXTURES == [
        "conversation",
        "imported-quarantine",
        "minimal",
        "retrieval-sharded",
        "retrieval-single-file",
        "with-tombstones",
    ]


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_parses_and_matches_the_schema(name: str) -> None:
    root = EXAMPLES / name
    document = _fixture(name)
    assert list(VALIDATOR.iter_errors(document)) == []
    for shard in sorted((root / "items").glob("*.jsonl")):
        for line in shard.read_text("utf-8").splitlines():
            assert list(ITEM_VALIDATOR.iter_errors(json.loads(line))) == []
    data = (root / "agent-memory.json").read_bytes()
    result = load_agent_memory(
        root / "agent-memory.json", expected_sha256=hashlib.sha256(data).hexdigest()
    )
    assert result.stores and result.document_sha256 == hashlib.sha256(data).hexdigest()


def test_fixture_bytes_are_lf_only() -> None:
    # Shard and source digests are over exact bytes; a CRLF checkout would break them.
    for path in EXAMPLES.rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes(), path.name


def test_counts_kinds_and_layouts_are_reported() -> None:
    sharded = load_agent_memory(EXAMPLES / "retrieval-sharded" / "agent-memory.json").stores[0]
    assert (sharded.layout, sharded.shard_count, sharded.active, sharded.item_count) == (
        "sharded_jsonl",
        2,
        5,
        5,
    )
    conversation = load_agent_memory(EXAMPLES / "conversation" / "agent-memory.json").stores[0]
    assert (conversation.kind, conversation.active) == ("conversation", 5)
    tombs = load_agent_memory(EXAMPLES / "with-tombstones" / "agent-memory.json").stores[0]
    assert (tombs.active, tombs.deleted_by_policy) == (1, 2)
    assert tombs.tombstone_ids == frozenset({"cust-note-0042", "cust-note-0077"})
    imported = load_agent_memory(EXAMPLES / "imported-quarantine" / "agent-memory.json").stores[0]
    assert (imported.active, imported.quarantined, imported.imported) == (2, 1, 2)


def test_the_linked_embedding_inventory_is_valid_and_binds_to_the_items() -> None:
    # The reader does not follow the link (scoring does, later); the fixture is
    # built so that it can: one vector record per item, source digests equal.
    root = EXAMPLES / "retrieval-sharded"
    memory = load_agent_memory(root / "agent-memory.json").stores[0]
    link = memory.embeddings
    assert link is not None
    inventory = root / link.inventory_path
    data = inventory.read_bytes()
    assert hashlib.sha256(data).hexdigest() == link.inventory_sha256
    scan = scan_embedding_portability(inventory, evidence_sha256=link.inventory_sha256)
    assert [s.store_id for s in scan.stores] == [link.inventory_store_id]
    lines = (inventory.parent / "vectors" / "0000.jsonl").read_text("utf-8").splitlines()
    recorded = {row["record_id"]: row["source_text_sha256"] for row in map(json.loads, lines)}
    assert recorded == dict(memory.content_digests)


def test_item_text_is_not_retained_in_the_result() -> None:
    result = load_agent_memory(EXAMPLES / "retrieval-single-file" / "agent-memory.json")
    assert "Escalate" not in repr(result)


# --- negative cases --------------------------------------------------------------------
# (mutation, whether the schema rejects it too)


def _set(path: list[Any], value: Any) -> Any:
    def mutate(document: dict[str, Any]) -> None:
        target: Any = document
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value

    return mutate


def _delete(path: list[Any]) -> Any:
    def mutate(document: dict[str, Any]) -> None:
        target: Any = document
        for part in path[:-1]:
            target = target[part]
        del target[path[-1]]

    return mutate


def _with_text(index: int, text: str) -> Any:
    def mutate(document: dict[str, Any]) -> None:
        item = _items(document)[index]
        item["text"] = text
        item["content_sha256"] = _sha(text)

    return mutate


def _swap_first_two(document: dict[str, Any]) -> None:
    records = _items(document)
    records[0], records[1] = records[1], records[0]


def _duplicate_first(document: dict[str, Any]) -> None:
    records = _items(document)
    records.insert(1, copy.deepcopy(records[0]))
    document["stores"][0]["counts"]["active"] += 1


def _quarantine_without_provenance(document: dict[str, Any]) -> None:
    _items(document)[0]["status"] = "quarantined"
    document["stores"][0]["counts"]["active"] -= 1
    document["stores"][0]["counts"]["quarantined"] += 1


def _tombstone_with_content(document: dict[str, Any]) -> None:
    document["stores"][0]["tombstones"] = [
        {"item_id": "x-1", "deleted_at": "2026-11-30T08:12:00Z", "text": PLANTED}
    ]
    document["stores"][0]["counts"]["deleted_by_policy"] = 1


def _tombstone_of_live_item(document: dict[str, Any]) -> None:
    item_id = _items(document)[0]["item_id"]
    document["stores"][0]["tombstones"] = [
        {"item_id": item_id, "deleted_at": "2026-11-30T08:12:00Z"}
    ]
    document["stores"][0]["counts"]["deleted_by_policy"] = 1


def _unsorted_tombstones(document: dict[str, Any]) -> None:
    document["stores"][0]["tombstones"] = [
        {"item_id": "z-1", "deleted_at": "2026-11-30T08:12:00Z"},
        {"item_id": "a-1", "deleted_at": "2026-11-30T08:12:00Z"},
    ]
    document["stores"][0]["counts"]["deleted_by_policy"] = 2


def _deep_metadata(document: dict[str, Any]) -> None:
    nested: dict[str, Any] = {"leaf": PLANTED}
    for _ in range(9):
        nested = {"n": nested}
    _items(document)[0]["metadata"] = nested


def _duplicate_store(document: dict[str, Any]) -> None:
    document["stores"].append(copy.deepcopy(document["stores"][0]))


I0 = ["stores", 0, "items", "records", 0]
NEGATIVE: dict[str, tuple[Any, bool]] = {
    "wrong schema_version": (_set(["schema_version"], "agent-memory.v2"), True),
    "missing schema_version": (_delete(["schema_version"]), True),
    "unknown top-level key": (_set(["extra"], 1), True),
    "unknown store key": (_set(["stores", 0, "extra"], 1), True),
    "unknown item key": (_set([*I0, "extra"], PLANTED), True),
    "empty stores": (_set(["stores"], []), True),
    "duplicate store_id": (_duplicate_store, False),
    "bad store kind": (_set(["stores", 0, "kind"], "archive"), True),
    "message item in a retrieval store": (_set([*I0, "kind"], "message"), True),
    "bad item_id": (_set([*I0, "item_id"], f"../{PLANTED}"), True),
    "content digest mismatch": (_set([*I0, "text"], PLANTED), False),
    "malformed content digest": (_set([*I0, "content_sha256"], PLANTED), True),
    "empty document text": (_with_text(0, ""), True),
    "oversized text": (_with_text(0, "x" * (MAX_TEXT_BYTES + 1)), True),
    "bad status": (_set([*I0, "status"], "deleted_by_policy"), True),
    "quarantined without provenance": (_quarantine_without_provenance, True),
    "invalid created_at": (_set([*I0, "created_at"], "yesterday"), True),
    "impossible created_at": (_set([*I0, "created_at"], "2026-02-30T10:00:00Z"), False),
    "metadata not an object": (_set([*I0, "metadata"], [PLANTED]), True),
    "metadata nested too deeply": (_deep_metadata, False),
    "metadata string too long": (_set([*I0, "metadata"], {"k": PLANTED * 200}), False),
    "items out of order": (_swap_first_two, False),
    "duplicate item_id": (_duplicate_first, False),
    "declared counts differ": (_set(["stores", 0, "counts", "active"], 2), False),
    "negative count": (_set(["stores", 0, "counts", "active"], -1), True),
    "tombstone with content": (_tombstone_with_content, True),
    "tombstone of a live item": (_tombstone_of_live_item, False),
    "unsorted tombstones": (_unsorted_tombstones, False),
    "bad redaction profile": (_set(["export", "redaction_profile"], f"{PLANTED} x"), True),
    "locator in source": (
        _set(["export", "source", "dsn"], f"postgresql://u:{PLANTED}@h/db"),
        True,
    ),
    "bad embedding path": (
        _set(
            ["stores", 0, "embeddings"],
            {"inventory": {"path": "../x.json", "sha256": "0" * 64}, "inventory_store_id": "s"},
        ),
        True,
    ),
    "bad layout": (_set(["stores", 0, "items", "layout"], "zip"), True),
}


@pytest.mark.parametrize("case", sorted(NEGATIVE))
def test_the_reader_refuses(case: str) -> None:
    mutate, _ = NEGATIVE[case]
    document = base()
    mutate(document)

    with pytest.raises(AgentMemoryError) as raised:
        _parse(document)

    assert PLANTED not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("case", sorted(NEGATIVE))
def test_the_schema_rejects_exactly_what_it_can_express(case: str) -> None:
    mutate, schema_rejects = NEGATIVE[case]
    document = base()
    mutate(document)

    assert VALIDATOR.is_valid(document) is not schema_rejects


# --- conversations -------------------------------------------------------------------------


def _conversation() -> dict[str, Any]:
    return copy.deepcopy(_fixture("conversation"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: _items(d)[2].__setitem__("sequence", 7),
        lambda d: _items(d).pop(1) and d["stores"][0]["counts"].__setitem__("active", 4),
        lambda d: _items(d)[0].__setitem__("sequence", 1),
    ],
    ids=["gap", "missing message", "not starting at zero"],
)
def test_conversation_messages_must_be_numbered_in_order(mutate: Any) -> None:
    document = _conversation()
    mutate(document)
    with pytest.raises(AgentMemoryError, match="numbered"):
        _parse(document)


def test_a_message_may_have_empty_text_but_a_document_may_not() -> None:
    document = _conversation()
    assert _items(document)[2]["text"] == ""
    assert _parse(document).stores[0].active == 5


def test_a_bad_role_and_a_bad_tool_result_are_refused() -> None:
    document = _conversation()
    _items(document)[1]["role"] = "operator"
    with pytest.raises(AgentMemoryError):
        _parse(document)
    document = _conversation()
    _items(document)[3]["tool_results"][0]["is_error"] = "no"
    with pytest.raises(AgentMemoryError):
        _parse(document)
    assert not VALIDATOR.is_valid(document)


# --- shards and files ------------------------------------------------------------------------


def _copy_sharded(tmp_path: Path) -> Path:
    target = tmp_path / "retrieval-sharded"
    shutil.copytree(EXAMPLES / "retrieval-sharded", target)
    return target


def test_a_tampered_shard_invalidates_the_store(tmp_path: Path) -> None:
    root = _copy_sharded(tmp_path)
    shard = root / "items" / "0001.jsonl"
    shard.write_bytes(shard.read_bytes().replace(b"fourteen", b"forty"))
    with pytest.raises(AgentMemoryError, match="differs from its declared SHA-256"):
        load_agent_memory(root / "agent-memory.json")


def test_a_shard_count_mismatch_is_refused(tmp_path: Path) -> None:
    root = _copy_sharded(tmp_path)
    document = json.loads((root / "agent-memory.json").read_text("utf-8"))
    document["stores"][0]["items"]["shards"][0]["record_count"] = 2
    with pytest.raises(AgentMemoryError, match="more items than it declares"):
        _parse(document, root)


def test_a_missing_shard_and_a_repeated_shard_are_refused(tmp_path: Path) -> None:
    root = _copy_sharded(tmp_path)
    document = json.loads((root / "agent-memory.json").read_text("utf-8"))
    (root / "items" / "0001.jsonl").unlink()
    with pytest.raises(AgentMemoryError, match="missing"):
        _parse(document, root)
    root = _copy_sharded(tmp_path / "again")
    document = json.loads((root / "agent-memory.json").read_text("utf-8"))
    shards = document["stores"][0]["items"]["shards"]
    shards.append(copy.deepcopy(shards[0]))
    with pytest.raises(AgentMemoryError, match="listed twice"):
        _parse(document, root)


def test_an_overlong_shard_line_is_refused(tmp_path: Path) -> None:
    root = _copy_sharded(tmp_path)
    line = json.dumps({"item_id": "kb-0000", "pad": "x" * 70_000}).encode()
    (root / "items" / "0000.jsonl").write_bytes(line + b"\n")
    document = json.loads((root / "agent-memory.json").read_text("utf-8"))
    document["stores"][0]["items"]["shards"][0]["sha256"] = hashlib.sha256(line + b"\n").hexdigest()
    with pytest.raises(AgentMemoryError, match="line size"):
        _parse(document, root)


def test_a_shard_path_escaping_the_export_is_refused(tmp_path: Path) -> None:
    root = _copy_sharded(tmp_path)
    document = json.loads((root / "agent-memory.json").read_text("utf-8"))
    document["stores"][0]["items"]["shards"][0]["path"] = "../retrieval-sharded/items/0000.jsonl"
    with pytest.raises(AgentMemoryError, match="shard path"):
        _parse(document, root)


def test_a_symlinked_shard_is_refused(tmp_path: Path) -> None:
    root = _copy_sharded(tmp_path)
    shard = root / "items" / "0000.jsonl"
    real = tmp_path / "elsewhere.jsonl"
    real.write_bytes(shard.read_bytes())
    shard.unlink()
    try:
        shard.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(AgentMemoryError, match="linked"):
        load_agent_memory(root / "agent-memory.json")


def test_the_digest_binds_the_document_to_its_bytes(tmp_path: Path) -> None:
    path = EXAMPLES / "minimal" / "agent-memory.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_agent_memory(path, expected_sha256=digest).stores
    with pytest.raises(AgentMemoryError, match="differs"):
        load_agent_memory(path, expected_sha256="0" * 64)
    with pytest.raises(AgentMemoryError, match="expected digest"):
        load_agent_memory(path, expected_sha256="not-a-digest")


def test_an_oversized_document_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "agent-memory.json"
    path.write_bytes(b" " * (MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(AgentMemoryError, match="size limit"):
        load_agent_memory(path)


@pytest.mark.parametrize(
    "data",
    [
        b'{"schema_version":"agent-memory.v1","schema_version":"x"}',
        b'{"schema_version": NaN}',
        b"\xff\xfe",
        b"[]",
    ],
    ids=["duplicate keys", "NaN", "invalid UTF-8", "not an object"],
)
def test_malformed_json_is_refused(data: bytes) -> None:
    with pytest.raises(AgentMemoryError):
        parse_agent_memory(data, EXAMPLES)
