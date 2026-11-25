"""The schema net in conftest.py is installed everywhere, and it bites."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ai_infrastructure_suite import exit_report

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"
NET: Any = exit_report.build_exit_report


def _example(name: str) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(
        (EXAMPLES / name / "report.json").read_text(encoding="utf-8")
    )
    return report


def test_the_module_function_is_the_schema_checked_wrapper() -> None:
    assert getattr(NET, "schema_checked", False)
    # Exactly one layer: the wrapped function is the real one.
    assert not getattr(NET.__wrapped__, "schema_checked", False)
    assert NET.__wrapped__.__module__ == "ai_infrastructure_suite.exit_report"


def test_no_module_holds_the_unchecked_function() -> None:
    # A module that bound the name before conftest ran would bypass the net.
    holders = [
        name
        for name, module in list(sys.modules.items())
        if getattr(module, "build_exit_report", None) is NET.__wrapped__
    ]
    assert holders == []


def test_the_net_fails_a_report_with_an_undeclared_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(*_: Any, **__: Any) -> dict[str, Any]:
        return {**_example("exit-report"), "undeclared": True}

    monkeypatch.setattr(NET, "__wrapped__", build)

    with pytest.raises(AssertionError, match="undeclared"):
        exit_report.build_exit_report(Path("unused.json"))


def test_the_net_fails_a_report_with_an_unknown_level(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(*_: Any, **__: Any) -> dict[str, Any]:
        report = _example("exit-report")
        report["vectors"]["ontology"]["verification_level"] = "verified (trust me)"
        return report

    monkeypatch.setattr(NET, "__wrapped__", build)

    with pytest.raises(AssertionError, match="vectors/ontology"):
        exit_report.build_exit_report(Path("unused.json"))


def test_the_net_fails_a_malformed_re_embed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(*_: Any, **__: Any) -> dict[str, Any]:
        return {**_example("exit-report"), "re_embed_records": [{"path": "record.json"}]}

    monkeypatch.setattr(NET, "__wrapped__", build)

    with pytest.raises(AssertionError, match="re_embed_records"):
        exit_report.build_exit_report(Path("unused.json"))


@pytest.mark.parametrize("example", ["exit-report", "embedding-portability"])
def test_the_committed_example_reports_match_the_schema(example: str) -> None:
    assert NET.schema_errors(_example(example)) == []
