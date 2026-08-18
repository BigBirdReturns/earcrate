"""Gates for the A1-07 delivery master.

These protect the three properties the monitoring verdict actually bought: that the
transfer stays linear so the macro-dynamics survive exactly, that the master
reproduces bit for bit, and that the two ways of quietly reintroducing a limiter --
a clipped source and an unreachable loudness target -- refuse instead of proceeding.

They also protect the provenance boundary. The mastering stage must never enter the
digest that identifies the code which produced the accepted render.
"""

from __future__ import annotations

from array import array
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.a1_07_gold_v8 import common as c  # noqa: E402
from earcrate.a1_07_master import chain  # noqa: E402
from earcrate.a1_07_master import provenance as mp  # noqa: E402
from earcrate.a1_07_master import acceptance as acc  # noqa: E402
from earcrate.a1_07_master.receipt import (  # noqa: E402
    MASTER_QUALIFIED, MasterReceiptError, build_public_projection,
    load_monitoring_verdict)

# ffmpeg is a declared requirement of this gate suite, as it is for the gold-v8
# gates: every measurement here is an ebur128 or ffprobe reading.

RATE = 48000
CHANNELS = 2
FULL_SCALE = 2 ** 31 - 1
# Three sections at deliberately different levels, so a stage that is not a pure
# gain shows up as section drift rather than as an average that happens to match.
SECTION_DBFS = (-20.0, -12.0, -6.0)
SECTION_SECONDS = 4.0
SECTIONS = {
    "setup": (0.0, 4.0),
    "body": (4.0, 8.0),
    "payoff": (8.0, 12.0),
}


_PCM_CACHE: dict[bool, bytes] = {}


def _tone_pcm(clip: bool) -> bytes:
    """A deterministic three-level tone, synthesized without any private media."""
    cached = _PCM_CACHE.get(clip)
    if cached is not None:
        return cached
    values = array("i")
    for dbfs in SECTION_DBFS:
        amplitude = (10.0 ** (dbfs / 20.0)) * FULL_SCALE
        if clip:
            amplitude *= 10.0  # drive well past full scale so the clamp leaves flat tops
        for n in range(int(RATE * SECTION_SECONDS)):
            raw = amplitude * math.sin(2.0 * math.pi * 440.0 * n / RATE)
            sample = max(-FULL_SCALE, min(FULL_SCALE, int(raw)))
            values.append(sample)
            values.append(sample)
    pcm = c.samples_to_bytes(values)
    _PCM_CACHE[clip] = pcm
    return pcm


def _write_tone(path: Path, *, clip: bool = False) -> Path:
    c.write_s32_wav(path, _tone_pcm(clip), sample_rate=RATE, channels=CHANNELS)
    return path


def test_the_chain_is_one_linear_gain_and_nothing_else():
    good = ["ffmpeg", "-i", "in.wav", "-af", "volume=2.5dB", "-c:a", "pcm_s24le", "out.wav"]
    chain.assert_linear_chain(good)

    for argv in (
        ["ffmpeg", "-af", "volume=2.5dB,alimiter=limit=0.9", "out.wav"],
        ["ffmpeg", "-af", "equalizer=f=3000:width_type=o:width=1:g=2", "out.wav"],
        ["ffmpeg", "-af", "volume=2.5dB", "-af", "volume=1dB", "out.wav"],
        ["ffmpeg", "-af", "loudnorm=I=-14", "out.wav"],
        ["ffmpeg", "-af", "volume=2.5dB", "-dither_method", "triangular", "out.wav"],
        ["ffmpeg", "-c:a", "pcm_s24le", "out.wav"],
    ):
        with pytest.raises(chain.MasteringError):
            chain.assert_linear_chain(argv)


