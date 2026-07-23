from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from earcrate.live.crate import live_load_crate_atlas
from earcrate.live.model import LiveError
from earcrate.live.playback import live_audio_device_capability
from earcrate.live.stream import live_render_next_phrase, live_stream_capability
from earcrate.midi.codec import midi_write
from earcrate.midi.render import _atomic_wav


def _audio_cli_read_json(path: str | Path) -> Any:
    source = Path(path).expanduser().resolve()
    return json.loads(source.read_text(encoding="utf-8"))


def _audio_cli_controls(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    value = _audio_cli_read_json(path)
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise LiveError("live audio controls must be a JSON array of objects")
    return [dict(row) for row in value]


def _audio_cli_atomic_json(path: str | Path, value: Mapping[str, Any], *, overwrite: bool) -> dict[str, Any]:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite live audio artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"path": str(destination), "bytes": destination.stat().st_size}


def live_audio_cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="earcrate live-audio",
        description="Prepare exact phrase audio from a precompiled live crate. Planning and sample rendering happen before the device callback.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capability", help="report phrase-stream and optional audio-device capabilities")

    phrase = sub.add_parser("phrase", help="apply controls, plan one legal phrase, and prepare exact rack-rendered PCM")
    phrase.add_argument("crate_atlas")
    phrase.add_argument("state")
    phrase.add_argument("output_root")
    phrase.add_argument("--controls", default="")
    phrase.add_argument("--commit-bars", type=int, default=0)
    phrase.add_argument("--horizon-bars", type=int, default=0)
    phrase.add_argument("--beam-width", type=int, default=32)
    phrase.add_argument("--candidate-limit", type=int, default=12)
    phrase.add_argument("--target-bpm", type=float, default=0.0)
    phrase.add_argument("--target-peak", type=float, default=0.90)
    phrase.add_argument("--overwrite", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "capability":
            result = {
                "ok": True,
                "prepared_stream": live_stream_capability(),
                "audio_device": live_audio_device_capability(),
            }
        else:
            crate = live_load_crate_atlas(args.crate_atlas, verify_sources=True)
            state = _audio_cli_read_json(args.state)
            if not isinstance(state, Mapping):
                raise LiveError("live audio state file must contain a JSON object")
            root = Path(args.output_root).expanduser().resolve()
            paths = {
                "audio": root / "phrase.wav",
                "receipt": root / "phrase.receipt.json",
                "step": root / "phrase.step.json",
                "state": root / "next.state.json",
                "midi": root / "phrase.mid",
                "lowering": root / "phrase.midi-lowering.json",
                "binding": root / "phrase.binding.json",
                "program": root / "phrase.render-program.json",
                "execution": root / "phrase.execution.json",
            }
            if not args.overwrite:
                conflicts = [str(path) for path in paths.values() if path.exists()]
                if conflicts:
                    raise FileExistsError("refusing partial live audio phrase because output path(s) already exist: " + ", ".join(conflicts))
            root.mkdir(parents=True, exist_ok=True)
            prepared = live_render_next_phrase(
                crate,
                state,
                controls=_audio_cli_controls(args.controls),
                horizon_bars=args.horizon_bars,
                commit_bars=args.commit_bars,
                beam_width=args.beam_width,
                candidate_limit=args.candidate_limit,
                target_bpm=args.target_bpm,
                target_peak=args.target_peak,
            )
            try:
                import soundfile as sf
            except Exception as exc:
                raise LiveError("live audio phrase writing requires soundfile") from exc
            _atomic_wav(paths["audio"], prepared["audio"], int(prepared["receipt"]["sample_rate"]), sf)
            _audio_cli_atomic_json(paths["receipt"], prepared["receipt"], overwrite=bool(args.overwrite))
            _audio_cli_atomic_json(paths["step"], prepared["step"], overwrite=bool(args.overwrite))
            _audio_cli_atomic_json(paths["state"], prepared["next_state"], overwrite=bool(args.overwrite))
            _audio_cli_atomic_json(paths["lowering"], prepared["midi_lowering"], overwrite=bool(args.overwrite))
            _audio_cli_atomic_json(paths["binding"], prepared["binding"], overwrite=bool(args.overwrite))
            _audio_cli_atomic_json(paths["program"], prepared["render_program"], overwrite=bool(args.overwrite))
            _audio_cli_atomic_json(
                paths["execution"],
                {
                    "phrase_sha256": prepared["receipt"]["phrase_sha256"],
                    "complete": True,
                    "selected_event_count": prepared["receipt"]["selected_event_count"],
                    "executed_event_count": prepared["receipt"]["executed_event_count"],
                    "events": prepared["execution_outcomes"],
                },
                overwrite=bool(args.overwrite),
            )
            midi_write(prepared["midi_lowering"]["ledger"], paths["midi"], overwrite=bool(args.overwrite))
            result = {
                "ok": True,
                "complete": True,
                "phrase_sha256": prepared["receipt"]["phrase_sha256"],
                "step_sha256": prepared["step"]["step_sha256"],
                "state_after_sha256": prepared["next_state"]["state_sha256"],
                "absolute_start_bar_index": prepared["receipt"]["absolute_start_bar_index"],
                "bars": prepared["receipt"]["bars"],
                "frames": prepared["receipt"]["frames"],
                "duration_seconds": prepared["receipt"]["duration_seconds"],
                "selected_event_count": prepared["receipt"]["selected_event_count"],
                "materials_scanned_during_render": prepared["receipt"]["materials_scanned_during_render"],
                "samples_decoded_during_callback": prepared["receipt"]["samples_decoded_during_callback"],
                "paths": {name: str(path) for name, path in paths.items()},
            }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (LiveError, OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "type": type(exc).__name__},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
