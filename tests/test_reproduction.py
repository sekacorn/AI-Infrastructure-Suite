"""The scanner's own copy of the two built-in hashing schemes.

This is a third implementation of arithmetic that also lives in AgentForge and
PrivateAIStack, and that is deliberate: two implementations agreeing is
evidence, one implementation agreeing with itself is not. Importing
PrivateAIStack's embedders here would mean the scanner using the audited
party's code to verify the audited party's claim.

The cost of a third copy is drift, and the defence against drift is the
cross-repo agreement test in PrivateAIStack's nightly contract job, which
asserts that its embedders and these functions produce identical vectors for
the same inputs. These tests cover the properties that hold without reference
to the other implementation.
"""

from __future__ import annotations

import math

import pytest

from ai_infrastructure_suite.reproduction import (
    AGENTFORGE_HASHED_BOW,
    MAX_SAMPLE_SIZE,
    PRIVATEAISTACK_HASHED_BOW,
    ReproductionError,
    embed,
    is_reproducible,
    sample_seed,
    select_sample,
    vectors_match,
)

SCHEMES = (AGENTFORGE_HASHED_BOW, PRIVATEAISTACK_HASHED_BOW)
TEXT = "Escalate to the on-call engineer when the queue depth exceeds 40."


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("dimensions", (1, 8, 64, 384))
def test_a_vector_has_the_declared_width_and_unit_norm(scheme: str, dimensions: int) -> None:
    values = embed(TEXT, model_id=scheme, revision="1", dimensions=dimensions)

    assert len(values) == dimensions
    assert math.isclose(math.sqrt(sum(value * value for value in values)), 1.0, rel_tol=1e-9)


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_same_text_always_gives_the_same_vector(scheme: str) -> None:
    first = embed(TEXT, model_id=scheme, revision="1", dimensions=16)
    again = embed(TEXT, model_id=scheme, revision="1", dimensions=16)

    assert first == again


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_same_scheme_at_two_widths_is_two_models(scheme: str) -> None:
    # The bucket a token lands in is hash(token) % dimensions, so changing the
    # width moves every component. This is why identity is the whole triple.
    narrow = embed(TEXT, model_id=scheme, revision="1", dimensions=8)
    wide = embed(TEXT, model_id=scheme, revision="1", dimensions=64)

    assert len(narrow) != len(wide)
    assert not vectors_match(narrow, wide)


def test_the_two_schemes_disagree_on_the_same_text() -> None:
    # Different arithmetic. If these ever matched, one of them would be wrong.
    left = embed(TEXT, model_id=AGENTFORGE_HASHED_BOW, revision="1", dimensions=32)
    right = embed(TEXT, model_id=PRIVATEAISTACK_HASHED_BOW, revision="1", dimensions=32)

    assert not vectors_match(left, right)


@pytest.mark.parametrize("scheme", SCHEMES)
def test_text_with_no_tokens_gives_a_zero_vector(scheme: str) -> None:
    values = embed("   ", model_id=scheme, revision="1", dimensions=8)

    assert values == [0.0] * 8


def test_the_two_schemes_tokenise_differently() -> None:
    # AgentForge takes alphanumeric runs; PrivateAIStack splits on whitespace.
    # Punctuation-only text therefore has no tokens for one and one token for
    # the other, which is a real behavioural difference and not a bug.
    assert embed("!!!", model_id=AGENTFORGE_HASHED_BOW, revision="1", dimensions=8) == [0.0] * 8
    assert embed("!!!", model_id=PRIVATEAISTACK_HASHED_BOW, revision="1", dimensions=8) != [0.0] * 8


# --- identity recognition -------------------------------------------------


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_exact_triple_is_recognised(scheme: str) -> None:
    assert is_reproducible(scheme, "1", 16)


@pytest.mark.parametrize(
    ("model_id", "revision", "dimensions"),
    (
        ("some-vendor-embed", "1", 16),
        (PRIVATEAISTACK_HASHED_BOW, "2", 16),
        (PRIVATEAISTACK_HASHED_BOW, "1", 0),
        (PRIVATEAISTACK_HASHED_BOW, "1", 8193),
    ),
)
def test_anything_but_the_exact_triple_is_refused(
    model_id: str, revision: str, dimensions: int
) -> None:
    assert not is_reproducible(model_id, revision, dimensions)
    with pytest.raises(ReproductionError, match="cannot recompute"):
        embed(TEXT, model_id=model_id, revision=revision, dimensions=dimensions)


def test_a_recognised_id_at_an_unknown_revision_is_refused() -> None:
    # revision is the only part of an identity that can carry a change of
    # behaviour, so recognising a newer one by name and running the old
    # arithmetic would produce a confident mismatch.
    assert not is_reproducible(PRIVATEAISTACK_HASHED_BOW, "99", 16)


# --- sampling -------------------------------------------------------------


def test_the_sample_seed_is_a_function_of_the_target_digest() -> None:
    assert sample_seed("a" * 64) == sample_seed("a" * 64)
    assert sample_seed("a" * 64) != sample_seed("b" * 64)


def test_a_sample_is_reproducible_from_its_seed() -> None:
    ids = tuple(f"r{index:03d}" for index in range(50))

    first = select_sample(ids, seed="seed-one", size=8)
    again = select_sample(ids, seed="seed-one", size=8)
    other = select_sample(ids, seed="seed-two", size=8)

    assert first == again
    assert first != other
    assert len(first) == 8
    assert set(first) <= set(ids)


def test_a_sample_is_not_the_first_records_by_identifier() -> None:
    # Nothing says identifiers are unrelated to record size or age, so a sample
    # of the first N would be a sample of whatever they correlate with.
    ids = tuple(f"r{index:03d}" for index in range(50))

    assert select_sample(ids, seed="seed-one", size=8) != ids[:8]


@pytest.mark.parametrize("size", (0, -1, MAX_SAMPLE_SIZE + 1))
def test_an_out_of_range_sample_size_is_refused(size: int) -> None:
    with pytest.raises(ReproductionError, match="sample size"):
        select_sample(("r0", "r1"), seed="seed", size=size)


# --- comparison -----------------------------------------------------------


def test_vectors_of_different_widths_never_match() -> None:
    assert not vectors_match([1.0, 0.0], [1.0, 0.0, 0.0])


def test_a_json_round_trip_still_matches() -> None:
    # The arithmetic is deterministic but the vectors travel through JSON, and
    # a decimal round trip is not required to be bit-exact.
    import json

    values = embed(TEXT, model_id=PRIVATEAISTACK_HASHED_BOW, revision="1", dimensions=64)
    restored = json.loads(json.dumps(values))

    assert vectors_match(values, restored)


def test_a_real_difference_still_fails() -> None:
    values = embed(TEXT, model_id=PRIVATEAISTACK_HASHED_BOW, revision="1", dimensions=16)
    changed = list(values)
    changed[3] += 1e-6

    assert not vectors_match(values, changed)
