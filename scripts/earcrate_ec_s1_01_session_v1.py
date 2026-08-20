"""EC-S1-01: write the DAW session and the machine-readable arrangement diff.

The commission's deliverable is not a mix, it is something openable and changeable. So the
session places one item per section per role rather than one long item per role: the sections
are where the arrangement decisions live, and an owner who wants the payoff to arrive earlier
should be able to drag the payoff, not slice a stem apart first.

Withheld roles get no item at all. That is the point of the session -- the gap where the drums
are not playing is visible on the timeline, which is what a withholding decision looks like
when it is real.

The arrangement diff is emitted in the same shape the session is written from, so two
revisions can be compared without opening any audio, and so an owner edit coming back from the
DAW can be read as a delta against a known state rather than as a new file.

    python scripts/earcrate_ec_s1_01_session_v1.py --track <dir> --foreground <dir> --out <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.identity import seal, sha256_file  # noqa: E402

COMMISSION = "EC-S1-01"
SAMPLE_RATE = 48_000
TRACK_ORDER = ("foreground", "bass", "drums")
TRACK_NAMES = {"foreground": "Foreground piano", "bass": "Bass", "drums": "Drums"}


class SessionError(RuntimeError):
    pass


def _quote(value: str) -> str:
    text = str(value)
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    return "`" + text.replace("`", "'") + "`"


def clips(arrangement: dict, foreground: dict, track_dir: Path) -> list[dict]:
    """One clip per section per role, with the withheld roles simply absent."""
    layout = arrangement["layout"]
    plan = arrangement["arrangement"]
    rows: list[dict] = []
    for section_row in layout:
        section = section_row["section"]
        start = section_row["start_seconds"]
        length = section_row["end_seconds"] - section_row["start_seconds"]

        source = next((row for row in foreground["layout"] if row["section"] == section), None)
        if source is None:
            raise SessionError(f"the foreground has no {section}")
        rows.append({
            "role": "foreground", "section": section, "clip_id": f"foreground-{section.lower()}",
            "position_seconds": round(start, 6), "length_seconds": round(length, 6),
            "source_offset_seconds": 0.0, "file": source["path"],
            "material": "+".join(source["plays"]), "gain_db": arrangement["gain_db"]["foreground"],
        })
        for role in ("bass", "drums"):
            material = plan[section][role]
            if material is None:
                continue
            piece = track_dir / "work" / f"{role}-{section.lower()}.wav"
            if not piece.is_file():
                raise SessionError(f"no rendered piece for {role} in {section}")
            rows.append({
                "role": role, "section": section, "clip_id": f"{role}-{section.lower()}",
                "position_seconds": round(start, 6), "length_seconds": round(length, 6),
                "source_offset_seconds": 0.0, "file": str(piece), "material": material,
                "gain_db": arrangement["gain_db"][role],
            })
    return rows


def write_rpp(rows: list[dict], arrangement: dict, foreground: dict, path: Path) -> str:
    bpm = float(arrangement["grid"]["measured_bpm"])
    lines = [
        '<REAPER_PROJECT 0.1 "7.0/x64" 1700000000',
        f"  // EARCRATE_COMMISSION {COMMISSION}",
        f"  // EARCRATE_GRID_MEASURED_BPM {bpm}",
        f"  // EARCRATE_BAR_SECONDS {arrangement['grid']['bar_seconds']}",
        "  // EARCRATE_NOTE the tempo is the material's measured tempo, not a requested one",
        "  RIPPLE 0",
        "  GROUPOVERRIDE 0 0 0",
        "  AUTOXFADE 1",
        f"  TEMPO {bpm:.9f} 4 4",
        f"  SAMPLERATE {SAMPLE_RATE} 0 0",
    ]
    for role in TRACK_ORDER:
        lines += ["  <TRACK", f"    NAME {_quote(TRACK_NAMES[role])}",
                  f"    // EARCRATE_ROLE {role}", "    VOLPAN 1 0 -1 -1 1"]
        for row in [item for item in rows if item["role"] == role]:
            volume = 10.0 ** (float(row["gain_db"]) / 20.0)
            note = f"section={row['section']} material={row['material']} role={row['role']}"
            lines += [
                "    <ITEM",
                f"      POSITION {row['position_seconds']:.12f}",
                f"      LENGTH {row['length_seconds']:.12f}",
                f"      SOFFS {row['source_offset_seconds']:.12f}",
                f"      VOLPAN {volume:.12f} 0.000000000000 1 -1",
                f"      NAME {_quote(row['clip_id'])}",
                f"      NOTES {_quote(note)}",
                "      <SOURCE WAVE",
                f"        FILE {_quote(row['file'])}",
                "      >",
                "    >",
            ]
        lines.append("  >")

    # The composed part travels as MIDI too, so the tune itself is editable as notes.
    lines += ["  <TRACK", f"    NAME {_quote('Foreground piano (MIDI reference)')}",
              "    // EARCRATE_ROLE foreground_midi",
              f"    // EARCRATE_MIDI_FILE {foreground['midi']['path']}",
              "    VOLPAN 1 0 -1 -1 1", "  >"]
    lines.append(">")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return sha256_file(path)


def diff_shape(rows: list[dict], arrangement: dict) -> dict:
    """The arrangement as data: what plays where, comparable without opening audio."""
    layout = arrangement["layout"]
    plan = arrangement["arrangement"]
    sections = []
    for row in layout:
        section = row["section"]
        sounding = sorted({item["role"] for item in rows if item["section"] == section})
        sections.append({
            "section": section,
            "start_bar": row["start_bar"], "end_bar": row["end_bar"],
            "start_seconds": row["start_seconds"], "end_seconds": row["end_seconds"],
            "roles_sounding": sounding,
            "material": {item["role"]: item["material"]
                         for item in rows if item["section"] == section},
            "withholds": plan[section]["withholds"],
        })
    transitions = []
    for left, right in zip(sections, sections[1:]):
        entering = sorted(set(right["roles_sounding"]) - set(left["roles_sounding"]))
        leaving = sorted(set(left["roles_sounding"]) - set(right["roles_sounding"]))
        changed = sorted(role for role in
                         set(left["material"]) & set(right["material"])
                         if left["material"][role] != right["material"][role])
        transitions.append({
            "from": left["section"], "to": right["section"],
            "roles_entering": entering, "roles_leaving": leaving,
            "material_changed": changed,
            "is_content_change": bool(entering or leaving or changed),
        })
    return {"sections": sections, "transitions": transitions,
            "every_transition_changes_content": all(row["is_content_change"]
                                                    for row in transitions)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", required=True, type=Path)
    parser.add_argument("--foreground", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    track_dir = args.track.expanduser().resolve()
    fg_dir = args.foreground.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    arrangement = json.loads((track_dir / "arrangement.json").read_text(encoding="utf-8"))
    foreground = json.loads((fg_dir / "foreground.json").read_text(encoding="utf-8"))

    rows = clips(arrangement, foreground, track_dir)
    print(f"{len(rows)} clips across {len(TRACK_ORDER)} role tracks")
    for section in [row["section"] for row in arrangement["layout"]]:
        placed = [row["role"] for row in rows if row["section"] == section]
        print(f"  {section:<7} {'+'.join(sorted(placed))}")

    session = out / f"{COMMISSION}.rpp"
    digest = write_rpp(rows, arrangement, foreground, session)
    print(f"session: {session.name} ({digest[:16]})")

    shape = diff_shape(rows, arrangement)
    receipt = seal({
        "kind": "earcrate_ec_s1_01_public_session_receipt",
        "schema_version": 1, "commission": COMMISSION,
        "grid": arrangement["grid"],
        "track": {"seconds": arrangement["master"]["seconds"],
                  "lufs": arrangement["master"]["lufs"],
                  "sha256": arrangement["master"]["sha256"]},
        "session": {"format": "rpp", "sha256": digest, "clips": len(rows),
                    "role_tracks": list(TRACK_ORDER),
                    "withheld_roles_have_no_item": True},
        "midi": {"notes": foreground["midi"]["notes"],
                 "sha256": foreground["midi"]["sha256"]},
        "arrangement": shape,
        "states": arrangement["states"],
        "role_presence": arrangement["role_presence"],
        "boundary": {"private_paths_included": False, "renders_remain_local": True},
    }, "receipt_sha256")
    (out / "session.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    (out / "arrangement-diff.json").write_text(
        json.dumps(shape, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    print("\ntransitions, as content changes:")
    for row in shape["transitions"]:
        moves = []
        if row["roles_entering"]:
            moves.append("+" + "+".join(row["roles_entering"]))
        if row["roles_leaving"]:
            moves.append("-" + "-".join(row["roles_leaving"]))
        if row["material_changed"]:
            moves.append("~" + "~".join(row["material_changed"]))
        print(f"  {row['from']:>7} -> {row['to']:<7} {' '.join(moves)}")
    print(f"  every transition changes content: {shape['every_transition_changes_content']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
