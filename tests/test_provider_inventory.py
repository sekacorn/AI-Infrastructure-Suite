"""``provider-inventory.v1``: the reader, the packaged schema, and the fixtures.

Every negative case is refused by the reader. Each is also marked with whether
the JSON Schema can express it: cross-field rules (uniqueness, references,
derived hosting) are the reader's alone, and the test pins that division so a
schema change cannot silently claim more than it checks.
"""

from __future__ import annotations

import copy
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from ai_infrastructure_suite.provider_inventory import (
    CLAIM_ENDPOINT_OVERRIDE,
    CLAIM_REGISTRY_MODELS,
    MAX_INVENTORY_BYTES,
    SCHEMA_VERSION,
    ProviderInventoryError,
    load_provider_inventory,
    origin_is_local,
    parse_provider_inventory,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples" / "provider-inventory"
FIXTURES = sorted(p.name for p in EXAMPLES.glob("*.json"))
SECRET = "inv-secret-77c1-do-not-log"
SCHEMA = json.loads(
    files("ai_infrastructure_suite")
    .joinpath("data/provider-inventory.schema.json")
    .read_text(encoding="utf-8")
)
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
#: The schema exactly as suite 0.2.0b1 shipped it, before the two optional
#: producer markers existed.
SCHEMA_0_2_0B1 = json.loads(
    (
        Path(__file__).resolve().parent / "fixtures" / "provider-inventory.schema.0.2.0b1.json"
    ).read_text(encoding="utf-8")
)
#: Shipped examples that use neither optional marker.
WITHOUT_MARKERS = [name for name in FIXTURES if name != "registry-and-override.json"]


def _fixture(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    return document


def _parse(document: object) -> Any:
    return parse_provider_inventory(json.dumps(document).encode("utf-8"))


def _schema_accepts(document: object) -> bool:
    return VALIDATOR.is_valid(document)


def base() -> dict[str, Any]:
    return copy.deepcopy(_fixture("hosted-with-fallback.json"))


# --- the shipped fixtures ----------------------------------------------------


def test_the_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["properties"]["schema_version"]["const"] == SCHEMA_VERSION


def test_the_expected_fixtures_ship() -> None:
    assert FIXTURES == [
        "hosted-with-fallback.json",
        "local-only.json",
        "minimal.json",
        "registry-and-override.json",
        "vendor-only-features.json",
    ]


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_parses_and_matches_the_schema(name: str) -> None:
    document = _fixture(name)

    assert _parse(document).providers
    assert list(VALIDATOR.iter_errors(document)) == []


def test_fixtures_use_placeholders_only() -> None:
    # Vendor names and real hosts belong in presets and docs, never fixtures.
    for name in FIXTURES:
        text = (EXAMPLES / name).read_text(encoding="utf-8")
        for host in (
            p["endpoint_origin"] for p in json.loads(text)["providers"] if p["endpoint_origin"]
        ):
            assert host.endswith(".example") or origin_is_local(host), (name, host)


def test_hosting_is_derived_and_a_declaration_only_adds_hostedness() -> None:
    inventory = _parse(_fixture("hosted-with-fallback.json"))

    assert inventory.provider("gateway_a").hosting == "hosted"
    assert inventory.provider("gateway_a").hosting_verification == "derived"
    assert inventory.provider("local_vllm").hosting == "local"

    # Declaring a local endpoint hosted is honoured. It then needs a
    # jurisdiction, because not_applicable is only for local providers.
    document = _fixture("local-only.json")
    document["providers"][0]["hosting_declared"] = "hosted"
    document["providers"][0]["jurisdiction"] = "unknown"
    assert _parse(document).providers[0].hosting == "hosted"


def test_an_sdk_default_endpoint_is_hosted_and_said_to_be_attested() -> None:
    inventory = _parse(_fixture("vendor-only-features.json"))
    provider = inventory.providers[0]

    assert (provider.endpoint_origin, provider.hosting, provider.hosting_verification) == (
        None,
        "hosted",
        "sdk_default",
    )
    assert "that an SDK default endpoint is the vendor's" in inventory.attested_claims()


def test_an_offline_provider_is_local_with_no_endpoint() -> None:
    provider = _parse(_fixture("minimal.json")).providers[0]

    assert (provider.hosting, provider.hosting_verification) == ("local", "offline")


def test_jurisdiction_is_always_attested() -> None:
    for name in FIXTURES:
        for provider in _parse(_fixture(name)).providers:
            assert provider.jurisdiction_verification == "attested"


def test_the_attested_claims_name_what_the_reader_cannot_check() -> None:
    claims = _parse(_fixture("hosted-with-fallback.json")).attested_claims()

    assert {"models in use", "features in use", "jurisdiction", "producer"} <= set(claims)
    assert "that the declared fallback works" in claims


# --- optional producer markers (suite 0.2.0b2) ------------------------------


def test_registry_models_and_an_endpoint_override_are_read() -> None:
    provider = _parse(_fixture("registry-and-override.json")).providers[0]

    assert provider.models == ("vendor-c-large", "vendor-c-small")
    assert provider.registry_models == ("vendor-c-small",)
    assert provider.endpoint_override_present is True
    # Present, not reflected: the origin stays null and hosting stays hosted.
    assert (provider.endpoint_origin, provider.hosting, provider.hosting_verification) == (
        None,
        "hosted",
        "sdk_default",
    )


def test_both_markers_feed_the_attested_claims() -> None:
    claims = _parse(_fixture("registry-and-override.json")).attested_claims()

    assert CLAIM_REGISTRY_MODELS in claims
    assert CLAIM_ENDPOINT_OVERRIDE in claims
    assert "from registry, not confirmed in use" in CLAIM_REGISTRY_MODELS


@pytest.mark.parametrize("name", WITHOUT_MARKERS)
def test_an_inventory_without_markers_claims_neither(name: str) -> None:
    inventory = _parse(_fixture(name))

    for provider in inventory.providers:
        assert not provider.registry_models
        assert not provider.endpoint_override_present
    assert CLAIM_REGISTRY_MODELS not in inventory.attested_claims()
    assert CLAIM_ENDPOINT_OVERRIDE not in inventory.attested_claims()


def test_a_declared_source_is_not_a_registry_model() -> None:
    document = base()
    document["providers"][0]["models"] = [{"model": "model-large", "source": "declared"}]

    assert _parse(document).providers[0].registry_models == ()
    assert _schema_accepts(document)


@pytest.mark.parametrize("name", WITHOUT_MARKERS)
def test_a_default_inventory_still_validates_against_the_0_2_0b1_schema(name: str) -> None:
    # Emitted only when they carry information, so an inventory without either
    # marker stays readable by suite 0.2.0b1.
    assert list(jsonschema.Draft202012Validator(SCHEMA_0_2_0B1).iter_errors(_fixture(name))) == []


def test_the_0_2_0b1_schema_refuses_each_marker() -> None:
    # Pins the compatibility boundary the AgentForge changelog states: an
    # inventory that uses either marker needs suite 0.2.0b2 or later.
    old = jsonschema.Draft202012Validator(SCHEMA_0_2_0B1)
    with_source = base()
    with_source["providers"][0]["models"][0]["source"] = "registry"
    with_override = _fixture("vendor-only-features.json")
    with_override["providers"][0]["endpoint_override_present"] = True

    assert _schema_accepts(with_source) and not old.is_valid(with_source)
    assert _schema_accepts(with_override) and not old.is_valid(with_override)


def test_an_override_on_each_sdk_default_kind_is_accepted() -> None:
    for kind in ("openai", "anthropic", "bedrock"):
        document = _fixture("vendor-only-features.json")
        document["providers"][0]["kind"] = kind
        document["providers"][0]["endpoint_override_present"] = True

        assert _parse(document).providers[0].endpoint_override_present
        assert _schema_accepts(document)


# --- the local rule, pinned to AgentForge's and ModelSwapBench's cases -------


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8000",
        "http://127.0.0.1:1234",
        "http://[::1]:8000",
        "http://192.168.1.5",
        "http://10.1.2.3",
        "http://172.16.0.9",
        "http://169.254.1.1",
        "http://0.0.0.0:11434",
    ],
)
def test_local_origins(origin: str) -> None:
    assert origin_is_local(origin)


