"""An absent OpenOntologyLite JSON-LD API fails clearly, not as a traceback.

The failure mode these tests reproduce is real: an installed
`openontologylite` that predates the JSON-LD transfer API imports fine, so
`from open_ontology_lite import json_ld_text` raises `ImportError` deep inside
a check. Replacing the module with one that lacks the names reproduces that
exactly, without needing an older release installed.
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_infrastructure_suite._dependencies import (
    OPENONTOLOGYLITE_JSON_LD_VERSION,
    MissingDependencyError,
    missing_ontology_api,
)
from ai_infrastructure_suite.cli import ExitCode, app
from ai_infrastructure_suite.exit_report import build_exit_report
from ai_infrastructure_suite.restore_verification import verify_restore_summary
from test_restore_verification import make_restore_evidence

ONTOLOGY_EXAMPLE = Path(__file__).parents[1] / "docs/examples/exit-report/manifest.json"


@contextmanager
def without_json_ld_api() -> Iterator[None]:
    """Replace open_ontology_lite with a module that lacks the JSON-LD API.

    Entered inside the test body, not as a fixture: building restore evidence
    runs the real check, so the stub must go up only once the fixture exists.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "open_ontology_lite", types.ModuleType("open_ontology_lite"))
        patch.delitem(sys.modules, "open_ontology_lite.errors", raising=False)
        yield


def test_the_message_names_the_required_version_and_no_local_path() -> None:
    error = missing_ontology_api()
    message = str(error)

    assert isinstance(error, MissingDependencyError)
    assert f"openontologylite>={OPENONTOLOGYLITE_JSON_LD_VERSION}" in message
    assert "this check could not run" in message
    # The underlying ImportError text names the local install path; this must
    # not, because the CLI prints this message verbatim.
    assert "site-packages" not in message
    assert "\\" not in message and "/" not in message


def test_a_missing_dependency_is_not_a_value_error() -> None:
    # The per-profile handler catches ValueError and OSError and records
    # behavior drift. A missing dependency must escape it instead.
    error = missing_ontology_api()

    assert not isinstance(error, (ValueError, OSError))


def test_the_ontology_vector_reports_the_missing_api() -> None:
    with without_json_ld_api(), pytest.raises(MissingDependencyError, match="transfer API"):
        build_exit_report(ONTOLOGY_EXAMPLE)


def test_restore_verification_reports_the_missing_api(tmp_path: Path) -> None:
    make_restore_evidence(tmp_path)

    # Previously this surfaced as an unhandled ImportError traceback.
    with without_json_ld_api(), pytest.raises(MissingDependencyError, match="transfer API"):
        verify_restore_summary(tmp_path / "restore-summary.json")


def test_a_missing_api_is_not_reported_as_behavior_drift(tmp_path: Path) -> None:
    manifest = make_restore_evidence(tmp_path)

    with without_json_ld_api(), pytest.raises(MissingDependencyError) as caught:
        build_exit_report(manifest, verify_restores=True)

    assert "behavior drift" not in str(caught.value)
    assert isinstance(caught.value.__cause__, ImportError)


def test_the_cli_exits_two_with_the_actionable_message(tmp_path: Path) -> None:
    manifest = make_restore_evidence(tmp_path)

    with without_json_ld_api():
        result = CliRunner().invoke(
            app, ["exit-report", str(manifest), "--verify-restores", "--json"]
        )

    assert result.exit_code == ExitCode.ERROR
    assert f"openontologylite>={OPENONTOLOGYLITE_JSON_LD_VERSION}" in result.output
    # Not replaced by the generic redacted message.
    assert "Invalid or inaccessible" not in result.output


def test_an_absent_distribution_reports_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ModuleNotFoundError is an ImportError, so one guard covers both cases.
    monkeypatch.setitem(sys.modules, "open_ontology_lite", None)
    data = b'{"format_version":"x"}'
    (tmp_path / "ontology.jsonld").write_bytes(data)
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "exit-manifest.v1",
                "vectors": {
                    "ontology": {
                        "state": "importer_validated",
                        "path": "ontology.jsonld",
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MissingDependencyError, match="JSON-LD transfer API"):
        build_exit_report(path)


def test_the_embedding_scanner_needs_no_ontology_dependency() -> None:
    example = Path(__file__).parents[1] / "docs/examples/embedding-portability/manifest.json"

    with without_json_ld_api():
        report = build_exit_report(example)

    # Embedding scanning imports no ecosystem project at all.
    assert report["vectors"]["embeddings"]["score"] == 25
