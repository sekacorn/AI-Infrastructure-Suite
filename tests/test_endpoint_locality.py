"""The shared ``endpoint-locality-cases.v1`` table, and this package's copy of the rule.

The table is packaged so AgentForge and ModelSwapBench can check their own
copies of the rule against it in their ``suite-contract`` jobs. This package's
copy is canonical.
"""

from __future__ import annotations

import ipaddress
import json
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

import jsonschema
import pytest

from ai_infrastructure_suite.provider_inventory import NOT_LOCAL_NETWORKS, origin_is_local


def _data(name: str) -> Any:
    return json.loads(files("ai_infrastructure_suite").joinpath(f"data/{name}").read_text("utf-8"))


TABLE = _data("endpoint-locality-cases.json")


def test_the_table_matches_its_schema() -> None:
    schema = _data("endpoint-locality-cases.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(TABLE)
    assert TABLE["schema_version"] == "endpoint-locality-cases.v1"


@pytest.mark.parametrize("case", TABLE["cases"], ids=lambda case: case["url"])
def test_origin_is_local_agrees_with_every_case(case: dict[str, Any]) -> None:
    assert origin_is_local(case["url"]) is case["local"], case["why"]


def _addresses(*, local: bool) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    found = []
    for case in TABLE["cases"]:
        if case["local"] is not local:
            continue
        try:
            found.append(ipaddress.ip_address(urlsplit(case["url"]).hostname or ""))
        except ValueError:
            continue
    return found


@pytest.mark.parametrize("network", NOT_LOCAL_NETWORKS, ids=str)
def test_the_table_covers_every_excluded_range(network: Any) -> None:
    assert any(address in network for address in _addresses(local=False))


def test_the_table_covers_mapped_forms_of_the_excluded_ranges() -> None:
    mapped = [
        address.ipv4_mapped
        for address in _addresses(local=False)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
    ]
    assert any(any(m in network for network in NOT_LOCAL_NETWORKS) for m in mapped)


def test_urls_in_the_table_are_unique() -> None:
    urls = [case["url"] for case in TABLE["cases"]]
    assert len(urls) == len(set(urls))
