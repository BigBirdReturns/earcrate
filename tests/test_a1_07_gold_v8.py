from __future__ import annotations

from array import array
import json
import math
from pathlib import Path

import pytest

from earcrate.a1_07_gold_v8 import cli
from earcrate.a1_07_gold_v8 import common as c
from earcrate.a1_07_gold_v8 import compound as p
from earcrate.a1_07_gold_v8 import custody as u

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "configs" / "album_one" / "a1-07" / "gold-v8-arc-rungs.v1.json"


def pack(values) -> bytes:
    result = array("i", values)
    if c.sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


def test_contract_is_sealed_and_orders_rungs() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert c.validate_seal(contract, "contract_sha256") == (
        "b17df74823d6194bd36a5e77489e0e09571eb87bc3b94578b4fdff5600e139ab"
    )
    assert [row["candidate_id"] for row in contract["rungs"]] == [
        "gold-v8-arc-control",
        "gold-v8-arc-production",
        "gold-v8-arc-handoff",
    ]
    assert contract["review_policy"]["owner_diagnosis_required"] is False


def test_find_exact_subsequence_requires_one_aligned_copy() -> None:
    assert c.find_exact_subsequence(
        pack([1, 2, 10, 11, 12, 13, 7, 8]),
        pack([10, 11, 12, 13]),
        frame_bytes=4,
    ) == 2


def test_crossfade_replacement_preserves_outside_region() -> None:
    base = array("i", [0, 0, 10, 10, 20, 20, 30, 30, 40, 40])
    replacement = array("i", [100, 100, 200, 200, 300, 300])
    c.blend_regions(
        base,
        replacement,
        base_start_frame=1,
        replacement_start_frame=0,
        frames=3,
        channels=2,
        fade_in_frames=2,
        fade_out_frames=2,
    )
    assert base[0:2] == array("i", [0, 0])
    assert base[-2:] == array("i", [40, 40])
    assert base[4:6] == array("i", [200, 200])


def test_handoff_selection_prefers_fill_semantics_over_raw_delta() -> None:
    selected = u.choose_handoff_mask(
        [
            {
                "start_sample": 0,
                "end_sample": 5,
                "musical_function": "generic ownership",
            },
            {
                "start_sample": 5,
                "end_sample": 10,
                "musical_function": "drum fill launch",
            },
        ],
        parent_pcm=pack([0] * 40),
        interplay_pcm=pack([1000] * 40),
        channels=1,
    )
    assert selected["start_sample"] == 5
    assert selected["selection_reason"]["semantic_priority"] == 100


def test_compound_plan_renders_deterministically(tmp_path: Path) -> None:
    sources: list[dict] = []
    paths: dict[str, Path] = {}
    for name, data in (
        ("arc", pack(list(range(30)))),
        ("prod", pack([100 + value for value in range(10)])),
        ("inter", pack([200 + value for value in range(10)])),
    ):
        path = tmp_path / f"{name}.wav"
        c.write_s32_wav(path, data, sample_rate=1000, channels=1)
        paths[name] = path
        sources.append(u.source_row(name, name, path, c.sha256_bytes(data)))
    plan = p.make_plan(
        plan_id="test",
        title="test",
        sample_rate=1000,
        channels=1,
        duration_frames=30,
        sources=sources,
        operations=[
            {
                "op": "replace_with_crossfade",
                "source_id": "arc",
                "source_start_sample": 0,
                "target_start_sample": 0,
                "duration_samples": 30,
                "fade_in_samples": 0,
                "fade_out_samples": 0,
            },
            {
                "op": "replace_with_crossfade",
                "source_id": "prod",
                "source_start_sample": 0,
                "target_start_sample": 10,
                "duration_samples": 10,
                "fade_in_samples": 2,
                "fade_out_samples": 2,
            },
            {
                "op": "replace_with_crossfade",
                "source_id": "inter",
                "source_start_sample": 3,
                "target_start_sample": 13,
                "duration_samples": 4,
                "fade_in_samples": 1,
                "fade_out_samples": 1,
            },
        ],
        authority={"human_acceptance": False},
    )
    first = p.render_plan(plan, paths, ffmpeg="ffmpeg")
    assert first == p.render_plan(plan, paths, ffmpeg="ffmpeg")
    values = c.bytes_to_samples(first)
    assert list(values[:10]) == list(range(10))
    assert list(values[20:]) == list(range(20, 30))
    assert values[14] == 204


def _tone(frames: int, frequency: float, amplitude: int, sample_rate: int) -> bytes:
    return pack(
        int(amplitude * math.sin(2 * math.pi * frequency * value / sample_rate))
        for value in range(frames)
    )