@pytest.mark.parametrize(
    "origin",
    [
        "https://api.vendor.example",
        "https://8.8.8.8",
        "http://db.internal.corp",
        "",
        "junk",
        "http://[bad",
    ],
)
def test_everything_else_is_hosted_and_names_are_never_resolved(origin: str) -> None:
    # db.internal.corp may resolve privately; a name is not a route.
    assert not origin_is_local(origin)


# --- negative cases ----------------------------------------------------------
# (mutation, the schema rejects it too)


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


P0 = ["providers", 0]
NEGATIVE: dict[str, tuple[Any, bool]] = {
    "wrong schema_version": (_set(["schema_version"], "provider-inventory.v2"), True),
    "missing schema_version": (_delete(["schema_version"]), True),
    "unknown top-level key": (_set(["extra"], 1), True),
    "unknown provider key": (_set([*P0, "extra"], 1), True),
    "unknown features key": (_set([*P0, "features", "extra"], 1), True),
    "missing generated_by": (_delete(["generated_by"]), True),
    "empty providers": (_set(["providers"], []), True),
    "bad provider_id": (_set([*P0, "provider_id"], "Gateway-A"), True),
    "unknown kind": (_set([*P0, "kind"], "vendor_sdk"), True),
    "preset without source": (_set([*P0, "preset_source"], None), True),
    "origin with credentials": (
        _set([*P0, "endpoint_origin"], f"https://user:{SECRET}@llm.vendor-a.example"),
        True,
    ),
    "origin with a path": (_set([*P0, "endpoint_origin"], "https://llm.vendor-a.example/v1"), True),
    "origin with a query": (
        _set([*P0, "endpoint_origin"], f"https://llm.vendor-a.example?key={SECRET}"),
        True,
    ),
    "origin with a fragment": (
        _set([*P0, "endpoint_origin"], "https://llm.vendor-a.example#x"),
        True,
    ),
    "origin with a trailing slash": (
        _set([*P0, "endpoint_origin"], "https://llm.vendor-a.example/"),
        True,
    ),
    "non-http origin": (_set([*P0, "endpoint_origin"], "ftp://llm.vendor-a.example"), True),
    "uppercase origin": (_set([*P0, "endpoint_origin"], "https://LLM.vendor-a.example"), True),
    "bad port": (_set([*P0, "endpoint_origin"], "https://llm.vendor-a.example:notaport"), True),
    "port zero": (_set([*P0, "endpoint_origin"], "https://llm.vendor-a.example:0"), False),
    "null origin on openai_compatible": (_set([*P0, "endpoint_origin"], None), False),
    "lowercase jurisdiction": (_set([*P0, "jurisdiction"], "xa"), True),
    "three-letter jurisdiction": (_set([*P0, "jurisdiction"], "XAB"), True),
    "not_applicable on a hosted provider": (_set([*P0, "jurisdiction"], "not_applicable"), False),
    "empty models": (_set([*P0, "models"], []), True),
    "duplicate model": (_set([*P0, "models"], [{"model": "m"}, {"model": "m"}]), False),
    "unknown reconstruction": (
        _set([*P0, "features", "vendor_only"], [{"feature": "x", "reconstruction": "maybe"}]),
        True,
    ),
    "duplicate vendor-only feature": (
        _set(
            [*P0, "features", "vendor_only"],
            [
                {"feature": "x", "reconstruction": "none"},
                {"feature": "x", "reconstruction": "none"},
            ],
        ),
        False,
    ),
    "string flag": (_set([*P0, "features", "streaming"], "yes"), True),
    "unavailable fallback naming a provider": (
        _set(
            [*P0, "openai_compatible_fallback"], {"available": False, "provider_id": "local_vllm"}
        ),
        True,
    ),
    "fallback naming a missing provider": (
        _set([*P0, "openai_compatible_fallback"], {"available": True, "provider_id": "nowhere"}),
        False,
    ),
    "fallback naming itself": (
        _set([*P0, "openai_compatible_fallback"], {"available": True, "provider_id": "gateway_a"}),
        False,
    ),
    "duplicate provider_id": (_set(["providers", 1, "provider_id"], "gateway_a"), False),
    "local declared for a public endpoint": (_set([*P0, "hosting_declared"], "local"), False),
    "unknown model source": (_set([*P0, "models", 0, "source"], "guessed"), True),
    "model source not a string": (_set([*P0, "models", 0, "source"], 1), True),
    "endpoint override false": (_set([*P0, "endpoint_override_present"], False), True),
    "endpoint override on a provider with an origin": (
        _set([*P0, "endpoint_override_present"], True),
        True,
    ),
}


