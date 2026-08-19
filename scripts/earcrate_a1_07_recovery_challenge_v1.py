"""Issue the A1-07 withheld-answer recovery challenge, with a control worth beating.

A1-07 has an accepted album master. It has no completed system reference, and those are
different claims: the first says the owner accepted this music, the second says EarCrate
could have found it. Reference Zero's Phase B is how the second one gets tested -- withhold
the accepted decisions, declare a naive control in advance, and require an inferred
candidate to blindly beat that control.

Three things have to exist before an inference attempt means anything, and this builds all
three.

**The gold receipt.** Reference Zero needs the owner's acceptance in its own schema. That
receipt is *transcribed*, never invented: this refuses to run unless the accepted score, the
render receipt, the sealed blind verdict, the monitoring ratification and the master
acceptance all agree by digest, and the eight dimension scores it records are the owner's
own from the sealed verdict.

**The control.** A challenge is only as strong as the thing it makes a candidate beat, and a
straw man proves nothing. So the control gets every cheap win that does not require the
answer: each source aligned to where its own music starts, level-matched, played together
for the full timeline. Those decisions are derivable from the challenge alone. What it does
not get is arrangement -- no section mapping, no progressive entry, no bar placement, no
pitch shift, no timing law -- because that is exactly what the candidate is supposed to
supply. The previously reviewed compound is excluded outright: it carries accepted
arrangement work, and a control holding part of the answer is not a control.

**The withheld key.** The challenge publishes source identities, the timeline and the gold
commitments. It does not publish a single clip decision.

Paths are arguments. Nothing here copies source audio, and no private path reaches the
public receipt.

    python scripts/earcrate_a1_07_recovery_challenge_v1.py \
        --session <a1-07-full-form-v1 session dir> --out <challenge dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate import reference_zero as rz  # noqa: E402
from earcrate.evidence.identity import seal  # noqa: E402

TRACK_ID = "A1-07"
CANDIDATE = "full-form-v1-native-pocket"
PUBLIC_RECEIPT = ROOT / "proofs" / "album_one" / "a1-07-recovery-challenge-v1.public.json"

# The compound is a previously reviewed object. It is not raw material, so it may not appear
# in a control that is supposed to hold no arrangement authority.
EXCLUDED_FROM_CONTROL = "gold_v6_reviewed_compound"

ONSET_FRAME = 1024                  # samples per RMS frame when finding where music starts
ONSET_THRESHOLD_DBFS = -40.0
CONTROL_FADE_SAMPLES = 480          # 10 ms at 48 kHz: click hygiene, not arrangement
CONTROL_CEILING_DBTP = -1.0         # four stems summed at unity clip; a control may not
HEADROOM_PROBE_GAIN_DB = -12.0      # measure the overshoot somewhere it cannot be clamped
REVIEWER_ID = "operator:owner"


class ChallengeError(RuntimeError):
    pass


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_accepted_lineage(session: Path) -> dict:
    """Refuse to transcribe an acceptance the evidence does not actually support.

    Five objects have to agree before a gold receipt may be written: the score, the render
    made from it, the sealed blind verdict that chose it, the monitoring verdict that
    accepted that render, and the master acceptance that names what the render became. If any
    link is missing the honest answer is that there is no gold to withhold.
    """
    candidate = session / "frontier" / "candidates" / CANDIDATE
    score = load(candidate / "performance-score.json")
    render_receipt = load(candidate / "render-a.receipt.json")
    verdict = load(session / "OWNER_VERDICT.sealed.json")
    ratification = load(session / "MONITORING_RATIFICATION.json")
    acceptance = load(session / "MASTER_ACCEPTANCE_VERDICT.json")

    score_sha = rz.validate_performance_score(score)
    receipt_sha = rz.validate_seal(render_receipt, kind="earcrate_performance_render_receipt")
    if render_receipt.get("score_sha256") != score_sha:
        raise ChallengeError("the render receipt does not belong to this score")

    render_pcm = render_receipt["output"]["canonical_pcm_sha256"]
    if acceptance["audited"]["source_canonical_pcm_sha256"] != render_pcm:
        raise ChallengeError(
            "the accepted master was not cut from this render: acceptance names "
            f"{acceptance['audited']['source_canonical_pcm_sha256'][:16]}, the render is "
            f"{render_pcm[:16]}")
    if ratification["disposition"].get("accepts_production_render") is not True:
        raise ChallengeError("the monitoring verdict did not accept the production render")
    if verdict.get("verdict") != "A":
        raise ChallengeError(f"the sealed blind verdict chose {verdict.get('verdict')!r}")
    for name, document in (("verdict", verdict), ("ratification", ratification),
                           ("acceptance", acceptance)):
        if document.get("descent_id") != "a1-07-full-form-v1":
            raise ChallengeError(f"the sealed {name} belongs to another descent")

    return {
        "score": score,
        "score_sha256": score_sha,
        "render_receipt": render_receipt,
        "render_receipt_sha256": receipt_sha,
        "render_canonical_pcm_sha256": render_pcm,
        "verdict": verdict,
        "ratification": ratification,
        "acceptance": acceptance,
        "candidate_directory": candidate,
    }


def transcribe_gold_receipt(lineage: dict) -> dict:
    """Put the owner's own acceptance into Reference Zero's schema, unchanged."""
    verdict = lineage["verdict"]
    winning = verdict["scores"][verdict["verdict"]]
    return rz.create_gold_receipt(
        lineage["score"], lineage["render_receipt"],
        reviewer_id=REVIEWER_ID, disposition="accept",
        dimensions={name: float(value) for name, value in sorted(winning.items())},
        notes=[
            "Transcribed, not newly given. Every value here is the owner's own.",
            "The blind frontier verdict selected this candidate before the letter map was "
            f"revealed; sealed verdict {verdict['verdict_sha256'][:16]}, total "
            f"{verdict['totals'][verdict['verdict']]}.",
            "The monitoring room accepted the production render this score produced and "
            "authorized mastering under stated constraints.",
            "The owner then accepted the mastered object by identity; that master was cut "
            "from this render's canonical PCM.",
            "If the owner disagrees that this is an accurate transcription, the challenge "
            "built on it is void and must be reissued.",
        ])


def decode(path: Path, *, sample_rate: int, channels: int) -> np.ndarray:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-ar", str(sample_rate), "-ac", str(channels), "-f", "f32le", "-c:a", "pcm_f32le", "-"],
        capture_output=True, timeout=3600)
    if result.returncode:
        raise ChallengeError(result.stderr.decode("utf-8", "replace")[-400:])
    return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, channels)


def first_music_sample(audio: np.ndarray) -> int:
    """Where this source's own music starts, by a stated threshold and nothing else."""
    mono = audio.mean(axis=1)
    usable = len(mono) - (len(mono) % ONSET_FRAME)
    if usable < ONSET_FRAME:
        return 0
    frames = mono[:usable].reshape(-1, ONSET_FRAME)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    threshold = 10.0 ** (ONSET_THRESHOLD_DBFS / 20.0)
    above = np.flatnonzero(rms > threshold)
    return int(above[0] * ONSET_FRAME) if len(above) else 0


