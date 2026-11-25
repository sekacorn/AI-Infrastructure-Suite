"""Scoring the ``memory`` vector from an ``agent-memory.v1`` export.

The manifest's ``memory`` entry declares ``state: memory_inventory`` with the
export's path and SHA-256, and optionally an ``import_report``: the
``agent-memory-import.v1`` a destination wrote when it imported this export.

**What the scanner verifies itself:** the export's bytes and every shard;
every item's text against its digest; ordering, uniqueness, counts and
tombstones; a lossless canonical round trip of every item; when a store links an
``embedding-portability.v1`` inventory, that the inventory verifies at its own
digest, names the store, and records for every item a vector built from exactly
that item's text; and, when an import report is supplied, that it binds to this
export by digest and accounts for every item and tombstone.

**What stays attested:** that the export is complete; that policy deletions
happened; that redaction was adequate; ``created_at`` values; provenance of
imported items; and that any import actually ran at the destination.

Per store: 25 needs a verified export, full vector coverage when vectors are
linked, and a bound import report that refused nothing; otherwise 50. Invalid
evidence is refused, never scored. **Nothing reaches 0**: the scanner never runs
a restore of memory, and 0 is never given on attested evidence. The worst store
sets the vector. The suite never imports AgentForge.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import portable_relative_path
from ai_infrastructure_suite.agent_memory import (
    AgentMemoryExport,
    MemoryImportReport,
    MemoryStoreSummary,
    load_agent_memory,
    load_memory_import_report,
)

STATE: Final = "memory_inventory"
LEVEL: Final = "verified (memory export)"
_ENTRY_KEYS: Final = frozenset({"state", "path", "sha256"})
_OPTIONAL_KEYS: Final = frozenset({"import_report"})

CLAIM_COMPLETE: Final = "that the export holds every item"
CLAIM_DELETIONS: Final = "that policy deletions happened"
CLAIM_REDACTION: Final = "that the redaction profile was adequate"
CLAIM_CREATED_AT: Final = "created_at values"
CLAIM_PROVENANCE: Final = "provenance of imported items"
CLAIM_IMPORT_RAN: Final = "that the import ran at the destination"
CLAIM_EMBEDDING_MODEL: Final = "the linked embedding model's declarations"

FINDING_RETAINED: Final = "vector retained after item deletion"
FINDING_ORPHAN: Final = "vector without a memory item"


@dataclass(frozen=True)
class MemoryVectorResult:
    """The vector row and its per-store detail section."""

    row: dict[str, Any]
    section: list[dict[str, Any]]


def _reference(entry: object, key: str) -> tuple[str, str]:
    if not isinstance(entry, dict):
        raise ValueError(f"Invalid {key} reference.")
    if entry.keys() != {"path", "sha256"}:
        raise ValueError(f"Invalid {key} reference.")
    digest = entry["sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"Invalid {key} digest.")
    return portable_relative_path(entry["path"]), digest


def _embeddings(store: MemoryStoreSummary, export_root: Path) -> tuple[dict[str, Any] | None, int]:
    """Bind a store's items to their vectors. Returns the detail and uncovered count."""
    link = store.embeddings
    if link is None:
        return None, 0
    from ai_infrastructure_suite.embedding_portability import scan_embedding_portability

    scan = scan_embedding_portability(
        export_root / link.inventory_path, evidence_sha256=link.inventory_sha256
    )
    matches = [s for s in scan.stores if s.store_id == link.inventory_store_id]
    if not matches:
        raise ValueError("A memory store's embedding inventory names no such store.")
    recorded = matches[0].recorded_source_sha256
    bound = uncovered = 0
    for item_id, digest in store.content_digests.items():
        if item_id not in recorded:
            uncovered += 1
        elif recorded[item_id] != digest:
            # The vector was built from different text: invalid evidence, not
            # a lower score.
            raise ValueError(
                "A memory item's text differs from the text its vector was built from."
            )
        else:
            bound += 1
    extra = set(recorded) - set(store.content_digests)
    findings: list[dict[str, Any]] = []
    retained = sorted(extra & store.tombstone_ids)
    orphan = sorted(extra - store.tombstone_ids)
    if retained:
        findings.append({"finding": FINDING_RETAINED, "count": len(retained)})
    if orphan:
        findings.append({"finding": FINDING_ORPHAN, "count": len(orphan)})
    detail = {
        "inventory_path": link.inventory_path,
        "inventory_sha256": link.inventory_sha256,
        "inventory_store_id": link.inventory_store_id,
        "items_bound": bound,
        "items_without_vector": uncovered,
        "findings": findings,
    }
    return detail, uncovered


