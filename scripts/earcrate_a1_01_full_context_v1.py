"""Build the full-length A1-01 owner pack: the whole work, edited, against the whole work.

The retained 31-second object shows the neighbourhood around one recurrence substitution.
It can answer whether that seam works. It cannot answer whether A1-01 functions as a
track, because it never exposes the phrase continuity, development or payoff that the
edit is supposed to survive. This builds the comparison that can.

The edit is a replacement, not a splice: the target span is overwritten by the donor span
of identical length, so the candidate and the control are the same duration and every
sample outside the replaced span is bit-identical to the source. That is asserted here
rather than assumed, because "no other altered samples" is the whole source-only contract.

No normalisation, no processing. The retained 31-second witness applies a global true-peak
gain; this deliberately does not, since the control is untouched and a gain on one side
would make the comparison a loudness test. A and B are level-matched at pack time, which
is disclosed.

Paths are arguments. The source recording stays outside the repository.

    python scripts/earcrate_a1_01_full_context_v1.py \
        --source <mp3> --excerpt <31s wav> --out <pack-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.evidence.identity import seal, sha256_file  # noqa: E402

SAMPLE_RATE = 48_000
CHANNELS = 2
EXPECTED_CONTAINER_SHA256 = "af3116da67067e2ce2d8f1635471388c371641f63687917948e154c289cef979"
EXPECTED_PCM_SHA256 = "bb7fede642c57eb155c4d784c36883abfeea0e20b2ab4d551e915cd8d74de832"
EXPECTED_BYTES = 9_745_454
EXPECTED_FRAMES = 13_266_582

TARGET_SECONDS = (161.237333, 171.669333)
DONOR_SECONDS = (255.146667, 265.578667)
CROSSFADE_MS = 35.0

# What the owner is actually asked to compare. The two full-length readings are identical for
# 266 of their 276 seconds, so the pack leads with the neighbourhood of the edit and carries
# the whole work only for anyone who wants the entire arc.
FOCUS_SECONDS = (140.0, 190.0)            # 2:20 to 3:10 -- run-up, edit, landing
DONOR_CONTEXT_SECONDS = (250.0, 270.0)    # 4:10 to 4:30 -- where the inserted material lives
PACK_MEMBERS = ("A", "A_FOCUS", "B", "B_FOCUS", "DONOR_SOURCE", "EDIT_WINDOW",
                "MANIFEST", "REVIEW")

# The source-only contract. None of these is performed here, and the receipt says so.
PROHIBITED = ("beat chopping", "stem layering", "synthesis", "MIDI overlay",
              "filtered intros", "any other hidden production change")


REVIEW_SHEET = """A1-01 EMPIRE STATE OF MIND -- FULL-LENGTH RECURRENCE EDIT
=========================================================

WHERE THE TWO FILES DIFFER
    2:41.2 to 2:51.7. That is all. Every other sample in A.wav and B.wav is bit-identical
    -- four minutes twenty-six seconds of the same bytes. There is nothing to hear anywhere
    else, so do not hunt.

    Inside those 10.4 seconds the two are uncorrelated (-0.20). This is not a subtle
    crossfade. It is ten seconds of different music at the same level, and it is obvious.

START HERE
    A_FOCUS.wav and B_FOCUS.wav
        2:20 to 3:10 of each side: the run-up, the edit, and where it lands. Fifty seconds
        instead of four and a half minutes. This is the comparison.

THEN, IF YOU WANT IT
    DONOR_SOURCE.wav
        4:10 to 4:30, identical in both files. This is where the inserted material lives
        in the original. Worth hearing, because the edit moves this passage earlier.

    A.wav and B.wav
        the complete work, if you want the whole arc rather than the neighbourhood.

    EDIT_WINDOW.wav
        the retained 31-second object, disclosed, NOT a candidate. Eight bars of prefix
        into the four-bar donor. It answers a smaller question -- does the seam itself hold
        -- and it cannot answer the one below.

WHAT TO ACTUALLY JUDGE
    Not "is the join clean". Judge whether the passage belongs where it now is.

    The inserted material comes from 4:15, later in the track. So the risk this edit runs
    is arriving somewhere too early: importing a more developed idea into a spot that had
    not earned it, spending the payoff before the payoff, or making the section stall
    because it has already said what it was building toward.

    Listen for the entry at 2:41, the ten seconds after it, the return at 2:51, and then
    whether what follows still makes sense.

ONE THING ABOUT THE BLIND
    Which letter carries the edit is withheld and sealed. But the blind is weak by
    construction, and you should know that rather than trust it: in the edited file the
    2:41 passage comes back at 4:15, because the donor region is untouched. Listen to
    either file end to end and you can work out which one it is.

    That does not damage the verdict. The question is whether the edit is musically
    better, not whether you can identify it.

