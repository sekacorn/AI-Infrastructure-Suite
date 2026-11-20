import hashlib
import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_infrastructure_suite.cli import app
from ai_infrastructure_suite.exit_report import VECTORS, build_exit_report

EXAMPLE = Path(__file__).parents[1] / "docs/examples/exit-report/manifest.json"


def test_offline_cli_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unexpected network or component import")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("ai_infrastructure_suite.doctor.probe_import", forbidden)
    runner = CliRunner()
    args = ["exit-report", str(EXAMPLE)]
    first = runner.invoke(app, [*args, "--json"])
    assert first.exit_code == 0, first.exception
    assert runner.invoke(app, [*args, "--json"]).stdout == first.stdout
    payload = json.loads(first.stdout)
    assert tuple(payload["vectors"]) == VECTORS
    assert payload["vectors"]["prompts"]["score"] == 50
    assert payload["vectors"]["prompts"]["verification_level"] == "attested"
    assert payload["vectors"]["ontology"]["score"] == 25
    assert payload["vectors"]["ontology"]["verification_level"] == "verified"
    assert payload["vectors"]["contract_terms"]["state"] == "attested"
    assert payload["vectors"]["contract_terms"]["verification_level"] == "attested"
    assert payload["vectors"]["memory"]["state"] == "unknown"
    assert payload["vectors"]["memory"]["verification_level"] == "unverified"
    assert len(payload["components"]) == 7
    markdown = runner.invoke(app, args)
    assert markdown.exit_code == 0
    assert "| prompts | 50 |" in markdown.stdout
    assert "not legal advice" in markdown.stdout.lower()
    assert payload["manifest_sha256"] in markdown.stdout


@pytest.mark.parametrize(
    ("state", "score"),
    [
        ("restore_tested", 0),
        ("importer_validated", 25),
        ("reconstruction", 75),
        ("non_exportable", 100),
        ("inapplicable", None),
    ],
)
def test_evidence_states(tmp_path: Path, state: str, score: int | None) -> None:
    data = b"synthetic operator record"
    (tmp_path / "record.txt").write_bytes(data)
    path = tmp_path / "manifest.json"
    entry = {"path": "record.txt", "sha256": hashlib.sha256(data).hexdigest(), "state": state}
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {"memory": entry}})
    )
    assert build_exit_report(path)["vectors"]["memory"]["score"] == score
    assert build_exit_report(path)["vectors"]["memory"]["verification_level"] == "attested"
    (tmp_path / "record.txt").write_bytes(b"changed")
    assert CliRunner().invoke(app, ["exit-report", str(path)]).exit_code == 2


@pytest.mark.parametrize("name", ["../outside.txt", "C:/abs", "//host/share", "CON", "a:b", ".env"])
def test_rejects_unsafe_references(tmp_path: Path, name: str) -> None:
    doc = json.loads(EXAMPLE.read_text())
    doc["vectors"]["prompts"]["path"] = name
    path = tmp_path / "input.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="relative"):
        build_exit_report(path)


@pytest.mark.parametrize(
    "data",
    [
        b'{"schema_version":"exit-manifest.v1","vectors":{},"vectors":{}}',
        b'{"schema_version":"exit-manifest.v1","vectors":{"extra":{}}}',
        b" " * 65537,
    ],
)
def test_invalid_manifest_is_bounded(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "private.json"
    path.write_bytes(data)
    result = CliRunner().invoke(app, ["exit-report", str(path)])
    assert result.exit_code == 2
    assert path.name not in result.output


def test_linked_manifest_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "alias.json"
    try:
        path.symlink_to(EXAMPLE)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="Linked"):
        build_exit_report(path)


def test_ontology_requires_loadable_openontologylite_json_ld(tmp_path: Path) -> None:
    data = b'{"format_version":"wrong"}'
    (tmp_path / "ontology.jsonld").write_bytes(data)
    entry = {
        "state": "importer_validated",
        "path": "ontology.jsonld",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {"ontology": entry}})
    )
    with pytest.raises(ValueError):
        build_exit_report(path)


def test_contract_terms_must_be_attested(tmp_path: Path) -> None:
    data = b'{"schema_version":"contract-terms-checklist.v1","checklist":{}}'
    (tmp_path / "terms.json").write_bytes(data)
    entry = {
        "state": "exported",
        "path": "terms.json",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": "exit-manifest.v1", "vectors": {"contract_terms": entry}})
    )
    with pytest.raises(ValueError, match="attested"):
        build_exit_report(path)
