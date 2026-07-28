from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

continuation = ROOT / "earcrate" / "specimen" / "continuation.py"
text = continuation.read_text(encoding="utf-8")
old = """from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.midi.codec import midi_write
"""
new = """import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import soundfile as sf

from earcrate.midi.codec import midi_write
"""
if text.count(old) != 1:
    raise SystemExit("continuation import patch point is missing or ambiguous")
text = text.replace(old, new, 1)

marker = "\ndef children_compose_adjacent_move(\n"
helper = '''
def _canonical_pcm_sha256(path: str | Path) -> str:
    """Hash decoded stereo float PCM, independent of WAV container metadata."""
    audio, sample_rate = sf.read(
        str(Path(path).expanduser().resolve()),
        always_2d=True,
        dtype="float32",
    )
    payload = int(sample_rate).to_bytes(4, "little")
    payload += int(audio.shape[1]).to_bytes(2, "little")
    payload += np.asarray(audio, dtype="<f4").tobytes(order="C")
    return hashlib.sha256(payload).hexdigest()


def children_compose_adjacent_move(
'''
if text.count(marker) != 1:
    raise SystemExit("continuation function patch point is missing or ambiguous")
text = text.replace(marker, "\n" + helper, 1)

old = '''            "neutral_wav_sha256": str(render["output_sha256"]),
            "stem_count": len(render.get("stems") or []),
'''
new = '''            "neutral_pcm_f32le_sha256": _canonical_pcm_sha256(neutral_path),
            "neutral_wav_sha256": str(render["output_sha256"]),
            "stem_count": len(render.get("stems") or []),
'''
if text.count(old) != 1:
    raise SystemExit("continuation MIDI receipt patch point is missing or ambiguous")
text = text.replace(old, new, 1)

old = '''    receipt["receipt_sha256"] = specimen_sha256_json(receipt)
'''
new = '''    receipt["receipt_hash_policy"] = {
        "authority": "decoded stereo float32 PCM",
        "excluded_delivery_fields": ["midi.neutral_wav_sha256"],
        "reason": "WAV container metadata is not musical identity",
    }
    receipt_payload = deepcopy(receipt)
    receipt_payload["midi"].pop("neutral_wav_sha256", None)
    receipt["receipt_sha256"] = specimen_sha256_json(receipt_payload)
'''
if text.count(old) != 1:
    raise SystemExit("continuation receipt hash patch point is missing or ambiguous")
continuation.write_text(text.replace(old, new, 1), encoding="utf-8")

tests = ROOT / "tests" / "test_children_continuation.py"
text = tests.read_text(encoding="utf-8")
if '"neutral_wav_sha256"' not in text:
    raise SystemExit("continuation determinism test patch point is missing")
tests.write_text(text.replace('"neutral_wav_sha256"', '"neutral_pcm_f32le_sha256"'), encoding="utf-8")

schema = ROOT / "schemas" / "earcrate_children_adjacent_move_receipt_v1.schema.json"
text = schema.read_text(encoding="utf-8")
if '"neutral_wav_sha256"' not in text:
    raise SystemExit("continuation schema PCM patch point is missing")
schema.write_text(text.replace('"neutral_wav_sha256"', '"neutral_pcm_f32le_sha256"'), encoding="utf-8")

Path(__file__).unlink()
workflow = ROOT / ".github" / "workflows" / "apply-continuation-determinism.yml"
workflow.unlink(missing_ok=True)
