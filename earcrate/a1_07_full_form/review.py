"""Build the A1-07 full-form owner pack.

The pack is blind in exactly one respect: which letter carries which timing law.
Everything else is disclosed, because the owner should never have to infer the
mechanism from the audio — that is how a frontier turns into a guessing game. The
label map lives in the private authority file, and the cut notes state what varies,
what is invariant, and which defects are inherited rather than candidate-specific.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import secrets
from typing import Any, Mapping, Sequence

from ..a1_07_gold_v8 import common as c
from ..a1_07_gold_v8.review import level_match

LETTERS = ("A", "B", "C", "D", "E")


def _assign_letters(candidate_ids: Sequence[str], nonce: str) -> dict[str, str]:
    """Permute the review labels under a nonce held only in private custody.

    Seeding this from anything tracked -- the contract seal, a fixed table --
    would make the mapping recomputable by anyone holding the repository, which
    is not a blind. The nonce is generated once per pack, never leaves
    `authority.json`, and is recorded there so the permutation stays auditable
    after the verdict is sealed.
    """
    ranked = sorted(
        candidate_ids,
        key=lambda cid: hashlib.sha256(f"{nonce}:{cid}".encode("utf-8")).hexdigest())
    return {cid: LETTERS[index] for index, cid in enumerate(ranked)}


def write_review_pack(
    frontier: Path,
    contract: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    incumbent_audio: Path,
    *,
    ffmpeg: str,
) -> dict[str, Any]:
    normalization = contract["review_normalization"]
    target = float(normalization["target_lufs"])
    ceiling = float(normalization["peak_ceiling_dbfs"])
    policy = contract["review_policy"]

    public = frontier / "review" / "public"
    private = frontier / "review" / "private"
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)

    nonce = secrets.token_hex(16)
    labels = _assign_letters([str(row["candidate_id"]) for row in candidates], nonce)
    options: dict[str, Any] = {}
    authority_rows: list[dict[str, Any]] = []
    for row in candidates:
        candidate_id = str(row["candidate_id"])
        letter = labels[candidate_id]
        source = Path(row["artifacts"]["render_a"])
        destination = public / f"{letter}.flac"
        match = level_match(source, destination, target_lufs=target,
                            peak_ceiling=ceiling, ffmpeg=ffmpeg)
        if match["output_peak_dbfs"] > ceiling + 0.05:
            raise c.DescentError(
                f"review cut {letter} exceeds the peak ceiling: {match['output_peak_dbfs']} dBFS")
        options[letter] = {
            "media_kind": "audio/flac",
            "sha256": match["sha256"],
            "bytes": match["bytes"],
            "duration_seconds": row["duration_seconds"],
            "output_lufs": match["output_lufs"],
            "output_peak_dbfs": match["output_peak_dbfs"],
        }
        authority_rows.append({
            "review_label": letter,
            "candidate_id": candidate_id,
            "timing_law": row["label"],
            "canonical_pcm_sha256": row["canonical_pcm_sha256"],
            "score_sha256": row["score_sha256"],
            "level_match": match,
        })

    # The control: the strongest retained incumbent, level matched the same way.
    incumbent_letter = "INCUMBENT"
    incumbent_cut = public / "INCUMBENT.flac"
    incumbent_match = level_match(incumbent_audio, incumbent_cut, target_lufs=target,
                                  peak_ceiling=ceiling, ffmpeg=ffmpeg)
    options[incumbent_letter] = {
        "media_kind": "audio/flac",
        "sha256": incumbent_match["sha256"],
        "bytes": incumbent_match["bytes"],
        "output_lufs": incumbent_match["output_lufs"],
        "output_peak_dbfs": incumbent_match["output_peak_dbfs"],
        "role": "retained incumbent control (gold-v7 arc, 38.15 s, NOT full form)",
        "disclosed": True,
    }

    assignment = c.seal({
        "kind": "earcrate_a1_07_full_form_public_review_assignment",
        "schema_version": 1,
        "track_id": "A1-07",
        "descent_id": contract["descent_id"],
        "contract_sha256": contract["contract_sha256"],
        "dimensions": list(policy["dimensions"]),
        "choices": list(policy["choices"]),
        "options": options,
        "instructions": (
            "Listen level matched. Every option is the same 45-120 s form built from the same "
            "sources, the same Frankie rows and the same authored body. The ONLY thing that varies "
            "between the lettered options is the donor-band timing law. INCUMBENT is the retained "
            "38.15 s gold-v7 arc, disclosed, for reference only - it is not a full-form option. "
            "Choose the option whose groove survives setup, development and payoff."),
        "invariants_disclosed": {
            "frankie_rows_identical": True,
            "authored_body_identical": True,
            "payoff_sample_identical_to_gold_v6": True,
            "sources_identical": True,
            "form_identical": True,
            "varying_mechanism": policy["single_varying_mechanism"],
        },
        "known_inherited_defects": contract["known_inherited_defects"],
        "acceptance": {
            "relative_preference_does_not_equal_album_acceptance": True,
            "reject_all_closes_family": True,
        },
    }, "assignment_sha256")
    c.atomic_write_json(public / "assignment.json", assignment)

    authority = c.seal({
        "kind": "earcrate_a1_07_full_form_private_review_authority",
        "schema_version": 1,
        "descent_id": contract["descent_id"],
        "assignment_sha256": assignment["assignment_sha256"],
        "label_nonce": nonce,
        "label_nonce_note": "The permutation seed. It exists nowhere else, so the mapping "
                            "cannot be recomputed from the repository. Reveal only after the "
                            "verdict is sealed.",
        "label_map": authority_rows,
        "incumbent": {"artifact_path": str(incumbent_audio), "level_match": incumbent_match},
        "visibility": "private",
    }, "authority_sha256")
    c.atomic_write_json(private / "authority.json", authority)

    lines = [
        "# A1-07 FULL-FORM v1 - CUT NOTES",
        "",
        "You are not being asked to guess what changed.",
        "",
        f"Every lettered option is the same {contract['form']['declared_total_seconds']} s form:",
        "setup (the retained quiet-to-crescendo build), body (newly authored Frankie development),",
        "payoff (the protected gold-v6 compound, sample identical).",
        "",
        "**The only thing that differs between the letters is the donor-band timing law.**",
        "Frankie's rows, the authored body, the sources and the form are byte-identical across all of them.",
        "",
        "## What each timing law does",
        "",
    ]
    for row in contract["timing_laws"]:
        lines.extend([
            f"- **{row['label']}** - {row['mechanism']}",
            f"  - tempo rule: {row['tempo_rule']}",
            f"  - phase rule: {row['phase_rule']}",
            f"  - why it exists: {row['why']}",
            "",
        ])
    lines.extend([
        "(Which letter carries which law is withheld only so the letters do not anchor your ranking.)",
        "",
        "## Reference",
        "",
        "`INCUMBENT.flac` is the retained gold-v7 arc. It is disclosed, it is 38.15 s, and it is NOT",
        "a full-form option. It is there so you can hear what the lane sounded like before it had a body.",
        "",
        "## Signal conditions - so you do not mistake one for a musical difference",
        "",
    ])
    for row in contract["known_inherited_defects"]:
        lines.extend([
            f"- {row['defect']}",
            f"  - inherited from: {row['inherited_from']}",
            f"  - status in these options: {row.get('status', 'present')} - {row.get('how', row['affects'])}",
            "",
        ])
    lines.extend([
        "Every lettered option renders with **zero** full-scale runs and is delivered at a matched",
        "level. Loudness and clipping are therefore not variables between A, B and C.",
        "",
    ])
    lines.extend([
        "## What your answer means",
        "",
        "A winner promotes that timing law to the first owner frontier. A tie is a real answer.",
        "`reject_all` closes this timing and arrangement family and justifies changing organs rather",
        "than generating another nearby timing specimen. Preference is not album acceptance.",
        "",
    ])
    (public / "CUT_NOTES.md").write_text("\n".join(lines), encoding="utf-8")

    return {
        "assignment_sha256": assignment["assignment_sha256"],
        "authority_sha256": authority["authority_sha256"],
        "public": str(public),
        "private": str(private),
        "options": sorted(options),
    }
