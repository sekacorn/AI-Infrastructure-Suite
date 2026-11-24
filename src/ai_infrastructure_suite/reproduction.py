"""Recomputing a sample of a rebuilt store's vectors, to check a claim.

A re-embed record says a store was rebuilt with a different model. Condition
(a) of the approved scoring rule is that *this scanner* reproduced that rebuild
rather than taking the record's word for it, and this module is how.

Why a third implementation
--------------------------

The obvious alternative was to import PrivateAIStack's embedders and call them.
It was rejected: the scanner would be using the audited party's code to verify
the audited party's claim, which is not independent verification whatever the
arithmetic says. It would also invert the layering, and make a verification
level depend on what happened to be installed, so the same evidence would score
differently on two machines.

Two implementations agreeing is evidence. One implementation agreeing with
itself is not. The cost is a third copy of the arithmetic, and that cost is
paid by a cross-repo agreement test in PrivateAIStack's nightly contract job,
which asserts that its embedders and these functions produce identical vectors
for the same inputs.

What this does and does not do
------------------------------

The suite never runs a model or embeds text with one. These functions
recompute the two built-in hash-based schemes, using the standard library only
and over a bounded sample, to verify a claim. There are no weights, no socket
and no provider SDK: it is a ``hashlib`` bucket sum over bytes already on disk,
the same kind of re-run as the JSON-LD round trip that earns ``verified``.

An identity this module does not implement is **refused**, never approximated.
Model identity is the triple ``id`` + ``revision`` + ``dimensions``, because
the bucket a token lands in is ``hash(token) % dimensions``: the same scheme at
two dimensions is two models.

A sample is not a whole store
-----------------------------

Reproduction checks a bounded sample, so a hundred-thousand-record store does
not make a report slow. Nothing here may be reported as a whole-store digest,
and :class:`Reproduction` carries the sample size so a caller cannot lose track
of what was actually checked.

Seeds are derived, never accepted
---------------------------------

The sample is chosen by a seed derived from the target store's own digest. An
operator who could choose it could grind it — sample after sample until one
matched — and a seed written into a record has no influence here at all.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ai_infrastructure_suite._evidence import load_json_document, read_bounded_file

AGENTFORGE_HASHED_BOW: Final = "agentforge.hashed-bow.blake2b"
PRIVATEAISTACK_HASHED_BOW: Final = "privateaistack.hashed-bow.sha256-signed"

REPRODUCIBLE_SCHEMES: Final = frozenset({AGENTFORGE_HASHED_BOW, PRIVATEAISTACK_HASHED_BOW})
"""The identities this scanner can recompute. Anything else is refused."""

REPRODUCIBLE_REVISIONS: Final = {
    AGENTFORGE_HASHED_BOW: frozenset({"1"}),
    PRIVATEAISTACK_HASHED_BOW: frozenset({"1"}),
}
"""Revisions whose behaviour this module implements.

``revision`` is the only part of an identity that can carry a change of
behaviour, so a revision not listed here is not reproducible even when the
``id`` is familiar. Recognising a newer revision by name and recomputing the
old arithmetic would produce a confident mismatch.
"""

MAX_DIMENSIONS: Final = 8192
MAX_SOURCE_TEXT_BYTES: Final = 1024 * 1024
MAX_SHARD_BYTES: Final = 64 * 1024 * 1024
MAX_RECORDS: Final = 100_000
DEFAULT_SAMPLE_SIZE: Final = 16
MAX_SAMPLE_SIZE: Final = 256
VECTOR_TOLERANCE: Final = 1e-9
"""Float comparison tolerance.

