"""Clear failures when an analysis dependency is absent or too old.

Some checks need an API from another ecosystem project. When that API is not
importable the command cannot complete, and saying so plainly is more useful
than a traceback or a generic "invalid input" message: the operator's evidence
may be perfectly valid.

A message raised from here names a distribution and a version and nothing
else. It carries no filesystem path, manifest field, or evidence content, so
the CLI prints it verbatim rather than replacing it with a redacted summary.
"""

from __future__ import annotations

# The JSON-LD transfer and round-trip API used by ontology verification is
# published by this release. Earlier installs import but lack the names.
OPENONTOLOGYLITE_JSON_LD_VERSION = "0.2.0b3"


class MissingDependencyError(Exception):
    """A required analysis dependency is missing or lacks a required API."""


def missing_ontology_api() -> MissingDependencyError:
    """Build the error for an unavailable OpenOntologyLite JSON-LD API.

    Covers both an absent distribution and an installed one that predates the
    API, which raise ``ModuleNotFoundError`` and ``ImportError`` respectively.
    The original exception is not quoted, because its text names the local
    install path.
    """
    version = OPENONTOLOGYLITE_JSON_LD_VERSION
    return MissingDependencyError(
        "Ontology verification needs the OpenOntologyLite JSON-LD transfer API, "
        "which the installed openontologylite does not provide. Install "
        f"openontologylite>={version} and run the command again. No evidence was "
        "rejected: this check could not run."
    )
