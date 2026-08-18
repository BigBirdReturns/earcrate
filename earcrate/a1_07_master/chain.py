"""Deterministic mastering chain for the accepted A1-07 frontier.

The monitoring verdict asked for four things: preserve the macro-dynamics, do not
turn vocal authority into hardness, leave the kick/bass relationship alone, and
keep true-peak headroom. Measurement showed the source already sits at -3.5 dBTP,
so reaching a -1.0 dBTP ceiling needs +2.5 dB and nothing else. The chain is
therefore a single linear gain: no limiter, no EQ, no multiband, no resampling.

That is not minimalism for its own sake. A linear gain is order-preserving, so the
8.5 LU span across setup, body and payoff survives exactly rather than
approximately, and nothing is introduced that could harden the upper mids or move
the low end.

Dither is deliberately omitted. Dither is stochastic, so it would break canonical
PCM equality between the two required executions -- the very property that makes a
master verifiable. Requantizing 24-bit to 24-bit after a gain leaves truncation
artefacts near -144 dBFS, far below any playback noise floor, so determinism is
worth more here than a theoretical improvement nobody can hear.

Two conditions refuse rather than proceed, because either one would quietly
reintroduce the thing the verdict ruled out:

* a source that is already hard-clipped, where attenuation cannot restore samples
  destroyed before mastering began; and
* a requested loudness target needing more gain than the peak ceiling allows,
  which can only be reached by limiting.

Neither is a rare hypothetical. The second is the ordinary streaming-normalization
ask: -14 LUFS from this -16.8 LUFS source wants +2.8 dB, and the ceiling grants
+2.5 dB. That 0.3 dB is exactly where a limiter gets added by accident, so the
shortfall raises rather than warns.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..a1_07_full_form.peaks import peak_conditions
from ..a1_07_gold_v8 import common as c

# The ceiling the monitoring verdict set. Held here as the default rather than
# scattered through callers, and always restated in the plan it produces.
CEILING_DBTP = -1.0

# Anything that would make the transfer non-linear, stochastic, or resampled.
FORBIDDEN_FILTERS = (
    "alimiter", "acompressor", "compand", "loudnorm", "dynaudnorm", "speechnorm",
    "equalizer", "firequalizer", "superequalizer", "bass=", "treble=", "highpass",
    "lowpass", "aresample", "asoftclip", "aexciter", "crossfeed", "adeclip",
)

_LINEAR_CHAIN = re.compile(r"^volume=-?\d+(?:\.\d+)?dB$")

# A loudness target within this much of the available headroom counts as reachable.
LOUDNESS_TOLERANCE_DB = 0.01


class MasteringError(RuntimeError):
    pass


def measure(path: Path, *, ffmpeg: str = "ffmpeg",
            start: float | None = None, end: float | None = None) -> dict[str, float]:
    """Integrated loudness and true peak, from the ebur128 summary block."""
    argv = [ffmpeg, "-nostdin", "-hide_banner"]
    if start is not None:
        argv += ["-ss", f"{start:.6f}"]
    if end is not None:
        argv += ["-to", f"{end:.6f}"]
    argv += ["-i", str(path), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"]
    result = c.run(argv, timeout=1800)
    tail = result.stderr.rsplit("Summary:", 1)[-1]
    loudness = re.search(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", tail, re.S)
    peak = re.search(r"True peak:\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", tail, re.S)
    if not loudness:
        raise MasteringError(f"could not measure loudness: {path}")
    return {"integrated_lufs": float(loudness.group(1)),
            "true_peak_dbtp": float(peak.group(1)) if peak else float("nan")}


def solve_gain(source: Path, *, ceiling_dbtp: float = CEILING_DBTP,
               target_lufs: float | None = None, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """The exact gain that lands the true peak on the ceiling.

    Solved from measurement rather than chosen, so the plan states what the source
    actually was. A loudness target is optional and is *checked*, never chased: the
    applied gain is always the peak-solved one, and a target that outruns the
    available headroom sets `limiting_required` for `refuse_if_limiting` to stop on.
    """
    observed = measure(source, ffmpeg=ffmpeg)
    peak = observed["true_peak_dbtp"]
    if peak != peak:  # NaN: the summary block carried no true-peak line
        raise MasteringError(f"no true peak measurement for {source}; cannot solve a ceiling")
    gain = ceiling_dbtp - peak

    plan: dict[str, Any] = {
        "source_integrated_lufs": observed["integrated_lufs"],
        "source_true_peak_dbtp": peak,
        "ceiling_dbtp": ceiling_dbtp,
        "solved_gain_db": round(gain, 6),
        "available_headroom_db": round(gain, 6),
        "projected_integrated_lufs": round(observed["integrated_lufs"] + gain, 2),
        "loudness_target_lufs": target_lufs,
        "loudness_target_gain_db": None,
        "loudness_shortfall_db": None,
        "limiting_required": False,
    }
    if target_lufs is not None:
        wanted = target_lufs - observed["integrated_lufs"]
        plan["loudness_target_gain_db"] = round(wanted, 6)
        plan["loudness_shortfall_db"] = round(wanted - gain, 6)
        plan["limiting_required"] = bool(wanted > gain + LOUDNESS_TOLERANCE_DB)
    return plan


def refuse_if_limiting(plan: Mapping[str, Any]) -> None:
    """Stop before a limiter can be introduced to close a loudness gap."""
    if not plan.get("limiting_required"):
        return
    raise MasteringError(
        f"reaching {plan['loudness_target_lufs']} LUFS needs "
        f"{plan['loudness_target_gain_db']:+.2f} dB but the {plan['ceiling_dbtp']} dBTP ceiling "
        f"allows {plan['solved_gain_db']:+.2f} dB, a shortfall of "
        f"{plan['loudness_shortfall_db']:.2f} dB. Closing that gap requires limiting, which the "
        "monitoring verdict ruled out. Lower the loudness target or re-open the verdict.")


def refuse_if_source_is_clipped(source: Path, *, sample_rate: int, channels: int,
                                ffmpeg: str = "ffmpeg",
                                ffprobe: str = "ffprobe") -> dict[str, Any]:
    """A hard-clipped source cannot be rescued by a gain stage downstream of it."""
    conditions = peak_conditions(source, sample_rate=sample_rate, channels=channels,
                                 ffmpeg=ffmpeg, ffprobe=ffprobe)
    if conditions["hard_clipped"]:
        raise MasteringError(
            f"source is hard-clipped ({conditions['flat_top_sample_count']} pinned samples in "
            f"{conditions['flat_top_run_count']} runs): {conditions['diagnosis']}")
    return conditions


def assert_linear_chain(argv: Sequence[str]) -> None:
    """The rendered command must be one gain and nothing else.

    Checked at render time rather than trusted from the docstring: the claim that
    the macro-dynamics survive exactly holds only while the transfer stays linear.
    """
    values = [argv[i + 1] for i, token in enumerate(argv) if token in ("-af", "-filter:a")]
    if len(values) != 1 or not _LINEAR_CHAIN.fullmatch(values[0]):
        raise MasteringError(f"mastering chain is not a single linear gain: {values}")
    joined = " ".join(argv)
    for name in FORBIDDEN_FILTERS:
        if name in joined:
            raise MasteringError(f"forbidden mastering stage present: {name}")
    if "dither" in joined:
        raise MasteringError("dither is stochastic and would break canonical PCM equality")


def render_master(source: Path, destination: Path, *, gain_db: float,
                  codec: str = "pcm_s24le", ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """Apply the solved gain and write the delivery master. Deterministic by design."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise MasteringError(f"refusing to overwrite {destination}")
    argv = [
        ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y",
        "-i", str(source),
        "-map", "0:a:0",
        # One filter. No limiter, no EQ, no resampling, and no dither -- see module docstring.
        "-af", f"volume={gain_db:.12g}dB",
        "-c:a", codec,
        "-map_metadata", "-1",
        "-fflags", "+bitexact", "-flags", "+bitexact",
        str(destination),
    ]
    assert_linear_chain(argv)
    result = c.run(argv, timeout=3600)
    if result.returncode != 0 or not destination.is_file():
        raise MasteringError(f"master render failed: {result.stderr[-2000:]}")
    return {"command": argv, "codec": codec, "gain_db": gain_db,
            "container_sha256": c.sha256_file(destination),
            "bytes": destination.stat().st_size}