@pytest.mark.parametrize("case", sorted(NEGATIVE))
def test_the_reader_refuses(case: str) -> None:
    mutate, _ = NEGATIVE[case]
    document = base()
    mutate(document)

    with pytest.raises(ProviderInventoryError) as raised:
        _parse(document)

    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None or SECRET not in str(raised.value.__cause__)


@pytest.mark.parametrize("case", sorted(NEGATIVE))
def test_the_schema_rejects_exactly_what_it_can_express(case: str) -> None:
    mutate, schema_rejects = NEGATIVE[case]
    document = base()
    mutate(document)

    assert _schema_accepts(document) is not schema_rejects


def test_an_offline_provider_with_an_endpoint_is_refused() -> None:
    document = _fixture("minimal.json")
    document["providers"][0]["endpoint_origin"] = "http://localhost:1"

    with pytest.raises(ProviderInventoryError, match="offline"):
        _parse(document)


def test_a_null_origin_on_other_is_refused() -> None:
    document = _fixture("minimal.json")
    document["providers"][0]["kind"] = "other"

    with pytest.raises(ProviderInventoryError, match="needs an endpoint_origin"):
        _parse(document)


# --- parsing bounds ----------------------------------------------------------


def test_duplicate_json_keys_are_refused() -> None:
    with pytest.raises(ProviderInventoryError, match="Duplicate"):
        parse_provider_inventory(b'{"schema_version":"provider-inventory.v1","schema_version":"x"}')


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_refused(literal: str) -> None:
    with pytest.raises(ProviderInventoryError, match="Non-finite"):
        parse_provider_inventory(f'{{"schema_version": {literal}}}'.encode())


