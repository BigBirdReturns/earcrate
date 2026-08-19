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


def compare(candidate: dict, gold: dict) -> dict:
    """What the attempt got right, and what it got wrong, source by source."""
    left, right = profile(candidate), profile(gold)
    shared = sorted(set(left) & set(right))
    findings = []

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
        gap = min(c["gain_db"]) - min(g["gain_db"])
        if abs(gap) >= 3.0:
            findings.append({"source_id": source_id, "decision": "balance",
                             "candidate_quietest_db": min(c["gain_db"]),
                             "gold_quietest_db": min(g["gain_db"]),
                             "candidate_is_louder_by_db": round(gap, 2),
                             "assessment": "wrong"})
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
    comparison = compare(candidate, gold)

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
