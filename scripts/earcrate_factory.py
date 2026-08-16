#!/usr/bin/env python3
from __future__ import annotations

"""Thin checkout entrypoint for the EarCrate Homelab organ factory."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.estate.homelab_factory import factory_cli_main


if __name__ == "__main__":
    raise SystemExit(factory_cli_main())