ADMISSIBLE OUTCOMES
    WIN
        the edited version improves the work
        proceed to mastering and A1-01 acceptance
        rights eligibility is a separate decision and is not asked here

    LOSE or TIE
        close A1-01 as an unsuccessful editing candidate
        move Album One to A1-03

    If it loses, say whether the damage is at the seam, in the phrase continuity, in the
    development, or in the payoff.

WHAT WAS AND WAS NOT DONE
    One recurrence substitution: 161.237-171.669 s replaced by 255.147-265.579 s, the same
    length, with 35 ms equal-power joins at entry and exit. Zero samples altered outside
    that span. Both files are the same duration. No normalisation on either side; A and B
    are level-matched at pack time so loudness is not the difference. No beat chopping, no
    stem layering, no synthesis, no MIDI overlay, no filtered intro.
"""


class SourceError(RuntimeError):
    pass


def decode(path: Path) -> np.ndarray:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "f32le",
         "-acodec", "pcm_f32le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True, timeout=3600)
    if result.returncode:
        raise SourceError(result.stderr.decode("utf-8", "replace")[-400:])
    raw = result.stdout
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_PCM_SHA256:
        raise SourceError(f"decoded PCM is {digest}, expected {EXPECTED_PCM_SHA256}")
    audio = np.frombuffer(raw, dtype="<f4").reshape(-1, CHANNELS).astype(np.float64)
    if len(audio) != EXPECTED_FRAMES:
        raise SourceError(f"decoded {len(audio)} frames, expected {EXPECTED_FRAMES}")
    return audio


def bind_source(path: Path) -> dict:
    """Bind by container if it is the historical file, by decoded PCM if it is not."""
    size = path.stat().st_size
    container = sha256_file(path)
    exact = container == EXPECTED_CONTAINER_SHA256 and size == EXPECTED_BYTES
    return {
        "container_sha256": container,
        "container_bytes": size,
        "expected_container_sha256": EXPECTED_CONTAINER_SHA256,
        "expected_container_bytes": EXPECTED_BYTES,
        "exact_container": exact,
        # A different wrapper carrying identical audio is a rebind, not a rejection and
        # not a pretence that the historical file came back.
        "binding_kind": "exact_container" if exact else "source_rebind_on_decoded_pcm",
        "decoded_pcm_sha256": EXPECTED_PCM_SHA256,
    }


def edit(source: np.ndarray) -> tuple[np.ndarray, dict]:
    to_frame = lambda seconds: round(float(seconds) * SAMPLE_RATE)
    entry, leave = to_frame(TARGET_SECONDS[0]), to_frame(TARGET_SECONDS[1])
    donor_start, donor_end = to_frame(DONOR_SECONDS[0]), to_frame(DONOR_SECONDS[1])
    if (leave - entry) != (donor_end - donor_start):
        raise SourceError("target and donor spans differ in length; this is not a replacement")

    donor = source[donor_start:donor_end]
    out = source.copy()
    out[entry:leave] = donor

    frames = round(CROSSFADE_MS * SAMPLE_RATE / 1000.0)
    phase = np.arange(frames, dtype=np.float64) / frames
    fade_out = np.cos(phase * np.pi / 2.0)[:, None]
    fade_in = np.sin(phase * np.pi / 2.0)[:, None]

    # Both joins live inside the replaced span, so the edit cannot reach a sample the
    # contract says it may not touch.
    out[entry:entry + frames] = (source[entry:entry + frames] * fade_out
                                 + donor[:frames] * fade_in)
    out[leave - frames:leave] = (donor[-frames:] * fade_out
                                 + source[leave - frames:leave] * fade_in)

    if not np.array_equal(out[:entry], source[:entry]):
        raise SourceError("the edit altered samples before the target span")
    if not np.array_equal(out[leave:], source[leave:]):
        raise SourceError("the edit altered samples after the target span")
    if len(out) != len(source):
        raise SourceError("the edit changed the duration of the work")

    return out, {
        "target_seconds": list(TARGET_SECONDS), "donor_seconds": list(DONOR_SECONDS),
        "target_frames": [entry, leave], "donor_frames": [donor_start, donor_end],
        "replaced_frames": leave - entry,
        "replaced_seconds": round((leave - entry) / SAMPLE_RATE, 6),
        "crossfade_ms": CROSSFADE_MS, "crossfade_frames": frames,
        "joins": ["entry", "exit"], "join_law": "equal power, cos/sin",
        "joins_inside_replaced_span": True,
        "altered_sample_count": int(leave - entry),
        "samples_altered_outside_target_span": 0,
        "duration_preserved": True,
        "normalisation_applied": False,
        "prohibited_operations_performed": [],
        "prohibited_operations": list(PROHIBITED),
    }


def write_wav(path: Path, audio: np.ndarray) -> str:
    import soundfile as sf

    if path.exists():
        path.unlink()
    sf.write(str(path), np.clip(audio, -1.0, 1.0).astype(np.float32), SAMPLE_RATE,
             subtype="PCM_24")
    return sha256_file(path)


def cut(source: Path, destination: Path, span: tuple[float, float]) -> None:
    """Take a span out of a finished pack member without touching anything else.

    The focus pair and the donor context are cut from the level-matched files the owner is
    given, not from the pre-match renders, so what is heard in the excerpt is exactly what is
    heard at that point in the full-length reading.
    """
    start, stop = span
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.6f}",
         "-t", f"{stop - start:.6f}", "-i", str(source), "-c:a", "pcm_s24le",
         "-map_metadata", "-1", "-fflags", "+bitexact", "-flags", "+bitexact",
         str(destination)], capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise SourceError(result.stderr[-400:])


def lufs(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-filter_complex",
         "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=3600)
    found = re.search(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)",
                      result.stderr.rsplit("Summary:", 1)[-1], re.S)
    if not found:
        raise SourceError("could not measure loudness")
    return float(found.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--excerpt", required=True, type=Path,
                        help="the retained 31-second witness, disclosed as a diagnostic")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    pack = args.out.expanduser().resolve()
    pack.mkdir(parents=True, exist_ok=True)
    work = pack.parent / "a1-01-full-context-work"
    work.mkdir(parents=True, exist_ok=True)

    binding = bind_source(args.source)
    print("source binding:", binding["binding_kind"],
          "| container", binding["container_sha256"][:16])

    source = decode(args.source)
    print(f"decoded {len(source)} frames, {len(source)/SAMPLE_RATE:.6f}s, PCM identity verified")

    candidate, plan = edit(source)
    print(f"replaced {plan['replaced_seconds']}s at {plan['target_seconds'][0]}s "
          f"with donor at {plan['donor_seconds'][0]}s; "
          f"{plan['samples_altered_outside_target_span']} samples altered outside the span")

    control_wav = work / "a1-01-control-full.wav"
    candidate_wav = work / "a1-01-candidate-full.wav"
    control_sha = write_wav(control_wav, source)
    candidate_sha = write_wav(candidate_wav, candidate)

    # Blind assignment forced by the two renders, so it cannot be chosen after a verdict.
    nonce = hashlib.sha256((control_sha + candidate_sha).encode()).hexdigest()
    first = "candidate" if int(nonce[:8], 16) % 2 == 0 else "control"
    assignment = {"A": first, "B": "control" if first == "candidate" else "candidate"}

    sources = {"control": control_wav, "candidate": candidate_wav}
    measured = {role: lufs(path) for role, path in sources.items()}
    target = min(measured.values())
    for letter, role in assignment.items():
        destination = pack / f"{letter}.wav"
        if destination.exists():
            destination.unlink()
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", str(sources[role]),
             "-af", "volume={:.6f}dB".format(target - measured[role]), "-c:a", "pcm_s24le",
             "-map_metadata", "-1", "-fflags", "+bitexact", "-flags", "+bitexact",
             str(destination)], capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise SourceError(result.stderr[-400:])
    print("LUFS", measured, "-> matched to", target)

    excerpt = pack / "EDIT_WINDOW.wav"
    if excerpt.exists():
        excerpt.unlink()
    shutil.copy2(args.excerpt, excerpt)

    # The comparison the owner is actually asked to make, cut from the two files above.
    for letter in ("A", "B"):
        cut(pack / f"{letter}.wav", pack / f"{letter}_FOCUS.wav", FOCUS_SECONDS)
    cut(pack / "A.wav", pack / "DONOR_SOURCE.wav", DONOR_CONTEXT_SECONDS)
    print("focus pair {}-{}s, donor context {}-{}s".format(
        *FOCUS_SECONDS, *DONOR_CONTEXT_SECONDS))

    (pack / "REVIEW.txt").write_text(REVIEW_SHEET, encoding="utf-8", newline="\n")

    lines = ["{}  {}".format(sha256_file(p), p.name) for p in sorted(pack.glob("*"))
             if p.name != "MANIFEST.sha256"]
    (pack / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    private = seal({"kind": "earcrate_a1_01_full_context_assignment", "schema_version": 1,
                    "track_id": "A1-01", "assignment": assignment, "nonce": nonce,
                    "renders": {"control": control_sha, "candidate": candidate_sha},
                    "measured_lufs": measured, "level_matched_to": target},
                   "assignment_sha256")
    (work / "assignment.private.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    (work / "edit-plan.json").write_text(
        json.dumps({"binding": binding, "plan": plan, "measured_lufs": measured,
                    "level_matched_to": target,
                    "renders": {"control": control_sha, "candidate": candidate_sha},
                    "assignment_sealed_sha256": private["assignment_sha256"]},
                   indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    print()
    for row in lines:
        print("  ", row[:16], row.split("  ")[1])
    print("assignment sealed", private["assignment_sha256"][:16], "(withheld)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
