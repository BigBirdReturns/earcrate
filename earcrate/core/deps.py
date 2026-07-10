#!/usr/bin/env python3
"""
Jukebreaker GT v0.6.3 Workspace Scout
Local-first TasteSpec runtime-ledger compiler: scans songs into ear-crated phrase atoms, builds deterministic compatibility graphs, records wall-clock stage timings, then renders only taste-contract-satisfying mashups.

This is a single-file build intended for zipapp packaging. It uses Python for the
actual audio engine and shells to ffmpeg/ffprobe only for media decoding/probing.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import contextlib
import datetime as _dt
import functools
import hashlib
import html
import io
import json
import math
import mimetypes
import os
import random
import re
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

try:
    import soundfile as sf
    import librosa
    import pyloudnorm as pyln
    from mutagen import File as MutagenFile
except Exception as exc:  # pragma: no cover
    print("Jukebreaker GT could not import required audio packages.", file=sys.stderr)
    print("Run Install-Dependencies.cmd or pip install -r requirements.txt", file=sys.stderr)
    print(f"Import error: {exc}", file=sys.stderr)
    raise

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wv"}
ENGINE_VERSION = "earcrate_v073"
ANALYZER_VERSION = "gt-v0.6.1-earcrate-feasibility"
APP_NAME = "JukebreakerGT"
DEFAULT_SAMPLE_RATE = 44100
MAX_ANALYSIS_SECONDS = 12 * 60          # hard ceiling, never analyze more than this
DEFAULT_ANALYSIS_SECONDS = 180          # characterizing BPM/key/sections needs ~3 min, not 12
VALID_OPS = {"render_mashup", "create_playlist", "ingest_copy", "organize_copy"}
ROLE_ORDER = ["drum_anchor", "bass", "harmony", "vocal", "texture", "fx", "full"]
EAR_ROLE_ORDER = ["VOX_HOOK", "VOX_VERSE", "VOX_SHOUT", "DRUM_BREAK", "BASS_RIFF", "BED_CHORD", "RIFF_ID", "TEXTURE", "PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL"]
EAR_TO_RENDER_ROLE = {"VOX_HOOK": "vocal", "VOX_VERSE": "vocal", "VOX_SHOUT": "vocal", "DRUM_BREAK": "drum_anchor", "BASS_RIFF": "bass", "BED_CHORD": "harmony", "RIFF_ID": "harmony", "TEXTURE": "texture", "PICKUP_FILL": "texture", "DROP_HIT": "fx", "TRANSITION_TAIL": "texture"}
TASTE_PROFILES = {
    "girl_talk_v1": {
        "name": "Girl Talk v1",
        "contract": "recognizable foreground + stable floor + fast source turnover + phrase-grid transitions",
        "source_seconds": 11.5,
        "first_foreground_s": 8.0,
        "max_source_run_s": 16.0,
        "min_feasible_sources": 11,
        "floor_coverage": 0.70,
        "foreground_coverage": 0.50,
        "max_silent_gap_s": 2.0,
        "min_edge_score": 0.54,
    }
}


