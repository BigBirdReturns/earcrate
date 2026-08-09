from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("beggin_timing_pass", ROOT / "scripts" / "beggin_timing_pass.py")
assert SPEC and SPEC.loader
btp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = btp
SPEC.loader.exec_module(btp)


def tone_phrase(sr: int, duration: float, pattern: int, pitch_ratio: float = 1.0) -> np.ndarray:
    t = np.arange(int(round(duration * sr)), dtype=np.float64) / sr
    base = (170.0 + 37.0 * pattern) * pitch_ratio
    phase = 0.11 * pattern
    carrier = (
        0.58 * np.sin(2 * np.pi * base * t + phase)
        + 0.26 * np.sin(2 * np.pi * base * 2.17 * t + phase * 2)
        + 0.16 * np.sin(2 * np.pi * base * 3.41 * t + phase * 3)
    )
    syllable = 0.55 + 0.45 * np.sin(2 * np.pi * (2.5 + 0.2 * pattern) * t) ** 2
    envelope = np.sin(np.pi * np.clip(t / max(duration, 1e-6), 0, 1)) ** 0.45
    consonant = np.zeros_like(t)
    consonant[: max(1, int(0.035 * sr))] = np.linspace(0.8, 0.0, max(1, int(0.035 * sr)))
    noise = np.random.default_rng(pattern).normal(0.0, 0.09, t.size) * consonant
    return (0.45 * carrier * syllable * envelope + noise).astype(np.float32)


def build_vocal(sr: int, duration: float, intervals: list[tuple[float, float]], pitch_ratio: float) -> np.ndarray:
    out = np.zeros(int(round(duration * sr)), dtype=np.float32)
    for index, (start, end) in enumerate(intervals):
        phrase = tone_phrase(sr, end - start, index, pitch_ratio)
        begin = int(round(start * sr))
        finish = min(out.size, begin + phrase.size)
        out[begin:finish] += phrase[: finish - begin]
    out += np.random.default_rng(1234).normal(0.0, 0.0003, out.size).astype(np.float32)
    return out


def build_drums(sr: int, duration: float) -> np.ndarray:
    out = np.zeros(int(round(duration * sr)), dtype=np.float32)
    rng = np.random.default_rng(88)
    for beat_index, start in enumerate(np.arange(0.0, duration, 0.5)):
        begin = int(round(start * sr))
        length = min(int(0.12 * sr), out.size - begin)
        if length <= 0:
            continue
        t = np.arange(length) / sr
        if beat_index % 2 == 0:
            hit = np.sin(2 * np.pi * (75.0 - 25.0 * t) * t) * np.exp(-32.0 * t)
        else:
            hit = rng.normal(0.0, 1.0, length) * np.exp(-28.0 * t)
        out[begin : begin + length] += (0.42 * hit).astype(np.float32)
    stereo = np.column_stack([out, out * 0.97])
    return stereo


class BegginTimingPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sr = 48000
        self.source_start = 0.5
        self.source_end = 6.5
        self.target_start = 0.3
        self.target_end = 5.8
        self.output_duration = 7.0
        source_rel = [
            (0.05, 0.76),
            (0.98, 1.66),
            (1.92, 2.74),
            (3.50, 3.94),
            (4.24, 4.71),
            (5.03, 5.52),
        ]
        target_rel = [
            (0.04, 0.66),
            (0.82, 1.42),
            (1.67, 2.36),
            (2.91, 3.31),
            (3.61, 4.02),
            (4.36, 4.80),
        ]
        source_full = np.zeros(int(8.0 * self.sr), dtype=np.float32)
        source_window = build_vocal(self.sr, self.source_end - self.source_start, source_rel, 1.0)
        begin = int(self.source_start * self.sr)
        source_full[begin : begin + source_window.size] = source_window
        target_full = np.zeros(int(8.0 * self.sr), dtype=np.float32)
        target_window = build_vocal(self.sr, self.target_end - self.target_start, target_rel, 0.84)
        begin = int(self.target_start * self.sr)
        target_full[begin : begin + target_window.size] = target_window
        self.source = self.root / "four-seasons-vocal.wav"
        self.target = self.root / "maneskin-vocal-witness.wav"
        self.instrumental = self.root / "maneskin-instrumental.wav"
        self.drums = self.root / "maneskin-drums.wav"
        wavfile.write(self.source, self.sr, source_full)
        wavfile.write(self.target, self.sr, target_full)
        wavfile.write(self.drums, self.sr, build_drums(self.sr, self.output_duration))
        wavfile.write(self.instrumental, self.sr, build_drums(self.sr, 8.0))
        self.source_binding = self._binding(self.source, "four_seasons_beggin")
        self.target_binding = self._binding(self.instrumental, "maneskin_beggin")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _binding(self, path: Path, source_id: str) -> Path:
        value = {
            "schema_version": 1,
            "kind": "earcrate_homelab_specimen_source_binding",
            "case_id": "beggin-four-seasons-x-maneskin-handoff",
            "source_id": source_id,
            "artifact_path": str(path),
            "artifact_sha256": btp.sha256_file(path),
            "artifact_bytes": path.stat().st_size,
        }
        value = btp.seal(value, "binding_sha256")
        target = self.root / f"{source_id}.binding.json"
        btp.write_json(target, value)
        return target

    def test_locate_witness_requires_current_strong_bytes(self) -> None:
        digest = btp.sha256_file(self.target)
        inventory = {
            "items": [
                {
                    "item_id": "witness",
                    "raw_sha256": digest,
                    "hash_status": "strong",
                    "absolute_path": str(self.target),
                    "bytes": self.target.stat().st_size,
                    "metadata": {},
                }
            ]
        }
        inventory_path = self.root / "inventory.json"
        btp.write_json(inventory_path, inventory)
        result = btp.locate_witness(inventory_path, digest)
        self.assertEqual(result["selected"]["sha256"], digest)
        self.assertEqual(Path(result["selected"]["path"]), self.target)

    def test_phrase_mapping_keeps_terminal_calls_independent(self) -> None:
        source_audio = btp.decode_mono(
            self.source,
            start=self.source_start,
            end=self.source_end,
            sample_rate=16000,
            ffmpeg="ffmpeg",
        )
        target_audio = btp.decode_mono(
            self.target,
            start=self.target_start,
            end=self.target_end,
            sample_rate=16000,
            ffmpeg="ffmpeg",
        )
        sf = btp.audio_features(source_audio, 16000)
        tf = btp.audio_features(target_audio, 16000)
        ss = btp.activity_segments(sf, merge_gap_seconds=0.14)
        ts = btp.activity_segments(tf, merge_gap_seconds=0.14)
        mapping = btp.constrained_dtw(sf, tf, band_seconds=1.5)
        maps = btp.create_time_maps(
            source_bundle=sf,
            target_bundle=tf,
            mapping=mapping,
            source_segments=ss,
            target_segments=ts,
            source_window_start=self.source_start,
            source_window_end=self.source_end,
            target_window_start=self.target_start,
            target_window_end=self.target_end,
            final_call_count=3,
        )
        calls = maps["terminal_calls"]
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(row["mode"] == "terminal_call_onset_locked" for row in calls))
        self.assertTrue(all(0.88 <= row["atempo"] <= 1.13 for row in calls))
        self.assertLess(mapping["normalized_cost"], 1.25)

    def test_end_to_end_produces_three_level_matched_candidates_and_verifies(self) -> None:
        out = self.root / "run"
        command = [
            sys.executable,
            str(ROOT / "scripts" / "beggin_timing_pass.py"),
            "run",
            "--source-vocal-binding",
            str(self.source_binding),
            "--target-instrumental-binding",
            str(self.target_binding),
            "--target-vocal-witness",
            str(self.target),
            "--drum-stem",
            str(self.drums),
            "--output",
            str(out),
            "--source-start",
            str(self.source_start),
            "--source-end",
            str(self.source_end),
            "--target-start",
            str(self.target_start),
            "--target-end",
            str(self.target_end),
            "--output-duration",
            str(self.output_duration),
            "--dtw-band-seconds",
            "1.5",
            "--campaign-sha256",
            "c" * 64,
            "--suite-sha256",
            "e" * 64,
        ]
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(set(result["candidate_sha256s"]), {"onset_only", "global_block", "phrase_local"})
        for name in ("A.wav", "B.wav", "C.wav", "assignment.json"):
            self.assertTrue((out / "review-public" / name).is_file())
        verification = btp.verify_output(out)
        self.assertTrue(verification["ok"], verification)
        receipt = btp.load_json(out / "campaign-receipt.json")
        self.assertEqual(receipt["status"], "signal_sane_human_review_pending")
        measurements = [row["integrated_lufs"] for row in receipt["signal_receipts"].values()]
        self.assertLess(max(measurements) - min(measurements), 0.75)
        self.assertTrue(all(-15.5 <= value <= -13.0 for value in measurements))

    def test_review_submission_reveals_selected_candidate_only_in_private_receipt(self) -> None:
        public = btp.seal(
            {
                "schema_version": 1,
                "kind": "earcrate_beggin_timing_public_assignment",
                "options": {"A": {"sha256": "1" * 64}},
                "private_authority_sha256": "placeholder",
            },
            "assignment_sha256",
        )
        authority = btp.seal(
            {
                "schema_version": 1,
                "kind": "earcrate_beggin_timing_private_assignment",
                "option_map": {"A": "phrase_local"},
            },
            "authority_sha256",
        )
        public.pop("assignment_sha256")
        public["private_authority_sha256"] = authority["authority_sha256"]
        public = btp.seal(public, "assignment_sha256")
        public_path = self.root / "assignment.json"
        authority_path = self.root / "authority.json"
        btp.write_json(public_path, public)
        btp.write_json(authority_path, authority)
        args = type("Args", (), {
            "assignment": str(public_path),
            "private_authority": str(authority_path),
            "choice": "A",
            "dimensions_json": '{"terminal phrase placement":5}',
            "reviewer_id": "operator:owner",
            "note": ["calls land"],
            "output": str(self.root / "review.json"),
            "replace": False,
        })()
        result = btp.submit_review(args)
        self.assertEqual(result["selected_candidate_id"], "phrase_local")

    def test_output_directory_is_never_overwritten(self) -> None:
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            btp.require_new_directory(occupied)


if __name__ == "__main__":
    unittest.main()
