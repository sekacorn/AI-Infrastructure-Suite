"""The ``model_providers`` vector, rubric 2 and ``exit-report.v2``.

Builds real manifests on disk from the shipped inventory and swap-evidence
examples, so every report here also passes through the schema net in
conftest.py. The design's negative tests (section 5) are each pinned below.
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
from typer.testing import CliRunner

from ai_infrastructure_suite.cli import app
from ai_infrastructure_suite.exit_report import VECTORS, build_exit_report, render_markdown

DOCS = Path(__file__).resolve().parents[1] / "docs" / "examples"
SWAP = DOCS / "model-swap-evidence"
WORKED = DOCS / "model-providers"
PLANTED = "mp-planted-4e1d-do-not-log"
ORIGINAL_ELEVEN = VECTORS[:11]


def _swap(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((SWAP / name).read_text(encoding="utf-8"))
    return document


def _provider(provider_id: str = "gateway_a", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "provider_id": provider_id,
        "kind": "openai_compatible",
        "preset": None,
        "preset_source": None,
        "endpoint_origin": "https://llm.vendor-a.example",
        "jurisdiction": "XA",
        "models": [{"model": "model-large"}],
        "openai_compatible_fallback": {"available": False},
        "features": {"tool_calling": False, "streaming": False, "vendor_only": []},
    }
    features = overrides.pop("features", {})
    entry.update(overrides)
    entry["features"] = {**entry["features"], **features}
    return entry


def _inventory(*providers: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "provider-inventory.v1",
        "generated_by": {"tool": "operator", "version": "1"},
        "providers": list(providers) or [_provider()],
    }


def _write(root: Path, name: str, document: object) -> dict[str, str]:
    data = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    (root / name).write_bytes(data)
    return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}


def _manifest(
    root: Path,
    inventory: dict[str, Any] | None = None,
    alternatives: list[dict[str, Any]] | None = None,
    **entry_overrides: Any,
) -> Path:
    entry: dict[str, Any] = {
        "state": "provider_inventory",
        **_write(root, "inventory.json", inventory or _inventory()),
    }
    if alternatives is not None:
        entry["tested_alternatives"] = [
            _write(root, f"swap-{index}.json", document)
            for index, document in enumerate(alternatives)
        ]
    entry.update(entry_overrides)
    path = root / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {"model_providers": entry}}),
        encoding="utf-8",
    )
    return path


def _report(root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return build_exit_report(_manifest(root, *args, **kwargs))


def _provider_row(report: dict[str, Any], provider_id: str = "gateway_a") -> dict[str, Any]:
    rows = {row["provider_id"]: row for row in report["model_providers"]["providers"]}
    row: dict[str, Any] = rows[provider_id]
    return row


def _local(**summary: Any) -> dict[str, Any]:
    """An accepted local alternative for model-large, tools exercised."""
    document = _swap("tools-and-policy.json")
    document["candidate"]["summary"].update(summary)
    return document


def _local_without_tools() -> dict[str, Any]:
    return _swap("local-accepted.json")


def _hosted() -> dict[str, Any]:
    return _swap("hosted-accepted.json")


def _for_model(document: dict[str, Any], model: str) -> dict[str, Any]:
    moved = copy.deepcopy(document)
    moved["baseline"]["model"] = model
    return moved


# --- report shape ---------------------------------------------------------------


def test_the_report_is_v2_rubric_2_with_twelve_vectors(tmp_path: Path) -> None:
    report = _report(tmp_path)

    assert (report["schema_version"], report["rubric_version"]) == ("exit-report.v2", "2")
    assert len(VECTORS) == 12
    assert VECTORS[-1] == "model_providers"
    assert tuple(report["vectors"]) == VECTORS


def test_an_absent_vector_is_an_unknown_row_never_a_missing_one(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":"exit-manifest.v1","vectors":{}}', encoding="utf-8")
    report = build_exit_report(manifest)

    assert report["vectors"]["model_providers"] == {
        "state": "unknown",
        "score": 100,
        "verification_level": "unverified",
        "reason": "No evidence supplied.",
    }
    assert "model_providers" not in report


def test_the_original_eleven_rows_are_unchanged_by_the_vector(tmp_path: Path) -> None:
    worked = build_exit_report(WORKED / "manifest.json")
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    plain = plain_dir / "manifest.json"
    plain.write_text('{"schema_version":"exit-manifest.v1","vectors":{}}', encoding="utf-8")
    baseline = build_exit_report(plain)

    for vector in ORIGINAL_ELEVEN:
        assert worked["vectors"][vector] == baseline["vectors"][vector], vector
    # And for the shipped example with evidence in other vectors.
    example = build_exit_report(DOCS / "exit-report" / "manifest.json")
    committed = json.loads((DOCS / "exit-report" / "report.json").read_text(encoding="utf-8"))
    for vector in ORIGINAL_ELEVEN:
        assert example["vectors"][vector] == committed["vectors"][vector], vector


def test_the_v1_schema_stays_packaged_and_still_validates_v1_reports() -> None:
    v1 = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/exit-report.schema.json")
        .read_text(encoding="utf-8")
    )
    v2 = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/exit-report.v2.schema.json")
        .read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator.check_schema(v2)
    report = build_exit_report(DOCS / "exit-report" / "manifest.json")
    v1_report = copy.deepcopy(report)
    v1_report.update(schema_version="exit-report.v1", rubric_version="1")
    v1_report["vectors"].pop("model_providers")

    assert jsonschema.Draft202012Validator(v1).is_valid(v1_report)
    assert jsonschema.Draft202012Validator(v2).is_valid(report)
    # Neither accepts the other's shape.
    assert not jsonschema.Draft202012Validator(v1).is_valid(report)
    assert not jsonschema.Draft202012Validator(v2).is_valid(v1_report)


def test_the_worked_example_reproduces_its_committed_report() -> None:
    report = build_exit_report(WORKED / "manifest.json")
    committed = json.loads((WORKED / "report.json").read_text(encoding="utf-8"))
    report.pop("components")
    committed.pop("components")
    report.pop("suite_version")
    committed.pop("suite_version")

    assert report == committed
    assert report["vectors"]["model_providers"]["score"] == 75
    assert report["model_providers"]["governing_provider"] == "vendor_b"


def test_the_worked_example_digests_match_the_checked_out_bytes() -> None:
    manifest = json.loads((WORKED / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["vectors"]["model_providers"]
    for reference in [entry, *entry["tested_alternatives"]]:
        data = (WORKED / reference["path"]).read_bytes()
        assert b"\r" not in data, reference["path"]
        assert hashlib.sha256(data).hexdigest() == reference["sha256"], reference["path"]


# --- the ladder ------------------------------------------------------------------


def test_25_needs_a_local_accepted_alternative_that_exercised_every_feature(tmp_path: Path) -> None:
    inventory = _inventory(_provider(features={"tool_calling": True}))
    report = _report(tmp_path, inventory, [_local()])
    row = _provider_row(report)

    assert (row["score"], row["verification_level"], row["cases"]) == (
        25,
        "verified (benchmark arithmetic)",
        [],
    )
    assert report["vectors"]["model_providers"]["score"] == 25
    assert report["vectors"]["model_providers"]["verification_level"] == (
        "verified (benchmark arithmetic)"
    )


def test_nothing_ever_scores_0(tmp_path: Path) -> None:
    inventory = _inventory(_provider())
    for alternatives in ([_local()], [_local(), _local_without_tools()], [_local(), _hosted()]):
        report = _report(tmp_path, inventory, alternatives)
        assert report["vectors"]["model_providers"]["score"] != 0
        assert all(row["score"] >= 25 for row in report["model_providers"]["providers"])


def test_50_hosted_alternative_names_its_case(tmp_path: Path) -> None:
    row = _provider_row(_report(tmp_path, _inventory(_provider()), [_hosted()]))

    assert (row["score"], row["cases"]) == (50, ["hosted_alternative"])
    assert row["verification_level"] == "verified (benchmark arithmetic)"
    assert "hosted, which moves the workload between vendors, not off them" in row["reason"]


def test_50_untested_fallback_names_its_case(tmp_path: Path) -> None:
    inventory = _inventory(_provider(openai_compatible_fallback={"available": True}))
    row = _provider_row(_report(tmp_path, inventory))

    assert (row["score"], row["cases"], row["verification_level"]) == (
        50,
        ["untested_fallback"],
        "attested",
    )
    assert "fallback was not tested" in row["reason"]


def test_50_feature_not_exercised_names_the_feature(tmp_path: Path) -> None:
    inventory = _inventory(_provider(features={"tool_calling": True}))
    row = _provider_row(_report(tmp_path, inventory, [_local_without_tools()]))

    assert (row["score"], row["cases"], row["features_not_exercised"]) == (
        50,
        ["feature_not_exercised"],
        ["tool_calling"],
    )
    assert "not exercised by an accepted local alternative: tool_calling" in row["reason"]


def test_streaming_is_never_exercised(tmp_path: Path) -> None:
    inventory = _inventory(_provider(features={"streaming": True}))
    row = _provider_row(_report(tmp_path, inventory, [_local()]))

    assert (row["score"], row["features_not_exercised"]) == (50, ["streaming"])


def test_a_provider_hitting_two_cases_names_both(tmp_path: Path) -> None:
    inventory = _inventory(_provider(features={"tool_calling": True, "streaming": True}))
    row = _provider_row(_report(tmp_path, inventory, [_hosted()]))

    assert row["cases"] == ["hosted_alternative", "feature_not_exercised"]
    assert row["features_not_exercised"] == ["tool_calling", "streaming"]
    assert "hosted" in row["reason"]
    assert "tool_calling, streaming" in row["reason"]


def test_a_fallback_and_an_unexercised_feature_are_both_named(tmp_path: Path) -> None:
    inventory = _inventory(
        _provider(openai_compatible_fallback={"available": True}, features={"streaming": True})
    )
    row = _provider_row(_report(tmp_path, inventory))

    assert row["cases"] == ["untested_fallback", "feature_not_exercised"]


def test_a_local_alternative_without_tools_and_a_hosted_one_with_tools(tmp_path: Path) -> None:
    # Neither alone covers the model: the local one is the better case, and
    # the reason names only what is literally true of it.
    inventory = _inventory(_provider(features={"tool_calling": True}))
    hosted_with_tools = _hosted()
    hosted_with_tools["candidate"]["summary"]["tool_accuracy"] = 1.0
    row = _provider_row(_report(tmp_path, inventory, [_local_without_tools(), hosted_with_tools]))

    assert (row["score"], row["cases"]) == (50, ["feature_not_exercised"])


def test_a_local_alternative_beats_a_hosted_one_for_the_same_model(tmp_path: Path) -> None:
    row = _provider_row(_report(tmp_path, _inventory(_provider()), [_hosted(), _local()]))
    assert row["score"] == 25


def test_every_model_must_be_covered(tmp_path: Path) -> None:
    inventory = _inventory(_provider(models=[{"model": "model-large"}, {"model": "model-xl"}]))
    partial = _provider_row(_report(tmp_path, inventory, [_local()]))
    assert (partial["score"], partial["models_with_accepted_alternative"]) == (100, 1)

    both = _provider_row(_report(tmp_path, inventory, [_local(), _for_model(_local(), "model-xl")]))
    assert (both["score"], both["models_with_accepted_alternative"]) == (25, 2)


def test_75_and_100_for_vendor_only_features_override_any_benchmark(tmp_path: Path) -> None:
    documented = _inventory(
        _provider(
            features={"vendor_only": [{"feature": "prompt_cache", "reconstruction": "documented"}]}
        )
    )
    row = _provider_row(_report(tmp_path, documented, [_local()]))
    assert (row["score"], row["verification_level"]) == (75, "attested")

    none = _inventory(
        _provider(features={"vendor_only": [{"feature": "file_search", "reconstruction": "none"}]})
    )
    row = _provider_row(_report(tmp_path, none, [_local()]))
    assert row["score"] == 100
    assert "no reconstruction path" in row["reason"]


def test_100_with_no_fallback_and_no_accepted_alternative(tmp_path: Path) -> None:
    row = _provider_row(_report(tmp_path, _inventory(_provider())))
    assert (row["score"], row["cases"], row["verification_level"]) == (100, [], "attested")


def test_the_worst_provider_governs_and_a_tie_takes_the_weaker_level(tmp_path: Path) -> None:
    inventory = _inventory(
        _provider("gateway_a"),
        _provider(
            "gateway_b",
            endpoint_origin="https://llm.vendor-b.example",
            openai_compatible_fallback={"available": True},
        ),
    )
    # gateway_a: hosted alternative, 50 verified. gateway_b: fallback, 50 attested.
    report = _report(tmp_path, inventory, [_hosted()])
    row = report["vectors"]["model_providers"]

    assert (row["score"], row["verification_level"]) == (50, "attested")
    assert report["model_providers"]["governing_provider"] == "gateway_b"
    assert row["reason"].startswith("Worst of 2 providers: gateway_b scores 50")


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("fake-candidate.json", "the candidate is a test fixture, not a model"),
        ("insufficient-samples.json", "too few samples"),
        ("not-recommended.json", "failed gate: quality_drop"),
        ("policy-failed.json", "failed gate: policy"),
        ("nothing-executed.json", "no case executed, so there is no quality score"),
    ],
)
def test_evidence_that_is_not_accepted_is_reported_and_moves_nothing(
    tmp_path: Path, name: str, why: str
) -> None:
    report = _report(tmp_path, _inventory(_provider()), [_swap(name)])
    alternative = report["model_providers"]["tested_alternatives"][0]

    assert alternative["accepted"] is False
    assert why in alternative["reasons"]
    assert _provider_row(report)["score"] == 100


def test_a_fake_candidate_is_labelled_fake(tmp_path: Path) -> None:
    report = _report(tmp_path, _inventory(_provider()), [_swap("fake-candidate.json")])
    assert report["model_providers"]["tested_alternatives"][0]["candidate_hosting"] == "fake"


def test_cost_is_ignored(tmp_path: Path) -> None:
    dearer = _local()
    dearer["thresholds"]["minimum_cost_reduction"] = 0.99
    assert _provider_row(_report(tmp_path, _inventory(_provider()), [dearer]))["score"] == 25


def test_tool_calling_in_use_with_a_null_tool_accuracy_is_50_not_25(tmp_path: Path) -> None:
    inventory = _inventory(_provider(features={"tool_calling": True}))
    row = _provider_row(_report(tmp_path, inventory, [_local(tool_accuracy=None)]))
    assert row["score"] == 50


def test_jurisdiction_never_changes_a_score(tmp_path: Path) -> None:
    scores = {
        jurisdiction: _report(
            tmp_path, _inventory(_provider(jurisdiction=jurisdiction)), [_local()]
        )["vectors"]["model_providers"]["score"]
        for jurisdiction in ("XA", "XB", "unknown")
    }
    assert set(scores.values()) == {25}


def test_a_self_hosted_candidate_at_a_public_host_is_hosted_and_flagged(tmp_path: Path) -> None:
    document = _local_without_tools()
    document["candidate"].update(
        endpoint_origin="https://gpu.vendor-c.example", deployment_declared="self_hosted"
    )
    report = _report(tmp_path, _inventory(_provider()), [document])
    alternative = report["model_providers"]["tested_alternatives"][0]

    assert (
        alternative["candidate_hosting"],
        alternative["candidate_declaration_contradicted"],
    ) == (
        "hosted",
        True,
    )
    assert _provider_row(report)["cases"] == ["hosted_alternative"]


# --- refused, not skipped ----------------------------------------------------------


def _refused(path: Path, match: str) -> None:
    with pytest.raises(ValueError, match=match) as excinfo:
        build_exit_report(path)
    assert PLANTED not in str(excinfo.value)


def test_a_candidate_declared_local_at_a_public_host_is_refused(tmp_path: Path) -> None:
    document = _local()
    document["candidate"].update(endpoint_origin="https://gpu.vendor-c.example")
    _refused(_manifest(tmp_path, _inventory(_provider()), [document]), "declares local deployment")


def test_evidence_that_binds_to_no_provider_is_refused(tmp_path: Path) -> None:
    _refused(
        _manifest(tmp_path, _inventory(_provider()), [_for_model(_local(), "model-other")]),
        "matches no provider",
    )


def test_evidence_that_binds_to_two_providers_is_refused(tmp_path: Path) -> None:
    inventory = _inventory(_provider("gateway_a"), _provider("gateway_a_too"))
    _refused(_manifest(tmp_path, inventory, [_local()]), "more than one provider")


def test_a_stated_verdict_that_disagrees_is_refused(tmp_path: Path) -> None:
    document = _local()
    document["candidate"]["summary"]["quality_score"] = 0.5
    _refused(_manifest(tmp_path, _inventory(_provider()), [document]), "disagrees")


def test_a_digest_mismatch_is_refused(tmp_path: Path) -> None:
    path = _manifest(tmp_path, _inventory(_provider()), [_local()])
    (tmp_path / "swap-0.json").write_bytes(b"{}")
    _refused(path, "SHA-256")
    path = _manifest(tmp_path, _inventory(_provider()), [_local()])
    (tmp_path / "inventory.json").write_bytes(b"{}")
    _refused(path, "SHA-256")


def test_more_than_64_alternatives_and_duplicates_are_refused(tmp_path: Path) -> None:
    path = _manifest(tmp_path, _inventory(_provider()), [_local()])
    document = json.loads(path.read_text(encoding="utf-8"))
    entry = document["vectors"]["model_providers"]
    one = entry["tested_alternatives"][0]
    entry["tested_alternatives"] = [one, one]
    path.write_text(json.dumps(document), encoding="utf-8")
    _refused(path, "declared twice")
    entry["tested_alternatives"] = [one] * 65
    path.write_text(json.dumps(document), encoding="utf-8")
    _refused(path, "tested_alternatives")


def test_an_invalid_inventory_is_refused(tmp_path: Path) -> None:
    inventory = _inventory(_provider(endpoint_origin=f"https://user:{PLANTED}@host.example"))
    _refused(_manifest(tmp_path, inventory), "Invalid provider inventory")


@pytest.mark.parametrize(
    "state",
    ["exported", "restore_tested", "importer_validated", "embedding_inventory", "attested"],
)
def test_only_provider_inventory_and_inapplicable_are_accepted(tmp_path: Path, state: str) -> None:
    _refused(_manifest(tmp_path, state=state), "accepts only provider_inventory or inapplicable")


def test_inapplicable_is_scored_null_and_takes_no_alternatives(tmp_path: Path) -> None:
    data = b"This system calls no model provider."
    (tmp_path / "why.txt").write_bytes(data)
    entry = {"state": "inapplicable", "path": "why.txt", "sha256": hashlib.sha256(data).hexdigest()}
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {"model_providers": entry}}),
        encoding="utf-8",
    )
    row = build_exit_report(path)["vectors"]["model_providers"]
    assert (row["state"], row["score"], row["verification_level"]) == (
        "inapplicable",
        None,
        "attested",
    )
    entry["tested_alternatives"] = []
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {"model_providers": entry}}),
        encoding="utf-8",
    )
    _refused(path, "inapplicable model_providers entry")


def test_another_vector_cannot_declare_a_provider_inventory(tmp_path: Path) -> None:
    path = _manifest(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["vectors"]["prompts"] = document["vectors"].pop("model_providers")
    path.write_text(json.dumps(document), encoding="utf-8")
    _refused(path, "Unrecognized evidence state|Each entry requires")


def test_an_unknown_field_in_the_entry_is_refused(tmp_path: Path) -> None:
    _refused(_manifest(tmp_path, notes=PLANTED), "Unexpected field")


# --- what the report never contains -------------------------------------------------


def test_no_credentials_or_full_urls_anywhere_in_the_report(tmp_path: Path) -> None:
    report = _report(tmp_path, _inventory(_provider()), [_local(), _hosted()])
    text = json.dumps(report) + render_markdown(report)
    assert "/v1" not in text
    assert "@" not in text
    assert "?" not in text.replace("? ", "")


def test_markdown_shows_the_section_and_rubric_2(tmp_path: Path) -> None:
    report = _report(tmp_path, _inventory(_provider()), [_hosted()])
    markdown = render_markdown(report)

    assert "| Rubric 2" in markdown
    assert "| model_providers | 50 |" in markdown
    assert "## Model providers" in markdown
    assert "### Tested alternatives" in markdown
    assert "moves the workload between vendors" in markdown


def test_operator_text_cannot_break_the_markdown_table(tmp_path: Path) -> None:
    inventory = _inventory(_provider(models=[{"model": "model|large\n# heading`"}]))
    document = _for_model(_local(), "model|large\n# heading`")
    markdown = render_markdown(_report(tmp_path, inventory, [document]))
    table = markdown.split("### Tested alternatives", 1)[1]

    assert "model?large?# heading?" in table
    assert "\n# heading" not in markdown


def test_the_cli_prints_a_v2_report(tmp_path: Path) -> None:
    path = _manifest(tmp_path, _inventory(_provider()), [_local()])
    result = CliRunner().invoke(app, ["exit-report", str(path), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["schema_version"] == "exit-report.v2"


def test_the_manifest_schema_accepts_the_vector_only_with_its_two_states() -> None:
    schema = json.loads(
        files("ai_infrastructure_suite")
        .joinpath("data/exit-manifest.schema.json")
        .read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    worked = json.loads((WORKED / "manifest.json").read_text(encoding="utf-8"))
    assert validator.is_valid(worked)

    for state in ("exported", "embedding_inventory"):
        bad = copy.deepcopy(worked)
        bad["vectors"]["model_providers"]["state"] = state
        assert not validator.is_valid(bad)
    inapplicable = copy.deepcopy(worked)
    inapplicable["vectors"]["model_providers"]["state"] = "inapplicable"
    assert not validator.is_valid(inapplicable)
    inapplicable["vectors"]["model_providers"].pop("tested_alternatives")
    assert validator.is_valid(inapplicable)
    elsewhere = copy.deepcopy(worked)
    elsewhere["vectors"]["prompts"] = elsewhere["vectors"].pop("model_providers")
    assert not validator.is_valid(elsewhere)


# --- a provider that is itself local --------------------------------------------------

LOCAL_ONLY = DOCS / "model-providers-local"


def _ollama(**overrides: Any) -> dict[str, Any]:
    return _provider(
        "local_ollama",
        kind="ollama",
        endpoint_origin="http://127.0.0.1:11434",
        jurisdiction="not_applicable",
        models=[{"model": "open-model-8b"}],
        **overrides,
    )


def test_an_ollama_only_inventory_scores_25_never_0_and_never_100(tmp_path: Path) -> None:
    report = _report(tmp_path, _inventory(_ollama()))
    row = report["vectors"]["model_providers"]
    provider = _provider_row(report, "local_ollama")

    assert (row["score"], row["verification_level"]) == (25, "attested")
    assert (provider["score"], provider["cases"]) == (25, [])
    assert "derives its endpoint as local" in provider["reason"]
    assert "that the local model is open or self-hostable" in provider["attested_claims"]
    assert "that the local model is open or self-hostable" in row["reason"]


def test_the_shipped_local_example_scores_25() -> None:
    report = build_exit_report(LOCAL_ONLY / "manifest.json")
    committed = json.loads((LOCAL_ONLY / "report.json").read_text(encoding="utf-8"))
    for document in (report, committed):
        document.pop("components")
        document.pop("suite_version")

    assert report == committed
    assert report["vectors"]["model_providers"]["score"] == 25


@pytest.mark.parametrize(
    "origin", ["http://localhost:8000", "http://10.0.0.7:8000", "http://[::1]:8000"]
)
def test_any_endpoint_the_scanner_derives_as_local_scores_25(tmp_path: Path, origin: str) -> None:
    inventory = _inventory(
        _provider(endpoint_origin=origin, jurisdiction="not_applicable", hosting_declared="local")
    )
    assert _provider_row(_report(tmp_path, inventory))["score"] == 25


def test_a_local_endpoint_declared_hosted_does_not_get_the_local_rule(tmp_path: Path) -> None:
    declared_hosted = _ollama(hosting_declared="hosted")
    declared_hosted["jurisdiction"] = "unknown"
    report = _report(tmp_path, _inventory(declared_hosted))
    assert _provider_row(report, "local_ollama")["score"] == 100


def test_an_sdk_default_endpoint_never_gets_the_local_rule(tmp_path: Path) -> None:
    inventory = _inventory(_provider(kind="anthropic", endpoint_origin=None))
    assert _provider_row(_report(tmp_path, inventory))["score"] == 100


def test_a_vendor_only_feature_still_governs_a_local_provider(tmp_path: Path) -> None:
    none = _ollama(features={"vendor_only": [{"feature": "file_search", "reconstruction": "none"}]})
    assert _provider_row(_report(tmp_path, _inventory(none)), "local_ollama")["score"] == 100
    documented = _ollama(
        features={"vendor_only": [{"feature": "prompt_cache", "reconstruction": "documented"}]}
    )
    assert _provider_row(_report(tmp_path, _inventory(documented)), "local_ollama")["score"] == 75


def test_a_local_provider_with_only_a_hosted_alternative_is_still_25(tmp_path: Path) -> None:
    hosted = _for_model(_hosted(), "open-model-8b")
    hosted["baseline"].update(
        provider_kind="ollama",
        endpoint_origin="http://127.0.0.1:11434",
        deployment_declared="local",
    )
    provider = _provider_row(_report(tmp_path, _inventory(_ollama()), [hosted]), "local_ollama")
    assert (provider["score"], provider["verification_level"], provider["cases"]) == (
        25,
        "attested",
        [],
    )


def test_a_local_provider_with_an_accepted_local_alternative_is_25_verified(tmp_path: Path) -> None:
    local = _for_model(_local_without_tools(), "open-model-8b")
    local["baseline"].update(
        provider_kind="ollama",
        endpoint_origin="http://127.0.0.1:11434",
        deployment_declared="local",
    )
    provider = _provider_row(_report(tmp_path, _inventory(_ollama()), [local]), "local_ollama")
    assert (provider["score"], provider["verification_level"]) == (
        25,
        "verified (benchmark arithmetic)",
    )
    assert "that the local model is open or self-hostable" not in provider["attested_claims"]


# --- every declared input is listed as attested ----------------------------------------


DECLARED = [
    "models in use",
    "features in use (tool calling, streaming)",
    "vendor-only features in use, or that none are",
    "jurisdiction",
]


def test_a_verified_50_still_lists_every_declared_input(tmp_path: Path) -> None:
    inventory = _inventory(_provider(features={"tool_calling": True, "streaming": True}))
    report = _report(tmp_path, inventory, [_hosted()])
    provider = _provider_row(report)

    assert (provider["score"], provider["verification_level"]) == (
        50,
        "verified (benchmark arithmetic)",
    )
    for claim in [*DECLARED, "that any benchmarked model was run", "every measured number"]:
        assert claim in provider["attested_claims"], claim
        assert claim in report["model_providers"]["attested_claims"], claim
        assert claim in report["vectors"]["model_providers"]["reason"], claim


def test_reconstruction_paths_are_listed_whenever_a_vendor_only_feature_is(tmp_path: Path) -> None:
    documented = _inventory(
        _provider(
            features={"vendor_only": [{"feature": "prompt_cache", "reconstruction": "documented"}]}
        )
    )
    claims = _provider_row(_report(tmp_path, documented))["attested_claims"]
    assert "reconstruction paths for vendor-only features" in claims
    plain = _provider_row(_report(tmp_path, _inventory(_provider())))["attested_claims"]
    assert "reconstruction paths for vendor-only features" not in plain
    assert "vendor-only features in use, or that none are" in plain


def test_every_provider_lists_every_declared_input(tmp_path: Path) -> None:
    report = build_exit_report(WORKED / "manifest.json")
    for provider in report["model_providers"]["providers"]:
        for claim in DECLARED:
            assert claim in provider["attested_claims"], (provider["provider_id"], claim)
    assert report["model_providers"]["attested_claims"][-1] == "producer"


# --- optional producer markers (suite 0.2.0b2) -----------------------------------------


def test_registry_models_and_an_override_are_listed_as_attested(tmp_path: Path) -> None:
    from ai_infrastructure_suite.provider_inventory import (
        CLAIM_ENDPOINT_OVERRIDE,
        CLAIM_REGISTRY_MODELS,
    )

    marked = _provider(
        kind="openai",
        endpoint_origin=None,
        jurisdiction="unknown",
        endpoint_override_present=True,
        models=[{"model": "model-large", "source": "registry"}],
    )
    report = _report(tmp_path, _inventory(marked))
    provider = _provider_row(report)

    for claim in (CLAIM_REGISTRY_MODELS, CLAIM_ENDPOINT_OVERRIDE):
        assert claim in provider["attested_claims"], claim
        assert claim in report["model_providers"]["attested_claims"], claim
        assert claim in report["vectors"]["model_providers"]["reason"], claim


def test_the_markers_never_change_a_score(tmp_path: Path) -> None:
    plain = _provider(kind="openai", endpoint_origin=None, jurisdiction="unknown")
    marked = copy.deepcopy(plain)
    marked["endpoint_override_present"] = True
    marked["models"] = [{"model": "model-large", "source": "registry"}]

    before = _provider_row(_report(tmp_path, _inventory(plain)))
    after = _provider_row(_report(tmp_path, _inventory(marked)))
    assert (before["score"], before["verification_level"], before["cases"]) == (
        after["score"],
        after["verification_level"],
        after["cases"],
    )


def test_an_unmarked_provider_lists_neither_marker(tmp_path: Path) -> None:
    from ai_infrastructure_suite.provider_inventory import (
        CLAIM_ENDPOINT_OVERRIDE,
        CLAIM_REGISTRY_MODELS,
    )

    claims = _provider_row(_report(tmp_path, _inventory(_provider())))["attested_claims"]
    assert CLAIM_REGISTRY_MODELS not in claims
    assert CLAIM_ENDPOINT_OVERRIDE not in claims
