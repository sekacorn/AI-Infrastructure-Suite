"""AI Infrastructure Suite — unified installer and compatibility layer for Linux of AI.

The suite installs and diagnoses seven independent components. It deliberately
contains none of their business logic.

Quick start::

    from ai_infrastructure_suite import (
        compatibility_report,
        ecosystem_components,
        inspect_installation,
    )

    for component in ecosystem_components():
        print(component.id, component.version_specifier)

    report = inspect_installation()
    print(report.status, report.summary())

Importing this package never imports the seven components. They are imported only
when a diagnostic explicitly asks for it, and that behaviour can be disabled.

This is an independent open-source project. It is not affiliated with, endorsed
by, or sponsored by the Linux Foundation.
"""

from __future__ import annotations

from ai_infrastructure_suite._version import __version__
from ai_infrastructure_suite.compatibility import (
    CompatibilityLayer,
    CompatibilityReport,
    ContractCheck,
    LayerResult,
    canonical_json,
    compatibility_report,
    digest_value,
    offline_contract_check,
)
from ai_infrastructure_suite.components import (
    Component,
    EcosystemManifest,
    ExtraGroup,
    available_extras,
    component_ids,
    ecosystem_components,
    load_manifest,
    manifest_text,
)
from ai_infrastructure_suite.doctor import (
    CheckStatus,
    ComponentStatus,
    Diagnostic,
    DiagnosticCode,
    InstallationReport,
    inspect_installation,
    python_supported,
)
from ai_infrastructure_suite.errors import (
    AISuiteError,
    ComponentNotFoundError,
    ManifestError,
    UnsupportedPythonError,
)

__all__ = [
    "AISuiteError",
    "CheckStatus",
    "CompatibilityLayer",
    "CompatibilityReport",
    "Component",
    "ComponentNotFoundError",
    "ComponentStatus",
    "ContractCheck",
    "Diagnostic",
    "DiagnosticCode",
    "EcosystemManifest",
    "ExtraGroup",
    "InstallationReport",
    "LayerResult",
    "ManifestError",
    "UnsupportedPythonError",
    "__version__",
    "available_extras",
    "canonical_json",
    "compatibility_report",
    "component_ids",
    "digest_value",
    "ecosystem_components",
    "inspect_installation",
    "load_manifest",
    "manifest_text",
    "offline_contract_check",
    "python_supported",
]
