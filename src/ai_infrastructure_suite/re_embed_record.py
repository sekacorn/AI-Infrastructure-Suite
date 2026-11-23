"""Reading and validating ``re-embed-record.v1`` evidence.

A re-embed record says that one embedding store was rebuilt with a different
model. It links the source and target ``embedding-portability.v1`` documents by
digest, declares the target model identity, and accounts for what was rebuilt,
what left the machine, and what a quality comparison found.

This module only reads. It never embeds text, loads weights, opens a socket, or
imports a provider SDK: the suite stays diagnostics-only. Reproducing a rebuild
belongs to the tool that produced it.

A ``quality`` block is evidence that a stated comparison ran and what it
returned. It is never evidence that retrieval is good. The query set is the
operator's, so its representativeness is attested and is reported as such.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import load_json_document, read_bounded_file

SCHEMA_VERSION: Final = "re-embed-record.v1"

MAX_RECORD_BYTES: Final = 1024 * 1024
MAX_IDENTIFIER_CHARS: Final = 128
MAX_LICENSE_CHARS: Final = 512
MAX_REASON_CHARS: Final = 512
MAX_DIMENSIONS: Final = 8192
MAX_BATCH_SIZE: Final = 10_000
MAX_TOP_K: Final = 1000
MAX_FAILURES: Final = 20

AVAILABILITY: Final = frozenset({"open_weight_self_hostable", "vendor_only"})
QUALITY_STATUS: Final = frozenset({"sampled", "below_threshold", "not_measured"})

#: The default batch size, chosen to stay bounded on a 16 GB machine. Sixty-four
#: records at the 1 MiB per-record source-text ceiling is 64 MiB of text, and 64
#: vectors at the 8192-dimension ceiling is a few megabytes. The store is never
#: loaded whole, so this holds regardless of how large it is.
DEFAULT_BATCH_SIZE: Final = 64

#: Stable identities for the two embedding schemes already shipped in this
#: ecosystem. They are descriptive names for existing code, not new APIs.
#: Dimensions are declared per store and are deliberately absent here: model
#: identity is the triple id + revision + dimensions, so the same scheme at two
#: dimensions is two models.
KNOWN_SCHEMES: Final = {
    "agentforge.hashed-bow.blake2b": "unsigned term frequency over blake2b buckets",
    "privateaistack.hashed-bow.sha256-signed": "signed unit weights over sha256 buckets",
}

#: What a re-embed record can never establish, whatever its quality status.
ATTESTED_CLAIMS: Final = (
    "model availability",
    "query set representativeness",
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ReEmbedRecordError(ValueError):
    """The record is not valid ``re-embed-record.v1`` evidence."""


@dataclass(frozen=True)
class StoreReference:
    """A store named by identifier and bound to content by digest."""

    store_id: str
    store_evidence_sha256: str


@dataclass(frozen=True)
class ModelIdentity:
    """The triple that identifies a model, plus its attested claims."""

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

    @property
    def is_known_scheme(self) -> bool:
        """Whether this names one of the schemes shipped in this ecosystem."""
        return self.id in KNOWN_SCHEMES

    @property
    def triple(self) -> tuple[str, str, int]:
        return (self.id, self.revision, self.dimensions)


@dataclass(frozen=True)
class BatchLog:
    batch_size: int
    batches_completed: int
    records_embedded: int
    resumed: bool
    checkpoint_sha256: str | None


@dataclass(frozen=True)
class Failure:
    record_id: str
    reason: str


@dataclass(frozen=True)
class Coverage:
    total_records: int
    retained_rebuilt: int
    missing_skipped: int
    deleted_by_policy_skipped: int
    failed: int
    failures: tuple[Failure, ...]
    failures_truncated: bool

    @property
    def complete(self) -> bool:
        """Whether every record that could be rebuilt was rebuilt.

        Records with missing or policy-deleted source text have no text to
        embed, so they do not count against completeness. A failure does.
        """
        return self.failed == 0


@dataclass(frozen=True)
class Egress:
    remote_provider_used: bool
    provider: str | None
    records_transmitted: int
    raw_source_text_transmitted: bool


@dataclass(frozen=True)
class Quality:
    status: str
    query_set_size: int
    top_k: int
    sufficient_evidence: bool | None
    metrics: dict[str, float]
    checks: tuple[dict[str, Any], ...]
    notes: str | None

    @property
    def passed(self) -> bool:
        return self.status == "sampled"


@dataclass(frozen=True)
class ReEmbedRecord:
    """A validated re-embed record."""

    source_store: StoreReference
    target_store: StoreReference
    target_model: ModelIdentity
    batch_log: BatchLog
    coverage: Coverage
    egress: Egress
    quality: Quality | None

    @property
    def quality_status(self) -> str:
        """``sampled``, ``below_threshold`` or ``not_measured``."""
        return self.quality.status if self.quality is not None else "not_measured"

    def label(self, *, rebuild_reproduced: bool) -> str:
        """The verification label this record earns.

        ``rebuild_reproduced`` is the caller's statement that it re-ran the
        re-embedding itself and obtained the target store's declared digest. A
        rebuild nobody reproduced is ``attested``, however good its quality
        numbers look, which is what keeps a live-model run out of the verified
        levels.

        Not wired into ``exit-report`` yet; that is a later checkpoint.
        """
        if not rebuild_reproduced:
            return "attested"
        return {
            "sampled": "verified (re-embedded, quality sampled)",
            "below_threshold": "verified (re-embedded, quality below threshold)",
            "not_measured": "verified (re-embedded, quality not measured)",
        }[self.quality_status]

    def improves_score(self, *, rebuild_reproduced: bool) -> bool:
        """Whether this record may move a store from 50 to 25.

        All four approved conditions must hold: the rebuild was reproduced by
        the caller, the quality check passed its thresholds, the target model is
        self-hostable, and no retained record failed to rebuild. A record can
        never earn a score of 0; that needs a verified restore.
        """
        return (
            rebuild_reproduced
            and self.quality_status == "sampled"
            and self.target_model.self_hostable
            and self.coverage.complete
        )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReEmbedRecordError(f"Expected an object for {label}.")
    return value


def _exact_keys(document: dict[str, Any], expected: set[str], label: str) -> None:
    if set(document) != expected:
        raise ReEmbedRecordError(f"Unexpected fields in {label}.")


def _text(value: object, label: str, limit: int = MAX_IDENTIFIER_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ReEmbedRecordError(f"Invalid {label}.")
    return value


def _identifier(value: object, label: str) -> str:
    name = _text(value, label)
    if not _IDENTIFIER.fullmatch(name):
        raise ReEmbedRecordError(f"Invalid {label}.")
    return name


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ReEmbedRecordError(f"Invalid {label}.")
    return value


def _count(value: object, label: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReEmbedRecordError(f"Invalid {label}.")
    if maximum is not None and value > maximum:
        raise ReEmbedRecordError(f"Invalid {label}.")
    return value


def _flag(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ReEmbedRecordError(f"Invalid {label}.")
    return value


def _ratio(value: object, label: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReEmbedRecordError(f"Invalid {label}.")
    if not math.isfinite(value) or not low <= value <= high:
        raise ReEmbedRecordError(f"Invalid {label}.")
    return float(value)


def _store_reference(raw: object, label: str) -> StoreReference:
    document = _mapping(raw, label)
    _exact_keys(document, {"store_id", "store_evidence_sha256"}, label)
    return StoreReference(
        store_id=_identifier(document["store_id"], f"{label} store_id"),
        store_evidence_sha256=_digest(
            document["store_evidence_sha256"], f"{label} store evidence digest"
        ),
    )


def _model_identity(raw: object) -> ModelIdentity:
    document = _mapping(raw, "target_model")
    required = {"id", "revision", "dimensions", "availability", "availability_verification"}
    optional = {"weights_sha256", "license"}
    if not required <= set(document) or set(document) - (required | optional):
        raise ReEmbedRecordError("Unexpected fields in target_model.")
    dimensions = document["dimensions"]
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise ReEmbedRecordError("Invalid target model dimensions.")
    if not 1 <= dimensions <= MAX_DIMENSIONS:
        raise ReEmbedRecordError("Invalid target model dimensions.")
    availability = _text(document["availability"], "target model availability")
    if availability not in AVAILABILITY:
        raise ReEmbedRecordError("Invalid target model availability.")
    if document["availability_verification"] != "attested":
        raise ReEmbedRecordError("Target model availability must be labeled attested.")
    weights = document.get("weights_sha256")
    license_text = document.get("license")
    return ModelIdentity(
        id=_text(document["id"], "target model id"),
        revision=_text(document["revision"], "target model revision"),
        dimensions=dimensions,
        availability=availability,
        weights_sha256=None if weights is None else _digest(weights, "model weights digest"),
        license=(
            None
            if license_text is None
            else _text(license_text, "model license", MAX_LICENSE_CHARS)
        ),
    )


def _batch_log(raw: object) -> BatchLog:
    document = _mapping(raw, "batch_log")
    required = {"batch_size", "batches_completed", "records_embedded", "resumed"}
    if not required <= set(document) or set(document) - (required | {"checkpoint_sha256"}):
        raise ReEmbedRecordError("Unexpected fields in batch_log.")
    batch_size = _count(document["batch_size"], "batch_size", maximum=MAX_BATCH_SIZE)
    if batch_size < 1:
        raise ReEmbedRecordError("Invalid batch_size.")
    checkpoint = document.get("checkpoint_sha256")
    return BatchLog(
        batch_size=batch_size,
        batches_completed=_count(document["batches_completed"], "batches_completed"),
        records_embedded=_count(document["records_embedded"], "records_embedded"),
        resumed=_flag(document["resumed"], "resumed"),
        checkpoint_sha256=(
            None if checkpoint is None else _digest(checkpoint, "checkpoint digest")
        ),
    )


def _coverage(raw: object) -> Coverage:
    document = _mapping(raw, "coverage")
    required = {
        "total_records",
        "retained_rebuilt",
        "missing_skipped",
        "deleted_by_policy_skipped",
        "failed",
    }
    optional = {"failures", "failures_truncated"}
    if not required <= set(document) or set(document) - (required | optional):
        raise ReEmbedRecordError("Unexpected fields in coverage.")
    counts = {name: _count(document[name], name) for name in required}
    parts = (
        counts["retained_rebuilt"]
        + counts["missing_skipped"]
        + counts["deleted_by_policy_skipped"]
        + counts["failed"]
    )
    if parts != counts["total_records"]:
        raise ReEmbedRecordError("Coverage counts do not reconcile with total_records.")

    failures: list[Failure] = []
    raw_failures = document.get("failures", [])
    if not isinstance(raw_failures, list):
        raise ReEmbedRecordError("Invalid coverage failures.")
    if len(raw_failures) > MAX_FAILURES:
        raise ReEmbedRecordError("Too many coverage failures listed.")
    for entry in raw_failures:
        item = _mapping(entry, "coverage failure")
        _exact_keys(item, {"record_id", "reason"}, "coverage failure")
        failures.append(
            Failure(
                record_id=_identifier(item["record_id"], "failure record_id"),
                reason=_text(item["reason"], "failure reason", MAX_REASON_CHARS),
            )
        )
    truncated = _flag(document.get("failures_truncated", False), "failures_truncated")
    # A listed failure that is not counted, or a truncation flag on a list that
    # was not truncated, would let a reader under-read the damage.
    if len(failures) > counts["failed"]:
        raise ReEmbedRecordError("More failures listed than counted.")
    if not truncated and counts["failed"] > len(failures):
        raise ReEmbedRecordError("Fewer failures listed than counted without a truncation flag.")
    return Coverage(
        total_records=counts["total_records"],
        retained_rebuilt=counts["retained_rebuilt"],
        missing_skipped=counts["missing_skipped"],
        deleted_by_policy_skipped=counts["deleted_by_policy_skipped"],
        failed=counts["failed"],
        failures=tuple(failures),
        failures_truncated=truncated,
    )


def _egress(raw: object) -> Egress:
    document = _mapping(raw, "egress")
    required = {"remote_provider_used", "records_transmitted", "raw_source_text_transmitted"}
    if not required <= set(document) or set(document) - (required | {"provider"}):
        raise ReEmbedRecordError("Unexpected fields in egress.")
    used = _flag(document["remote_provider_used"], "remote_provider_used")
    provider = document.get("provider")
    transmitted = _count(document["records_transmitted"], "records_transmitted")
    raw_text = _flag(document["raw_source_text_transmitted"], "raw_source_text_transmitted")
    # A local run cannot have transmitted anything. Letting these disagree would
    # make the egress block useless as a privacy report.
    if not used and (provider is not None or transmitted or raw_text):
        raise ReEmbedRecordError("A local run cannot report transmitted content.")
    if used and provider is None:
        raise ReEmbedRecordError("A remote run must name its provider.")
    return Egress(
        remote_provider_used=used,
        provider=None if provider is None else _text(provider, "egress provider"),
        records_transmitted=transmitted,
        raw_source_text_transmitted=raw_text,
    )


def _metrics(raw: object) -> dict[str, float]:
    document = _mapping(raw, "quality metrics")
    bounds = {
        "top_k_overlap": (0.0, 1.0),
        "rank_correlation": (-1.0, 1.0),
        "hit_rate": (0.0, 1.0),
        "hit_rate_before": (0.0, 1.0),
        "mrr": (0.0, 1.0),
        "mrr_before": (0.0, 1.0),
    }
    if set(document) - set(bounds):
        raise ReEmbedRecordError("Unexpected fields in quality metrics.")
    return {
        name: _ratio(value, f"metric {name}", low=bounds[name][0], high=bounds[name][1])
        for name, value in document.items()
    }


def _quality(raw: object) -> Quality:
    document = _mapping(raw, "quality")
    required = {"status", "query_set_size", "top_k"}
    optional = {"sufficient_evidence", "metrics", "checks", "notes"}
    if not required <= set(document) or set(document) - (required | optional):
        raise ReEmbedRecordError("Unexpected fields in quality.")
    status = _text(document["status"], "quality status")
    if status not in QUALITY_STATUS:
        raise ReEmbedRecordError("Invalid quality status.")
    top_k = _count(document["top_k"], "top_k", maximum=MAX_TOP_K)
    if top_k < 1:
        raise ReEmbedRecordError("Invalid top_k.")
    query_set_size = _count(document["query_set_size"], "query_set_size")
    sufficient = document.get("sufficient_evidence")
    if sufficient is not None:
        sufficient = _flag(sufficient, "sufficient_evidence")
    metrics = _metrics(document["metrics"]) if "metrics" in document else {}

    checks: list[dict[str, Any]] = []
    raw_checks = document.get("checks", [])
    if not isinstance(raw_checks, list):
        raise ReEmbedRecordError("Invalid quality checks.")
    for entry in raw_checks:
        item = _mapping(entry, "quality check")
        required_check = {"name", "passed", "actual", "threshold"}
        if not required_check <= set(item) or set(item) - (required_check | {"detail"}):
            raise ReEmbedRecordError("Unexpected fields in quality check.")
        checks.append(
            {
                "name": _text(item["name"], "check name"),
                "passed": _flag(item["passed"], "check passed"),
                "actual": _ratio(item["actual"], "check actual", low=-1e9, high=1e9),
                "threshold": _ratio(item["threshold"], "check threshold", low=-1e9, high=1e9),
                "detail": (
                    None
                    if item.get("detail") is None
                    else _text(item["detail"], "check detail", MAX_REASON_CHARS)
                ),
            }
        )

    # The status has to agree with the evidence behind it, or the three labels
    # mean nothing.
    if status == "sampled":
        if not checks:
            raise ReEmbedRecordError("A sampled quality result requires its threshold checks.")
        if any(not check["passed"] for check in checks):
            raise ReEmbedRecordError("A sampled quality result cannot contain a failed check.")
        if query_set_size < 1:
            raise ReEmbedRecordError("A sampled quality result requires a non-empty query set.")
        if sufficient is False:
            raise ReEmbedRecordError(
                "A sampled quality result cannot declare insufficient evidence."
            )
    if status == "below_threshold" and not any(not check["passed"] for check in checks):
        raise ReEmbedRecordError(
            "A below-threshold quality result requires at least one failed check."
        )
    if status == "not_measured" and (metrics or checks):
        raise ReEmbedRecordError("An unmeasured quality result cannot carry metrics or checks.")
    return Quality(
        status=status,
        query_set_size=query_set_size,
        top_k=top_k,
        sufficient_evidence=sufficient,
        metrics=metrics,
        checks=tuple(checks),
        notes=(
            None
            if document.get("notes") is None
            else _text(document["notes"], "quality notes", MAX_REASON_CHARS)
        ),
    )


def parse_re_embed_record(data: bytes) -> ReEmbedRecord:
    """Validate ``re-embed-record.v1`` bytes and return the record."""
    document = _mapping(load_json_document(data, "re-embed record"), "re-embed record")
    required = {
        "schema_version",
        "source_store",
        "target_store",
        "target_model",
        "batch_log",
        "coverage",
        "egress",
    }
    if not required <= set(document) or set(document) - (required | {"quality"}):
        raise ReEmbedRecordError("Unexpected fields in re-embed record.")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ReEmbedRecordError(f"Expected a {SCHEMA_VERSION} document.")

    source = _store_reference(document["source_store"], "source_store")
    target = _store_reference(document["target_store"], "target_store")
    # A record whose source and target are the same document has not rebuilt
    # anything with a different model.
    if source.store_evidence_sha256 == target.store_evidence_sha256:
        raise ReEmbedRecordError("Source and target stores must be different documents.")

    model = _model_identity(document["target_model"])
    batch_log = _batch_log(document["batch_log"])
    coverage = _coverage(document["coverage"])
    if batch_log.records_embedded != coverage.retained_rebuilt:
        raise ReEmbedRecordError(
            "Records embedded in the batch log differ from the rebuilt coverage count."
        )
    egress = _egress(document["egress"])
    if egress.remote_provider_used and egress.records_transmitted > coverage.retained_rebuilt:
        raise ReEmbedRecordError("More records transmitted than were rebuilt.")
    quality = _quality(document["quality"]) if "quality" in document else None
    return ReEmbedRecord(
        source_store=source,
        target_store=target,
        target_model=model,
        batch_log=batch_log,
        coverage=coverage,
        egress=egress,
        quality=quality,
    )


def load_re_embed_record(path: Path, *, expected_sha256: str | None = None) -> ReEmbedRecord:
    """Read and validate a record from a bounded local file.

    ``expected_sha256`` binds the record to a digest a manifest declared, the
    same way the embedding and restore scanners bind theirs.
    """
    data = read_bounded_file(path, MAX_RECORD_BYTES)
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != _digest(
        expected_sha256, "expected digest"
    ):
        raise ReEmbedRecordError("Re-embed record differs from its declared SHA-256.")
    return parse_re_embed_record(data)