def build_naive_control(lineage: dict, bindings: dict) -> tuple[dict, dict]:
    """Co-play, level-matched, aligned to each source's own downbeat-free start.

    Every decision below is derivable from the challenge: source identities, the timeline,
    and measurements of the sources themselves. None of it comes from the gold.
    """
    score = lineage["score"]
    timeline = score["timeline"]
    sample_rate, channels = int(timeline["sample_rate"]), int(timeline["channels"])
    duration = int(timeline["duration_samples"])

    paths = {row["source_id"]: Path(row["artifact_path"]) for row in bindings["bindings"]}
    used = [row for row in score["sources"] if row["source_id"] != EXCLUDED_FROM_CONTROL]
    if not used:
        raise ChallengeError("every source was excluded; there is nothing to co-play")

    measurements: dict[str, dict] = {}
    for row in used:
        source_id = row["source_id"]
        path = paths.get(source_id)
        if path is None or not path.is_file():
            raise ChallengeError(f"control cannot be built: {source_id} is not bound")
        audio = decode(path, sample_rate=sample_rate, channels=channels)
        start = first_music_sample(audio)
        if start + duration > len(audio):
            start = max(0, len(audio) - duration)
        if len(audio) < duration:
            raise ChallengeError(f"{source_id} is shorter than the timeline")
        loudness, true_peak = rz._measure_loudness(path)
        measurements[source_id] = {
            "first_music_sample": start,
            "first_music_seconds": round(start / sample_rate, 3),
            "integrated_lufs": round(loudness, 2),
            "true_peak_dbfs": round(true_peak, 2),
            "source_frames": int(len(audio)),
        }

    # Equal contribution, referenced to the quietest source, so no source is boosted.
    target = min(row["integrated_lufs"] for row in measurements.values())
    clips = []
    for row in used:
        source_id = row["source_id"]
        measured = measurements[source_id]
        gain = round(target - measured["integrated_lufs"], 2)
        measured["gain_db"] = gain
        clips.append({
            "clip_id": f"naive-coplay-{source_id}",
            "source_id": source_id,
            "source_start_sample": measured["first_music_sample"],
            "source_end_sample": measured["first_music_sample"] + duration,
            "target_start_sample": 0,
            "tempo_scale": 1.0,
            "pitch_semitones": 0.0,
            "gain_db": gain,
            "pan": 0.0,
            "fade_in_samples": CONTROL_FADE_SAMPLES,
            "fade_out_samples": CONTROL_FADE_SAMPLES,
            "musical_function": "co-play from the top, no arrangement decision of any kind",
        })

    control = rz.seal({
        "schema_version": rz.SCHEMA_VERSION,
        "kind": "earcrate_performance_score",
        "score_id": "album-one-a1-07-naive-coplay-control-v1",
        "title": "A1-07 naive co-play control",
        "created_at": rz.now_utc(),
        "timeline": {"sample_rate": sample_rate, "channels": channels,
                     "duration_samples": duration, "shared_events": []},
        "sources": [{k: v for k, v in row.items()} for row in used],
        "tracks": [{"track_id": "naive-coplay", "clips": clips}],
        "master": {"codec": "pcm_s24le", "gain_db": 0.0, "peak_limit_dbfs": None},
        "invariants": {
            "renderer_may_invent_decisions": False,
            "source_mutation_forbidden": True,
            "all_selected_clips_must_render": True,
        },
        "authority": {
            "control_for": "a1-07-recovery-challenge-v1",
            "derived_from_gold": False,
            "arrangement_decisions": 0,
        },
        "command_history": [{
            "sequence": 1,
            "command_id": "naive-coplay-v1",
            "description": ("align each source to its own first music above "
                            f"{ONSET_THRESHOLD_DBFS} dBFS, match integrated loudness to the "
                            "quietest, play them together for the whole timeline"),
        }],
    })
    rz.validate_performance_score(control)
    design = {
        "kind": "naive_co_play",
        "sources_used": sorted(row["source_id"] for row in used),
        "source_excluded": EXCLUDED_FROM_CONTROL,
        "exclusion_reason": ("it is a previously reviewed compound and carries accepted "
                             "arrangement work; a control holding part of the answer is not "
                             "a control"),
        "decisions_taken": [
            "align each source to its own first music above a stated RMS threshold",
            "match integrated loudness to the quietest source",
            "play every source for the whole timeline",
            "10 ms fades at the ends",
        ],
        "decisions_refused": [
            "section mapping", "progressive entry", "bar-level placement",
            "pitch shift", "tempo scaling", "phrase placement", "per-section gain",
        ],
        "every_decision_derivable_without_the_gold": True,
        "onset_threshold_dbfs": ONSET_THRESHOLD_DBFS,
        "loudness_reference": "quietest source, so nothing is boosted",
        "measurements": measurements,
    }
    return control, design