def test_a_loudness_target_that_needs_limiting_is_refused(tmp_path):
    source = _write_tone(tmp_path / "source.wav")
    plan = chain.solve_gain(source, ceiling_dbtp=-1.0, ffmpeg="ffmpeg")
    assert plan["limiting_required"] is False, "a peak-solved plan never needs limiting"
    headroom = float(plan["solved_gain_db"])

    reachable = chain.solve_gain(
        source, ceiling_dbtp=-1.0,
        target_lufs=plan["source_integrated_lufs"] + headroom - 0.5, ffmpeg="ffmpeg")
    assert reachable["limiting_required"] is False
    chain.refuse_if_limiting(reachable)
    assert reachable["solved_gain_db"] == plan["solved_gain_db"], \
        "a loudness target must never change the applied gain, only qualify it"

    # The real case: the streaming-normalization ask that overruns the ceiling by a
    # fraction of a dB. That fraction is where a limiter gets added by accident.
    unreachable = chain.solve_gain(
        source, ceiling_dbtp=-1.0,
        target_lufs=plan["source_integrated_lufs"] + headroom + 0.3, ffmpeg="ffmpeg")
    assert unreachable["limiting_required"] is True
    assert unreachable["loudness_shortfall_db"] == pytest.approx(0.3, abs=0.02)
    with pytest.raises(chain.MasteringError, match="requires limiting"):
        chain.refuse_if_limiting(unreachable)


def test_a_hard_clipped_source_is_refused_before_any_master_is_written(tmp_path):
    clipped = _write_tone(tmp_path / "clipped.wav", clip=True)
    with pytest.raises(chain.MasteringError, match="hard-clipped"):
        chain.refuse_if_source_is_clipped(clipped, sample_rate=RATE, channels=CHANNELS)

    clean = _write_tone(tmp_path / "clean.wav")
    conditions = chain.refuse_if_source_is_clipped(clean, sample_rate=RATE, channels=CHANNELS)
    assert conditions["hard_clipped"] is False


def test_the_master_reproduces_bit_for_bit_across_two_executions(tmp_path):
    source = _write_tone(tmp_path / "source.wav")
    plan = chain.solve_gain(source, ceiling_dbtp=-1.0, ffmpeg="ffmpeg")
    rendered = chain.render_master_pair(
        source, tmp_path / "pair", gain_db=float(plan["solved_gain_db"]),
        sample_rate=RATE, channels=CHANNELS)

    assert rendered["deterministic_executions"] == 2
    assert rendered["canonical_pcm_equality_across_executions"] is True
    # The containers must match too, which is only possible without dither.
    assert rendered["container_equality_across_executions"] is True
    first, second = rendered["executions"]
    assert first["container_sha256"] == second["container_sha256"]
    assert Path(first["path"]).read_bytes() == Path(second["path"]).read_bytes()


def test_render_master_refuses_to_overwrite_an_existing_master(tmp_path):
    source = _write_tone(tmp_path / "source.wav")
    destination = tmp_path / "master.wav"
    chain.render_master(source, destination, gain_db=1.0)
    with pytest.raises(chain.MasteringError, match="refusing to overwrite"):
        chain.render_master(source, destination, gain_db=1.0)


def test_section_gain_invariance_holds_for_a_linear_gain(tmp_path):
    source = _write_tone(tmp_path / "source.wav")
    plan = chain.solve_gain(source, ceiling_dbtp=-1.0, ffmpeg="ffmpeg")
    gain = float(plan["solved_gain_db"])
    master = tmp_path / "linear.wav"
    chain.render_master(source, master, gain_db=gain)

    report = chain.verify_master(master, source, plan, SECTIONS,
                                 sample_rate=RATE, channels=CHANNELS)
    assert report["true_peak_within_ceiling"] is True
    assert report["hard_clipped"] is False
    for name, row in report["sections"].items():
        assert row["delta_db"] == pytest.approx(gain, abs=0.2), \
            f"{name} moved by {row['delta_db']} dB, not by the applied {gain} dB"
    assert report["max_section_gain_drift_db"] <= 0.35
    assert report["macro_dynamics_preserved"] is True
    assert report["macro_span_lu_master"] == pytest.approx(report["macro_span_lu_source"], abs=0.2)


