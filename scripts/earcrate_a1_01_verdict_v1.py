"""Record the owner's verdict on the A1-01 recurrence edit, and what it exposed.

The verdict was that the two sides are indistinguishable. That is not a listener failing to
concentrate and it is not a null result -- it is the answer to the question the pack asked,
and the pack had already claimed it could not happen. `REVIEW.txt` said the difference was
"ten seconds of different music at the same level, and it is obvious".

It was not obvious, because the pack was reading the wrong number. A waveform correlation of
-0.199 says the two spans are not the same samples. It says nothing about whether they sound
different. In loop-based production any two bars of the same section decorrelate completely
while carrying the same material, and measured after the verdict that is exactly what these
are: MFCC cosine 1.0000, chroma cosine 1.0000, spectral centroid within 0.1 %, level within
0.4 %, identical tempo.

So the edit substitutes a passage for a perceptually equivalent one. Under the pack's own
admissible outcomes an indistinguishable pair closes the retained edit, because a change has
to earn itself and this one is inaudible.

    python scripts/earcrate_a1_01_verdict_v1.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.identity import seal  # noqa: E402

TRACK_ID = "A1-01"
PACK_RECEIPT = ROOT / "proofs" / "album_one" / "a1-01-full-context-pack-v1.public.json"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-01-verdict-v1.public.json"


def main() -> int:
    pack = json.loads(PACK_RECEIPT.read_text(encoding="utf-8"))

    receipt = seal({
        "kind": "earcrate_a1_01_public_verdict_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("The A1-01 recurrence edit is inaudible. The two sides are perceptually "
                     "identical, the retained edit closes, and the pack's claim that the "
                     "difference was obvious rested on a measurement that cannot support it."),
        "verdict": {
            "authority": "owner",
            "answer": "no",
            "outcome": "TIE",
            "selected": "neither",
            "owner_words": "i can't tell the difference",
            "decisive": ("the substituted passage is perceptually equivalent to the one it "
                         "replaced, so the edit changes nothing a listener can hear. A change "
                         "that cannot be heard has not earned itself"),
            "failure_primary": "inaudible_edit",
            "credited": [],
            "why_nothing_is_credited": ("no musical positive was granted, and the artifact was "
                                        "verified sound before the verdict was accepted"),
        },
        "the_artifact_was_checked_first": {
            "why": ("an owner who cannot hear a difference and a pack that failed to deliver "
                    "one are different findings, and only a measurement separates them"),
            "focus_cuts_differ": True,
            "differing_span_seconds": [21.237, 31.669],
            "expected_span_seconds": [21.237, 31.669],
            "differing_sample_fraction": 0.2086,
            "waveform_correlation_inside_span": -0.1988,
            "conclusion": "the pack delivered exactly what it claimed to deliver",
        },
        "why_it_is_inaudible": {
            "mfcc_cosine": 1.0,
            "chroma_cosine": 1.0,
            "spectral_centroid_hz": [2791.7, 2795.3],
            "rms": [0.32505, 0.32379],
            "tempo_bpm": [92.29, 92.29],
            "measured": "after the verdict, to explain it rather than to produce it",
        },
        "the_measurement_defect_this_exposes": {
            "defect": ("the lane treated waveform correlation inside the replaced span as "
                       "evidence that the replacement is audible, and told the owner the "
                       "difference was obvious"),
            "why_it_is_wrong": ("correlation measures sample alignment. Two different bars of "
                                "the same loop-based section are uncorrelated and sound the "
                                "same, so a low correlation is consistent with an inaudible "
                                "edit and cannot distinguish the two"),
            "what_would_have_caught_it": ("a perceptual comparison of the replaced and "
                                          "replacing spans -- timbre, harmony, brightness, "
                                          "level -- before the pack was ever built"),
            "scope": ("this is a defect in how difference was argued, not in how the edit was "
                      "cut; the edit is confined to its declared span and every other sample "
                      "is bit-identical, which the gates already hold"),
        },
        "disposition": {
            "a1_01_album_master": "unaccepted",
            "retained_edit": "closed",
            "a1_01_status": "unsuccessful editing candidate",
            "mastering_authorized": False,
            "rights_or_release_permission": False,
            "album_one_accepted_masters": "1/7",
            "moves_album_counter": False,
            "further_revisions_of_this_edit": "none",
            "next_owner_facing_action": "none",
        },
        "album_state_after_this_verdict": {
            "a1_01": "closed on a musical result",
            "a1_02": "closed on a musical result",
            "a1_03": "chart-driven realization closed on a musical result",
            "a1_04": "source recordings absent",
            "a1_05": "source recordings absent",
            "a1_06": "unmaterialized",
            "a1_07": ("accepted master; system reference closed negative"),
            "executable_lanes_remaining": 0,
            "note": ("recorded because it is the state, not as an argument for authorizing "
                     "anything. No new program follows from a negative result"),
        },
        "pack_verified_against": pack["receipt_sha256"],
        "boundary": {"private_paths_included": False, "source_audio_exported": False,
                     "renders_remain_local": True},
        "new_organs_added": 0,
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"verdict {receipt['verdict']['outcome']} -- "
          f"{receipt['disposition']['a1_01_status']}")
    print(f"executable lanes remaining: "
          f"{receipt['album_state_after_this_verdict']['executable_lanes_remaining']}")
    print(f"receipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