The arithmetic is deterministic, but the vectors travel through JSON, and a
decimal round trip is not required to be bit-exact. A tolerance this tight
still fails on any real difference in bucket or sign.
"""

_AGENTFORGE_TOKEN: Final = re.compile(r"[a-z0-9]+")


class ReproductionError(ValueError):
    """Raised when a reproduction cannot be attempted as declared."""


@dataclass(frozen=True)
class Reproduction:
    """What the scanner recomputed, and whether it matched.

    ``sample_size`` is part of the result on purpose: a caller reporting this
    must say how many vectors were checked, because a sample is not a
    whole-store digest.
    """

    attempted: bool
    matched: bool
    sample_size: int
    records_checked: tuple[str, ...]
    reason: str

    @property
    def reproduced(self) -> bool:
        return self.attempted and self.matched


def is_reproducible(model_id: str, revision: str, dimensions: int) -> bool:
    """Whether this scanner implements the exact identity triple.

    All three parts, never two. A recognised ``id`` at an unknown ``revision``
    or a different ``dimensions`` is a different model.
    """
    if model_id not in REPRODUCIBLE_SCHEMES:
        return False
    if revision not in REPRODUCIBLE_REVISIONS[model_id]:
        return False
    return 1 <= dimensions <= MAX_DIMENSIONS


def embed(text: str, *, model_id: str, revision: str, dimensions: int) -> list[float]:
    """Recompute one vector, or refuse.

    Independently written from PrivateAIStack's embedders rather than copied,
    so that the two agreeing means something. The cross-repo agreement test is
    what keeps them from drifting apart.
    """
    if not is_reproducible(model_id, revision, dimensions):
        raise ReproductionError(
            f"This scanner cannot recompute {model_id}@{revision} at {dimensions} dimensions."
        )
    values = [0.0] * dimensions
    if model_id == AGENTFORGE_HASHED_BOW:
        for token in _AGENTFORGE_TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            values[int.from_bytes(digest, "big") % dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in values))
        if norm > 0:
            values = [value / norm for value in values]
        return values
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        values[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def sample_seed(target_store_sha256: str) -> str:
    """Derive the sample seed from the target store's digest.

    Derived, never accepted. A seed an operator could choose is a seed an
    operator could grind, and a seed written into a record has no influence on
    which vectors this scanner checks.
    """
    return hashlib.sha256(f"reproduction-sample:{target_store_sha256}".encode()).hexdigest()


def select_sample(record_ids: tuple[str, ...], *, seed: str, size: int) -> tuple[str, ...]:
    """Choose which records to recompute, reproducibly and without bias.

    Ordered by ``sha256(seed + ":" + record_id)`` rather than by identifier,
    because nothing says identifiers are unrelated to record size or age, and a
    sample of the first N would be a sample of whatever they correlate with.
    """
    if not 1 <= size <= MAX_SAMPLE_SIZE:
        raise ReproductionError(
            f"The sample size must be between 1 and {MAX_SAMPLE_SIZE}, not {size}."
        )
    ordered = sorted(
        record_ids, key=lambda record_id: hashlib.sha256(f"{seed}:{record_id}".encode()).hexdigest()
    )
    return tuple(ordered[:size])


def vectors_match(recomputed: list[float], recorded: list[float]) -> bool:
    """Whether two vectors agree within tolerance, including their width."""
    if len(recomputed) != len(recorded):
        return False
    return all(
        abs(left - right) <= VECTOR_TOLERANCE
        for left, right in zip(recomputed, recorded, strict=True)
    )


def retained_sources(store: dict[str, Any]) -> dict[str, str]:
    """Map retained record identifiers to their declared source-text path."""
    vectors = store.get("vectors")
    if not isinstance(vectors, dict):
        raise ReproductionError("The target store declares no vectors.")
    layout = vectors.get("layout")
    entries: list[Any]
    if layout == "single_file":
        entries = vectors.get("records") or []
    elif layout == "sharded_jsonl":
        entries = vectors.get("source_text") or []
    else:
        raise ReproductionError(f"Unknown target vector layout: {layout!r}")
    if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
        raise ReproductionError("The target store's record list is not usable.")
    retained: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source_text")
        record_id = entry.get("record_id")
        if not isinstance(source, dict) or not isinstance(record_id, str):
            continue
        if source.get("status") != "retained":
            continue
        path = source.get("path")
        if isinstance(path, str):
            retained[record_id] = path
    return retained


def recorded_vectors(store: dict[str, Any], root: Path, wanted: set[str]) -> dict[str, list[float]]:
    """Read only the sampled records' vectors, so a large store stays bounded."""
    vectors = store.get("vectors")
    if not isinstance(vectors, dict):
        raise ReproductionError("The target store declares no vectors.")
    found: dict[str, list[float]] = {}
    if vectors.get("layout") == "single_file":
        for entry in vectors.get("records") or []:
            if not isinstance(entry, dict) or entry.get("record_id") not in wanted:
                continue
            reference = entry.get("vector")
            if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
                continue
            data = read_bounded_file(root / reference["path"], MAX_SOURCE_TEXT_BYTES)
            if hashlib.sha256(data).hexdigest() != reference.get("sha256"):
                raise ReproductionError("A sampled vector file does not match its declared digest.")
            content = load_json_document(data, "vector file")
            if not isinstance(content, dict) or not isinstance(content.get("values"), list):
                raise ReproductionError("A sampled vector file has no values.")
            found[str(entry["record_id"])] = [float(value) for value in content["values"]]
        return found
    for shard in vectors.get("shards") or []:
        if not isinstance(shard, dict) or not isinstance(shard.get("path"), str):
            continue
        data = read_bounded_file(root / shard["path"], MAX_SHARD_BYTES)
        if hashlib.sha256(data).hexdigest() != shard.get("sha256"):
            raise ReproductionError("A target shard does not match its declared digest.")
        for line in data.decode("utf-8").splitlines():
            if not line.strip():
                continue
            row = load_json_document(line.encode("utf-8"), "shard line")
            if not isinstance(row, dict) or row.get("record_id") not in wanted:
                continue
            if not isinstance(row.get("values"), list):
                raise ReproductionError("A sampled shard line has no values.")
            found[str(row["record_id"])] = [float(value) for value in row["values"]]
    return found


