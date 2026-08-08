"""Exception hierarchy for AI Infrastructure Suite.

Every error raised by this package derives from :class:`AISuiteError`, so callers
can guard the whole surface with one ``except`` clause.
"""

from __future__ import annotations


class AISuiteError(Exception):
    """Base class for all AI Infrastructure Suite errors."""


class ManifestError(AISuiteError):
    """The packaged ecosystem manifest is missing, unreadable, or malformed."""


class ComponentNotFoundError(AISuiteError):
    """A component identifier does not exist in the ecosystem manifest."""


class UnsupportedPythonError(AISuiteError):
    """The running interpreter is outside the supported Python range."""


__all__ = [
    "AISuiteError",
    "ComponentNotFoundError",
    "ManifestError",
    "UnsupportedPythonError",
]
