#!/usr/bin/env python3
"""
EarCrate runtime (descends from the Jukebreaker GT line)
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
    print("EarCrate could not import required audio packages.", file=sys.stderr)
    print("Run Install-Dependencies.cmd or pip install -r requirements.txt", file=sys.stderr)
    print(f"Import error: {exc}", file=sys.stderr)
    raise

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wv"}
ENGINE_VERSION = "earcrate_v085"
ENGINE_DISPLAY_VERSION = "v0.8.5"   # bump this EVERY shipped batch so the header visibly changes; keep in step with CHANGELOG
BUILD_STAMP = "__BUILD_STAMP__"     # sentinel; the single-file builder replaces it with the package content hash
ANALYZER_VERSION = "gt-v0.6.1-earcrate-feasibility"
APP_NAME = "JukebreakerGT"
DEFAULT_SAMPLE_RATE = 44100
MAX_ANALYSIS_SECONDS = 12 * 60          # hard ceiling, never analyze more than this
DEFAULT_ANALYSIS_SECONDS = 180          # characterizing BPM/key/sections needs ~3 min, not 12
VALID_OPS = {"render_mashup", "create_playlist", "ingest_copy", "organize_copy"}
ROLE_ORDER = ["drum_anchor", "bass", "harmony", "vocal", "texture", "fx", "full"]
EAR_ROLE_ORDER = ["VOX_HOOK", "VOX_VERSE", "VOX_SHOUT", "DRUM_BREAK", "BASS_RIFF", "BED_CHORD", "RIFF_ID", "TEXTURE", "PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL"]
EAR_TO_RENDER_ROLE = {"VOX_HOOK": "vocal", "VOX_VERSE": "vocal", "VOX_SHOUT": "vocal", "DRUM_BREAK": "drum_anchor", "BASS_RIFF": "bass", "BED_CHORD": "harmony", "RIFF_ID": "harmony", "TEXTURE": "texture", "PICKUP_FILL": "texture", "DROP_HIT": "fx", "TRANSITION_TAIL": "texture"}
# ONE source of truth for style math: profiles/girl_talk_v1.json (versioned,
# schema'd, hashed — see PERSONAS/GIRL_TALK_V1.md for the derivations and
# profiles/tastespec.schema.json for the shape). This flat dict is a projection
# of that JSON, never a place to define numbers. Adding a persona = adding a
# JSON file, not editing code. Loading fails LOUDLY on a missing/corrupt
# profile — a shadow literal here would be a second constitution.
from earcrate.tastespec.profiles import load_tastespec, flat_profile, profile_summary, tastespec_hash, available_profiles
TASTE_PROFILES = {pid: flat_profile(load_tastespec(pid)) for pid in available_profiles()}