def test_invalid_utf8_is_refused() -> None:
    with pytest.raises(ProviderInventoryError, match="UTF-8"):
        parse_provider_inventory(b"\xff\xfe")


@pytest.mark.parametrize(
    ("path", "count"),
    [(["providers"], 65), ([*P0, "models"], 65), ([*P0, "features", "vendor_only"], 17)],
)
def test_every_count_is_bounded(path: list[Any], count: int) -> None:
    document = base()
    if path == ["providers"]:
        template = document["providers"][1]
        document["providers"] = [dict(template, provider_id=f"p{i}") for i in range(count)]
    elif path[-1] == "models":
        document["providers"][0]["models"] = [{"model": f"m{i}"} for i in range(count)]
    else:
        document["providers"][0]["features"]["vendor_only"] = [
            {"feature": f"f{i}", "reconstruction": "none"} for i in range(count)
        ]

    with pytest.raises(ProviderInventoryError):
        _parse(document)
    assert not _schema_accepts(document)


def test_overlong_text_is_refused() -> None:
    document = base()
    document["providers"][0]["models"] = [{"model": "m" * 257}]

    with pytest.raises(ProviderInventoryError):
        _parse(document)
    assert not _schema_accepts(document)


def test_a_document_over_the_size_limit_is_refused() -> None:
    with pytest.raises(ProviderInventoryError, match="size limit"):
        parse_provider_inventory(b" " * (MAX_INVENTORY_BYTES + 1))


# --- files and digests -------------------------------------------------------


def test_the_digest_binds_the_inventory_to_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "inventory.json"
    path.write_bytes((EXAMPLES / "local-only.json").read_bytes())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    assert load_provider_inventory(path, expected_sha256=digest).providers
    with pytest.raises(ProviderInventoryError, match="differs"):
        load_provider_inventory(path, expected_sha256="0" * 64)
    with pytest.raises(ProviderInventoryError, match="Invalid expected digest"):
        load_provider_inventory(path, expected_sha256="not-a-digest")


def test_an_oversized_file_is_refused_before_it_is_read(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_bytes(b" " * (MAX_INVENTORY_BYTES + 1))

    with pytest.raises(ValueError, match="bounded regular file"):
        load_provider_inventory(path)


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_bytes(b"")

    with pytest.raises(ProviderInventoryError):
        load_provider_inventory(path)


def test_a_symlinked_inventory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_bytes((EXAMPLES / "local-only.json").read_bytes())
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(ValueError, match="Linked"):
        load_provider_inventory(link)
