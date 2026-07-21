#!/usr/bin/env python3
from __future__ import annotations

import argparse
import compileall
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description="Verify the EarCrate release package")
parser.add_argument(
    "--skip-gates",
    action="store_true",
    help="skip the gate suite only when it already passed in this same CI job",
)
args = parser.parse_args()

checks = {
    "package_compiles": bool(compileall.compile_dir(str(ROOT / "earcrate"), quiet=1)),
    "one_changelog": (ROOT / "CHANGELOG.md").exists() and not list(ROOT.glob("PATCH_NOTES_*.txt")),
    "static_ui": (ROOT / "earcrate/ui/static/index.html").exists(),
}
details: dict[str, object] = {}

build = subprocess.run(
    [sys.executable, str(ROOT / "build/make_singlefile.py")],
    capture_output=True,
    text=True,
)
checks["singlefile_builds"] = build.returncode == 0
if not checks["singlefile_builds"]:
    details["singlefile_build"] = (build.stdout + build.stderr)[-2000:]

if checks["singlefile_builds"]:
    artifact = ROOT / "dist" / "earcrate.py"
    selftest = subprocess.run(
        [sys.executable, str(artifact), "--self-test"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    checks["singlefile_selftest"] = "SELF_TEST_OK" in (selftest.stdout + selftest.stderr)
    if not checks["singlefile_selftest"]:
        details["singlefile_selftest"] = (selftest.stdout + selftest.stderr)[-2000:]

    try:
        import mido
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory(prefix="earcrate-package-midi-") as raw_tmp:
            tmp = Path(raw_tmp)
            fixture = tmp / "package-smoke.mid"
            midi = mido.MidiFile(type=1, ticks_per_beat=192)
            track = mido.MidiTrack()
            track.append(mido.MetaMessage("track_name", name="Package Piano", time=0))
            track.append(mido.Message("program_change", channel=0, program=0, time=0))
            track.append(mido.Message("note_on", channel=0, note=60, velocity=100, time=0))
            track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=96))
            track.append(mido.MetaMessage("end_of_track", time=0))
            midi.tracks.append(track)
            midi.save(fixture)
            smoke = subprocess.run(
                [sys.executable, str(artifact), "midi", "inspect", str(fixture)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            payload = json.loads(smoke.stdout) if smoke.returncode == 0 else {}
            stats = payload.get("statistics") or {}
            checks["singlefile_midi_smoke"] = (
                smoke.returncode == 0
                and payload.get("ok") is True
                and stats.get("declared_track_count") == 1
                and stats.get("note_on_count") == 1
                and stats.get("note_off_count") == 1
            )
            if not checks["singlefile_midi_smoke"]:
                details["singlefile_midi_smoke"] = {
                    "returncode": smoke.returncode,
                    "stdout": smoke.stdout[-2000:],
                    "stderr": smoke.stderr[-2000:],
                }

            source = tmp / "package-sample.wav"
            sample_rate = 8_000
            time = np.arange(int(0.5 * sample_rate), dtype=np.float64) / sample_rate
            sf.write(source, (0.25 * np.sin(2.0 * np.pi * 261.625565 * time)).astype(np.float32), sample_rate, subtype="FLOAT")
            draft = tmp / "package-rack.draft.json"
            sealed = tmp / "package-rack.json"
            binding = tmp / "package-binding.json"
            rendered = tmp / "package-rack.wav"
            draft.write_text(
                json.dumps(
                    {
                        "rack_id": "package-piano",
                        "name": "Package Piano",
                        "mode": "pitched",
                        "metadata": {"tags": ["piano"]},
                        "created_by": {"actor": "package_verifier", "reason": "single-file rack smoke"},
                        "zones": [
                            {
                                "zone_id": "middle-c",
                                "sample_path": str(source),
                                "key_range": [60, 60],
                                "velocity_range": [1, 127],
                                "root_key": 60,
                                "trigger_mode": "gate",
                                "loop": {"enabled": False, "start_frame": 0, "end_frame": 0, "crossfade_frames": 0},
                                "attack_ms": 2.0,
                                "release_ms": 12.0,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            seal = subprocess.run(
                [sys.executable, str(artifact), "midi", "rack-seal", str(draft), str(sealed)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            bind = subprocess.run(
                [sys.executable, str(artifact), "midi", "bind", str(fixture), str(binding), str(sealed)],
                capture_output=True,
                text=True,
                timeout=60,
            ) if seal.returncode == 0 else None
            rack_render = subprocess.run(
                [
                    sys.executable,
                    str(artifact),
                    "midi",
                    "render-rack",
                    str(fixture),
                    str(binding),
                    str(rendered),
                    "--rack",
                    str(sealed),
                    "--sample-rate",
                    str(sample_rate),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            ) if bind is not None and bind.returncode == 0 else None
            rack_payload = json.loads(rack_render.stdout) if rack_render is not None and rack_render.returncode == 0 else {}
            checks["singlefile_rack_smoke"] = (
                seal.returncode == 0
                and bind is not None
                and bind.returncode == 0
                and rack_render is not None
                and rack_render.returncode == 0
                and rack_payload.get("ok") is True
                and rack_payload.get("complete_execution") is True
                and rack_payload.get("selected_event_count") == 1
                and rack_payload.get("executed_event_count") == 1
                and Path(rack_payload.get("output_path") or "").is_file()
            )
            if not checks["singlefile_rack_smoke"]:
                details["singlefile_rack_smoke"] = {
                    "seal": None if seal is None else {"returncode": seal.returncode, "stdout": seal.stdout[-1200:], "stderr": seal.stderr[-1200:]},
                    "bind": None if bind is None else {"returncode": bind.returncode, "stdout": bind.stdout[-1200:], "stderr": bind.stderr[-1200:]},
                    "render": None if rack_render is None else {"returncode": rack_render.returncode, "stdout": rack_render.stdout[-1200:], "stderr": rack_render.stderr[-1200:]},
                }
    except Exception as exc:
        checks["singlefile_midi_smoke"] = False
        checks["singlefile_rack_smoke"] = False
        details["singlefile_package_smoke"] = f"{type(exc).__name__}: {exc}"

skipped = []
if args.skip_gates:
    skipped.append("gates (already passed in this CI job)")
else:
    gates = subprocess.run(
        [sys.executable, str(ROOT / "tests/run_gates.py")],
        capture_output=True,
        text=True,
    )
    checks["gates"] = gates.returncode == 0
    if not checks["gates"]:
        details["gates"] = (gates.stdout + gates.stderr)[-4000:]

result = {
    "ok": all(checks.values()),
    "checks": checks,
    "skipped": skipped,
    "package": ROOT.name,
    "details": details,
}
print(json.dumps(result, indent=2, sort_keys=True))
sys.exit(0 if result["ok"] else 1)