def test_a_non_linear_stage_is_caught_by_the_section_invariance_check(tmp_path):
    """The invariance check must have teeth, or the macro-dynamics claim is decoration.

    A limiter set between the quiet and loud section levels attenuates one section
    and leaves another alone. That is exactly the failure the check exists to catch,
    so it is built here on purpose and must be rejected.
    """
    source = _write_tone(tmp_path / "source.wav")
    plan = chain.solve_gain(source, ceiling_dbtp=-1.0, ffmpeg="ffmpeg")
    limited = tmp_path / "limited.wav"
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", str(source),
         "-map", "0:a:0", "-af", f"volume={plan['solved_gain_db']:.6g}dB,alimiter=limit=0.25",
         "-c:a", "pcm_s24le", "-map_metadata", "-1", str(limited)],
        capture_output=True, text=True, timeout=600, check=False)
    assert result.returncode == 0 and limited.is_file(), result.stderr[-800:]

    report = chain.verify_master(limited, source, plan, SECTIONS,
                                 sample_rate=RATE, channels=CHANNELS)
    assert report["macro_dynamics_preserved"] is False, \
        "a limiter changed the section relationships and the check did not notice"
    assert report["max_section_gain_drift_db"] > 0.35
    assert report["macro_span_lu_master"] < report["macro_span_lu_source"], \
        "limiting must show up as a compressed macro span"


def test_the_master_stage_stays_outside_the_render_provenance_digest():
    """Mastering cannot change a sample of the render, so it must not move its digest.

    If it did, adding this package would contradict the manifest the accepted render
    carries and drop the lane to representative_invocation_ready = False, forcing a
    re-render to re-prove something the change could not have touched.
    """
    from earcrate.a1_07_full_form.provenance import ADAPTER_PATHS

    for entry in ADAPTER_PATHS:
        assert not entry.startswith("earcrate/a1_07_master"), \
            f"the mastering stage is inside the render digest via {entry}"
    for entry in mp.MASTER_PATHS:
        assert not entry.startswith(("earcrate/a1_07_full_form", "earcrate/a1_07_gold_v8")), \
            f"the master digest covers render code via {entry}"

    tracked = {path for path, _ in mp.tracked_blobs(ROOT, mp.MASTER_PATHS)}
    assert tracked, "the mastering stage must be tracked before it can be identified"
    assert not any(path.startswith("earcrate/a1_07_full_form/") for path in tracked)


def test_both_provenance_implementations_agree_on_identical_inputs():
    """The algorithm is duplicated across two stages; it must not drift."""
    from earcrate.a1_07_full_form.provenance import ADAPTER_PATHS, adapter_tree_digest

    assert mp.tree_digest(ROOT, ADAPTER_PATHS)["digest"] == adapter_tree_digest(ROOT)["digest"]
    digest = mp.master_tree_digest(ROOT)
    assert digest["identity_source"].startswith("git blob")
    assert digest["digest"] == mp.master_tree_digest(ROOT)["digest"], "digest must be stable"


def _verdict(pcm: str, **overrides) -> dict:
    value = {
        "kind": "earcrate_a1_07_monitoring_ratification",
        "schema_version": 1,
        "track_id": "A1-07",
        "descent_id": "a1-07-full-form-v1",
        "verdict": "ACCEPT_FOR_MASTERING",
        "reviewed": {"canonical_pcm_sha256": pcm},
        "authority": {"human_review": True, "blind": False, "reopens_timing_law": False},
        "constraints": ["preserve macro-dynamics"],
        "ceiling_dbtp": -1.0,
        "disposition": {"accepts_production_render": True, "authorizes_mastering": True,
                        "accepts_mastered_object": False},
    }
    value.update(overrides)
    return c.seal(value, "verdict_sha256")