def _bind_import(report: MemoryImportReport, export: AgentMemoryExport) -> dict[str, Any]:
    """Refuse an import report that is not about exactly this export."""
    if report.source_export_sha256 != export.document_sha256:
        raise ValueError("The import report is bound to a different export digest.")
    if report.source_export_id != export.export_id:
        raise ValueError("The import report names a different export.")
    by_store = {store.source_store_id: store for store in report.stores}
    if by_store.keys() != {store.store_id for store in export.stores}:
        raise ValueError("The import report does not cover exactly this export's stores.")
    for store in export.stores:
        entry = by_store[store.store_id]
        if (
            entry.received != store.item_count
            or entry.tombstones_applied != store.deleted_by_policy
        ):
            raise ValueError("The import report's counts do not match this export.")
    return dict(by_store)


def _store_row(
    store: MemoryStoreSummary,
    embeddings: dict[str, Any] | None,
    uncovered: int,
    imported: Any | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    if embeddings is not None and uncovered:
        blockers.append(f"{uncovered} item(s) have no vector in the linked inventory")
    if imported is None:
        blockers.append("no import report is bound to this export")
    elif imported.refused:
        blockers.append(f"the destination refused {imported.refused} item(s)")
    if blockers:
        score = 50
        reason = "Verified portable export; not 25 because " + "; ".join(blockers) + "."
    else:
        score = 25
        reason = (
            "Verified export"
            + (" with every item bound to its vector" if embeddings is not None else "")
            + ", and an import report bound to it by digest accounts for every item "
            "(the import itself is attested)."
        )
    return {
        "store_id": store.store_id,
        "kind": store.kind,
        "layout": store.layout,
        "shard_count": store.shard_count,
        "active": store.active,
        "quarantined": store.quarantined,
        "deleted_by_policy": store.deleted_by_policy,
        "imported_items": store.imported,
        "canonical_sha256": store.canonical_sha256,
        "embeddings": embeddings,
        "import": None
        if imported is None
        else {
            "destination_store_id": imported.destination_store_id,
            "imported": imported.imported,
            "unchanged": imported.unchanged,
            "refused": imported.refused,
        },
        "score": score,
        "verification_level": LEVEL,
        "reason": reason,
    }


def build_vector(root: Path, entry: object) -> MemoryVectorResult:
    """Score the memory vector from a manifest entry declaring ``memory_inventory``."""
    if not isinstance(entry, dict) or entry.get("state") != STATE:
        raise ValueError("Expected a memory_inventory entry.")
    if not entry.keys() >= _ENTRY_KEYS or entry.keys() - _ENTRY_KEYS - _OPTIONAL_KEYS:
        raise ValueError("A memory_inventory entry takes state, path, sha256 and import_report.")
    path, digest = _reference({"path": entry["path"], "sha256": entry["sha256"]}, "memory")
    export = load_agent_memory(root / path, expected_sha256=digest)
    export_root = (root / path).absolute().parent

    import_entry = entry.get("import_report")
    import_ref: dict[str, str] | None = None
    by_store: dict[str, Any] = {}
    report: MemoryImportReport | None = None
    if import_entry is not None:
        report_path, report_digest = _reference(import_entry, "import_report")
        report = load_memory_import_report(root / report_path, expected_sha256=report_digest)
        by_store = _bind_import(report, export)
        import_ref = {"path": report_path, "sha256": report_digest}

    section: list[dict[str, Any]] = []
    for store in export.stores:
        detail, uncovered = _embeddings(store, export_root)
        section.append(_store_row(store, detail, uncovered, by_store.get(store.store_id)))

    worst = max(section, key=lambda row: row["score"])
    attested = [CLAIM_COMPLETE]
    if any(store.deleted_by_policy for store in export.stores):
        attested.append(CLAIM_DELETIONS)
    if export.redaction_profile is not None:
        attested.append(CLAIM_REDACTION)
    attested.append(CLAIM_CREATED_AT)
    if any(store.imported for store in export.stores):
        attested.append(CLAIM_PROVENANCE)
    if report is not None:
        attested.append(CLAIM_IMPORT_RAN)
    if any(store.embeddings is not None for store in export.stores):
        attested.append(CLAIM_EMBEDDING_MODEL)

    count = len(section)
    reason = (
        f"Worst of {count} store(s): {worst['store_id']} scores {worst['score']}. "
        f"{worst['reason']} "
        + (
            "No redaction profile was applied at export. "
            if export.redaction_profile is None
            else ""
        )
        + f"Attested, not re-run: {', '.join(attested)}."
    )
    row: dict[str, Any] = {
        "state": STATE,
        "score": worst["score"],
        "verification_level": LEVEL,
        "reason": reason,
        "path": path,
        "sha256": digest,
        "attested_claims": attested,
    }
    if import_ref is not None:
        row["import_report"] = import_ref
    return MemoryVectorResult(row=row, section=section)
