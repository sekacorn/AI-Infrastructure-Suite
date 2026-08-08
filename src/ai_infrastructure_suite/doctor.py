"""Installation diagnostics for the Linux of AI ecosystem.

``inspect_installation`` answers a narrow question: for each component in the
manifest, is it installed, is the installed version inside the supported range,
does its public package import, and does it expose the API and console script we
expect? Every check is local, bounded, and offline.

What this module never does: start a container, reach a database, contact a model
provider, read credentials, execute a shell, emit telemetry, or write files.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from ai_infrastructure_suite._inspection import (
    EnvironmentInfo,
    console_scripts,
    environment_info,
    installed_version,
    probe_import,
    python_version_string,
    redact_text,
)
from ai_infrastructure_suite._version import __version__
from ai_infrastructure_suite.components import Component, EcosystemManifest, load_manifest

REPORT_SCHEMA_VERSION: Final = "1.0"


class CheckStatus(StrEnum):
    """Outcome classification for a single check.

    ``StrEnum`` so a status serialises directly to JSON as its value.
    """

    OK = "ok"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    IMPORT_ERROR = "import_error"
    OPTIONAL = "optional"
    NOT_CHECKED = "not_checked"
    UNKNOWN = "unknown"


# Severity order used to fold several check results into one component status.
_SEVERITY: Final[dict[CheckStatus, int]] = {
    CheckStatus.OK: 0,
    CheckStatus.OPTIONAL: 1,
    CheckStatus.NOT_CHECKED: 2,
    CheckStatus.UNKNOWN: 3,
    CheckStatus.MISSING: 4,
    CheckStatus.INCOMPATIBLE: 5,
    CheckStatus.IMPORT_ERROR: 6,
}


class DiagnosticCode(StrEnum):
    """Stable machine-readable diagnostic identifiers."""

    COMPONENT_MISSING = "AIS1001"
    VERSION_INCOMPATIBLE = "AIS1002"
    IMPORT_FAILED = "AIS1003"
    API_SYMBOLS_MISSING = "AIS1004"
    CLI_ENTRY_POINT_MISSING = "AIS1005"
    OPTIONAL_COMPONENT_ABSENT = "AIS1006"
    HEAVY_INFRASTRUCTURE_REQUIRED = "AIS1007"
    VERSION_METADATA_MISMATCH = "AIS1008"
    PYTHON_UNSUPPORTED = "AIS2001"
    IMPORT_CHECKS_SKIPPED = "AIS2002"
    MANIFEST_RANGE_UNPARSEABLE = "AIS2003"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One finding with a stable code and a suggested next action."""

    code: DiagnosticCode
    message: str
    action: str

    def to_dict(self) -> dict[str, str]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "code": self.code.value,
            "message": self.message,
            "action": self.action,
        }


