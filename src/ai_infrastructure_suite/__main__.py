"""Entry point for ``python -m ai_infrastructure_suite``."""

from __future__ import annotations

from ai_infrastructure_suite.cli import main

if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    main()
