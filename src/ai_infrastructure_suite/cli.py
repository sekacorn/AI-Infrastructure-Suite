"""The ``ai-suite`` command line interface.

Output discipline used throughout this module:

* Rich markup is disabled on the console, so no manifest or third-party string is
  ever interpreted as terminal formatting.
* Table borders fall back to ASCII when the active encoding cannot carry
  box-drawing characters.
* JSON is emitted with ``ensure_ascii`` so it survives any console encoding and
  stays machine-parseable.
* Nothing prints an environment variable, a credential, or an absolute path.
"""

from __future__ import annotations

import json
import sys
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any, Final

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ai_infrastructure_suite._inspection import environment_info, python_version_string
from ai_infrastructure_suite._version import __version__
from ai_infrastructure_suite.compatibility import compatibility_report
from ai_infrastructure_suite.components import (
    available_extras,
    ecosystem_components,
    load_manifest,
)
from ai_infrastructure_suite.doctor import CheckStatus, inspect_installation
from ai_infrastructure_suite.errors import AISuiteError

SUITE_DISTRIBUTION: Final = "ai-infrastructure-suite"


class ExitCode(IntEnum):
    """Process exit codes.

    ``SUCCESS`` means every check that ran passed. ``PROBLEMS_FOUND`` means the
    command completed and reported actionable findings. ``ERROR`` means the
    command itself could not complete.
    """

    SUCCESS = 0
    PROBLEMS_FOUND = 1
    ERROR = 2


_STATUS_STYLE: Final[dict[CheckStatus, str]] = {
    CheckStatus.OK: "green",
    CheckStatus.OPTIONAL: "cyan",
    CheckStatus.NOT_CHECKED: "dim",
    CheckStatus.UNKNOWN: "yellow",
    CheckStatus.MISSING: "yellow",
    CheckStatus.INCOMPATIBLE: "red",
    CheckStatus.IMPORT_ERROR: "red",
}


def _console() -> Console:
    """Build a console that never interprets markup from data."""
    return Console(markup=False, highlight=False, emoji=False)


def _error_console() -> Console:
    """Build a stderr console for diagnostics."""
    return Console(markup=False, highlight=False, emoji=False, stderr=True)


def _supports_unicode_box() -> bool:
    """Report whether the active stdout encoding can carry box-drawing glyphs."""
    encoding = getattr(sys.stdout, "encoding", None) or ""
    try:
        "─".encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _table(*titles: str) -> Table:
    """Create a table with an encoding-safe border style."""
    table = Table(
        box=box.SQUARE if _supports_unicode_box() else box.ASCII,
        header_style="bold",
        show_lines=False,
        pad_edge=False,
    )
    for title in titles:
        table.add_column(title, overflow="fold")
    return table


def _emit_json(payload: dict[str, Any]) -> None:
    """Write a deterministic, ASCII-safe JSON document to stdout."""
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=False))


def _status_text(status: CheckStatus) -> Text:
    """Render a status value with its colour, without markup parsing."""
    return Text(status.value, style=_STATUS_STYLE.get(status, ""))


app = typer.Typer(
    name="ai-suite",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help=(
        "Unified installer and compatibility layer for the Linux of AI ecosystem. "
        "Independent open-source project; not affiliated with the Linux Foundation."
    ),
)


@app.command()
def version() -> None:
    """Show suite, Python, and platform versions."""
    console = _console()
    env = environment_info()
    table = _table("Field", "Value")
    table.add_row("ai-infrastructure-suite", Text(__version__))
    table.add_row("python", Text(env.python_version))
    table.add_row("python implementation", Text(env.python_implementation))
    table.add_row("platform", Text(env.platform_system))
    table.add_row("architecture", Text(env.platform_machine))
    table.add_row("manifest schema", Text(load_manifest().manifest_schema_version))
    console.print(table)


