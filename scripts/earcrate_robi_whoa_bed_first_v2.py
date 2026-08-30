#!/usr/bin/env python3
"""Execute the governed Robi WHOA bed-first v2 production campaign.

Implementation is split under :mod:`robi_whoa_v2` so custody, bed qualification, candidate
mechanisms, source-locked mixing, and delivery remain independently reviewable.
"""
from __future__ import annotations

from pathlib import Path
import sys

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from robi_whoa_v2.common import *  # noqa: E402,F401,F403
from robi_whoa_v2.gates import *  # noqa: E402,F401,F403
from robi_whoa_v2.candidates import *  # noqa: E402,F401,F403
from robi_whoa_v2.candidates import _remove_external_foreground  # noqa: E402,F401
from robi_whoa_v2.mix import *  # noqa: E402,F401,F403
from robi_whoa_v2.delivery import *  # noqa: E402,F401,F403
from robi_whoa_v2.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
