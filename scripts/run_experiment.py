#!/usr/bin/env python3
"""CLI entry point for the local experiment runner."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from coding_agent.experiment import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
