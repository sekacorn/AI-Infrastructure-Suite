"""CLI behaviour: help, tables, JSON, exit codes."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from ai_infrastructure_suite._version import __version__
from ai_infrastructure_suite.cli import ExitCode, app
from ai_infrastructure_suite.components import load_manifest

runner = CliRunner()


class TestHelp:
    """Discovery surface."""

    def test_root_help_lists_every_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for command in (
            "version",
            "components",
            "doctor",
            "compatibility",
            "info",
            "manifest",
            "extras",
        ):
            assert command in result.output

    def test_no_args_shows_help(self) -> None:
        assert "Usage" in runner.invoke(app, []).output

    @pytest.mark.parametrize(
        "command",
        ["version", "components", "doctor", "compatibility", "info", "manifest", "extras"],
    )
    def test_each_command_has_help(self, command: str) -> None:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0

    def test_help_states_non_affiliation(self) -> None:
        assert "not affiliated" in runner.invoke(app, ["--help"]).output.lower()


class TestVersion:
    """`ai-suite version`."""

    def test_reports_suite_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_reports_python_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in result.output


class TestComponents:
    """`ai-suite components`."""

    def test_table_lists_all_seven(self) -> None:
        result = runner.invoke(app, ["components"])
        assert result.exit_code == 0
        for name in (
            "AgentForge",
            "AgentPolicyPack",
            "AIAuditLog",
            "AIMeter",
            "ModelSwapBench",
            "OpenOntologyLite",
            "PrivateAIStack",
        ):
            assert name in result.output

    def test_table_shows_distribution_and_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Rich wraps cells to the terminal width; widen it so values stay intact.
        monkeypatch.setenv("COLUMNS", "240")
        output = runner.invoke(app, ["components"]).output
        assert "agentforge-oss" in output
        # The displayed range tracks the manifest rather than a hard-coded string.
        agentforge = load_manifest().component("agentforge")
        assert agentforge.version_specifier in output
        assert "model_swap_bench" in output

    def test_json_is_parseable_and_complete(self) -> None:
        result = runner.invoke(app, ["components", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert len(payload["components"]) == 7
        assert payload["suite_version"] == __version__


class TestDoctor:
    """`ai-suite doctor`."""

    def test_table_mode_succeeds(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code in (ExitCode.SUCCESS, ExitCode.PROBLEMS_FOUND)
        assert "Component" in result.output

    def test_json_mode_is_parseable(self) -> None:
        result = runner.invoke(app, ["doctor", "--json"])
        payload = json.loads(result.output)
        assert payload["schema_version"] == "1.0"
        assert len(payload["components"]) == 7

    def test_json_carries_required_fields(self) -> None:
        payload = json.loads(runner.invoke(app, ["doctor", "--json"]).output)
        for field in (
            "schema_version",
            "suite_version",
            "environment",
            "python_supported",
            "status",
            "summary",
            "components",
        ):
            assert field in payload
        component = payload["components"][0]
        for field in (
            "distribution",
            "installed_version",
            "required_specifier",
            "status",
            "diagnostics",
        ):
            assert field in component

    def test_no_imports_flag_is_recorded(self) -> None:
        payload = json.loads(runner.invoke(app, ["doctor", "--json", "--no-imports"]).output)
        assert payload["imports_checked"] is False

    def test_no_imports_does_not_fail(self) -> None:
        result = runner.invoke(app, ["doctor", "--no-imports"])
        assert result.exit_code == ExitCode.SUCCESS

    def test_exit_code_matches_ok_flag(self) -> None:
        result = runner.invoke(app, ["doctor", "--json"])
        payload = json.loads(result.output)
        expected = ExitCode.SUCCESS if payload["ok"] else ExitCode.PROBLEMS_FOUND
        assert result.exit_code == expected


class TestCompatibility:
    """`ai-suite compatibility`."""

    def test_table_mode_lists_layers(self) -> None:
        result = runner.invoke(app, ["compatibility"])
        for layer in ("installation", "imports", "cli", "offline_contract", "live_runtime"):
            assert layer in result.output

    def test_table_states_live_runtime_untested(self) -> None:
        assert "not tested" in runner.invoke(app, ["compatibility"]).output.lower()

    def test_json_mode_is_parseable(self) -> None:
        payload = json.loads(runner.invoke(app, ["compatibility", "--json"]).output)
        assert len(payload["layers"]) == 5
        assert payload["schema_version"] == "1.0"

    def test_json_live_runtime_not_checked(self) -> None:
        payload = json.loads(runner.invoke(app, ["compatibility", "--json"]).output)
        live = next(item for item in payload["layers"] if item["layer"] == "live_runtime")
        assert live["status"] == "not_checked"


class TestInfoAndExtras:
    """`ai-suite info` and `ai-suite extras`."""

    def test_info_states_non_affiliation(self) -> None:
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert "Linux Foundation" in result.output

    def test_info_shows_install_commands(self) -> None:
        assert "pip install" in runner.invoke(app, ["info"]).output

    def test_extras_lists_every_group(self) -> None:
        output = runner.invoke(app, ["extras"]).output
        for extra in ("benchmarking", "full", "governance", "local", "observability"):
            assert extra in output


class TestManifestCommand:
    """`ai-suite manifest`."""

    def test_emits_parseable_json(self) -> None:
        result = runner.invoke(app, ["manifest"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert len(payload["components"]) == 7

    def test_writes_only_to_the_requested_path(self, tmp_path: object) -> None:
        target = tmp_path / "manifest.json"  # type: ignore[operator]
        result = runner.invoke(app, ["manifest", "--output", str(target)])
        assert result.exit_code == 0
        assert json.loads(target.read_text(encoding="utf-8"))["ecosystem"] == "Linux of AI"


class TestModuleEntryPoint:
    """`python -m ai_infrastructure_suite`."""

    def test_module_invocation_works(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "ai_infrastructure_suite", "version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0
        assert __version__ in completed.stdout

    def test_module_json_output_is_parseable(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "ai_infrastructure_suite", "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert json.loads(completed.stdout)["schema_version"] == "1.0"
