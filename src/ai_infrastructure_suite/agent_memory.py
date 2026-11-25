"""Reading and validating ``agent-memory.v1`` evidence.

An agent-memory document is a portable export of an agent's memory: retrieval
documents or a conversation transcript, tombstones for items deleted under
policy, and provenance for items that arrived by import. This module only reads
and validates it; scoring the ``memory`` vector is a later checkpoint.

What the reader checks itself, over the bytes it was given:

- the document and every shard against its declared SHA-256, before any line is
  trusted (one failed shard invalidates the whole store);
- every item's ``content_sha256`` against the SHA-256 of its text;
- unique item IDs, canonical ordering, contiguous conversation sequences;
- declared counts against the items and tombstones actually present;
- that a tombstone carries no content and no tombstoned ID appears as an item;
- that a quarantined item records where it came from;
- the bounds below, including metadata depth.

Vectors are never stored here. A store with embeddings references an
``embedding-portability.v1`` document; this module parses that reference and
does not follow it.

This module never opens a socket. Error messages never repeat a value from the
document, because memory holds user data.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import (
    load_json_document,
    portable_relative_path,
    read_bounded_file,
)

SCHEMA_VERSION: Final = "agent-memory.v1"

MAX_DOCUMENT_BYTES: Final = 4 * 1024 * 1024
MAX_SHARD_BYTES: Final = 16 * 1024 * 1024
MAX_SHARD_LINE_BYTES: Final = 64 * 1024
MAX_TEXT_BYTES: Final = 48 * 1024
MAX_STORES: Final = 64
MAX_SHARDS_PER_STORE: Final = 1024
MAX_ITEMS_PER_STORE: Final = 100_000
MAX_TOMBSTONES_PER_STORE: Final = 100_000
MAX_TOOL_ENTRIES: Final = 64
MAX_PRODUCER_CHARS: Final = 128
MAX_NAME_CHARS: Final = 128
MAX_METADATA_DEPTH: Final = 8
MAX_METADATA_KEYS: Final = 64
MAX_METADATA_ARRAY: Final = 256
MAX_METADATA_STRING: Final = 4096

STORE_KINDS: Final = frozenset({"retrieval", "conversation"})
ITEM_KIND_FOR_STORE: Final = {"retrieval": "document", "conversation": "message"}
STATUSES: Final = frozenset({"active", "quarantined"})
ROLES: Final = frozenset({"system", "user", "assistant", "tool"})
LAYOUTS: Final = frozenset({"single_file", "sharded_jsonl"})

_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z"
)

_DOCUMENT_KEYS: Final = frozenset(
    {"item_id", "kind", "text", "content_sha256", "metadata", "created_at", "status", "provenance"}
)
_MESSAGE_KEYS: Final = _DOCUMENT_KEYS | {"sequence", "role", "name", "tool_calls", "tool_results"}
_PROVENANCE_KEYS: Final = frozenset(
    {
        "source_system",
        "source_backend",
        "source_store_id",
        "export_id",
        "export_sha256",
        "original_item_id",
        "imported_at",
        "importer",
    }
)


class AgentMemoryError(ValueError):
    """An agent-memory document is malformed, inconsistent, or exceeds a bound."""


@dataclass(frozen=True)
class EmbeddingLink:
    """A store's reference to the embedding-portability.v1 document holding its vectors."""

    inventory_path: str
    inventory_sha256: str
    inventory_store_id: str


@dataclass(frozen=True)
class MemoryStoreSummary:
    """One validated store. Item text is deliberately not retained."""

    store_id: str
    kind: str
    layout: str
    shard_count: int
    active: int
    quarantined: int
    deleted_by_policy: int
    embeddings: EmbeddingLink | None
    #: item_id -> content_sha256, for binding items to their vectors.
    content_digests: Mapping[str, str]
    tombstone_ids: frozenset[str]
    #: Items carrying provenance, i.e. that arrived by import.
    imported: int

    @property
    def item_count(self) -> int:
        return self.active + self.quarantined


