"""Build the A1-02 production candidate from the flat 136 performance.

Renders the performance as three role stems, puts them back on the performance's own
balance, and arranges them with a MixScore. Also renders a zero-delta control, whose
whole purpose is to fail loudly if the production path is doing anything other than
moving levels: with every arrangement move set to zero it must reproduce the
performance.

Paths are arguments. The rack, the score ledger and the renders stay outside the
repository, so nothing here may default to a local location.

    python scripts/earcrate_a1_02_production_v1.py \
        --rack <rack-dir> --ledger <score-v2-ledger.json> --out <render-dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_02.performance.demand import compile_demand  # noqa: E402
from earcrate.a1_02.performance.rack import bind, parse_sfz, verify_sources  # noqa: E402
from earcrate.a1_02.performance.rack_render import render  # noqa: E402
from earcrate.a1_02.score_v2 import interpretation as itp  # noqa: E402
from earcrate.evidence.identity import sha256_file  # noqa: E402
from earcrate.mix.model import mixscore_seal  # noqa: E402
from earcrate.mix.render import mixscore_render_to_files  # noqa: E402

TEMPO = 136.0
END_BEAT = 435.0
ROLES = ("melody", "inner", "bass")

# Where the arrangement moves, in dB from the performance's own balance, and why.
# The score already has an arc -- a thin melody-led opening, a real left hand arriving
# at B, a long body, a bass-driven C, a return, and a coda that is by a wide margin the
# densest music in the piece. These moves support that shape rather than impose another.
ANCHORS = (
    (0.0, {"melody": -1.5, "inner": 0.0, "bass": -9.0}, "intro: the tune alone"),
    (8.0, {"melody": -1.5, "inner": 0.0, "bass": -3.0}, "the left hand arrives underneath"),
    (16.0, {"melody": 0.0, "inner": 0.0, "bass": 0.0}, "A: full balance"),
    (32.0, {"melody": 0.5, "inner": 0.0, "bass": 0.5}, "A repeat: a small lift"),
    (44.0, {"melody": 1.5, "inner": 1.0, "bass": 1.5}, "second ending pushes into B"),
    (48.0, {"melody": 1.0, "inner": -1.0, "bass": 1.5}, "B: the left hand finally has content"),
    (80.0, {"melody": -0.5, "inner": -3.0, "bass": -0.5}, "B repeat: second time inward"),
    (112.0, {"melody": -1.0, "inner": -1.0, "bass": -1.0}, "body opens held back"),
    (160.0, {"melody": 0.5, "inner": 0.0, "bass": 0.5}, "body builds"),
    (200.0, {"melody": 2.0, "inner": 0.5, "bass": 2.0}, "body peak"),
    (252.0, {"melody": 0.5, "inner": 0.0, "bass": 1.5}, "C: bass-driven; no inner voices exist here"),
    (288.0, {"melody": 1.0, "inner": 0.0, "bass": 1.0}, "C repeat"),
    (312.0, {"melody": 2.0, "inner": 1.0, "bass": 2.0}, "second ending pushes into the return"),
    (324.0, {"melody": -2.0, "inner": -2.0, "bass": -2.5}, "D.S.: a return should read as a return"),
    (360.0, {"melody": -0.5, "inner": -0.5, "bass": -0.5}, "the return rebuilds"),
    (400.0, {"melody": 2.0, "inner": 1.0, "bass": 2.0}, "Coda: the densest music in the piece"),
    (417.0, {"melody": 2.0, "inner": 1.0, "bass": 2.0}, "let the last chord ring"),
)

# Static. This rack has a long release and a pan step under sustained notes jumps audibly.
PAN = {"melody": 0.18, "inner": 0.0, "bass": -0.22}


def classify(notes):
    """The tune is the top of the right hand at each onset; the rest accompanies it."""
    by_onset: dict[float, list[int]] = {}
    for index, note in enumerate(notes):
        if int(note["staff"]) == 1:
            by_onset.setdefault(float(note["start_beat"]), []).append(index)
    melody = {max(group, key=lambda i: int(notes[i]["pitch"])) for group in by_onset.values()}

    out: dict[str, list] = {role: [] for role in ROLES}
    for index, note in enumerate(notes):
        role = "melody" if index in melody else ("inner" if int(note["staff"]) == 1 else "bass")
        out[role].append(note)
    return out


def read_pcm(path: Path) -> np.ndarray:
    import soundfile as sf

    pcm, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return pcm


def pad_to(path: Path, frames: int) -> None:
    """Pad a role stem with silence, losslessly, so no deck runs off the end of its asset."""
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="int32", always_2d=True)
    if len(data) >= frames:
        return
    padded = np.vstack([data, np.zeros((frames - len(data), data.shape[1]), dtype=data.dtype)])
    sf.write(str(path), padded, rate, subtype="PCM_24")
    back, _ = sf.read(str(path), dtype="int32", always_2d=True)
    if not np.array_equal(back[:len(data)], data):
        raise SystemExit(f"padding {path.name} altered the original samples")


def build_score(stems: dict, corrections: dict, counts: dict, *, arranged: bool) -> dict:
    assets, decks, events = [], [], []
    for role in ROLES:
        path = stems[role]
        assets.append({"asset_id": f"{role}_stem", "path": str(path), "source_bpm": TEMPO,
                       "downbeat_seconds": 0.0, "expected_file_sha256": sha256_file(path),
                       "metadata": {"role": role, "notes": counts[role],
                                    "incumbent_balance_db": corrections[role]}})
        first = ANCHORS[0][1][role] if arranged else 0.0
        decks.append({"deck_id": f"deck_{role}", "crossfader_side": "none",
                      "gain_db": round(corrections[role] + first, 4),
                      "pan": PAN[role] if arranged else 0.0, "metadata": {"role": role}})
        events.append({"op": "load", "at_beat": 0.0, "deck_id": f"deck_{role}",
                       "asset_id": f"{role}_stem"})
        events.append({"op": "play", "at_beat": 0.0, "deck_id": f"deck_{role}",
                       "asset_id": f"{role}_stem", "sync": True, "rate": 1.0,
                       "source_beat": 0.0})

    if arranged:
        for (beat0, deltas0, _), (beat1, deltas1, _) in zip(ANCHORS, ANCHORS[1:]):
            for role in ROLES:
                start = corrections[role] + deltas0[role]
                end = corrections[role] + deltas1[role]
                if abs(start - end) < 1e-9:
                    continue
                events.append({"op": "fade", "from_beat": beat0, "to_beat": beat1,
                               "deck_id": f"deck_{role}", "from_db": round(start, 4),
                               "to_db": round(end, 4), "curve": "s_curve"})

    return {"kind": "earcrate_mix_score", "schema_version": 1,
            "title": "A1-02 Children -- " + ("production" if arranged else "zero-delta control"),
            "clock": {"bpm": TEMPO, "beats_per_bar": 4, "sample_rate": 48000},
            "end_beat": END_BEAT, "peak_ceiling": 0.95, "master_gain_db": 0.0,
            "assets": assets, "decks": decks, "events": events,
            "metadata": {"track_id": "A1-02", "built_from": "flat 136 incumbent",
                         "arrangement": [{"beat": b, "intent": why} for b, _, why in ANCHORS]}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rack", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    out = args.out.expanduser().resolve()
    stems_dir = out / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)

    parent = json.loads(args.ledger.read_text(encoding="utf-8"))["performed"]
    flat = itp.derive_child(parent, tempo_bpm=TEMPO,
                            interpretation_id="performed_interpretation_136",
                            rationale="the production incumbent")
    notes = flat["notes"]
    roles = classify(notes)
    counts = {role: len(rows) for role, rows in roles.items()}
    print("roles", counts)

    zones, _ = parse_sfz(args.rack / "SalamanderGrandPianoV3.sfz")

    def render_subset(label, subset):
        performance = {**{k: v for k, v in flat.items() if k != "notes"}, "notes": subset}
        binding = bind(compile_demand(performance), zones)
        if not binding["all_events_bound"]:
            raise SystemExit(f"{label}: {binding['refused_event_count']} events refused")
        if not verify_sources(args.rack, binding)["sources_intact"]:
            raise SystemExit(f"{label}: rack sources missing or mutated")
        destination = stems_dir / f"a1-02-136-{label}.wav"
        if destination.exists():
            destination.unlink()
        result = render(binding, args.rack, tempo_bpm=TEMPO, destination=destination,
                        stems=False)
        print(f"  {label:8} {result['duration_seconds']:>8.2f}s  "
              f"gain {result['applied_gain_db']:+8.3f} dB  events {result['events_rendered']:5d}")
        return destination, result

    full_path, full = render_subset("full", notes)
    made = {role: render_subset(role, rows) for role, rows in roles.items()}

    # Each render normalised to its own peak, so three separate renders arrive with their
    # balance destroyed. Put each back on the full mix's gain, then prove the split by
    # summing: if the roles do not reconstruct the performance, this is not a split.
    full_pcm = read_pcm(full_path)
    accumulated = np.zeros_like(full_pcm)
    corrections = {}
    for role, (path, result) in made.items():
        corrections[role] = round(full["applied_gain_db"] - result["applied_gain_db"], 6)
        scaled = read_pcm(path) * (10.0 ** (corrections[role] / 20.0))
        if len(scaled) < len(accumulated):
            scaled = np.vstack([scaled, np.zeros((len(accumulated) - len(scaled), 2))])
        accumulated += scaled[:len(accumulated)]
    residual = float(np.abs(accumulated - full_pcm).max())
    print(f"\nthe three roles reconstruct the performance: residual {residual:.3e}")
    if residual > 1e-4:
        raise SystemExit("the role split does not reconstruct the performance")

    for role in ROLES:
        pad_to(made[role][0], len(full_pcm))
    stems = {role: made[role][0] for role in ROLES}

    rendered = {}
    for label, arranged in (("production", True), ("zero-delta-control", False)):
        score = build_score(stems, corrections, counts, arranged=arranged)
        destination = out / f"a1-02-{label}.wav"
        rendered[label] = mixscore_render_to_files(mixscore_seal(score), destination,
                                                   stems_dir=out / f"{label}.stems")
        print(f"{label:20} events {len(score['events']):4d} -> {destination.name}")

    # With every move set to zero the production path must reproduce the performance.
    control = read_pcm(out / "a1-02-zero-delta-control.wav")
    frames = min(len(full_pcm), len(control))
    scale = float(np.abs(full_pcm[:frames]).max()) / float(np.abs(control[:frames]).max())
    null_residual = float(np.abs(full_pcm[:frames] - control[:frames] * scale).max())
    print(f"a null arrangement reproduces the performance: residual {null_residual:.3e}")
    if null_residual > 1e-4:
        raise SystemExit("the zero-delta control does not reproduce the performance")

    (out / "role-split.json").write_text(json.dumps(
        {"roles": counts, "corrections_db": corrections,
         "full_gain_db": full["applied_gain_db"],
         "reconstruction_residual": residual,
         "null_arrangement_residual": null_residual,
         "duration_seconds": full["duration_seconds"]},
        indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
