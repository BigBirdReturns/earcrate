"""Record the owner's verdict on the A1-03 trio candidate, and close what it closes.

The candidate was admissible and it lost. The verdict was not a ranking of small deltas, and
it was not aimed at the mix: the owner heard daytime-television piano, and both objects in the
pack share the comp that produces that. So this closes the mechanism rather than adjusting it.

What the verdict actually says, stated once so it is not softened later. The object realizes
harmony and nothing else. Flim's identity is a melody, the recovery never attempted one, and
the realization never contained one -- so what remained was a chord chart played as block
voicings on two and four over a walking bass, which is a lounge idiom, and it sounded like
one. No revision of the bass figure, the drum sound or the balance addresses that.

What the verdict does not say is recorded with equal weight. A1-03 is not closed as a track.
The crate rack path is not condemned -- it did exactly what it was asked to do, and the parts
it rendered were measured playing what they were written to play. No new architecture program
is authorized by a negative result.

    python scripts/earcrate_a1_03_trio_verdict_v1.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.identity import seal  # noqa: E402

TRACK_ID = "A1-03"
TRIO_RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-trio-realization-v1.public.json"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-03-trio-verdict-v1.public.json"


def main() -> int:
    trio = json.loads(TRIO_RECEIPT.read_text(encoding="utf-8"))
    renders = trio["renders"]

    receipt = seal({
        "kind": "earcrate_a1_03_public_trio_verdict_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("The A1-03 trio candidate lost. It realizes harmony and no melody, and a "
                     "chord chart played that way is a lounge idiom -- which is what the owner "
                     "heard."),
        "verdict": {
            "authority": "owner",
            "answer": "no",
            "outcome": "LOSE",
            "selected": "neither",
            "aimed_at": ("the object, not the mix; both cuts in the pack share the comp that "
                         "produces the character the verdict named"),
            "owner_characterization": "daytime-television piano",
            "decisive": ("the realization contains no melody. Flim's identity is its tune; the "
                         "recovery never attempted one and the object never carried one, so "
                         "what was left is a chord chart played as block voicings on two and "
                         "four over a walking bass. That is a lounge idiom, and no revision of "
                         "the bass figure, the drum sound or the balance reaches it"),
            "failure_primary": "harmony_realized_without_melody",
            "failure_secondary": "generic_comp_and_walking_bass_idiom",
            "credited": [],
            "why_nothing_is_credited": ("the owner granted no musical positive, and inventing "
                                        "one from the machine result would misreport the "
                                        "verdict"),
            "judged_objects": {
                "candidate_pcm_sha256": renders["pcm_sha256"]["candidate"],
                "control_pcm_sha256": renders["pcm_sha256"]["control"],
            },
            "trio_receipt_sha256": trio["receipt_sha256"],
        },
        "disposition": {
            "a1_03_album_master": "unaccepted",
            "a1_03_status": "chart-driven realization closed; the track is not closed",
            "trio_candidate": "rejected",
            "piano_only_control": "not selected",
            "chart_driven_realization": "closed",
            "further_revisions_of_this_arrangement": "none",
            "album_one_accepted_masters": "1/7",
            "moves_album_counter": False,
            "next_owner_facing_action_from_a1_03": "none",
        },
        "what_this_does_not_close": {
            "the_track": ("A1-03 keeps its commission. A failing mechanism ends that "
                          "mechanism"),
            "the_source_binding": "the exact performance stays bound",
            "the_recovered_clock": "the performance's own grid is still the lane's timing "
                                   "authority",
            "the_crate_rack_path": ("not condemned. It rendered the parts it was given, and "
                                    "they were measured playing what they were written to "
                                    "play; the parts themselves were the wrong parts"),
            "ace_step": "stays where its own disposition left it, neither adopted nor "
                        "rejected globally",
        },
        "named_gap_this_verdict_creates": {
            "gap": ("a reconstruction of Flim requires the tune. Nothing in the lane recovers "
                    "or realizes melody, and a chord chart is not the piece"),
            "authorized_now": False,
            "why_not": ("a negative result does not authorize an architecture program. This is "
                        "recorded as the demand a future A1-03 commission would have to name, "
                        "not as work starting"),
        },
        "retained_evidence": [
            "the exact source binding",
            "the corrected bass-root chart over the whole form",
            "the recovered performance clock",
            "the crate rack build and its PCM identity",
        ],
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "renders_remain_local": True,
        },
        "new_organs_added": 0,
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"verdict {receipt['verdict']['outcome']} -- {receipt['disposition']['a1_03_status']}")
    print(f"receipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