def _manifest(pcm: str = "7" * 64, container: str = "8" * 64,
              master_state: str = MASTER_QUALIFIED) -> dict:
    """A synthetic master manifest, so the acceptance layer is testable without audio."""
    return {
        "master_manifest_sha256": "0" * 64,
        "master_tree": {"digest": "1" * 64, "member_count": 4, "declared_paths": ["x"]},
        "timeline": {"sample_rate": 48000, "channels": 2},
        "source": {"canonical_pcm_sha256": "2" * 64,
                   "artifact_path": r"D:\private\render-a.wav"},
        "authorizing_decisions": {
            "frontier_manifest_sha256": "3" * 64,
            "frontier_contract_sha256": "4" * 64,
            "render_provenance_digest": "5" * 64,
            "monitoring_verdict_sha256": "6" * 64,
            "monitoring_verdict": "ACCEPT_FOR_MASTERING",
            "monitoring_constraints": ["preserve macro-dynamics"],
        },
        "plan": {"solved_gain_db": 2.5, "ceiling_dbtp": -1.0, "source_integrated_lufs": -16.8,
                 "source_true_peak_dbtp": -3.5},
        "master": {"canonical_pcm_sha256": pcm, "container_sha256": container,
                   "deterministic_executions": 2,
                   "canonical_pcm_equality_across_executions": True,
                   "container_equality_across_executions": True,
                   "executions": [{"path": r"D:\private\master-a.wav"}]},
        "verification": {"integrated_lufs": -14.3, "true_peak_dbtp": -1.0,
                         "sample_peak_dbfs": -1.11, "flat_top_run_count": 0,
                         "flat_top_sample_count": 0, "hard_clipped": False,
                         "true_peak_within_ceiling": True,
                         "sections": {"setup": {"delta_db": 2.5}},
                         "macro_span_lu_source": 8.5, "macro_span_lu_master": 8.5,
                         "max_section_gain_drift_db": 0.0,
                         "macro_dynamics_preserved": True},
        "authority": {"master_state": master_state, "album_master_accepted": False},
    }


def _master_verdict(pcm: str, container: str, verdict: str = acc.ACCEPT, **overrides) -> dict:
    value = {
        "kind": "earcrate_a1_07_master_acceptance_verdict",
        "schema_version": 1,
        "track_id": "A1-07",
        "descent_id": "a1-07-full-form-v1",
        "verdict": verdict,
        "audited": {"canonical_pcm_sha256": pcm, "container_sha256": container},
        "authority": {"human_review": True, "blind": False, "reopens_timing_law": False,
                      "reopens_arrangement": False, "reopens_mix": False},
        "findings": "the master is the accepted render, 2.5 dB louder",
    }
    value.update(overrides)
    return c.seal(value, "verdict_sha256")


def test_the_monitoring_verdict_must_ratify_the_render_being_mastered(tmp_path):
    pcm = "a" * 64
    path = tmp_path / "verdict.json"
    c.atomic_write_json(path, _verdict(pcm))
    assert load_monitoring_verdict(path, accepted_pcm_sha256=pcm)["ceiling_dbtp"] == -1.0

    # The blind frontier verdict warned that the reviewed cut was a level-matched
    # projection, so a ratification naming any other object is about another object.
    with pytest.raises(MasterReceiptError, match="ratified"):
        load_monitoring_verdict(path, accepted_pcm_sha256="b" * 64)

    broken = tmp_path / "broken.json"
    value = _verdict(pcm)
    value["ceiling_dbtp"] = -0.1  # mutate after sealing
    c.atomic_write_json(broken, value)
    with pytest.raises(c.DescentError, match="verdict_sha256"):
        load_monitoring_verdict(broken, accepted_pcm_sha256=pcm)

    for name, mutation in (
        ("wrong-verdict", {"verdict": "ACCEPT"}),
        ("overreaching", {"disposition": {"accepts_mastered_object": True}}),
    ):
        path_ = tmp_path / f"{name}.json"
        c.atomic_write_json(path_, _verdict(pcm, **mutation))
        with pytest.raises(MasterReceiptError):
            load_monitoring_verdict(path_, accepted_pcm_sha256=pcm)

    reopening = tmp_path / "reopening.json"
    value = dict(_verdict(pcm))
    value["authority"] = {"human_review": True, "blind": False, "reopens_timing_law": True}
    c.atomic_write_json(reopening, c.seal(value, "verdict_sha256"))
    with pytest.raises(MasterReceiptError, match="reopen"):
        load_monitoring_verdict(reopening, accepted_pcm_sha256=pcm)