def _synthetic_v7(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    sample_rate = 8000
    parent = _tone(4000, 220, 500_000_000, sample_rate)
    production_values = c.bytes_to_samples(parent)
    for index in range(len(production_values)):
        production_values[index] = int(production_values[index] * 0.85)
    production = c.samples_to_bytes(production_values)
    interplay_values = array("i", production_values)
    for index in range(1500, 1900):
        interplay_values[index] += int(
            180_000_000 * math.sin(2 * math.pi * 800 * index / sample_rate)
        )
    interplay = c.samples_to_bytes(interplay_values)
    arc = (
        _tone(1600, 110, 120_000_000, sample_rate)
        + parent
        + _tone(800, 110, 90_000_000, sample_rate)
    )
    root = tmp_path / "v7"
    (root / "incumbent").mkdir(parents=True)
    owner = root / "incumbent" / "owner-review.receipt.json"
    owner.write_text('{"owner":true}\n', encoding="utf-8")
    c.write_s32_wav(
        root / "incumbent" / "gold-v6.wav",
        parent,
        sample_rate=sample_rate,
        channels=1,
    )
    scores = {
        "parent_score": "1" * 64,
        "production_score": "2" * 64,
        "interplay_score": "3" * 64,
        "arc_score": "4" * 64,
    }
    (root / "incumbent" / "performance-score.json").write_text(
        json.dumps({"score_sha256": scores["parent_score"]}),
        encoding="utf-8",
    )
    children = {
        "gold-v7-production": (production, scores["production_score"], []),
        "gold-v7-interplay": (
            interplay,
            scores["interplay_score"],
            [
                {
                    "start_sample": 1500,
                    "end_sample": 1900,
                    "musical_function": "drum fill launch",
                }
            ],
        ),
        "gold-v7-arc": (
            arc,
            scores["arc_score"],
            [
                {
                    "start_sample": 1600,
                    "end_sample": 5600,
                    "musical_function": "sample_identical_gold_v6_core",
                }
            ],
        ),
    }
    identities = {
        "owner_review": c.sha256_file(owner),
        "parent_score": scores["parent_score"],
        "parent_pcm": c.sha256_bytes(parent),
        "production_score": scores["production_score"],
        "production_pcm": c.sha256_bytes(production),
        "interplay_score": scores["interplay_score"],
        "interplay_pcm": c.sha256_bytes(interplay),
        "arc_score": scores["arc_score"],
        "arc_pcm": c.sha256_bytes(arc),
        "contract_v7": "5" * 64,
    }
    for child, (audio, score, masks) in children.items():
        machine = root / child / "machine"
        authoring = root / child / "authoring"
        machine.mkdir(parents=True)
        authoring.mkdir()
        c.write_s32_wav(
            machine / "qualified.wav",
            audio,
            sample_rate=sample_rate,
            channels=1,
        )
        (authoring / "performance-score.json").write_text(
            json.dumps({"score_sha256": score}),
            encoding="utf-8",
        )
        c.atomic_write_json(
            machine / "machine-receipt.json",
            c.seal(
                {
                    "schema_version": 1,
                    "kind": "a1_07_gold_v7_machine_receipt",
                    "candidate_id": child,
                    "candidate_score_sha256": score,
                    "candidate_pcm_sha256": c.sha256_bytes(audio),
                    "qualified_audio_name": "qualified.wav",
                    "declared_masks": masks,
                    "qualified": True,
                },
                "machine_receipt_sha256",
            ),
        )
    return root, identities


def test_end_to_end_build_creates_two_review_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v7, identities = _synthetic_v7(tmp_path)
    original = dict(c.EXPECTED)
    c.EXPECTED.clear()
    c.EXPECTED.update(identities)
    monkeypatch.setattr(cli, "current_git_head", lambda _root: "a" * 40)
    try:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        output = tmp_path / "v8"
        assert cli.build(
            v7,
            output,
            contract,
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        )["ok"] is True
        assert (
            output / "review" / "whole-arc" / "public" / "assignment.json"
        ).is_file()
        assert (
            output / "review" / "core-window" / "public" / "assignment.json"
        ).is_file()
        assert cli.verify_workspace(
            output,
            contract,
            ffmpeg="ffmpeg",
        )["ok"] is True
    finally:
        c.EXPECTED.clear()
        c.EXPECTED.update(original)


def test_failed_build_leaves_no_public_workspace(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    output = tmp_path / "v8"
    with pytest.raises(c.DescentError):
        cli.build(
            tmp_path / "missing-v7",
            output,
            contract,
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".v8.*"))
