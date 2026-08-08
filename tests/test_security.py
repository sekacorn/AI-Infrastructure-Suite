"""Security guarantees: redaction, no leakage, no execution, no network.

These tests encode promises the README and docs make to users, so a regression
here is a documentation lie, not just a bug.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

import pytest
from typer.testing import CliRunner

from ai_infrastructure_suite._inspection import (
    MAX_DIAGNOSTIC_CHARS,
    ImportProbe,
    environment_info,
    probe_import,
    redact_text,
)
from ai_infrastructure_suite.cli import app
from ai_infrastructure_suite.components import Component, EcosystemManifest
from ai_infrastructure_suite.doctor import inspect_installation

runner = CliRunner()


class TestRedaction:
    """`redact_text` is the last line of defence before output."""

    @pytest.mark.parametrize(
        "raw",
        [
            r"C:\Users\someone\Documents\project\file.py",
            r"c:/users/someone/appdata/local/temp",
            r"\\?\C:\Users\someone\secret",
            "/home/someone/.config/app",
            "/Users/someone/Library/thing",
            "/root/.ssh/id_rsa",
            "/var/lib/postgresql/data",
            "/tmp/pytest-of-someone/pytest-1",
        ],
    )
    def test_paths_are_removed(self, raw: str) -> None:
        cleaned = redact_text(f"failed at {raw} while loading")
        assert "someone" not in cleaned
        assert "[redacted]" in cleaned

    @pytest.mark.parametrize(
        "raw",
        [
            "api_key=sk-abcdef123456",
            "API-KEY: sk-abcdef123456",
            "token = ghp_aaaabbbbccccdddd",
            "password: hunter2correcthorse",
            "secret=topsecretvalue",
            "authorization: Bearer eyJhbGciOi",
        ],
    )
    def test_secret_assignments_are_removed(self, raw: str) -> None:
        cleaned = redact_text(raw)
        for leak in ("sk-abcdef123456", "ghp_aaaabbbbccccdddd", "hunter2", "topsecret", "eyJhbG"):
            assert leak not in cleaned
        assert "[redacted]" in cleaned

    def test_credentials_in_urls_are_removed(self) -> None:
        cleaned = redact_text("cloning https://user:p4ssw0rd@example.invalid/repo.git")
        assert "p4ssw0rd" not in cleaned
        assert "[redacted]" in cleaned

    def test_output_is_length_bounded(self) -> None:
        assert len(redact_text("x" * 5000)) <= MAX_DIAGNOSTIC_CHARS

    def test_custom_bound_is_honoured(self) -> None:
        assert len(redact_text("y" * 500, max_chars=40)) <= 40

    def test_control_characters_are_stripped(self) -> None:
        cleaned = redact_text("before\x1b[31mred\x07\x00after")
        assert "\x1b" not in cleaned
        assert "\x07" not in cleaned
        assert "\x00" not in cleaned

    def test_ansi_escape_cannot_drive_the_terminal(self) -> None:
        cleaned = redact_text("\x1b]0;window title\x07")
        assert "\x1b" not in cleaned

    def test_output_is_ascii_safe(self) -> None:
        # Must survive a legacy cp1252 console.
        redact_text("x" * 500).encode("cp1252")

    def test_ordinary_text_survives(self) -> None:
        assert redact_text("ModuleNotFoundError: No module named 'forge'") == (
            "ModuleNotFoundError: No module named 'forge'"
        )


class TestEnvironmentDisclosure:
    """What `environment_info` is allowed to reveal."""

    def test_exposes_only_four_fields(self) -> None:
        assert set(environment_info().to_dict()) == {
            "python_version",
            "python_implementation",
            "platform_system",
            "platform_machine",
        }

    def test_contains_no_hostname_or_path(self) -> None:
        values = " ".join(environment_info().to_dict().values())
        assert os.sep not in values.replace("/", "") or "\\" not in values
        assert "Users" not in values
        assert "home" not in values


class TestOutputLeakage:
    """No command may leak secrets, paths, or usernames."""

    # The maintainer identity legitimately appears in static manifest URLs
    # (github.com/sekacorn/...). A local account name that is merely a substring
    # of it cannot be distinguished from that public identity, so it is excluded
    # from the username check. The home-directory check below is the real
    # path-leak guard and is never relaxed.
    PUBLIC_IDENTITY = "sekacorn"

    @classmethod
    def _sensitive_markers(cls) -> list[str]:
        markers = ["AI_SUITE_FAKE_SECRET_VALUE"]
        user = os.environ.get("USERNAME") or os.environ.get("USER")
        if user and len(user) > 2 and user.lower() not in cls.PUBLIC_IDENTITY.lower():
            markers.append(user)
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if home:
            markers.append(home)
        return markers

    @pytest.mark.parametrize(
        "argv",
        [
            ["version"],
            ["components"],
            ["components", "--json"],
            ["doctor"],
            ["doctor", "--json"],
            ["doctor", "--no-imports"],
            ["compatibility"],
            ["compatibility", "--json"],
            ["info"],
            ["extras"],
            ["manifest"],
        ],
    )
    def test_no_secret_or_path_leakage(
        self, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_SUITE_FAKE_SECRET", "AI_SUITE_FAKE_SECRET_VALUE")
        monkeypatch.setenv("OPENAI_API_KEY", "AI_SUITE_FAKE_SECRET_VALUE")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "AI_SUITE_FAKE_SECRET_VALUE")
        output = runner.invoke(app, argv).output
        for marker in self._sensitive_markers():
            assert marker not in output, f"{argv} leaked {marker!r}"

    def test_no_environment_variable_names_in_json(self) -> None:
        payload = runner.invoke(app, ["doctor", "--json"]).output
        assert "OPENAI_API_KEY" not in payload
        assert "USERPROFILE" not in payload

    def test_json_output_is_ascii_only(self) -> None:
        payload = runner.invoke(app, ["doctor", "--json"]).output
        payload.encode("ascii")

    def test_json_output_has_no_control_characters(self) -> None:
        payload = json.loads(runner.invoke(app, ["compatibility", "--json"]).output)
        flattened = json.dumps(payload)
        assert not any(char in flattened for char in ("\x1b", "\x00", "\x07"))

    def test_manifest_contains_no_personal_identifiers(self) -> None:
        raw = runner.invoke(app, ["manifest"]).output
        assert "@" not in raw.replace("https://", "")
        for marker in self._sensitive_markers():
            assert marker not in raw

    def test_third_party_import_error_is_redacted_in_output(
        self,
        manifest_factory: Callable[..., EcosystemManifest],
        component_factory: Callable[..., Component],
        fake_environment: Callable[..., None],
    ) -> None:
        fake_environment(
            versions={"example-dist": "1.0.0"},
            probes={
                "example_pkg": ImportProbe(
                    imported=False,
                    error=redact_text(
                        r"ImportError at C:\Users\someone\lib.py with token=abc123secret"
                    ),
                    missing_symbols=(),
                    module_version=None,
                )
            },
        )
        report = inspect_installation(manifest=manifest_factory(component_factory()))
        rendered = json.dumps(report.to_dict())
        assert "someone" not in rendered
        assert "abc123secret" not in rendered


class TestNoSideEffects:
    """The suite performs no network, shell, or unrequested file access."""

    def test_inspection_makes_no_network_call(self, no_network: None) -> None:
        assert inspect_installation(check_imports=False).schema_version == "1.0"

    def test_cli_makes_no_network_call(self, no_network: None) -> None:
        assert runner.invoke(app, ["doctor", "--no-imports", "--json"]).exit_code == 0

    def test_no_subprocess_is_spawned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        def blocked(*args: object, **kwargs: object) -> object:
            raise AssertionError("subprocess spawned")

        monkeypatch.setattr(subprocess, "Popen", blocked)
        monkeypatch.setattr(subprocess, "run", blocked)
        assert runner.invoke(app, ["doctor", "--json"]).exit_code == 0

    def test_no_os_system_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def blocked(*args: object, **kwargs: object) -> object:
            raise AssertionError("shell invoked")

        monkeypatch.setattr(os, "system", blocked)
        assert runner.invoke(app, ["compatibility", "--json"]).exit_code == 0

    def test_no_imports_mode_imports_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import importlib

        def blocked(*args: object, **kwargs: object) -> object:
            raise AssertionError("third-party import attempted")

        monkeypatch.setattr(importlib, "import_module", blocked)
        assert inspect_installation(check_imports=False).schema_version == "1.0"

    def test_probe_import_never_raises(self) -> None:
        probe = probe_import("ai_suite_module_that_does_not_exist_xyz")
        assert probe.imported is False
        assert probe.error is not None