def test_the_public_master_receipt_carries_no_paths_or_media():
    manifest = {
        "master_manifest_sha256": "0" * 64,
        "master_tree": {"digest": "1" * 64, "member_count": 4, "declared_paths": ["x"]},
        "source": {"canonical_pcm_sha256": "2" * 64,
                   "artifact_path": r"D:\private\render-a.wav"},
        "authorizing_decisions": {
            "frontier_manifest_sha256": "3" * 64,
            "frontier_contract_sha256": "4" * 64,
            "render_provenance_digest": "5" * 64,
            "monitoring_verdict_sha256": "6" * 64,
            "monitoring_constraints": ["preserve the macro-dynamics"],
        },
        "plan": {"solved_gain_db": 2.5, "ceiling_dbtp": -1.0, "source_integrated_lufs": -16.8,
                 "source_true_peak_dbtp": -3.5},
        "master": {"canonical_pcm_sha256": "7" * 64, "container_sha256": "8" * 64,
                   "deterministic_executions": 2,
                   "canonical_pcm_equality_across_executions": True,
                   "container_equality_across_executions": True,
                   "executions": [{"path": r"D:\private\master-a.wav"}]},
        "verification": {"integrated_lufs": -14.3, "true_peak_dbtp": -1.0,
                         "sample_peak_dbfs": -1.11, "flat_top_run_count": 0,
                         "flat_top_sample_count": 0, "hard_clipped": False,
                         "true_peak_within_ceiling": True,
                         "sections": {"setup": {"delta_db": 2.5}},
                         "macro_span_lu_source": 8.5, "macro_span_lu_master": 8.5,
                         "max_section_gain_drift_db": 0.0,
                         "macro_dynamics_preserved": True},
    }
    public = build_public_projection(manifest)

    # Walk keys and string values rather than grepping the serialized blob: the
    # prose legitimately contains words like "executions", and a substring match
    # over prose is a leak test that fails for the wrong reason.
    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in ("artifact_path", "executions", "path"),                     f"the public receipt carries a private field at {path}/{key}"
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            for leak in ("D:\\", "C:\\", ".wav", "private-custody", "sessions/"):
                assert leak not in node, f"the public receipt leaks {leak!r} at {path}"

    walk(public)
    assert public["boundary"]["private_paths_included"] is False
    assert public["boundary"]["master_audio_exported"] is False
    # A qualification receipt reports machine evidence. It may never report that
    # anybody accepted anything, however transparent the transform was.
    assert public["state"]["master_state"] == MASTER_QUALIFIED
    assert public["state"]["mastering_chain_qualified"] is True
    assert public["state"]["deterministic_master_pair"] is True
    assert public["state"]["owner_master_acceptance"] is False
    assert public["state"]["accepted_album_master"] is False
    assert public["state"]["accepted_album_masters"] == 0
    assert public["state"]["system_reference_complete"] is False
    assert public["review"]["post_master_audition_complete"] is False
    assert c.validate_seal(public, "receipt_sha256") == public["receipt_sha256"]