@app.command()
def components(
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON instead of a table.")
    ] = False,
) -> None:
    """List the seven ecosystem components and their installed state."""
    report = inspect_installation(check_imports=False)
    if as_json:
        _emit_json(
            {
                "schema_version": report.schema_version,
                "suite_version": report.suite_version,
                "components": [item.to_dict() for item in report.components],
            }
        )
        return

    console = _console()
    table = _table(
        "Component",
        "Distribution",
        "Import",
        "CLI",
        "Layer",
        "Supported range",
        "Installed",
        "Status",
    )
    for item in report.components:
        table.add_row(
            Text(item.name),
            Text(item.distribution),
            Text(item.import_package),
            Text(item.cli or "-"),
            Text(item.layer),
            Text(item.required_specifier),
            Text(item.installed_version or "-"),
            _status_text(item.install_status),
        )
    console.print(table)

    purposes = _table("Component", "Purpose")
    for item in report.components:
        purposes.add_row(Text(item.name), Text(item.purpose))
    console.print(purposes)
    console.print(
        Text(
            "Status here reflects installation and version range only. "
            "Run 'ai-suite doctor' for import, CLI, and API checks.",
            style="dim",
        )
    )


@app.command()
def doctor(
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON instead of a table.")
    ] = False,
    no_imports: Annotated[
        bool,
        typer.Option(
            "--no-imports",
            help=(
                "Skip importing component packages. Use in restricted environments "
                "where running third-party import-time code is not acceptable."
            ),
        ),
    ] = False,
) -> None:
    """Diagnose the installed ecosystem: versions, imports, console scripts, API."""
    report = inspect_installation(check_imports=not no_imports)

    if as_json:
        _emit_json(report.to_dict())
        raise typer.Exit(ExitCode.SUCCESS if report.ok else ExitCode.PROBLEMS_FOUND)

    console = _console()
    env = report.environment
    header = _table("Field", "Value")
    header.add_row("suite version", Text(report.suite_version))
    header.add_row("python", Text(env.python_version))
    header.add_row("python supported", Text(str(report.python_supported)))
    header.add_row("platform", Text(f"{env.platform_system} {env.platform_machine}"))
    header.add_row("imports checked", Text(str(report.imports_checked)))
    header.add_row("overall", _status_text(report.status))
    console.print(header)

    table = _table("Component", "Installed", "Install", "Import", "API", "CLI", "Status")
    for item in report.components:
        table.add_row(
            Text(item.name),
            Text(item.installed_version or "-"),
            _status_text(item.install_status),
            _status_text(item.import_status),
            _status_text(item.api_status),
            _status_text(item.cli_status),
            _status_text(item.status),
        )
    console.print(table)

    findings = _table("Component", "Code", "Finding", "Suggested action")
    rows = 0
    for diagnostic in report.diagnostics:
        findings.add_row(
            Text("-"),
            Text(diagnostic.code.value),
            Text(diagnostic.message),
            Text(diagnostic.action),
        )
        rows += 1
    for item in report.components:
        for diagnostic in item.diagnostics:
            findings.add_row(
                Text(item.name),
                Text(diagnostic.code.value),
                Text(diagnostic.message),
                Text(diagnostic.action),
            )
            rows += 1
    if rows:
        console.print(findings)
    else:
        console.print(Text("No findings.", style="green"))

    summary = ", ".join(f"{key}={value}" for key, value in report.summary().items() if value)
    console.print(Text(f"Summary: {summary}", style="dim"))
    raise typer.Exit(ExitCode.SUCCESS if report.ok else ExitCode.PROBLEMS_FOUND)


