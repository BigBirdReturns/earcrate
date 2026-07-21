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

        with tempfile.TemporaryDirectory(prefix="earcrate-package-midi-") as raw_tmp:
            tmp = Path(raw_tmp)
            fixture = tmp / "package-smoke.mid"
            midi = mido.MidiFile(type=1, ticks_per_beat=192)
            track = mido.MidiTrack()
            track.append(mido.MetaMessage("track_name", name="Package smoke", time=0))
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
    except Exception as exc:
        checks["singlefile_midi_smoke"] = False
        details["singlefile_midi_smoke"] = f"{type(exc).__name__}: {exc}"

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