@dataclass(frozen=True, slots=True)
class ComponentStatus:
    """Diagnosis of one ecosystem component in the current environment."""

    component_id: str
    name: str
    distribution: str
    import_package: str
    cli: str | None
    layer: str
    installed_by_default: bool
    extras: tuple[str, ...]
    purpose: str
    required_specifier: str
    minimum_version: str
    latest_published_version: str
    installed_version: str | None
    status: CheckStatus
    install_status: CheckStatus
    import_status: CheckStatus
    api_status: CheckStatus
    cli_status: CheckStatus
    diagnostics: tuple[Diagnostic, ...]

    @property
    def is_healthy(self) -> bool:
        """True when this component needs no action."""
        return self.status in (CheckStatus.OK, CheckStatus.OPTIONAL)

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "component_id": self.component_id,
            "name": self.name,
            "distribution": self.distribution,
            "import_package": self.import_package,
            "cli": self.cli,
            "layer": self.layer,
            "installed_by_default": self.installed_by_default,
            "extras": list(self.extras),
            "purpose": self.purpose,
            "required_specifier": self.required_specifier,
            "minimum_version": self.minimum_version,
            "latest_published_version": self.latest_published_version,
            "installed_version": self.installed_version,
            "status": self.status.value,
            "install_status": self.install_status.value,
            "import_status": self.import_status.value,
            "api_status": self.api_status.value,
            "cli_status": self.cli_status.value,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class InstallationReport:
    """Aggregate diagnosis across every manifest component."""

    schema_version: str
    suite_version: str
    manifest_schema_version: str
    environment: EnvironmentInfo
    python_requires: str
    python_supported: bool
    imports_checked: bool
    components: tuple[ComponentStatus, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def status(self) -> CheckStatus:
        """Overall status: ``ok`` when nothing needs attention, else the worst finding.

        Components that are healthy — including optional ones that are simply
        absent — do not drag the overall status away from ``ok``.
        """
        if not self.python_supported:
            return CheckStatus.INCOMPATIBLE
        worst = CheckStatus.OK
        for component in self.components:
            if component.is_healthy:
                continue
            if _SEVERITY[component.status] > _SEVERITY[worst]:
                worst = component.status
        return worst

    @property
    def ok(self) -> bool:
        """True when nothing requires the operator's attention."""
        return self.python_supported and all(item.is_healthy for item in self.components)

    def summary(self) -> dict[str, int]:
        """Return counts per status value, including zero-count statuses."""
        counts = Counter(item.status.value for item in self.components)
        return {status.value: counts.get(status.value, 0) for status in CheckStatus}

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible mapping."""
        return {
            "schema_version": self.schema_version,
            "suite_version": self.suite_version,
            "manifest_schema_version": self.manifest_schema_version,
            "environment": self.environment.to_dict(),
            "python_requires": self.python_requires,
            "python_supported": self.python_supported,
            "imports_checked": self.imports_checked,
            "status": self.status.value,
            "ok": self.ok,
            "summary": self.summary(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "components": [item.to_dict() for item in self.components],
        }


def _fold_component_status(
    install_status: CheckStatus,
    import_status: CheckStatus,
    api_status: CheckStatus,
) -> CheckStatus:
    """Reduce one component's individual checks to a single status.

    Two rules keep the result honest:

    * When the distribution is absent, that fact *is* the answer. Downstream
      checks could not run, and reporting their ``not_checked`` would bury a
      plain "missing" or "optional" under a vaguer status.
    * ``not_checked`` never outvotes a real result, so ``--no-imports`` reports
      what it did verify instead of degrading every component.
    """
    if install_status in (CheckStatus.MISSING, CheckStatus.OPTIONAL):
        return install_status
    determined = [
        status
        for status in (install_status, import_status, api_status)
        if status is not CheckStatus.NOT_CHECKED
    ]
    if not determined:
        return CheckStatus.NOT_CHECKED
    return max(determined, key=lambda value: _SEVERITY[value])


def _version_in_range(version_text: str, specifier: SpecifierSet) -> bool | None:
    """Check membership, returning ``None`` when the version cannot be parsed."""
    try:
        parsed = Version(version_text)
    except InvalidVersion:
        return None
    # Ecosystem components publish alphas, so pre-releases must count as matches.
    return specifier.contains(parsed, prereleases=True)


def _install_pointer(component: Component) -> str:
    """Return the pip command that installs this component through the suite."""
    if component.installed_by_default:
        return "pip install ai-infrastructure-suite"
    extra = next((name for name in component.extras if name != "full"), "full")
    return f'pip install "ai-infrastructure-suite[{extra}]"'


def _diagnose_component(component: Component, *, check_imports: bool) -> ComponentStatus:
    """Run every check for one component and fold the results into a status."""
    diagnostics: list[Diagnostic] = []
    found = installed_version(component.distribution)

    install_status = CheckStatus.OK
    import_status = CheckStatus.NOT_CHECKED
    api_status = CheckStatus.NOT_CHECKED
    cli_status = CheckStatus.NOT_CHECKED

    try:
        specifier = component.specifier_set
    except Exception:  # surfaced as a diagnostic, never raised
        diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.MANIFEST_RANGE_UNPARSEABLE,
                message=(
                    f"{component.distribution}: manifest version range "
                    f"{component.version_specifier!r} could not be parsed."
                ),
                action="Report this as a bug in ai-infrastructure-suite.",
            )
        )
        return ComponentStatus(
            component_id=component.id,
            name=component.name,
            distribution=component.distribution,
            import_package=component.import_package,
            cli=component.cli,
            layer=component.layer,
            installed_by_default=component.installed_by_default,
            extras=component.extras,
            purpose=component.purpose,
            required_specifier=component.version_specifier,
            minimum_version=component.minimum_version,
            latest_published_version=component.latest_published_version,
            installed_version=found,
            status=CheckStatus.UNKNOWN,
            install_status=CheckStatus.UNKNOWN,
            import_status=CheckStatus.NOT_CHECKED,
            api_status=CheckStatus.NOT_CHECKED,
            cli_status=CheckStatus.NOT_CHECKED,
            diagnostics=tuple(diagnostics),
        )

    if found is None:
        if component.installed_by_default:
            install_status = CheckStatus.MISSING
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.COMPONENT_MISSING,
                    message=(
                        f"{component.distribution} is not installed but is part of the "
                        "default suite installation."
                    ),
                    action=_install_pointer(component),
                )
            )
        else:
            install_status = CheckStatus.OPTIONAL
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.OPTIONAL_COMPONENT_ABSENT,
                    message=(
                        f"{component.distribution} is not installed. It is optional and "
                        f"ships in the {component.layer} layer."
                    ),
                    action=_install_pointer(component),
                )
            )
    else:
        in_range = _version_in_range(found, specifier)
        if in_range is None:
            install_status = CheckStatus.UNKNOWN
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.VERSION_INCOMPATIBLE,
                    message=(
                        f"{component.distribution} reports version {redact_text(found)}, "
                        "which is not a valid PEP 440 version."
                    ),
                    action=f"Reinstall {component.distribution} from a trusted index.",
                )
            )
        elif not in_range:
            install_status = CheckStatus.INCOMPATIBLE
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.VERSION_INCOMPATIBLE,
                    message=(
                        f"{component.distribution} {found} is outside the supported range "
                        f"{component.version_specifier}."
                    ),
                    action=(f'pip install "{component.distribution}{component.version_specifier}"'),
                )
            )

        if component.cli is not None:
            scripts = console_scripts(component.distribution)
            if component.cli in scripts:
                cli_status = CheckStatus.OK
            else:
                cli_status = CheckStatus.INCOMPATIBLE
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.CLI_ENTRY_POINT_MISSING,
                        message=(
                            f"{component.distribution} does not register the expected "
                            f"{component.cli!r} console script."
                        ),
                        action=(
                            f"Reinstall {component.distribution}; the console script may "
                            "have been removed by a partial installation."
                        ),
                    )
                )

        if not check_imports:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.IMPORT_CHECKS_SKIPPED,
                    message=(
                        f"Import of {component.import_package!r} was skipped because "
                        "import checks are disabled."
                    ),
                    action="Re-run without --no-imports to verify the public import.",
                )
            )
        else:
            probe = probe_import(component.import_package, component.api_symbols)
            if not probe.imported:
                import_status = CheckStatus.IMPORT_ERROR
                api_status = CheckStatus.NOT_CHECKED
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.IMPORT_FAILED,
                        message=(
                            f"import {component.import_package} failed: "
                            f"{probe.error or 'unknown error'}"
                        ),
                        action=(
                            f"Check that {component.distribution} and its dependencies are "
                            "installed completely in this environment."
                        ),
                    )
                )
            else:
                import_status = CheckStatus.OK
                if probe.missing_symbols:
                    api_status = CheckStatus.INCOMPATIBLE
                    missing = ", ".join(probe.missing_symbols)
                    diagnostics.append(
                        Diagnostic(
                            code=DiagnosticCode.API_SYMBOLS_MISSING,
                            message=(
                                f"{component.import_package} does not expose expected "
                                f"public names: {missing}."
                            ),
                            action=(
                                f'pip install "{component.distribution}'
                                f'{component.version_specifier}"'
                            ),
                        )
                    )
                elif component.api_symbols:
                    api_status = CheckStatus.OK

                if (
                    probe.module_version is not None
                    and found is not None
                    and probe.module_version != found
                ):
                    diagnostics.append(
                        Diagnostic(
                            code=DiagnosticCode.VERSION_METADATA_MISMATCH,
                            message=(
                                f"{component.distribution} metadata reports {found} but "
                                f"{component.import_package}.__version__ reports "
                                f"{redact_text(probe.module_version)}."
                            ),
                            action=(
                                "Reinstall the distribution; the environment may hold a "
                                "stale copy alongside the installed one."
                            ),
                        )
                    )

        if component.requires_heavy_infrastructure:
            services = ", ".join(component.heavy_infrastructure)
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.HEAVY_INFRASTRUCTURE_REQUIRED,
                    message=(
                        f"{component.distribution} expects external services at runtime: "
                        f"{services}."
                    ),
                    action=(
                        "These services are never installed, started, or contacted by "
                        "this suite. Provision them separately when you need them."
                    ),
                )
            )

    status = _fold_component_status(install_status, import_status, api_status)
    return ComponentStatus(
        component_id=component.id,
        name=component.name,
        distribution=component.distribution,
        import_package=component.import_package,
        cli=component.cli,
        layer=component.layer,
        installed_by_default=component.installed_by_default,
        extras=component.extras,
        purpose=component.purpose,
        required_specifier=component.version_specifier,
        minimum_version=component.minimum_version,
        latest_published_version=component.latest_published_version,
        installed_version=found,
        status=status,
        install_status=install_status,
        import_status=import_status,
        api_status=api_status,
        cli_status=cli_status,
        diagnostics=tuple(diagnostics),
    )


def python_supported(manifest: EcosystemManifest | None = None) -> bool:
    """Check the running interpreter against the manifest's supported range."""
    resolved = manifest or load_manifest()
    try:
        specifier = SpecifierSet(resolved.python_requires)
    except InvalidSpecifier:  # pragma: no cover - guarded by manifest tests
        return False
    try:
        return specifier.contains(Version(python_version_string()), prereleases=True)
    except InvalidVersion:  # pragma: no cover - CPython always parses
        return False


def inspect_installation(
    *,
    check_imports: bool = True,
    manifest: EcosystemManifest | None = None,
) -> InstallationReport:
    """Diagnose every ecosystem component in the current environment.

    Args:
        check_imports: When ``True`` each installed component's public package is
            imported, which runs that package's import-time code. Set to ``False``
            in restricted environments to limit the check to installed metadata.
        manifest: Manifest override, mainly for testing. Defaults to the packaged
            manifest.

    Returns:
        An :class:`InstallationReport` whose components follow manifest order.
    """
    resolved = manifest or load_manifest()
    supported = python_supported(resolved)

    report_diagnostics: list[Diagnostic] = []
    if not supported:
        report_diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.PYTHON_UNSUPPORTED,
                message=(
                    f"Python {python_version_string()} is outside the supported range "
                    f"{resolved.python_requires}."
                ),
                action=(f"Run the suite on an interpreter matching {resolved.python_requires}."),
            )
        )
    if not check_imports:
        report_diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.IMPORT_CHECKS_SKIPPED,
                message="Import checks were disabled, so results cover installed metadata only.",
                action="Re-run without --no-imports for full verification.",
            )
        )

    components = tuple(
        _diagnose_component(component, check_imports=check_imports)
        for component in resolved.components
    )

    return InstallationReport(
        schema_version=REPORT_SCHEMA_VERSION,
        suite_version=__version__,
        manifest_schema_version=resolved.manifest_schema_version,
        environment=environment_info(),
        python_requires=resolved.python_requires,
        python_supported=supported,
        imports_checked=check_imports,
        components=components,
        diagnostics=tuple(report_diagnostics),
    )


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "CheckStatus",
    "ComponentStatus",
    "Diagnostic",
    "DiagnosticCode",
    "InstallationReport",
    "inspect_installation",
    "python_supported",
]
