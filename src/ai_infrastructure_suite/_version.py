"""Single source of truth for the package version.

Hatchling reads ``__version__`` from this file at build time (see
``[tool.hatch.version]`` in ``pyproject.toml``), and the package ``__init__``
re-exports it so ``ai_infrastructure_suite.__version__`` works at runtime
without an ``importlib.metadata`` lookup.
"""

__version__ = "0.1.0b1"