def render_master_pair(source: Path, directory: Path, *, gain_db: float,
                       sample_rate: int, channels: int,
                       codec: str = "pcm_s24le", ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """Render the master twice and prove the two executions are one object.

    A master nobody can reproduce is a file, not a master. The two executions must
    agree on canonical PCM *and* on container bytes; the containers can only be
    bit-identical because the chain carries no dither and writes no timestamps.
    """
    executions = []
    for suffix in ("a", "b"):
        destination = directory / f"master-{suffix}.wav"
        row = render_master(source, destination, gain_db=gain_db, codec=codec, ffmpeg=ffmpeg)
        row["canonical_pcm_sha256"] = c.canonical_pcm_sha256(
            destination, sample_rate=sample_rate, channels=channels, ffmpeg=ffmpeg)
        row["path"] = str(destination)
        executions.append(row)

    pcm = {row["canonical_pcm_sha256"] for row in executions}
    container = {row["container_sha256"] for row in executions}
    if len(pcm) != 1:
        raise MasteringError(f"the master did not reproduce: {sorted(pcm)}")
    if len(container) != 1:
        raise MasteringError(
            f"canonical PCM matched but the containers differ: {sorted(container)}")
    return {
        "executions": executions,
        "deterministic_executions": len(executions),
        "canonical_pcm_sha256": executions[0]["canonical_pcm_sha256"],
        "container_sha256": executions[0]["container_sha256"],
        "canonical_pcm_equality_across_executions": True,
        "container_equality_across_executions": True,
    }


def verify_master(master: Path, source: Path, plan: Mapping[str, Any],
                  sections: Mapping[str, tuple[float, float]], *,
                  sample_rate: int, channels: int, ffmpeg: str = "ffmpeg",
                  ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Prove the master met the ceiling and kept the shape it was told to keep."""
    whole = measure(master, ffmpeg=ffmpeg)
    conditions = peak_conditions(master, sample_rate=sample_rate, channels=channels,
                                 ffmpeg=ffmpeg, ffprobe=ffprobe)
    ceiling = float(plan["ceiling_dbtp"])
    gain = float(plan["solved_gain_db"])

    # A linear gain must move every section by the same amount. If a section drifts,
    # something non-linear happened and the macro-dynamics claim is false.
    rows: dict[str, Any] = {}
    drift = 0.0
    for name, (start, end) in sections.items():
        before = measure(source, ffmpeg=ffmpeg, start=start, end=end)["integrated_lufs"]
        after = measure(master, ffmpeg=ffmpeg, start=start, end=end)["integrated_lufs"]
        delta = after - before
        drift = max(drift, abs(delta - gain))
        rows[name] = {"source_lufs": before, "master_lufs": after,
                      "delta_db": round(delta, 2)}
    spans = [row["master_lufs"] for row in rows.values()]
    source_spans = [row["source_lufs"] for row in rows.values()]

    return {
        "integrated_lufs": whole["integrated_lufs"],
        "true_peak_dbtp": whole["true_peak_dbtp"],
        "sample_peak_dbfs": conditions["sample_peak_dbfs"],
        "flat_top_sample_count": conditions["flat_top_sample_count"],
        "flat_top_run_count": conditions["flat_top_run_count"],
        "hard_clipped": conditions["hard_clipped"],
        "true_peak_within_ceiling": whole["true_peak_dbtp"] <= ceiling + 0.05,
        "sections": rows,
        "macro_span_lu_source": round(max(source_spans) - min(source_spans), 2),
        "macro_span_lu_master": round(max(spans) - min(spans), 2),
        "max_section_gain_drift_db": round(drift, 3),
        "macro_dynamics_preserved": drift <= 0.35,
    }
