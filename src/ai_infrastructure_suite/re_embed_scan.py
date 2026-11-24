"""Assessing a re-embed record against the inventory it claims to rebuild.

Checkpoint 3b-6. :mod:`ai_infrastructure_suite.re_embed_record` validates a
record's own internal consistency. This module asks the two questions that
validation cannot: does this record actually bind to the store the manifest
declared, and can this scanner reproduce the rebuild rather than take the
record's word for it?

Binding is refused, not ignored
-------------------------------

A record's ``source_store`` must match the manifest's embeddings entry by both
digest and identifier. A record that does not bind is **refused**, because the
alternative -- quietly skipping it -- would let an operator attach a record
produced from a different, more flattering store and have the report simply not
mention the mismatch.

The target store's bytes are found beside the record, where the writing
pipeline puts them, and believed only on digest: the sibling
``embedding-portability.json`` must hash to the record's
``target_store.store_evidence_sha256``. Convention locates the file; the digest
decides whether to trust it. Missing or mismatched means no reproduction, so
the record stays ``attested``.

Seeds are derived, never accepted
---------------------------------

An operator who chooses a seed can grind it: draw query set after query set
until the numbers clear the bar, then publish the one that passed. So the
anchor seed is derived from the two store digests the record already commits
to, and a record whose ``anchor_seed`` is anything else is treated as
``anchor_selection: operator`` -- reported in full, moving no score. That is
the same treatment a hand-picked query set gets, for the same reason.

The reproduction sample is derived from the target store digest alone, so
nothing written into a record can influence which vectors this scanner checks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ai_infrastructure_suite._evidence import load_json_document, read_bounded_file
from ai_infrastructure_suite.re_embed_record import (
    ReEmbedRecord,
    ReEmbedRecordError,
    load_re_embed_record,
)
from ai_infrastructure_suite.reproduction import (
    DEFAULT_SAMPLE_SIZE,
    Reproduction,
    ReproductionError,
    reproduce_store_sample,
)

TARGET_DOCUMENT_NAME: Final = "embedding-portability.json"
MAX_TARGET_DOCUMENT_BYTES: Final = 4 * 1024 * 1024
MAX_SOURCE_TEXT_BYTES: Final = 1024 * 1024
MAX_SHARD_BYTES: Final = 64 * 1024 * 1024
MAX_RECORDS: Final = 100_000

#: Replaces the generic weights sentence, but only when the scanner both
#: recognised the exact identity triple and recomputed a matching sample.
#: Neither condition alone earns it.
DETERMINISTIC_REASON: Final = (
    "This model is a deterministic scheme this scanner re-ran: a sample of {count} "
    "vector(s) was recomputed and matched. Reproduction used source text and code, "
    "not pinned weights."
)


class ReEmbedScanError(ValueError):
    """Raised when a declared re-embed record cannot be trusted as evidence."""


def derive_anchor_seed(source_store_sha256: str, target_store_sha256: str) -> str:
    """The only anchor seed a record may claim and still move a score.

    A function of content the record already commits to, so neither the
    operator nor this scanner can steer it, and a third party recomputes the
    same query set from the record alone.
    """
    payload = f"re-embed-anchor:{source_store_sha256}:{target_store_sha256}"
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ReEmbedAssessment:
    """What one re-embed record is worth as evidence."""

    record: ReEmbedRecord
    path: str
    sha256: str
    reproduction: Reproduction
    label: str
    anchor_selection: str
    seed_was_ground: bool
    improves_score: bool
    reason: str

    @property
    def reproduced(self) -> bool:
        return self.reproduction.reproduced


def _reproduce(record: ReEmbedRecord, record_path: Path, sample_size: int) -> Reproduction:
    """Locate the target store beside the record, then recompute a sample of it.

    Convention finds the file; the digest decides whether to believe it. Every
    refusal returns rather than raises: a rebuild this scanner cannot reproduce
    is ordinary evidence that stays ``attested``, not a broken report.
    """
    model = record.target_model
    target_path = record_path.parent / TARGET_DOCUMENT_NAME
    try:
        data = read_bounded_file(target_path, MAX_TARGET_DOCUMENT_BYTES)
    except (OSError, ValueError):
        return Reproduction(
            attempted=False,
            matched=False,
            sample_size=0,
            records_checked=(),
            reason=(
                f"No readable {TARGET_DOCUMENT_NAME} beside the record, so the rebuild "
                "was not reproduced."
            ),
        )
    if hashlib.sha256(data).hexdigest() != record.target_store.store_evidence_sha256:
        return Reproduction(
            attempted=False,
            matched=False,
            sample_size=0,
            records_checked=(),
            reason=(
                f"The {TARGET_DOCUMENT_NAME} beside the record does not match the target "
                "digest the record declares, so the rebuild was not reproduced."
            ),
        )
    document = load_json_document(data, TARGET_DOCUMENT_NAME)
    if not isinstance(document, dict) or not isinstance(document.get("stores"), list):
        raise ReEmbedScanError("The target document declares no stores.")
    matches = [
        item
        for item in document["stores"]
        if isinstance(item, dict) and item.get("store_id") == record.target_store.store_id
    ]
    if len(matches) != 1:
        raise ReEmbedScanError(
            f"Expected exactly one store {record.target_store.store_id!r} in the target document."
        )
    try:
        return reproduce_store_sample(
            matches[0],
            target_path.parent,
            model_id=model.id,
            revision=model.revision,
            dimensions=model.dimensions,
            seed_digest=record.target_store.store_evidence_sha256,
            sample_size=sample_size,
        )
    except ReproductionError as exc:
        raise ReEmbedScanError(str(exc)) from None


def scan_re_embed_record(
    record_path: Path,
    *,
    declared_sha256: str,
    source_store_sha256: str,
    source_store_ids: frozenset[str],
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> ReEmbedAssessment:
    """Validate, bind, and try to reproduce one declared re-embed record."""
    try:
        record = load_re_embed_record(record_path, expected_sha256=declared_sha256)
    except ReEmbedRecordError as exc:
        raise ReEmbedScanError(str(exc)) from None

    # Binding first. A record that describes a different store is refused
    # rather than skipped, so a mismatch cannot pass unmentioned.
    if record.source_store.store_evidence_sha256 != source_store_sha256:
        raise ReEmbedScanError(
            "The re-embed record's source store digest does not match the embeddings evidence "
            "this manifest declares, so it describes a different store."
        )
    if record.source_store.store_id not in source_store_ids:
        raise ReEmbedScanError(
            f"The re-embed record names source store {record.source_store.store_id!r}, "
            "which the embeddings evidence does not contain."
        )

    reproduction = _reproduce(record, record_path, sample_size)
    label = record.label(rebuild_reproduced=reproduction.reproduced)

    # A seed nobody could choose is the only seed that counts. Anything else is
    # an operator-chosen query set by another name.
    derived = derive_anchor_seed(source_store_sha256, record.target_store.store_evidence_sha256)
    quality = record.quality
    declared_seed = quality.anchor_seed if quality is not None else None
    claimed_seeded = quality is not None and quality.anchor_selection == "seeded_random"
    seed_was_ground = claimed_seeded and declared_seed != derived
    if seed_was_ground:
        # A seed the operator could choose is a seed the operator could grind,
        # so the record's own claim of a seeded draw is overridden here rather
        # than believed. This must not fall through to the declared value.
        anchor_selection = "operator"
    elif claimed_seeded:
        anchor_selection = "seeded_random"
    elif quality is not None and quality.anchor_selection is not None:
        anchor_selection = quality.anchor_selection
    else:
        anchor_selection = "operator"

    improves = (
        record.improves_score(rebuild_reproduced=reproduction.reproduced)
        and anchor_selection == "seeded_random"
    )

    reasons = [reproduction.reason]
    if seed_was_ground:
        reasons.append(
            "The record declares a seeded query set with a seed this scanner did not derive, "
            "so it is read as operator-chosen and moves no score."
        )
    if improves:
        reasons.append("Moves this store from 50 to 25.")
    elif reproduction.reproduced:
        reasons.append("Reported, but moves no score.")
    return ReEmbedAssessment(
        record=record,
        path=record_path.name,
        sha256=declared_sha256,
        reproduction=reproduction,
        label=label,
        anchor_selection=anchor_selection,
        seed_was_ground=seed_was_ground,
        improves_score=improves,
        reason=" ".join(reasons),
    )