def test_only_a_verdict_naming_the_mastered_object_can_accept_it(tmp_path):
    """Acceptance binds to the mastered PCM, not to the render it came from.

    The monitoring verdict accepted the production render. If acceptance could be
    satisfied by a verdict naming that render, the mastered object would inherit an
    acceptance nobody gave it -- which is exactly the collapse this layer prevents.
    """
    pcm, container = "7" * 64, "8" * 64
    manifest = _manifest(pcm, container)

    good = tmp_path / "accept.json"
    c.atomic_write_json(good, _master_verdict(pcm, container))
    verdict = acc.load_master_verdict(good, master_pcm_sha256=pcm,
                                      master_container_sha256=container)
    receipt = acc.build_acceptance_receipt(manifest, verdict)
    assert receipt["verdict"] == acc.ACCEPT
    assert receipt["state"]["accepted_album_master"] is True
    assert receipt["state"]["accepted_album_masters"] == 1
    assert receipt["state"]["system_reference_complete"] is False, \
        "accepting a master must never complete the system reference"
    assert receipt["audited_object"]["canonical_pcm_sha256"] == pcm
    assert c.validate_seal(receipt, "receipt_sha256") == receipt["receipt_sha256"]

    # The render's identity, an unsealed verdict, an inadmissible outcome, and a
    # verdict that reopens a settled frontier are each refused.
    wrong_pcm = tmp_path / "wrong-pcm.json"
    c.atomic_write_json(wrong_pcm, _master_verdict("2" * 64, container))
    with pytest.raises(acc.AcceptanceError, match="audited"):
        acc.load_master_verdict(wrong_pcm, master_pcm_sha256=pcm,
                                master_container_sha256=container)

    wrong_container = tmp_path / "wrong-container.json"
    c.atomic_write_json(wrong_container, _master_verdict(pcm, "9" * 64))
    with pytest.raises(acc.AcceptanceError, match="container"):
        acc.load_master_verdict(wrong_container, master_pcm_sha256=pcm,
                                master_container_sha256=container)

    inadmissible = tmp_path / "inadmissible.json"
    c.atomic_write_json(inadmissible, _master_verdict(pcm, container, verdict="LGTM"))
    with pytest.raises(acc.AcceptanceError, match="inadmissible"):
        acc.load_master_verdict(inadmissible, master_pcm_sha256=pcm,
                                master_container_sha256=container)

    reopening = tmp_path / "reopens.json"
    c.atomic_write_json(reopening, _master_verdict(
        pcm, container, authority={"human_review": True, "reopens_mix": True}))
    with pytest.raises(acc.AcceptanceError, match="reopens_mix"):
        acc.load_master_verdict(reopening, master_pcm_sha256=pcm,
                                master_container_sha256=container)


def test_master_revision_required_leaves_the_counter_where_it_was(tmp_path):
    pcm, container = "7" * 64, "8" * 64
    path = tmp_path / "revise.json"
    c.atomic_write_json(path, _master_verdict(pcm, container, verdict=acc.REVISE))
    verdict = acc.load_master_verdict(path, master_pcm_sha256=pcm,
                                      master_container_sha256=container)
    receipt = acc.build_acceptance_receipt(_manifest(pcm, container), verdict)

    assert receipt["verdict"] == acc.REVISE
    assert receipt["master_state"] == MASTER_QUALIFIED
    assert receipt["state"]["accepted_album_master"] is False
    assert receipt["state"]["accepted_album_masters"] == 0


def test_an_unqualified_master_cannot_be_accepted(tmp_path):
    """Audition follows qualification; a master that failed its gates is not heard."""
    pcm, container = "7" * 64, "8" * 64
    path = tmp_path / "accept.json"
    c.atomic_write_json(path, _master_verdict(pcm, container))
    verdict = acc.load_master_verdict(path, master_pcm_sha256=pcm,
                                      master_container_sha256=container)
    with pytest.raises(acc.AcceptanceError, match="qualified"):
        acc.build_acceptance_receipt(_manifest(pcm, container, master_state="frontier_selected"),
                                     verdict)
