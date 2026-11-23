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

The optional fields added for the 3b-4 comparison are additive within
``re-embed-record.v1``: a record written before them still parses, and one
written with them is refused by any reader older than this release. Reading the
new fields needs ai-infrastructure-suite 0.1.0b3 or later.
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
HOSTING_MODE: Final = frozenset({"local", "hosted"})
PRIVACY_CLASSIFICATION: Final = frozenset(
    {"public", "internal", "confidential", "restricted", "unknown"}
)
MAX_WARNINGS: Final = 16
EXECUTOR_KIND: Final = frozenset({"local_builtin", "external_unverified"})
EXECUTION_VERIFICATION: Final = frozenset({"registry_owned", "attested"})
TRANSMISSION_BASIS: Final = frozenset({"handed_to_executor", "no_transmission"})
TRANSMISSION_ACCOUNTING: Final = frozenset({"distinct_records_in_completed_batches"})
DELIVERY_VERIFICATION: Final = frozenset({"unverified"})
NETWORK_BEHAVIOR_VERIFICATION: Final = frozenset({"attested"})
MAX_ENDPOINT_CHARS: Final = 2048
QUALITY_STATUS: Final = frozenset({"sampled", "below_threshold", "unjudged", "not_measured"})
QUALITY_MODE: Final = frozenset({"record_anchored", "text"})
METRIC_BASIS: Final = frozenset({"neighborhood_agreement", "retrieval_relevance"})
ANCHOR_SELECTION: Final = frozenset({"operator", "seeded_random"})
SIMILARITY: Final = frozenset({"cosine"})
MAX_THRESHOLDS: Final = 16

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
#: The thresholds belong here because the operator chose them: a run that
#: "passed" passed the bar its own operator set, and a reader who is shown the
#: verdict without the bar has been shown half the sentence.
ATTESTED_CLAIMS: Final = (
    "model availability",
    "query set representativeness",
    "quality thresholds, which the operator declared",
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
    executor_kind: str | None = None
    execution_verification: str | None = None
    external_execution_confirmed: bool | None = None
    executor_id: str | None = None
    endpoint_origin: str | None = None
    network_behavior_verification: str | None = None
    records_submitted_to_external_executor: int | None = None
    transmission_basis: str | None = None
    transmission_accounting: str | None = None
    transmission_attempts_may_exceed_count: bool | None = None
    delivery_verification: str | None = None
    hosting_mode: str | None = None
    hosted_execution_confirmed: bool | None = None
    dataset_privacy_classification: str | None = None
    redaction_applied: bool | None = None
    telemetry_enabled: bool | None = None
    warnings: tuple[str, ...] = ()

    @property
    def stayed_local(self) -> bool:
        """Whether nothing left the machine.

        Read from the facts rather than from ``hosting_mode``: a run can be
        configured as hosted, be blocked, and transmit nothing.
        """
        return not self.remote_provider_used and self.records_transmitted == 0

    @property
    def used_external_executor(self) -> bool:
        """Whether operator-supplied code ran the embedder."""
        return self.executor_kind == "external_unverified"

    @property
    def delivery_established(self) -> bool:
        """Always false.

        A record can say what was handed to a callable. Nothing in this format
        can say a provider received it, so this exists to be called rather
        than to be inferred from a count.
        """
        return False

    @property
    def submission_count(self) -> int:
        """Distinct records handed to an injected executor.

        Zero for a registry-owned run, which hands nothing to anything. Falls
        back to ``records_transmitted`` for a record written before the
        explicit field existed.
        """
        if self.records_submitted_to_external_executor is not None:
            return self.records_submitted_to_external_executor
        return self.records_transmitted if self.used_external_executor else 0

    @property
    def unverifiable_claims(self) -> tuple[str, ...]:
        """The parts of this block no scanner can check.

        Classification and redaction are judgements about content that only the
        operator can make. An injected executor's network behaviour is code
        this scanner never ran. Reporting any of them as facts would make an
        attestation look like a measurement.
        """
        claims = []
        if self.dataset_privacy_classification is not None:
            claims.append("dataset privacy classification")
        if self.redaction_applied is not None:
            claims.append("whether transmitted text was redacted")
        if self.used_external_executor:
            claims.append("injected executor network behaviour")
            claims.append("whether any provider received a submitted record")
        return tuple(claims)


@dataclass(frozen=True)
class Quality:
    status: str
    query_set_size: int
    top_k: int
    sufficient_evidence: bool | None
    metrics: dict[str, float]
    checks: tuple[dict[str, Any], ...]
    notes: str | None
    mode: str | None = None
    metric_basis: str | None = None
    anchor_selection: str | None = None
    anchor_seed: str | None = None
    similarity: str | None = None
    candidate_pool_size: int | None = None
    thresholds: dict[str, float] | None = None
    tie_break: str | None = None
    boundary_ties: int | None = None
    queries_skipped: int | None = None
    correlation_undefined: bool | None = None
    top_k_capped: bool | None = None

    @property
    def passed(self) -> bool:
        return self.status == "sampled"

    @property
    def judged(self) -> bool:
        """Whether a verdict was reached at all.

        ``unjudged`` means the comparison ran, produced metrics, and was held
        against no agreement floor, because the operator declared none. The
        numbers are real and the verdict does not exist. Reading such a result
        as a pass would be reading silence as approval.
        """
        return self.status in ("sampled", "below_threshold")

    @property
    def reproducible_query_set(self) -> bool:
        """Whether another party can redraw this query set from the record alone.

        Only a seeded draw is. An operator-supplied set may have been chosen
        after seeing the numbers it would produce, and no offline check can
        tell the difference, so it is reported and never scored.
        """
        return self.anchor_selection == "seeded_random" and bool(self.anchor_seed)

    @property
    def measures_user_search_quality(self) -> bool:
        """Always false for ``neighborhood_agreement``.

        Record-anchored agreement says the two stores put the same records near
        each other. That is a statement about the stores, not about whether a
        person searching either of them finds what they wanted, and nothing
        built on this record may describe it as the latter.
        """
        return self.metric_basis == "retrieval_relevance"

    def threshold_summary(self) -> str | None:
        """The declared thresholds as one line, for printing beside the label."""
        if not self.thresholds:
            return None
        parts = ", ".join(f"{name} {value:g}" for name, value in sorted(self.thresholds.items()))
        return f"operator-declared thresholds: {parts}"


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

    def attested_claims(self) -> tuple[str, ...]:
        """Everything in this record that a reader must take on the operator's word.

        The fixed claims are always listed. The declared thresholds are added
        when the record carries them, because a report that prints
        "quality sampled" without printing the bar that was cleared has told
        the reader the verdict and withheld the standard.
        """
        claims = list(ATTESTED_CLAIMS)
        summary = self.quality.threshold_summary() if self.quality is not None else None
        if summary is not None:
            claims.append(summary)
        # No scanner can read source text and tell whether an organisation
        # considers it confidential, or whether a redaction removed what the
        # operator thinks it removed.
        claims.extend(self.egress.unverifiable_claims)
        return tuple(claims)

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
            "unjudged": "verified (re-embedded, quality unjudged)",
            "not_measured": "verified (re-embedded, quality not measured)",
        }[self.quality_status]

    def improves_score(self, *, rebuild_reproduced: bool) -> bool:
        """Whether this record may move a store from 50 to 25.

        All four approved conditions must hold: the rebuild was reproduced by
        the caller, the quality check passed its thresholds, the target model is
        self-hostable, and no retained record failed to rebuild. A record can
        never earn a score of 0; that needs a verified restore.

        A fifth condition is the query set itself: it must have been drawn by
        seed, so another party can redraw it. An operator-chosen set is reported
        with its full label and metrics and moves nothing, because an operator
        who may pick the queries may pick the result. A record written before
        this field existed carries no ``anchor_selection`` and therefore moves
        no score either, which is the conservative reading of silence.
        """
        return (
            rebuild_reproduced
            and self.quality_status == "sampled"
            and self.target_model.self_hostable
            and self.coverage.complete
            and self.quality is not None
            and self.quality.reproducible_query_set
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


def _warnings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ReEmbedRecordError("Invalid egress warnings.")
    if len(raw) > MAX_WARNINGS:
        raise ReEmbedRecordError("Too many egress warnings listed.")
    return tuple(_text(item, "egress warning", MAX_REASON_CHARS) for item in raw)


def _check_executor_consistency(
    *,
    kind: str | None,
    execution: str | None,
    external_confirmed: bool | None,
    executor_id: str | None,
    endpoint: str | None,
    network: str | None,
    submitted: int | None,
    basis: str | None,
    accounting: str | None,
    may_exceed: bool | None,
    delivery: str | None,
    used: bool,
    transmitted: int,
) -> None:
    """Refuse an egress block whose executor fields contradict each other.

    A reader that accepted these independently would accept a record claiming
    registry-owned code handed payloads to a provider, or one reporting a
    transmission with no basis for the number. Every rule here is derivable
    from the others, so none of them asks the reader to trust the writer.
    """
    external = kind == "external_unverified"
    local_builtin = kind == "local_builtin"

    # Only operator-supplied code can be the subject of an external claim.
    if local_builtin:
        for value, name in (
            (executor_id, "executor_id"),
            (endpoint, "endpoint_origin"),
            (network, "network_behavior_verification"),
            (accounting, "transmission_accounting"),
            (delivery, "delivery_verification"),
        ):
            if value is not None:
                raise ReEmbedRecordError(f"A local_builtin run cannot declare {name}.")
        if may_exceed:
            raise ReEmbedRecordError(
                "A local_builtin run hands nothing over, so attempts cannot exceed a count."
            )
        if submitted:
            raise ReEmbedRecordError(
                "A local_builtin run cannot report records submitted to an external executor."
            )
        if external_confirmed:
            raise ReEmbedRecordError(
                "A local_builtin run cannot report external execution consent."
            )
        if basis is not None and basis != "no_transmission":
            raise ReEmbedRecordError("A local_builtin run must report a no_transmission basis.")
        if execution is not None and execution != "registry_owned":
            raise ReEmbedRecordError("A local_builtin run must report registry_owned execution.")
        if transmitted:
            raise ReEmbedRecordError("A local_builtin run cannot report transmitted records.")

    if external:
        # Code this scanner never ran cannot be described as anything but
        # attested, however the operator labels it.
        if execution is not None and execution != "attested":
            raise ReEmbedRecordError("An external_unverified run must report attested execution.")
        if network is not None and network != "attested":
            raise ReEmbedRecordError(
                "An external_unverified run must report attested network behaviour."
            )
        if executor_id is None:
            raise ReEmbedRecordError("An external_unverified run must name its executor.")
        if basis is not None and basis != "handed_to_executor":
            raise ReEmbedRecordError(
                "An external_unverified run must report a handed_to_executor basis."
            )
        # Default-deny: nothing may have been handed over unenabled.
        if external_confirmed is False and (submitted or transmitted):
            raise ReEmbedRecordError(
                "An external run cannot report submitted records "
                "without external execution consent."
            )

    # A basis is deliberately not required when a count is present. A record
    # written before the field existed is still valid, which is what additive
    # has to mean; requiring it here would reject evidence this reader used to
    # accept. The writer always emits it, and PrivateAIStack's contract test
    # enforces that end of the bargain.
    if transmitted and basis == "no_transmission":
        raise ReEmbedRecordError("A no_transmission basis cannot accompany transmitted records.")
    # Transmission is a subset of submission: a payload reaches a provider only
    # by way of the executor it was handed to.
    if submitted is not None and transmitted > submitted:
        raise ReEmbedRecordError("More records transmitted than were submitted to the executor.")
    if used and kind is not None and not external:
        raise ReEmbedRecordError(
            "Only an external_unverified executor can have used a remote provider."
        )


def _egress(raw: object) -> Egress:
    document = _mapping(raw, "egress")
    required = {"remote_provider_used", "records_transmitted", "raw_source_text_transmitted"}
    optional = {
        "provider",
        "hosting_mode",
        "hosted_execution_confirmed",
        "dataset_privacy_classification",
        "redaction_applied",
        "telemetry_enabled",
        "warnings",
        "executor_kind",
        "execution_verification",
        "external_execution_confirmed",
        "executor_id",
        "endpoint_origin",
        "network_behavior_verification",
        "records_submitted_to_external_executor",
        "transmission_basis",
        "transmission_accounting",
        "transmission_attempts_may_exceed_count",
        "delivery_verification",
    }
    if not required <= set(document) or set(document) - (required | optional):
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

    mode = (
        None
        if "hosting_mode" not in document
        else _enum(document["hosting_mode"], HOSTING_MODE, "egress hosting_mode")
    )
    confirmed = (
        None
        if "hosted_execution_confirmed" not in document
        else _flag(document["hosted_execution_confirmed"], "hosted_execution_confirmed")
    )
    classification = (
        None
        if "dataset_privacy_classification" not in document
        else _enum(
            document["dataset_privacy_classification"],
            PRIVACY_CLASSIFICATION,
            "egress dataset_privacy_classification",
        )
    )
    # Nothing may have left the machine without the operator having enabled it.
    # Enforcing it here is what makes an un-opted remote run unrepresentable
    # rather than merely discouraged by the tool that writes the record.
    if used and confirmed is False:
        raise ReEmbedRecordError(
            "A remote run cannot report that hosted execution was not confirmed."
        )
    if used and mode == "local":
        raise ReEmbedRecordError("A run that transmitted records cannot report local hosting.")

    kind = (
        None
        if "executor_kind" not in document
        else _enum(document["executor_kind"], EXECUTOR_KIND, "egress executor_kind")
    )
    execution = (
        None
        if "execution_verification" not in document
        else _enum(
            document["execution_verification"],
            EXECUTION_VERIFICATION,
            "egress execution_verification",
        )
    )
    external_confirmed = (
        None
        if "external_execution_confirmed" not in document
        else _flag(document["external_execution_confirmed"], "external_execution_confirmed")
    )
    executor_id = (
        None
        if "executor_id" not in document
        else _text(document["executor_id"], "egress executor_id")
    )
    endpoint = (
        None
        if "endpoint_origin" not in document
        else _text(document["endpoint_origin"], "egress endpoint_origin", MAX_ENDPOINT_CHARS)
    )
    network = (
        None
        if "network_behavior_verification" not in document
        else _enum(
            document["network_behavior_verification"],
            NETWORK_BEHAVIOR_VERIFICATION,
            "egress network_behavior_verification",
        )
    )
    submitted = (
        None
        if "records_submitted_to_external_executor" not in document
        else _count(
            document["records_submitted_to_external_executor"],
            "records_submitted_to_external_executor",
        )
    )
    basis = (
        None
        if "transmission_basis" not in document
        else _enum(document["transmission_basis"], TRANSMISSION_BASIS, "egress transmission_basis")
    )
    accounting = (
        None
        if "transmission_accounting" not in document
        else _enum(
            document["transmission_accounting"],
            TRANSMISSION_ACCOUNTING,
            "egress transmission_accounting",
        )
    )
    may_exceed = (
        None
        if "transmission_attempts_may_exceed_count" not in document
        else _flag(
            document["transmission_attempts_may_exceed_count"],
            "transmission_attempts_may_exceed_count",
        )
    )
    delivery = (
        None
        if "delivery_verification" not in document
        else _enum(
            document["delivery_verification"], DELIVERY_VERIFICATION, "egress delivery_verification"
        )
    )
    _check_executor_consistency(
        kind=kind,
        execution=execution,
        external_confirmed=external_confirmed,
        executor_id=executor_id,
        endpoint=endpoint,
        network=network,
        submitted=submitted,
        basis=basis,
        accounting=accounting,
        may_exceed=may_exceed,
        delivery=delivery,
        used=used,
        transmitted=transmitted,
    )
    return Egress(
        remote_provider_used=used,
        provider=None if provider is None else _text(provider, "egress provider"),
        records_transmitted=transmitted,
        raw_source_text_transmitted=raw_text,
        hosting_mode=mode,
        hosted_execution_confirmed=confirmed,
        dataset_privacy_classification=classification,
        redaction_applied=(
            None
            if "redaction_applied" not in document
            else _flag(document["redaction_applied"], "redaction_applied")
        ),
        telemetry_enabled=(
            None
            if "telemetry_enabled" not in document
            else _flag(document["telemetry_enabled"], "telemetry_enabled")
        ),
        warnings=() if "warnings" not in document else _warnings(document["warnings"]),
        executor_kind=kind,
        execution_verification=execution,
        external_execution_confirmed=external_confirmed,
        executor_id=executor_id,
        endpoint_origin=endpoint,
        network_behavior_verification=network,
        records_submitted_to_external_executor=submitted,
        transmission_basis=basis,
        transmission_accounting=accounting,
        transmission_attempts_may_exceed_count=may_exceed,
        delivery_verification=delivery,
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


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    name = _text(value, label)
    if name not in allowed:
        raise ReEmbedRecordError(f"Invalid {label}.")
    return name


def _thresholds(raw: object) -> dict[str, float]:
    document = _mapping(raw, "quality thresholds")
    if len(document) > MAX_THRESHOLDS:
        raise ReEmbedRecordError("Too many quality thresholds declared.")
    return {
        _text(name, "threshold name"): _ratio(value, f"threshold {name}", low=-1e9, high=1e9)
        for name, value in document.items()
    }


def _quality(raw: object) -> Quality:
    document = _mapping(raw, "quality")
    required = {"status", "query_set_size", "top_k"}
    optional = {
        "sufficient_evidence",
        "metrics",
        "checks",
        "notes",
        "mode",
        "metric_basis",
        "anchor_selection",
        "anchor_seed",
        "similarity",
        "candidate_pool_size",
        "thresholds",
        "tie_break",
        "boundary_ties",
        "queries_skipped",
        "correlation_undefined",
        "top_k_capped",
    }
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
    if status == "unjudged":
        # It measured, so the numbers must be there; it reached no verdict, so
        # nothing in it may read as one.
        if not metrics:
            raise ReEmbedRecordError("An unjudged quality result requires the metrics it measured.")
        if any(not check["passed"] for check in checks):
            raise ReEmbedRecordError(
                "An unjudged quality result cannot contain a failed check; that is below_threshold."
            )
        if query_set_size < 1:
            raise ReEmbedRecordError("An unjudged quality result requires a non-empty query set.")
        if sufficient is False:
            raise ReEmbedRecordError(
                "An unjudged quality result cannot declare insufficient evidence; "
                "that is not_measured."
            )
        if not isinstance(document.get("notes"), str) or not document["notes"].strip():
            # A result with no verdict has to say what would produce one, or it
            # reads as a dead end rather than as a missing declaration.
            raise ReEmbedRecordError(
                "An unjudged quality result must say in its notes what to declare to get a verdict."
            )
    if status == "not_measured" and (metrics or checks):
        raise ReEmbedRecordError("An unmeasured quality result cannot carry metrics or checks.")

    mode = None if "mode" not in document else _enum(document["mode"], QUALITY_MODE, "quality mode")
    basis = (
        None
        if "metric_basis" not in document
        else _enum(document["metric_basis"], METRIC_BASIS, "quality metric_basis")
    )
    selection = (
        None
        if "anchor_selection" not in document
        else _enum(document["anchor_selection"], ANCHOR_SELECTION, "quality anchor_selection")
    )
    seed = (
        None
        if "anchor_seed" not in document
        else _text(document["anchor_seed"], "quality anchor_seed")
    )
    similarity = (
        None
        if "similarity" not in document
        else _enum(document["similarity"], SIMILARITY, "quality similarity")
    )
    thresholds = None if "thresholds" not in document else _thresholds(document["thresholds"])
    pool = None
    if "candidate_pool_size" in document:
        pool = _count(document["candidate_pool_size"], "candidate_pool_size")
        if pool < 1:
            raise ReEmbedRecordError("Invalid candidate_pool_size.")

    # Record-anchored queries rank a store by one of its own vectors, so what
    # they can show is agreement between two neighborhoods, never relevance.
    # Letting a record claim otherwise is how an agreement number becomes a
    # sentence about search quality somewhere downstream.
    if mode == "record_anchored" and basis is not None and basis != "neighborhood_agreement":
        raise ReEmbedRecordError(
            "Record-anchored quality can only have a neighborhood_agreement basis."
        )
    # A seeded draw is only redrawable if the seed is in the record.
    if selection == "seeded_random" and seed is None:
        raise ReEmbedRecordError("A seeded_random query set must record its seed.")
    if selection != "seeded_random" and seed is not None:
        raise ReEmbedRecordError("Only a seeded_random query set may record a seed.")
    # A verdict without its bar is half a sentence, and the status is the
    # verdict.
    measured = status in ("sampled", "below_threshold", "unjudged")
    judged = measured and checks and thresholds is not None
    if judged and {check["name"] for check in checks} != set(thresholds or {}):
        raise ReEmbedRecordError("Declared thresholds do not match the checks that were run.")
    if status == "not_measured" and thresholds:
        # Nothing was measured, so nothing was judged against a threshold.
        raise ReEmbedRecordError("An unmeasured quality result cannot carry thresholds.")

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
        mode=mode,
        metric_basis=basis,
        anchor_selection=selection,
        anchor_seed=seed,
        similarity=similarity,
        candidate_pool_size=pool,
        thresholds=thresholds,
        tie_break=(
            None
            if "tie_break" not in document
            else _text(document["tie_break"], "quality tie_break")
        ),
        boundary_ties=(
            None
            if "boundary_ties" not in document
            else _count(document["boundary_ties"], "boundary_ties")
        ),
        queries_skipped=(
            None
            if "queries_skipped" not in document
            else _count(document["queries_skipped"], "queries_skipped")
        ),
        correlation_undefined=(
            None
            if "correlation_undefined" not in document
            else _flag(document["correlation_undefined"], "correlation_undefined")
        ),
        top_k_capped=(
            None
            if "top_k_capped" not in document
            else _flag(document["top_k_capped"], "top_k_capped")
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
