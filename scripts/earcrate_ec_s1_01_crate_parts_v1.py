"""EC-S1-01: play the crate as instruments, not as loops.

The library holds 198,447 analysed atoms. Every earlier lane treated them as material to place
on a timeline; this plays them from a keyboard. A hook becomes a pitched instrument that can
answer the piano, and a drop hit becomes something struck on an arrival.

Two parts, both written to the same measured grid the rest of the track uses:

    vocal chops   pitched, from approved VOX_HOOK atoms trimmed to chop length, answering the
                  melody in the gaps it leaves -- sparse in the build, conversational in the
                  payoff
    hits          triggered, from approved DROP_HIT and TEXTURE atoms, one on each section
                  arrival and a few accents inside the payoff

The withholding is preserved on purpose. Neither part plays in the intro or in the hold,
because those two sections exist to take things away, and spice sprayed evenly over a form is
the thing that stops it being a form.

Rights: these atoms come from the owner's own commercial library. Renders stay local, and this
version of the track is not a rights-clear object.

    python scripts/earcrate_ec_s1_01_crate_parts_v1.py \
        --foreground <dir> --track <dir> --crate <sqlite> --out <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earcrate.analyze.decode import decode_audio  # noqa: E402
from earcrate.evidence.identity import sha256_file  # noqa: E402
from earcrate.midi.codec import midi_read  # noqa: E402
from earcrate.rack.library import rack_build_from_atoms  # noqa: E402
from earcrate.rack.render_fix import rack_render_ledger  # noqa: E402

COMMISSION = "EC-S1-01"
SAMPLE_RATE = 48_000
TICKS_PER_BEAT = 960
BEATS_PER_BAR = 4
TASTE_PROFILE = "girl_talk_v1"

KEY_ROOT = 2                        # D
SCALE = (0, 2, 3, 5, 7, 8, 10)
PROGRESSION_ROOTS = (2, 10, 5, 0)   # Dm Bb F C, one per bar

# One octave and a bit, not a keyboard. A sampled voice dragged across sixteen semitones
# chipmunks at the top and muds at the bottom; the first version did exactly that and the
# result sounded scattered even though every chop came from a single record.
CHOP_REGISTER = (62, 69)            # D4 to A4, seven semitones of stretch at most
CHOP_SECONDS = 0.55                 # a chop, not a phrase -- the note length, not the region
# A pitched zone is gate mode: it stops at note-off. So the region only has to be long enough
# to survive being transposed down, and trimming it to the note length starved the transpose
# budget and got every hook rejected for insufficient duration.
CHOP_REGION_SECONDS = 1.6
HIT_SECONDS = 0.45
HIT_NOTE = 49                       # a crash-ish slot: high spectral fit

POOL_PER_VOICE = 10
MINIMUM_PEAK = 0.05
ATTACK_FRACTION = 0.35
SCREEN_SECONDS = 0.35

GAIN_DB = {"chops": -9.0, "hits": -11.0}


class CratePartsError(RuntimeError):
    pass


def _pitch(step: int) -> int:
    pitch_class = (KEY_ROOT + SCALE[step % len(SCALE)]) % 12
    note = CHOP_REGISTER[0] + ((pitch_class - CHOP_REGISTER[0]) % 12)
    while note > CHOP_REGISTER[1]:
        note -= 12
    return note


def parts(layout: list[dict], bar_seconds: float) -> tuple[list[dict], list[dict]]:
    """Answer phrases and arrival hits, placed against the piano rather than over it."""
    beat = bar_seconds / BEATS_PER_BAR
    sections = {row["section"]: row for row in layout}
    chops: list[dict] = []
    hits: list[dict] = []

    # An arrival hit wherever a section begins, except the very start.
    for row in layout[1:]:
        hits.append({"pitch": HIT_NOTE, "velocity": 104, "start": row["start_seconds"],
                     "duration": HIT_SECONDS, "why": f"arrival of {row['section']}"})

    def chop(bar: int, offset: float, step: int, velocity: int, why: str) -> None:
        chops.append({"pitch": _pitch(step), "velocity": velocity,
                      "start": bar * bar_seconds + offset * beat,
                      "duration": min(CHOP_SECONDS, beat * 0.9), "why": why})

    build = sections["BUILD"]
    for index, bar in enumerate(range(build["start_bar"], build["end_bar"])):
        if index % 2:                                   # every other bar only
            continue
        root_step = index % len(PROGRESSION_ROOTS)
        chop(bar, 3.0, root_step, 78, "build: one answer on four")

    payoff = sections["PAYOFF"]
    for index, bar in enumerate(range(payoff["start_bar"], payoff["end_bar"])):
        root_step = index % len(PROGRESSION_ROOTS)
        chop(bar, 1.5, root_step, 88, "payoff: answer on the and of two")
        chop(bar, 3.0, root_step + 2, 84, "payoff: answer on four")
        if index % 4 == 0:
            hits.append({"pitch": HIT_NOTE, "velocity": 92,
                         "start": bar * bar_seconds, "duration": HIT_SECONDS,
                         "why": "payoff: four-bar accent"})

    chops.sort(key=lambda row: row["start"])
    hits.sort(key=lambda row: row["start"])
    return chops, hits


def write_midi(chops: list[dict], hits: list[dict], bar_seconds: float, path: Path) -> dict:
    import mido

    beat = bar_seconds / BEATS_PER_BAR
    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=int(round(beat * 1_000_000)), time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    for name, channel, program, rows in (("Vocal chops", 0, 85, chops),
                                         ("Hits", 9, 0, hits)):
        events: list[tuple[int, int, int, int]] = []
        for row in rows:
            start = max(0, int(round(row["start"] / beat * TICKS_PER_BEAT)))
            end = start + max(1, int(round(row["duration"] / beat * TICKS_PER_BEAT)))
            events.append((start, 1, row["pitch"], row["velocity"]))
            events.append((end, 0, row["pitch"], 0))
        events.sort(key=lambda event: (event[0], event[1], event[2]))
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        if channel != 9:
            track.append(mido.Message("program_change", channel=channel, program=program,
                                      time=0))
        clock = 0
        for tick, on, pitch, velocity in events:
            track.append(mido.Message("note_on" if on else "note_off", channel=channel,
                                      note=pitch, velocity=velocity, time=tick - clock))
            clock = tick
        track.append(mido.MetaMessage("end_of_track", time=0))
        midi.tracks.append(track)
    midi.save(path)
    return {"path": str(path), "chops": len(chops), "hits": len(hits),
            "sha256": sha256_file(path)}


def _screen(path: str, start: float, end: float) -> tuple[float, float]:
    audio = decode_audio(Path(path), sr=SAMPLE_RATE, start=start,
                         duration=min(SCREEN_SECONDS, end - start))
    if not audio.size:
        return 0.0, 0.0
    envelope = np.abs(audio)
    peak = float(envelope.max())
    if peak <= 0.0:
        return 0.0, 0.0
    return peak, int(np.argmax(envelope > ATTACK_FRACTION * peak)) / float(SAMPLE_RATE)


def pool(crate: Path, file_id: str | None) -> tuple[list[dict], dict]:
    """Hooks that can carry a pitch, and hits that can carry an accent."""
    connection = sqlite3.connect(f"file:{crate.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query = ("select a.*, f.path as path, f.audio_sha256 as source_audio_sha256 "
             "from ear_atoms a join files f on f.id = a.file_id "
             "where a.status = 'approved' and f.present = 1 and {filter} "
             "order by {order} desc, a.id asc limit ?")
    if file_id is None:
        file_id = _one_record(connection)
    wanted = (
        ("chops", f"a.ear_role = 'VOX_HOOK' and a.file_id = '{file_id}'",
         "a.hook_score * a.intelligibility", CHOP_REGION_SECONDS),
        ("hits", "a.ear_role in ('DROP_HIT','DRUM_BREAK','PICKUP_FILL','TEXTURE') "
         f"and a.file_id = '{file_id}'",
         "a.spark_score * a.transient_density", HIT_SECONDS),
    )
    atoms: dict[str, dict] = {}
    report = {"considered": 0, "screened_out": 0, "aligned": 0, "by_voice": {}}
    for voice, condition, order, trim in wanted:
        taken = 0
        for row in connection.execute(query.format(filter=condition, order=order),
                                      (POOL_PER_VOICE,)):
            report["considered"] += 1
            start = float(row["start_s"])
            peak, attack = _screen(row["path"], start, float(row["end_s"]))
            if peak < MINIMUM_PEAK:
                report["screened_out"] += 1
                continue
            if attack > 0.0:
                start += attack
                report["aligned"] += 1
            atoms[row["id"]] = {
                "atom_id": row["id"], "loop_id": row["loop_id"], "file_id": row["file_id"],
                "atom_status": row["status"], "path": row["path"],
                "start_s": start, "end_s": start + trim,
                "ear_role": row["ear_role"], "render_role": row["render_role"],
                "key_root": row["key_root"], "bpm": row["bpm"], "score": row["score"],
                "hook_score": row["hook_score"], "bed_score": row["bed_score"],
                "floor_score": row["floor_score"], "bass_score": row["bass_score"],
                "spark_score": row["spark_score"], "intelligibility": row["intelligibility"],
                "low_share": row["low_share"], "mid_share": row["mid_share"],
                "high_share": row["high_share"], "loopability": row["loopability"],
                "transient_density": row["transient_density"],
                "source_audio_sha256": row["source_audio_sha256"] or "",
                "taste_profile": row["taste_profile"],
            }
            taken += 1
        report["by_voice"][voice] = taken
    connection.close()
    if not atoms:
        raise CratePartsError("the crate offered no audible atom for either voice")
    report["accepted"] = len(atoms)
    report["single_record"] = True
    report["source_file_id"] = file_id
    report["chop_register"] = list(CHOP_REGISTER)
    report["region_seconds"] = {"chops": CHOP_REGION_SECONDS, "hits": HIT_SECONDS}
    report["chop_note_seconds"] = CHOP_SECONDS
    report["taste_profile"] = TASTE_PROFILE
    return [atoms[key] for key in sorted(atoms)], report


def _one_record(connection: sqlite3.Connection) -> str:
    """Both voices from a single record, so the kit and the hook are the same instrument.

    Chosen on harmonic fit rather than on score alone: a hook already sitting in D, F, A or C
    lands on this progression without being retuned into something else.
    """
    friendly = (KEY_ROOT, 5, 9, 0)          # D, F, A, C against Dm - Bb - F - C
    row = connection.execute(
        "select f.id as file_id, "
        "  sum(case when a.ear_role='VOX_HOOK' then 1 else 0 end) as hooks, "
        "  sum(case when a.ear_role in ('DROP_HIT','DRUM_BREAK','PICKUP_FILL','TEXTURE') "
        "      then 1 else 0 end) as hits, "
        "  max(case when a.ear_role='VOX_HOOK' then a.hook_score * a.intelligibility end) "
        "      as quality "
        "from ear_atoms a join files f on f.id = a.file_id "
        "where a.status = 'approved' and f.present = 1 "
        "  and (a.ear_role != 'VOX_HOOK' or a.key_root in "
        f"      ({','.join(str(value) for value in friendly)})) "
        "group by f.id having hooks >= 3 and hits >= 2 "
        "order by quality desc limit 1").fetchone()
    if row is None:
        raise CratePartsError("no single record in the crate carries both a hook and a hit")
    return str(row["file_id"])


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args],
                            capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise CratePartsError(result.stderr[-500:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foreground", required=True, type=Path)
    parser.add_argument("--track", required=True, type=Path)
    parser.add_argument("--crate", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source-file-id", default=None,
                        help="draw both voices from this one record")
    args = parser.parse_args()

    fg = json.loads((args.foreground.expanduser().resolve() / "foreground.json")
                    .read_text(encoding="utf-8"))
    track_dir = args.track.expanduser().resolve()
    arrangement = json.loads((track_dir / "arrangement.json").read_text(encoding="utf-8"))
    out = args.out.expanduser().resolve()
    (out / "stems").mkdir(parents=True, exist_ok=True)

    bar_seconds = fg["grid"]["bar_seconds"]
    chops, hits = parts(fg["layout"], bar_seconds)
    print(f"{len(chops)} vocal chops, {len(hits)} hits, on the {fg['grid']['measured_bpm']} "
          "bpm measured grid")
    for row in fg["layout"]:
        inside = sum(1 for chop in chops
                     if row["start_seconds"] <= chop["start"] < row["end_seconds"])
        struck = sum(1 for hit in hits
                     if row["start_seconds"] <= hit["start"] < row["end_seconds"])
        print(f"  {row['section']:<7} chops {inside:>2}  hits {struck:>2}")

    midi = write_midi(chops, hits, bar_seconds, out / "crate-parts.mid")
    print(f"midi: {midi['chops']} + {midi['hits']} -> {Path(midi['path']).name}")

    atoms, report = pool(args.crate.expanduser().resolve(), args.source_file_id)
    print(f"crate: {report['accepted']} atoms accepted "
          f"({report['by_voice']}), {report['screened_out']} screened out, "
          f"{report['aligned']} moved onto their transient")

    ledger = midi_read(out / "crate-parts.mid")
    build = rack_build_from_atoms(ledger, atoms, out / "racks", taste_profile=TASTE_PROFILE,
                                  apply=True, sample_rate=SAMPLE_RATE, overwrite=True)
    if not build.get("complete"):
        raise CratePartsError(f"the crate could not cover the parts: "
                              f"{json.dumps(build.get('unresolved'))[:300]}")
    result = rack_render_ledger(ledger, build["binding"], build["rack_revisions"],
                                out / "crate-parts.wav", stems_dir=out / "stems",
                                sample_rate=SAMPLE_RATE, overwrite=True)
    if not result["complete_execution"]:
        raise CratePartsError(f"{result['refused_event_count']} events refused by the crate")
    print(f"rendered: {result['executed_event_count']} events, "
          f"{len(build['racks'])} racks sealed")

    proposal = json.loads(Path(build["proposal_path"]).read_text(encoding="utf-8"))
    selected = [{"slot": slot["slot_id"], "mode": slot["mode"],
                 "role_hint": slot["role_hint"],
                 "atoms": sorted({row["atom_id"] for row in slot["selected"]})}
                for slot in proposal["slots"]]
    for row in selected:
        print(f"  {row['role_hint']:<8} {row['mode']:<8} {len(row['atoms'])} atom(s)")

    (out / "crate-parts.json").write_text(json.dumps({
        "commission": COMMISSION, "grid": fg["grid"], "midi": midi,
        "crate": report, "selected": selected,
        "gain_db": GAIN_DB,
        "rights": ("atoms come from the owner's own commercial library; renders stay local "
                   "and this version is not a rights-clear object"),
        "render": {"path": str(out / "crate-parts.wav"),
                   "sha256": sha256_file(out / "crate-parts.wav"),
                   "events": result["executed_event_count"]},
        "stems": {Path(row["path"]).stem: sha256_file(Path(row["path"]))
                  for row in result["stems"]},
        "stem_paths": {Path(row["path"]).stem: row["path"] for row in result["stems"]},
    }, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"\ncrate-parts.json written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