@dataclass(frozen=True)
class AgentMemoryExport:
    """A validated agent-memory document."""

    document_sha256: str
    generated_by_tool: str
    generated_by_version: str
    export_id: str
    created_at: str
    redaction_profile: str | None
    source_system: str
    source_backend: str
    stores: tuple[MemoryStoreSummary, ...]

    def store(self, store_id: str) -> MemoryStoreSummary:
        for item in self.stores:
            if item.store_id == store_id:
                return item
        raise KeyError(store_id)


# --- field readers -------------------------------------------------------------------


def _fail(message: str) -> AgentMemoryError:
    return AgentMemoryError(message)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(f"Expected an object for {label}.")
    return value


def _exact_keys(
    document: Mapping[str, Any], expected: frozenset[str] | set[str], label: str
) -> None:
    if set(document) != set(expected):
        raise _fail(f"Unexpected or missing fields in {label}.")


def _list(value: object, label: str, limit: int, *, allow_empty: bool = True) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit or (not value and not allow_empty):
        raise _fail(f"Invalid {label}.")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise _fail(f"Invalid {label}.")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _fail(f"Invalid {label}.")
    return value


def _short_text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise _fail(f"Invalid {label}.")
    return value


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise _fail(f"Invalid {label}.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _fail(f"Invalid {label}.") from None
    return value


def _count(value: object, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_ITEMS_PER_STORE
    ):
        raise _fail(f"Invalid {label}.")
    return value


def _producer(value: object, label: str) -> tuple[str, str]:
    block = _mapping(value, label)
    _exact_keys(block, {"tool", "version"}, label)
    return (
        _short_text(block["tool"], f"{label} tool", MAX_PRODUCER_CHARS),
        _short_text(block["version"], f"{label} version", MAX_PRODUCER_CHARS),
    )


def _text(value: object, label: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise _fail(f"Invalid {label}.")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise _fail(f"{label} exceeds the text size limit; chunk it before export.")
    return value


def _metadata(value: object, label: str, depth: int = 1) -> dict[str, Any]:
    """A JSON object within the depth, key, array and string bounds."""
    if depth > MAX_METADATA_DEPTH:
        raise _fail(f"{label} is nested too deeply.")
    block = _mapping(value, label)
    if len(block) > MAX_METADATA_KEYS:
        raise _fail(f"{label} has too many keys.")
    for key, item in block.items():
        if len(key) > MAX_METADATA_STRING:
            raise _fail(f"{label} has an overlong key.")
        _metadata_value(item, label, depth)
    return block


def _metadata_value(value: object, label: str, depth: int) -> None:
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > MAX_METADATA_STRING:
            raise _fail(f"{label} has an overlong string.")
        return
    if isinstance(value, list):
        if len(value) > MAX_METADATA_ARRAY:
            raise _fail(f"{label} has an overlong array.")
        if depth + 1 > MAX_METADATA_DEPTH:
            raise _fail(f"{label} is nested too deeply.")
        for item in value:
            _metadata_value(item, label, depth + 1)
        return
    _metadata(value, label, depth + 1)


def _provenance(value: object, label: str) -> bool:
    """Validate a provenance block; True when present."""
    if value is None:
        return False
    block = _mapping(value, label)
    _exact_keys(block, _PROVENANCE_KEYS, label)
    identifiers = (
        "source_system",
        "source_backend",
        "source_store_id",
        "export_id",
        "original_item_id",
    )
    for key in identifiers:
        _identifier(block[key], f"{label} {key}")
    _digest(block["export_sha256"], f"{label} export_sha256")
    _timestamp(block["imported_at"], f"{label} imported_at")
    _producer(block["importer"], f"{label} importer")
    return True


def _tool_calls(value: object, label: str) -> None:
    for entry in _list(value, label, MAX_TOOL_ENTRIES):
        call = _mapping(entry, label)
        _exact_keys(call, {"id", "name", "arguments"}, label)
        _identifier(call["id"], f"{label} id")
        _short_text(call["name"], f"{label} name", MAX_NAME_CHARS)
        _metadata(call["arguments"], f"{label} arguments")


def _tool_results(value: object, label: str) -> None:
    for entry in _list(value, label, MAX_TOOL_ENTRIES):
        result = _mapping(entry, label)
        _exact_keys(result, {"tool_call_id", "name", "content", "is_error"}, label)
        _identifier(result["tool_call_id"], f"{label} tool_call_id")
        _short_text(result["name"], f"{label} name", MAX_NAME_CHARS)
        _text(result["content"], f"{label} content", allow_empty=True)
        if not isinstance(result["is_error"], bool):
            raise _fail(f"Invalid {label} is_error.")


# --- items -----------------------------------------------------------------------------


@dataclass
class _Tally:
    """Running state while a store's items stream past."""

    kind: str
    active: int = 0
    quarantined: int = 0
    imported: int = 0
    last_key: str | int | None = None

    def __post_init__(self) -> None:
        self.digests: dict[str, str] = {}


def _item(raw: object, tally: _Tally) -> None:
    item = _mapping(raw, "memory item")
    expected_kind = ITEM_KIND_FOR_STORE[tally.kind]
    if item.get("kind") != expected_kind:
        raise _fail("An item's kind does not match its store's kind.")
    _exact_keys(
        item, _MESSAGE_KEYS if expected_kind == "message" else _DOCUMENT_KEYS, "memory item"
    )
    item_id = _identifier(item["item_id"], "item_id")
    if item_id in tally.digests:
        raise _fail("Duplicate item_id in store.")
    text = _text(item["text"], "item text", allow_empty=expected_kind == "message")
    declared = _digest(item["content_sha256"], "content_sha256")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != declared:
        raise _fail("An item's text differs from its content_sha256.")
    _metadata(item["metadata"], "item metadata")
    if item["created_at"] is not None:
        _timestamp(item["created_at"], "item created_at")
    status = item["status"]
    if not isinstance(status, str) or status not in STATUSES:
        raise _fail("Invalid item status.")
    has_provenance = _provenance(item["provenance"], "item provenance")
    if status == "quarantined" and not has_provenance:
        raise _fail("A quarantined item must record its provenance.")

    order_key: str | int
    if expected_kind == "message":
        sequence = item["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise _fail("Invalid message sequence.")
        expected_sequence = 0 if tally.last_key is None else int(tally.last_key) + 1
        if sequence != expected_sequence:
            raise _fail("Conversation messages must be numbered 0, 1, 2, ... in order.")
        role = item["role"]
        if not isinstance(role, str) or role not in ROLES:
            raise _fail("Invalid message role.")
        if item["name"] is not None:
            _short_text(item["name"], "message name", MAX_NAME_CHARS)
        _tool_calls(item["tool_calls"], "message tool_calls")
        _tool_results(item["tool_results"], "message tool_results")
        order_key = sequence
    else:
        if tally.last_key is not None and not str(tally.last_key) < item_id:
            raise _fail("Retrieval items must be sorted by item_id.")
        order_key = item_id

    tally.last_key = order_key
    tally.digests[item_id] = declared
    if len(tally.digests) > MAX_ITEMS_PER_STORE:
        raise _fail("Too many items in store.")
    if status == "active":
        tally.active += 1
    else:
        tally.quarantined += 1
    if has_provenance:
        tally.imported += 1


def _single_file(block: Mapping[str, Any], tally: _Tally) -> int:
    _exact_keys(block, {"layout", "records"}, "single-file items")
    for raw in _list(block["records"], "items records", MAX_ITEMS_PER_STORE):
        _item(raw, tally)
    return 0


def _sharded(block: Mapping[str, Any], root: Path, tally: _Tally) -> int:
    _exact_keys(block, {"layout", "shards"}, "sharded items")
    shards = _list(block["shards"], "item shards", MAX_SHARDS_PER_STORE, allow_empty=False)
    seen_paths: set[str] = set()
    for raw in shards:
        entry = _mapping(raw, "shard reference")
        _exact_keys(entry, {"path", "sha256", "record_count"}, "shard reference")
        try:
            name = portable_relative_path(entry["path"])
        except ValueError:
            raise _fail("Invalid shard path.") from None
        if name in seen_paths:
            raise _fail("A shard is listed twice.")
        seen_paths.add(name)
        declared = _digest(entry["sha256"], "shard digest")
        count = entry["record_count"]
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= MAX_ITEMS_PER_STORE
        ):
            raise _fail("Invalid shard record_count.")
        try:
            data = read_bounded_file(root / name, MAX_SHARD_BYTES)
        except (OSError, ValueError):
            raise _fail("A shard file is missing, linked, or over the size limit.") from None
        # The digest covers the exact bytes and is checked before any line is
        # trusted; one failed shard invalidates the whole store.
        if hashlib.sha256(data).hexdigest() != declared:
            raise _fail("A shard file differs from its declared SHA-256.")
        seen = 0
        for line in data.split(b"\n"):
            if not line.strip():
                continue
            if len(line) > MAX_SHARD_LINE_BYTES:
                raise _fail("A shard line exceeds the line size limit.")
            seen += 1
            if seen > count:
                raise _fail("A shard holds more items than it declares.")
            try:
                parsed = load_json_document(line, "shard line")
            except ValueError:
                raise _fail("A shard line is not well-formed JSON.") from None
            _item(parsed, tally)
        if seen != count:
            raise _fail("A shard's item count differs from its declaration.")
    return len(shards)


def _tombstones(value: object, digests: Mapping[str, str]) -> frozenset[str]:
    entries = _list(value, "tombstones", MAX_TOMBSTONES_PER_STORE)
    ids: list[str] = []
    for raw in entries:
        entry = _mapping(raw, "tombstone")
        # Exactly an id and a time: a tombstone never carries content.
        _exact_keys(entry, {"item_id", "deleted_at"}, "tombstone")
        ids.append(_identifier(entry["item_id"], "tombstone item_id"))
        _timestamp(entry["deleted_at"], "tombstone deleted_at")
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise _fail("Tombstones must be unique and sorted by item_id.")
    if any(item_id in digests for item_id in ids):
        raise _fail("A tombstoned item still appears as an item.")
    return frozenset(ids)


def _embeddings(value: object) -> EmbeddingLink | None:
    if value is None:
        return None
    block = _mapping(value, "store embeddings")
    _exact_keys(block, {"inventory", "inventory_store_id"}, "store embeddings")
    inventory = _mapping(block["inventory"], "embedding inventory reference")
    _exact_keys(inventory, {"path", "sha256"}, "embedding inventory reference")
    try:
        path = portable_relative_path(inventory["path"])
    except ValueError:
        raise _fail("Invalid embedding inventory path.") from None
    return EmbeddingLink(
        inventory_path=path,
        inventory_sha256=_digest(inventory["sha256"], "embedding inventory digest"),
        inventory_store_id=_identifier(block["inventory_store_id"], "inventory_store_id"),
    )


def _store(raw: object, root: Path) -> MemoryStoreSummary:
    store = _mapping(raw, "memory store")
    _exact_keys(
        store, {"store_id", "kind", "embeddings", "items", "counts", "tombstones"}, "memory store"
    )
    store_id = _identifier(store["store_id"], "store_id")
    kind = store["kind"]
    if not isinstance(kind, str) or kind not in STORE_KINDS:
        raise _fail("Invalid store kind.")
    embeddings = _embeddings(store["embeddings"])
    items = _mapping(store["items"], "store items")
    layout = items.get("layout")
    if not isinstance(layout, str) or layout not in LAYOUTS:
        raise _fail("Invalid store items layout.")
    tally = _Tally(kind=kind)
    shard_count = (
        _single_file(items, tally) if layout == "single_file" else _sharded(items, root, tally)
    )
    tombstone_ids = _tombstones(store["tombstones"], tally.digests)
    counts = _mapping(store["counts"], "store counts")
    _exact_keys(counts, {"active", "quarantined", "deleted_by_policy"}, "store counts")
    declared = (
        _count(counts["active"], "active count"),
        _count(counts["quarantined"], "quarantined count"),
        _count(counts["deleted_by_policy"], "deleted_by_policy count"),
    )
    if declared != (tally.active, tally.quarantined, len(tombstone_ids)):
        raise _fail("A store's declared counts differ from its items and tombstones.")
    return MemoryStoreSummary(
        store_id=store_id,
        kind=kind,
        layout=layout,
        shard_count=shard_count,
        active=tally.active,
        quarantined=tally.quarantined,
        deleted_by_policy=len(tombstone_ids),
        embeddings=embeddings,
        content_digests=dict(tally.digests),
        tombstone_ids=tombstone_ids,
        imported=tally.imported,
    )


# --- entry points ------------------------------------------------------------------------


def parse_agent_memory(data: bytes, root: Path) -> AgentMemoryExport:
    """Validate ``agent-memory.v1`` bytes; shard paths resolve against ``root``."""
    if len(data) > MAX_DOCUMENT_BYTES:
        raise _fail("Agent memory document exceeds the size limit.")
    try:
        document = _mapping(
            load_json_document(data, "agent memory document"), "agent memory document"
        )
    except AgentMemoryError:
        raise
    except ValueError as exc:
        raise _fail(str(exc)) from None
    _exact_keys(
        document, {"schema_version", "generated_by", "export", "stores"}, "agent memory document"
    )
    if document["schema_version"] != SCHEMA_VERSION:
        raise _fail(f"Expected an {SCHEMA_VERSION} document.")
    tool, version = _producer(document["generated_by"], "generated_by")
    export = _mapping(document["export"], "export")
    _exact_keys(export, {"export_id", "created_at", "redaction_profile", "source"}, "export")
    source = _mapping(export["source"], "export source")
    _exact_keys(source, {"system", "backend"}, "export source")
    redaction = export["redaction_profile"]
    if redaction is not None:
        redaction = _identifier(redaction, "redaction_profile")
    stores = tuple(
        _store(raw, root)
        for raw in _list(document["stores"], "stores", MAX_STORES, allow_empty=False)
    )
    ids = [store.store_id for store in stores]
    if len(set(ids)) != len(ids):
        raise _fail("Duplicate store_id.")
    return AgentMemoryExport(
        document_sha256=hashlib.sha256(data).hexdigest(),
        generated_by_tool=tool,
        generated_by_version=version,
        export_id=_identifier(export["export_id"], "export_id"),
        created_at=_timestamp(export["created_at"], "export created_at"),
        redaction_profile=redaction,
        source_system=_identifier(source["system"], "source system"),
        source_backend=_identifier(source["backend"], "source backend"),
        stores=stores,
    )


def load_agent_memory(path: Path, *, expected_sha256: str | None = None) -> AgentMemoryExport:
    """Read and validate an agent-memory document and its shards from local files.

    ``expected_sha256`` binds the document to the digest a manifest declared.
    """
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256)
    ):
        raise _fail("Invalid expected digest.")
    try:
        data = read_bounded_file(path, MAX_DOCUMENT_BYTES)
    except (OSError, ValueError):
        raise _fail(
            "The agent memory document is missing, linked, or over the size limit."
        ) from None
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise _fail("Agent memory document differs from its declared SHA-256.")
    return parse_agent_memory(data, path.absolute().parent)
