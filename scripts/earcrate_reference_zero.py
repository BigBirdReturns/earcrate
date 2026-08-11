#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.reference_zero import reference_zero_cli_main


if __name__ == "__main__":
    raise SystemExit(reference_zero_cli_main())