def reproduce_store_sample(
    store: dict[str, Any],
    root: Path,
    *,
    model_id: str,
    revision: str,
    dimensions: int,
    seed_digest: str,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> Reproduction:
    """Recompute a bounded sample of one store's vectors from its own source text.

    Used both to check a re-embed record's target store and to decide whether a
    store's reason may use the softer deterministic wording. In both cases the
    question is identical: does this scanner, recomputing the arithmetic itself,
    get the vectors the store records?

    Every refusal returns rather than raises. A store this scanner cannot
    recompute is ordinary evidence, not a broken report.
    """
    if not is_reproducible(model_id, revision, dimensions):
        return Reproduction(
            attempted=False,
            matched=False,
            sample_size=0,
            records_checked=(),
            reason=(
                f"This scanner does not implement {model_id}@{revision} at "
                f"{dimensions} dimensions, so its vectors were not recomputed."
            ),
        )
    retained = retained_sources(store)
    if not retained:
        return Reproduction(
            attempted=False,
            matched=False,
            sample_size=0,
            records_checked=(),
            reason="No retained source text, so the vectors could not be recomputed.",
        )
    seed = sample_seed(seed_digest)
    chosen = select_sample(tuple(sorted(retained)), seed=seed, size=min(sample_size, len(retained)))
    recorded = recorded_vectors(store, root, set(chosen))
    if any(record_id not in recorded for record_id in chosen):
        return Reproduction(
            attempted=True,
            matched=False,
            sample_size=len(chosen),
            records_checked=chosen,
            reason="A sampled record has no recorded vector in this store.",
        )
    for record_id in chosen:
        try:
            raw = read_bounded_file(root / retained[record_id], MAX_SOURCE_TEXT_BYTES)
            recomputed = embed(
                raw.decode("utf-8"), model_id=model_id, revision=revision, dimensions=dimensions
            )
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return Reproduction(
                attempted=True,
                matched=False,
                sample_size=len(chosen),
                records_checked=chosen,
                reason=f"Recomputation could not complete: {exc}",
            )
        if not vectors_match(recomputed, recorded[record_id]):
            return Reproduction(
                attempted=True,
                matched=False,
                sample_size=len(chosen),
                records_checked=chosen,
                reason=(
                    "A recomputed vector did not match the one this store records, over a "
                    f"sample of {len(chosen)}."
                ),
            )
    return Reproduction(
        attempted=True,
        matched=True,
        sample_size=len(chosen),
        records_checked=chosen,
        reason=f"Recomputed a sample of {len(chosen)} vector(s), all matching the store's records.",
    )