def solve_master_gain(score: dict, bindings: dict, *, work: Path,
                      ceiling_dbtp: float = CONTROL_CEILING_DBTP) -> tuple[dict, dict]:
    """Attenuate the master until nothing clips, and never boost.

    Four stems summed at unity overshoot full scale, and the renderer writes 24-bit PCM, so
    the overshoot becomes flat tops rather than headroom. A control that distorts is not a
    fair baseline -- the comparison would partly be about which option clips less. The fix is
    the same single solved linear gain A1-07's own mastering used: measured rather than
    chosen, and only ever downward, because boosting would be a decision the control is not
    allowed to make.
    """
    work.mkdir(parents=True, exist_ok=True)
    # Probe with the master pulled well down first. Measuring the unattenuated render would
    # measure a file whose samples the 24-bit write had already clamped, so the overshoot
    # would read as roughly zero and the solve could only ever recover the last dB of it.
    probe_body = {key: value for key, value in score.items() if key != "score_sha256"}
    probe_body["master"] = {**dict(probe_body["master"]), "gain_db": HEADROOM_PROBE_GAIN_DB}
    probe = rz.seal(probe_body)
    probe_bindings = rz.create_source_bindings(
        probe, paths={row["source_id"]: Path(row["artifact_path"])
                      for row in bindings["bindings"]}, verify_pcm=False)
    rz.render_performance_score(probe, probe_bindings, output_path=work / "probe.wav",
                                receipt_path=work / "probe.receipt.json")
    _, probe_peak = rz._measure_loudness(work / "probe.wav")
    true_peak = probe_peak - HEADROOM_PROBE_GAIN_DB
    gain = min(0.0, round(ceiling_dbtp - true_peak, 2))
    body = {key: value for key, value in score.items() if key != "score_sha256"}
    body["master"] = {**dict(body["master"]), "gain_db": gain}
    solved = rz.seal(body)
    rz.validate_performance_score(solved)
    return solved, {"measured_true_peak_dbtp": round(true_peak, 2),
                    "probe_gain_db": HEADROOM_PROBE_GAIN_DB,
                    "probe_true_peak_dbtp": round(probe_peak, 2),
                    "ceiling_dbtp": ceiling_dbtp,
                    "solved_master_gain_db": gain,
                    "boost_refused": True,
                    "solved_from": "measured true peak, not chosen"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--skip-control-render", action="store_true",
                        help="build and seal the control without rendering it twice")
    parser.add_argument("--reissue", action="store_true",
                        help="replace an already-issued challenge, retiring its digest")
    args = parser.parse_args()

    session = args.session.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Issuance is an event, not a build step. The gold receipt and the challenge carry
    # timestamps, so a second run mints different digests -- which would silently retire a
    # challenge an inference attempt may already be answering.
    existing = out / "recovery-challenge.source-free.json"
    if existing.is_file() and not args.reissue:
        raise ChallengeError(
            f"a challenge is already issued here ({load(existing)['challenge_sha256'][:16]}); "
            "pass --reissue to deliberately replace it")
    if args.reissue:
        # A reissue replaces the derived renders too. Leaving the old ones would let a new
        # challenge point at audio the retired control produced.
        for stale in ("control-render", "control-headroom"):
            shutil.rmtree(out / stale, ignore_errors=True)

    print("verifying the accepted lineage ...")
    lineage = verify_accepted_lineage(session)
    print(f"  score            {lineage['score_sha256'][:16]}")
    print(f"  render receipt   {lineage['render_receipt_sha256'][:16]}")
    print(f"  render pcm       {lineage['render_canonical_pcm_sha256'][:16]}  "
          "(the master was cut from this)")

    gold_receipt = transcribe_gold_receipt(lineage)
    rz.write_json(out / "gold-receipt.private.json", gold_receipt)
    print(f"  gold receipt     {gold_receipt['gold_receipt_sha256'][:16]}  (transcribed)")

    print("building the naive co-play control ...")
    bindings = load(lineage["candidate_directory"] / "source-bindings.private.json")
    control, design = build_naive_control(lineage, bindings)
    rz.write_json(out / "naive-control-score.json", control)
    for source_id, row in design["measurements"].items():
        print(f"  {source_id:<24} starts {row['first_music_seconds']:>6.3f}s  "
              f"{row['integrated_lufs']:>7.2f} LUFS  gain {row['gain_db']:+.2f} dB")

    control_render = None
    if not args.skip_control_render:
        print("rendering the control twice ...")
        paths = {row["source_id"]: Path(row["artifact_path"]) for row in bindings["bindings"]
                 if row["source_id"] != EXCLUDED_FROM_CONTROL}
        control_bindings = rz.create_source_bindings(control, paths=paths, verify_pcm=True)
        control, headroom = solve_master_gain(control, control_bindings,
                                              work=out / "control-headroom")
        design["headroom"] = headroom
        print("  true peak {:+.2f} dBTP -> master {:+.2f} dB for a {:+.1f} dBTP ceiling".format(
            headroom["measured_true_peak_dbtp"], headroom["solved_master_gain_db"],
            headroom["ceiling_dbtp"]))
        rz.write_json(out / "naive-control-score.json", control)
        control_bindings = rz.create_source_bindings(control, paths=paths, verify_pcm=True)
        rz.write_json(out / "control-bindings.private.json", control_bindings)
        control_render = rz.verify_reproduction(control, control_bindings,
                                                output_directory=out / "control-render")
        print(f"  identical canonical PCM: {control_render['ok']}  "
              f"{control_render['canonical_pcm_sha256'][:16]}  "
              f"(container identical: {control_render['container_byte_identity']})")
        if not control_render["ok"]:
            raise ChallengeError("the control does not reproduce; it cannot be a control")

    print("issuing the challenge ...")
    challenge = rz.create_recovery_challenge(
        lineage["score"], gold_receipt, control_score_sha256=control["score_sha256"])
    rz.write_json(out / "recovery-challenge.source-free.json", challenge)
    print(f"  challenge        {challenge['challenge_sha256'][:16]}")
    print(f"  clip decisions published: "
          f"{challenge['withheld_answer_key']['clip_decisions_published']}")

    leaked = [key for key in ("tracks", "clips") if key in challenge]
    if leaked:
        raise ChallengeError(f"the challenge leaks the answer: {leaked}")

    receipt = seal({
        "kind": "earcrate_a1_07_public_recovery_challenge_receipt",
        "schema_version": 1,
        "track_id": TRACK_ID,
        "headline": ("The A1-07 withheld-answer recovery challenge is issued, with a naive "
                     "co-play control that was declared before any inference ran."),
        "challenge": {
            "challenge_id": challenge["challenge_id"],
            "challenge_sha256": challenge["challenge_sha256"],
            "gold_score_sha256": challenge["withheld_answer_key"]["gold_score_sha256"],
            "gold_receipt_sha256": challenge["withheld_answer_key"]["gold_receipt_sha256"],
            "gold_render_pcm_commitment":
                challenge["withheld_answer_key"]["gold_render_pcm_sha256_commitment"],
            "clip_decisions_published": False,
            "control_score_sha256": challenge["control_score_sha256"],
            "review_dimensions": challenge["review_dimensions"],
            "acceptance": challenge["acceptance"],
            "source_count": len(challenge["source_identities"]),
            "timeline": challenge["timeline"],
        },
        "gold_receipt_is_a_transcription": {
            "transcribed": True,
            "invented": False,
            "sealed_blind_verdict_sha256": lineage["verdict"]["verdict_sha256"],
            "winning_total": lineage["verdict"]["totals"][lineage["verdict"]["verdict"]],
            "monitoring_accepted_production_render": True,
            "master_cut_from_this_render": True,
            "void_if_owner_disputes_the_transcription": True,
        },
        "control": {key: value for key, value in design.items()
                    if key not in {"measurements", "headroom"}},
        "control_headroom": design.get("headroom"),
        "control_score_sha256": control["score_sha256"],
        "control_reproduces_identically": None if control_render is None
            else control_render["ok"],
        "control_canonical_pcm_sha256": None if control_render is None
            else control_render["canonical_pcm_sha256"],
        "authority": {
            "album_master_accepted": True,
            "system_reference_completed": False,
            "inference_attempted": False,
            "candidate_beat_control": False,
            "rights_or_release_permission": False,
        },
        "boundary": {
            "private_paths_included": False,
            "source_audio_exported": False,
            "clip_decisions_exported": False,
            "control_measurements_exported": False,
        },
        "next_musical_action": (
            "Attempt inference against this challenge and nothing else. A candidate passes "
            "only by blindly beating the control; a tie, a reject-all, or the control "
            "winning terminates that candidate lineage and leaves the system reference "
            "open."),
    }, "receipt_sha256")

    PUBLIC_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\nreceipt {receipt['receipt_sha256'][:16]} -> "
          f"{PUBLIC_RECEIPT.relative_to(ROOT).as_posix()}")
    print("the answer key is withheld; the control is declared; inference may now run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