@app.command()
def compatibility(
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON instead of a table.")
    ] = False,
    no_imports: Annotated[
        bool,
        typer.Option("--no-imports", help="Skip importing component packages."),
    ] = False,
) -> None:
    """Report layered compatibility, offline and deterministically."""
    report = compatibility_report(check_imports=not no_imports)

    if as_json:
        _emit_json(report.to_dict())
        raise typer.Exit(ExitCode.SUCCESS if report.ok else ExitCode.PROBLEMS_FOUND)

    console = _console()
    table = _table("Layer", "Status", "Passed", "Detail")
    for layer in report.layers:
        counted = f"{layer.passed}/{layer.total}" if layer.total else "-"
        table.add_row(
            Text(layer.layer.value),
            _status_text(layer.status),
            Text(counted),
            Text(layer.detail),
        )
    console.print(table)

    checks = _table("Offline contract check", "Result", "Detail")
    for check in report.contract_checks:
        checks.add_row(
            Text(check.name),
            Text("pass" if check.passed else "fail", style="green" if check.passed else "red"),
            Text(check.detail),
        )
    console.print(checks)
    console.print(
        Text(
            "Live runtime integration across all seven components is not tested by this "
            "command and is not claimed.",
            style="dim",
        )
    )
    raise typer.Exit(ExitCode.SUCCESS if report.ok else ExitCode.PROBLEMS_FOUND)


@app.command()
def info() -> None:
    """Show what the suite is, its extras, and where to go next."""
    console = _console()
    data = load_manifest()

    console.print(Text("AI Infrastructure Suite", style="bold"))
    console.print(
        Text(
            "Unified installer and compatibility layer for the Linux of AI ecosystem: "
            "portable, governed, measurable, vendor-neutral AI infrastructure."
        )
    )
    console.print(Text(data.affiliation_notice, style="dim"))
    console.print()

    extras = _table("Install", "Adds", "Purpose")
    extras.add_row(
        Text(f"pip install {SUITE_DISTRIBUTION}"),
        Text(", ".join(item.distribution for item in data.components if item.installed_by_default)),
        Text("Lightweight core: orchestration, governance, audit, ontology, metering."),
    )
    for group in data.extras:
        extras.add_row(
            Text(f'pip install "{SUITE_DISTRIBUTION}[{group.name}]"'),
            Text(
                ", ".join(
                    data.component(cid).distribution
                    for cid in group.components
                    if cid in {c.id for c in data.components}
                )
            ),
            Text(group.purpose),
        )
    console.print(extras)

    console.print(Text("Next steps", style="bold"))
    console.print(Text("  ai-suite components      list every component and its state"))
    console.print(Text("  ai-suite doctor          diagnose the current installation"))
    console.print(Text("  ai-suite compatibility   layered compatibility report"))
    console.print(Text("  ai-suite manifest        emit the machine-readable manifest"))
    console.print()
    console.print(Text(f"Python supported: {data.python_requires}", style="dim"))
    console.print(Text(f"Running on Python {python_version_string()}", style="dim"))


@app.command()
def manifest(
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Write the manifest to this path instead of stdout.",
            dir_okay=False,
            writable=True,
        ),
    ] = None,
) -> None:
    """Emit the machine-readable ecosystem manifest."""
    payload = load_manifest().to_dict()
    document = json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=False)
    if output is None:
        typer.echo(document)
        return
    # The only path this package ever writes to is one the caller named explicitly.
    output.write_text(document + "\n", encoding="utf-8")
    _console().print(Text(f"Wrote manifest for {len(payload['components'])} components."))


@app.command()
def extras() -> None:
    """List the optional dependency groups and what each installs."""
    console = _console()
    table = _table("Extra", "Components", "Purpose")
    for group in available_extras():
        table.add_row(
            Text(group.name),
            Text(", ".join(group.components)),
            Text(group.purpose),
        )
    console.print(table)
    default = [item.id for item in ecosystem_components() if item.installed_by_default]
    console.print(Text(f"Installed by default: {', '.join(default)}", style="dim"))


def main() -> None:
    """Console-script entry point with bounded error reporting."""
    try:
        app()
    except AISuiteError as exc:
        _error_console().print(Text(f"ai-suite: {exc}", style="red"))
        raise SystemExit(ExitCode.ERROR) from exc


__all__ = ["ExitCode", "app", "main"]
