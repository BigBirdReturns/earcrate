#!/usr/bin/env python3
"""Entry point for the A1-07 full-form descent."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_07_full_form.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
