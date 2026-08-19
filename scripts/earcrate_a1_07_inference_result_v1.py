"""Record what the A1-07 recovery attempt was worth, now that a verdict exists.

The owner listened to the blind pair and chose the control. That terminates this candidate
lineage and leaves the system reference where it was, at zero of seven.

Comparing the candidate to the gold is permitted only from this point, and only because the
verdict already exists — the challenge's own acceptance block says so, and doing it earlier
would have let the answer leak backwards into the attempt. So the comparison lives here, in
a separate object, rather than being folded into the attempt's own receipt.

What it is for: the attempt got two of the hard things nearly right and three ordinary things
wrong, and the ordinary ones are what lost it. That is a more useful result than a pass would
have been, and it is the reason a losing attempt is worth writing down at all.

No private path, no option map, and no statement of which letter carried which object reaches
the public receipt. The outcome is public; the mapping stays with the owner and the pack.

    python scripts/earcrate_a1_07_inference_result_v1.py \
        --review-directory <recovery-review-001> --candidate-score <candidate-score.json> \
        --gold-score <performance-score.json>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate import reference_zero as rz  # noqa: E402
from earcrate.evidence.identity import seal  # noqa: E402

TRACK_ID = "A1-07"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-inference-result-v1.public.json"
TERMINAL_VERDICTS = {"control_wins", "tie_terminates_lineage", "reject_all_terminates_lineage"}
LEAD_SOURCE = "four_seasons_vocals"


class ResultError(RuntimeError):
    pass


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def profile(score: dict) -> dict:
    """Per-source decisions, at the resolution a comparison actually needs."""
    rows: dict[str, dict] = {}
    for track in score["tracks"]:
        for clip in track["clips"]:
            source_id = clip["source_id"]
            row = rows.setdefault(source_id, {"clips": 0, "pitch_semitones": set(),
                                              "tempo_scale": set(), "gain_db": set()})
            row["clips"] += 1
            row["pitch_semitones"].add(float(clip.get("pitch_semitones", 0.0)))
            row["tempo_scale"].add(float(clip.get("tempo_scale", 1.0)))
            row["gain_db"].add(float(clip.get("gain_db", 0.0)))
    return {source_id: {"clips": row["clips"],
                        "pitch_semitones": sorted(row["pitch_semitones"]),
                        "tempo_scale": sorted(row["tempo_scale"]),
                        "gain_db": sorted(row["gain_db"])}
            for source_id, row in sorted(rows.items())}


def compare(candidate: dict, gold: dict, source_lufs: dict | None = None) -> dict:
    """What the attempt got right, and what it got wrong, source by source.

    Balance needs care. A clip's `gain_db` is an attenuation applied to a source, and two
    scores can only be compared on it if their sources are equally loud to begin with -- which
    these are not. Comparing the raw numbers said the candidate's band was ten dB hot when it
    was not. With per-source loudness in hand the comparison runs on where each source
    actually sits relative to the lead; without it, the balance finding says it cannot be made
    rather than making it wrongly.
    """
    left, right = profile(candidate), profile(gold)
    shared = sorted(set(left) & set(right))
    findings = []

    lead = None
    if source_lufs and LEAD_SOURCE in left and LEAD_SOURCE in right and LEAD_SOURCE in source_lufs:
        lead = {"candidate": source_lufs[LEAD_SOURCE] + max(left[LEAD_SOURCE]["gain_db"]),
                "gold": source_lufs[LEAD_SOURCE] + max(right[LEAD_SOURCE]["gain_db"])}

    for source_id in shared:
        c, g = left[source_id], right[source_id]
        if c["pitch_semitones"] != g["pitch_semitones"]:
            near = (len(c["pitch_semitones"]) == len(g["pitch_semitones"]) == 1
                    and abs(c["pitch_semitones"][0] - g["pitch_semitones"][0]) <= 0.5)
            findings.append({
                "source_id": source_id, "decision": "pitch",
                "candidate": c["pitch_semitones"], "gold": g["pitch_semitones"],
                "assessment": "near" if near else "wrong",
            })
        if c["tempo_scale"] != g["tempo_scale"]:
            findings.append({"source_id": source_id, "decision": "tempo_scale",
                             "candidate": c["tempo_scale"], "gold": g["tempo_scale"],
                             "assessment": "wrong"})
        if source_lufs is None or source_id not in source_lufs:
            findings.append({"source_id": source_id, "decision": "balance",
                             "assessment": "not_comparable",
                             "why": ("gain_db is an attenuation on a source, and per-source "
                                     "loudness was not supplied, so the two scores cannot be "
                                     "compared on it")})
        elif lead is not None:
            c_rel = (source_lufs[source_id] + max(c["gain_db"])) - lead["candidate"]
            g_rel = (source_lufs[source_id] + max(g["gain_db"])) - lead["gold"]
            gap = c_rel - g_rel
            findings.append({"source_id": source_id, "decision": "balance",
                             "candidate_db_under_lead": round(c_rel, 1),
                             "gold_db_under_lead": round(g_rel, 1),
                             "candidate_is_louder_by_db": round(gap, 1),
                             "measured_in": "source loudness plus clip gain, against the lead",
                             "assessment": "wrong" if abs(gap) >= 3.0 else "near"})
        if c["clips"] != g["clips"]:
            findings.append({"source_id": source_id, "decision": "granularity",
                             "candidate_clips": c["clips"], "gold_clips": g["clips"],
                             "assessment": "coarser" if c["clips"] < g["clips"] else "finer"})

    return {
        "candidate_profile": left,
        "gold_profile": right,
        "sources_only_in_gold": sorted(set(right) - set(left)),
        "sources_only_in_candidate": sorted(set(left) - set(right)),
        "findings": findings,
        "candidate_clip_count": sum(row["clips"] for row in left.values()),
        "gold_clip_count": sum(row["clips"] for row in right.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-directory", required=True, type=Path)
    parser.add_argument("--candidate-score", required=True, type=Path)
    parser.add_argument("--gold-score", required=True, type=Path)
    parser.add_argument("--source-lufs", action="append", default=[],
                        help="source_id=integrated_lufs, so balance is comparable at all")
    args = parser.parse_args()

    root = args.review_directory.expanduser().resolve()
    ledger = load(root / "private" / "review-ledger.json")
    rz.validate_seal(ledger, kind="earcrate_reference_zero_review_ledger")
    verdict = str(ledger["verdict"])
    if verdict not in TERMINAL_VERDICTS | {"candidate_beats_control", "abstain"}:
        raise ResultError(f"unrecognised verdict: {verdict}")

    candidate = load(args.candidate_score)
    gold = load(args.gold_score)
    rz.validate_performance_score(candidate)
    rz.validate_performance_score(gold)
    source_lufs = {}
    for entry in args.source_lufs:
        source_id, _, value = entry.partition("=")
        source_lufs[source_id] = float(value)
    comparison = compare(candidate, gold, source_lufs or None)
    comparison["balance_measured_in"] = ("source integrated loudness plus clip gain, relative "
                                         "to the lead" if source_lufs else "not measured")

    print(f"verdict: {verdict}")
    print(f"  candidate {comparison['candidate_clip_count']} clips against gold "
          f"{comparison['gold_clip_count']}")
    for finding in comparison["findings"]:
        print(f"  {finding['assessment']:>8}  {finding['source_id']:<26} {finding['decision']}")

    receipt = seal({
        "kind": "earcrate_a1_07_public_inference_result_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("The owner chose the control. The inferred candidate lineage terminates "
                     "and the system reference stays open at zero of seven."),
        "verdict": verdict,
        "candidate_beat_control": verdict == "candidate_beats_control",
        "ledger_sha256": ledger["ledger_sha256"],
        "submission_sha256": ledger["submission_sha256"],
        "assignment_sha256": ledger["assignment_sha256"],
        "owner_notes": ledger["notes"],
        "numeric_dimension_scores_returned": bool(ledger["dimensions"]),
        "which_letter_carried_which_object": "withheld; the mapping stays with the owner",
        "gold_comparison": {
            "permitted_because": ("a verdict exists; the challenge forbids measuring gold "
                                  "similarity before submission"),
            "performed_after_verdict": True,
            **comparison,
        },
        "authority": {
            "system_reference_completed": False,
            "candidate_lineage": "terminated" if verdict in TERMINAL_VERDICTS else "open",
            "challenge_still_open": True,
            "track_still_accepted_as_album_master": True,
            "rights_or_release_permission": False,
            "moves_album_counter": False,
        },
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "option_map_exported": False,
        },
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\nreceipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
