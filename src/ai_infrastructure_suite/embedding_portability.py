"""Offline embedding lock-in detection for ``embedding-portability.v1`` evidence.

The scanner answers two questions without calling an embedding API: can every
stored vector be traced to retained source text that still matches the vector
record, and is the declared embedding model portable enough to reproduce
locally?

Everything here is local and bounded. The scanner hashes files, parses vectors,
counts coverage, and checks a restore link. It never imports a provider SDK,
opens a socket, downloads weights, embeds text, or prints source text or vector
values. Model availability, weight digests, policy deletions, and licenses are
operator declarations and stay labeled attested.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import (
    load_json_document,
    portable_relative_path,
    read_bounded_file,
)

SCHEMA_VERSION: Final = "embedding-portability.v1"

MAX_EVIDENCE_BYTES: Final = 4 * 1024 * 1024
MAX_VECTOR_FILE_BYTES: Final = 1024 * 1024
MAX_SHARD_BYTES: Final = 16 * 1024 * 1024
MAX_SOURCE_TEXT_BYTES: Final = 1024 * 1024
MAX_SHARD_LINE_BYTES: Final = 64 * 1024
MAX_STORES: Final = 64
MAX_SHARDS_PER_STORE: Final = 1024
MAX_RECORDS_PER_STORE: Final = 100_000
MAX_DIMENSIONS: Final = 8192
MAX_IDENTIFIER_CHARS: Final = 128
MAX_LICENSE_CHARS: Final = 512
MAX_DELETION_REASON_CHARS: Final = 512
FINDING_RECORD_CAP: Final = 20

AVAILABILITY: Final = frozenset({"open_weight_self_hostable", "vendor_only"})
SOURCE_STATUS: Final = frozenset({"retained", "missing", "deleted_by_policy"})
LAYOUTS: Final = frozenset({"single_file", "sharded_jsonl"})

RETAINED_VECTOR_FINDING: Final = "vector retained after source deletion"
ATTESTED_DELETION_WARNING: Final = (
    "Coverage depends on attested deletions the scanner cannot verify: "
    "policy deletions are a majority of this store."
)
UNPINNED_WEIGHTS_NOTE: Final = (
    "Model weights are not pinned, so re-embedding may not reproduce identical vectors."
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class RestoreEvidence:
    """What the scanner knows about a re-run Phase 2 restore of this evidence.

    ``supplied`` records that the manifest referenced a restore summary at all.
    ``verified`` is true only when the scanner itself rebuilt the fake profiles
    and the embeddings asset passed. ``stores`` maps a declared ``store_id`` to
    the evidence-document digest that restore run committed to.
    """

    supplied: bool = False
    verified: bool = False
    stores: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelDeclaration:
    """Operator-declared embedding model. Every field is attested."""

    id: str
    revision: str
    dimensions: int
    availability: str
    weights_sha256: str | None
    license: str | None

    @property
    def self_hostable(self) -> bool:
        return self.availability == "open_weight_self_hostable"

    @property
    def weights_pinned(self) -> bool:
        return self.weights_sha256 is not None


@dataclass(frozen=True)
class Finding:
    """A reported observation that never changes a score."""

    code: str
    store_id: str
    record_count: int
    record_ids: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class StoreResult:
    """Independent lock-in result for one vector store."""

    store_id: str
    model: ModelDeclaration
    layout: str
    shard_count: int
    vector_count: int
    retained: int
    missing: int
    deleted_by_policy: int
    coverage: float | None
    re_embeddable: str
    score: int | None
    restore_link: str
    reason: str
    warnings: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def scored(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class EmbeddingPortability:
    """Aggregate embeddings result across every declared store."""

    stores: tuple[StoreResult, ...]
    re_embeddable: str
    score: int | None
    reason: str


def _text(value: object, label: str, limit: int = MAX_IDENTIFIER_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"Invalid {label}.")
    return value


def _identifier(value: object, label: str) -> str:
    name = _text(value, label)
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"Invalid {label}.")
    return name


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"Invalid {label}.")
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object for {label}.")
    return value


def _exact_keys(document: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(document) != expected:
        raise ValueError(f"Unexpected fields in {label}.")


def _bounded_list(value: object, label: str, limit: int) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Expected a non-empty list for {label}.")
    if len(value) > limit:
        raise ValueError(f"Too many entries in {label}.")
    return value


def _vector_values(values: object, dimensions: int, label: str) -> None:
    if not isinstance(values, list) or len(values) != dimensions:
        raise ValueError(f"Vector dimension differs from the store declaration in {label}.")
    for item in values:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"Vector values must be numbers in {label}.")
        if not math.isfinite(item):
            raise ValueError(f"Vector values must be finite in {label}.")


def _model(raw: object) -> ModelDeclaration:
    document = _mapping(raw, "embedding_model")
    required = {"id", "revision", "dimensions", "availability", "availability_verification"}
    optional = {"weights_sha256", "license"}
    if not required <= set(document) or set(document) - (required | optional):
        raise ValueError("Unexpected fields in embedding_model.")
    dimensions = document["dimensions"]
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise ValueError("Invalid embedding model dimensions.")
    if not 1 <= dimensions <= MAX_DIMENSIONS:
        raise ValueError("Invalid embedding model dimensions.")
    availability = _text(document["availability"], "embedding model availability")
    if availability not in AVAILABILITY:
        raise ValueError("Invalid embedding model availability.")
    if document["availability_verification"] != "attested":
        raise ValueError("Embedding model availability must be labeled attested.")
    weights = document.get("weights_sha256")
    license_text = document.get("license")
    return ModelDeclaration(
        id=_text(document["id"], "embedding model id"),
        revision=_text(document["revision"], "embedding model revision"),
        dimensions=dimensions,
        availability=availability,
        weights_sha256=None if weights is None else _digest(weights, "model weights digest"),
        license=(
            None
            if license_text is None
            else _text(license_text, "model license", MAX_LICENSE_CHARS)
        ),
    )


def _source_text(raw: object, root: Path, recorded: str | None) -> str:
    """Validate one source-text entry and return its status.

    A ``retained`` entry is scanner verified: its bytes are hashed and must match
    both the evidence digest and the digest recorded with the vector. ``missing``
    and ``deleted_by_policy`` carry no file, and ``deleted_by_policy`` is an
    operator declaration the scanner cannot confirm.
    """
    document = _mapping(raw, "source_text")
    status = document.get("status")
    if not isinstance(status, str) or status not in SOURCE_STATUS:
        raise ValueError("Invalid source_text status.")
    if status == "retained":
        _exact_keys(document, {"status", "path", "sha256"}, "retained source_text")
        name = portable_relative_path(document["path"])
        declared = _digest(document["sha256"], "source text digest")
        data = read_bounded_file(root / name, MAX_SOURCE_TEXT_BYTES)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Retained source text must be UTF-8.") from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != declared:
            raise ValueError("Retained source text differs from its declared SHA-256.")
        if recorded is not None and actual != recorded:
            raise ValueError(
                "Retained source text differs from the digest recorded with the vector."
            )
        return status
    if status == "missing":
        _exact_keys(document, {"status"}, "missing source_text")
        return status
    if set(document) - {"status", "reason"}:
        raise ValueError("Unexpected fields in deleted_by_policy source_text.")
    if "reason" in document:
        _text(document["reason"], "deletion reason", MAX_DELETION_REASON_CHARS)
    return status


def _single_file_records(
    raw: object, root: Path, model: ModelDeclaration
) -> tuple[dict[str, str], int]:
    records = _bounded_list(raw, "store records", MAX_RECORDS_PER_STORE)
    statuses: dict[str, str] = {}
    for entry in records:
        document = _mapping(entry, "store record")
        _exact_keys(
            document,
            {"record_id", "vector", "source_text_sha256_in_vector_record", "source_text"},
            "store record",
        )
        record_id = _identifier(document["record_id"], "record_id")
        if record_id in statuses:
            raise ValueError("Duplicate record_id in store.")
        reference = _mapping(document["vector"], "vector reference")
        _exact_keys(reference, {"path", "sha256"}, "vector reference")
        name = portable_relative_path(reference["path"])
        declared = _digest(reference["sha256"], "vector digest")
        data = read_bounded_file(root / name, MAX_VECTOR_FILE_BYTES)
        if hashlib.sha256(data).hexdigest() != declared:
            raise ValueError("Vector file differs from its declared SHA-256.")
        vector = _mapping(load_json_document(data, "vector file"), "vector file")
        _exact_keys(vector, {"values"}, "vector file")
        _vector_values(vector["values"], model.dimensions, "vector file")
        recorded = _digest(
            document["source_text_sha256_in_vector_record"],
            "source digest recorded with the vector",
        )
        statuses[record_id] = _source_text(document["source_text"], root, recorded)
    return statuses, 0


def _shard_records(raw: object, root: Path, model: ModelDeclaration) -> tuple[dict[str, str], int]:
    document = _mapping(raw, "store vectors")
    _exact_keys(document, {"layout", "shards", "source_text"}, "sharded store vectors")
    shards = _bounded_list(document["shards"], "store shards", MAX_SHARDS_PER_STORE)
    recorded: dict[str, str] = {}
    for shard in shards:
        entry = _mapping(shard, "shard reference")
        _exact_keys(entry, {"path", "sha256", "record_count"}, "shard reference")
        name = portable_relative_path(entry["path"])
        declared = _digest(entry["sha256"], "shard digest")
        count = entry["record_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("Invalid shard record_count.")
        # The shard digest is checked over the exact bytes before any line is
        # trusted. A single failed shard invalidates the whole store rather than
        # leaving the surviving shards as a partial inventory.
        data = read_bounded_file(root / name, MAX_SHARD_BYTES)
        if hashlib.sha256(data).hexdigest() != declared:
            raise ValueError("Shard file differs from its declared SHA-256.")
        seen = 0
        for line in data.split(b"\n"):
            if not line.strip():
                continue
            if len(line) > MAX_SHARD_LINE_BYTES:
                raise ValueError("Shard line exceeds the line size limit.")
            seen += 1
            if seen > count or len(recorded) + 1 > MAX_RECORDS_PER_STORE:
                raise ValueError("Shard holds more records than it declares.")
            row = _mapping(load_json_document(line, "shard line"), "shard line")
            _exact_keys(row, {"record_id", "source_text_sha256", "values"}, "shard line")
            record_id = _identifier(row["record_id"], "record_id")
            if record_id in recorded:
                raise ValueError("Duplicate record_id in store.")
            _vector_values(row["values"], model.dimensions, "shard line")
            recorded[record_id] = _digest(
                row["source_text_sha256"], "source digest recorded with the vector"
            )
        if seen != count:
            raise ValueError("Shard record count differs from its declaration.")
    sources = _bounded_list(document["source_text"], "store source_text", MAX_RECORDS_PER_STORE)
    statuses: dict[str, str] = {}
    for item in sources:
        entry = _mapping(item, "source_text entry")
        _exact_keys(entry, {"record_id", "source_text"}, "source_text entry")
        record_id = _identifier(entry["record_id"], "record_id")
        if record_id in statuses:
            raise ValueError("Duplicate record_id in store source_text.")
        if record_id not in recorded:
            raise ValueError("source_text entry has no matching vector record.")
        statuses[record_id] = _source_text(entry["source_text"], root, recorded[record_id])
    if statuses.keys() != recorded.keys():
        raise ValueError("Every vector record requires one source_text entry.")
    return statuses, len(shards)


def _restore_link(store_id: str, evidence_sha256: str, restore: RestoreEvidence) -> str:
    if not restore.verified:
        return "unverified" if restore.supplied else "absent"
    committed = restore.stores.get(store_id)
    if committed is None:
        return "absent"
    return "verified" if committed == evidence_sha256 else "store_id_matched_digest_mismatch"


def _store_reason(
    store_id: str,
    model: ModelDeclaration,
    coverage: float | None,
    score: int | None,
    link: str,
    deleted: int,
    total: int,
) -> str:
    if score is None:
        return (
            f"Store {store_id!r} declares all {total} records deleted by policy, so source "
            "coverage is not applicable and the store is excluded from aggregation."
        )
    parts: list[str] = []
    if score == 100:
        parts.append(
            f"Store {store_id!r} retains no source text, so re-embedding cannot reproduce "
            "the index."
        )
    elif score == 75:
        parts.append(
            f"Store {store_id!r} retains {coverage:.2f}% of its source text, so only that "
            "subset can be re-embedded."
        )
    elif score == 50:
        parts.append(
            f"Store {store_id!r} has complete source coverage, but the attested model is "
            "vendor-only, so migration requires a replacement model and a full index "
            "rebuild; cost and quality may change."
        )
    else:
        parts.append(
            f"Store {store_id!r} has complete source coverage and the attested model is "
            "self-hostable."
        )
        if score == 25:
            if link == "store_id_matched_digest_mismatch":
                parts.append(
                    "Restore evidence names this store but commits to a different evidence "
                    "digest, so the restore is not counted."
                )
            elif link == "unverified":
                parts.append(
                    "A restore record was supplied but not verified, so no restore is counted."
                )
            else:
                parts.append("No verified restore names this store.")
            parts.append("This is a validated export with a compatible importer.")
        else:
            parts.append(
                "A scanner-verified restore names this store and commits to this evidence digest."
            )
        if not model.weights_pinned:
            parts.append(UNPINNED_WEIGHTS_NOTE)
    if deleted:
        parts.append(f"{deleted} of {total} records are excluded as attested policy deletions.")
    return " ".join(parts)


def _evaluate_store(
    store_id: str,
    model: ModelDeclaration,
    layout: str,
    shard_count: int,
    statuses: Mapping[str, str],
    evidence_sha256: str,
    restore: RestoreEvidence,
) -> StoreResult:
    total = len(statuses)
    retained = sum(1 for value in statuses.values() if value == "retained")
    missing = sum(1 for value in statuses.values() if value == "missing")
    deleted = sum(1 for value in statuses.values() if value == "deleted_by_policy")
    if retained + missing + deleted != total:
        raise ValueError("Source-text counts do not reconcile with the vector count.")
    considered = total - deleted
    link = _restore_link(store_id, evidence_sha256, restore)

    coverage: float | None
    if considered == 0:
        coverage, re_embeddable, score = None, "not_applicable", None
    else:
        coverage = round(retained * 100 / considered, 2)
        if retained == 0:
            re_embeddable, score = "no", 100
        elif retained < considered:
            re_embeddable, score = "partial", 75
        elif not model.self_hostable:
            re_embeddable, score = "yes", 50
        elif link == "verified":
            re_embeddable, score = "yes", 0
        else:
            re_embeddable, score = "yes", 25

    warnings: list[str] = []
    if deleted and deleted * 2 > total:
        warnings.append(ATTESTED_DELETION_WARNING)
    findings: list[Finding] = []
    # Every source_text entry describes an inventoried vector record, so a
    # deleted_by_policy status always means the vector outlived its source text.
    if deleted:
        affected = sorted(name for name, value in statuses.items() if value == "deleted_by_policy")
        findings.append(
            Finding(
                code=RETAINED_VECTOR_FINDING,
                store_id=store_id,
                record_count=len(affected),
                record_ids=tuple(affected[:FINDING_RECORD_CAP]),
                truncated=len(affected) > FINDING_RECORD_CAP,
            )
        )
    return StoreResult(
        store_id=store_id,
        model=model,
        layout=layout,
        shard_count=shard_count,
        vector_count=total,
        retained=retained,
        missing=missing,
        deleted_by_policy=deleted,
        coverage=coverage,
        re_embeddable=re_embeddable,
        score=score,
        restore_link=link,
        reason=_store_reason(store_id, model, coverage, score, link, deleted, total),
        warnings=tuple(warnings),
        findings=tuple(findings),
    )


def _aggregate(stores: tuple[StoreResult, ...]) -> EmbeddingPortability:
    scored = [store for store in stores if store.scored]
    if not scored:
        return EmbeddingPortability(
            stores=stores,
            re_embeddable="not_applicable",
            score=None,
            reason=(
                "Every declared store reports all source text deleted by policy, so no "
                "embedding lock-in score applies. The per-store counts remain reported."
            ),
        )
    # The worst store governs so one portable store cannot hide a locked store.
    limiting = max(scored, key=lambda store: (store.score or 0, store.store_id))
    results = {store.re_embeddable for store in scored}
    if results == {"yes"}:
        aggregate = "yes"
    elif results == {"no"}:
        aggregate = "no"
    else:
        aggregate = "partial"
    return EmbeddingPortability(
        stores=stores,
        re_embeddable=aggregate,
        score=limiting.score,
        reason=limiting.reason,
    )


def scan_embedding_portability(
    evidence_path: Path,
    *,
    evidence_sha256: str,
    restore: RestoreEvidence | None = None,
) -> EmbeddingPortability:
    """Score embedding migration lock-in from a local evidence document.

    ``evidence_sha256`` is the digest of the evidence document the manifest
    references. A store reaches 0 only when a scanner-verified restore names
    that store and commits to this exact digest.
    """
    link = restore or RestoreEvidence()
    root = evidence_path.absolute().parent
    data = read_bounded_file(evidence_path, MAX_EVIDENCE_BYTES)
    if hashlib.sha256(data).hexdigest() != evidence_sha256:
        raise ValueError("Embedding evidence differs from its declared SHA-256.")
    document = _mapping(load_json_document(data, "embedding evidence"), "embedding evidence")
    _exact_keys(document, {"schema_version", "stores"}, "embedding evidence")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Expected an {SCHEMA_VERSION} document.")
    entries = _bounded_list(document["stores"], "embedding stores", MAX_STORES)

    results: list[StoreResult] = []
    seen: set[str] = set()
    for entry in entries:
        store = _mapping(entry, "embedding store")
        _exact_keys(store, {"store_id", "embedding_model", "vectors"}, "embedding store")
        store_id = _identifier(store["store_id"], "store_id")
        if store_id in seen:
            raise ValueError("Duplicate store_id in embedding evidence.")
        seen.add(store_id)
        model = _model(store["embedding_model"])
        vectors = _mapping(store["vectors"], "store vectors")
        layout = vectors.get("layout")
        if not isinstance(layout, str) or layout not in LAYOUTS:
            raise ValueError("Invalid store vector layout.")
        if layout == "single_file":
            _exact_keys(vectors, {"layout", "records"}, "single-file store vectors")
            statuses, shard_count = _single_file_records(vectors["records"], root, model)
        else:
            statuses, shard_count = _shard_records(vectors, root, model)
        results.append(
            _evaluate_store(
                store_id,
                model,
                layout,
                shard_count,
                statuses,
                evidence_sha256,
                link,
            )
        )
    return _aggregate(tuple(results))
