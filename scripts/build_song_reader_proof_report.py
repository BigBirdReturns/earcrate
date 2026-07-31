from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.reader.body import reader_decode_stereo
from earcrate.reader.model import reader_seal, reader_sha256_file
from earcrate.reader.render import reader_compare_audio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--reader-output", required=True)
    parser.add_argument("--negative-control", action="append", default=[])
    args = parser.parse_args()
    output = Path(args.reader_output).expanduser().resolve()
    receipt = json.loads((output / "READER_RECEIPT.json").read_text(encoding="utf-8"))
    genome = json.loads((output / "SONG_GENOME.json").read_text(encoding="utf-8"))
    reference, body = reader_decode_stereo(args.reference, sample_rate=22_050, duration_seconds=30.0)
    comparisons = {}
    for value in args.negative_control:
        label, raw_path = value.split("=", 1)
        candidate, _ = reader_decode_stereo(raw_path, sample_rate=22_050, duration_seconds=30.0)
        comparisons[label] = {
            "path": str(Path(raw_path).expanduser().resolve()),
            "raw_sha256": reader_sha256_file(raw_path),
            "metrics": reader_compare_audio(reference, candidate, 22_050),
        }
    proof = {
        "schema": "earcrate/song-reader-thesis-proof@1",
        "source_byte_sha256": body["source_byte_sha256"],
        "source_body_sha256": body["body_sha256"],
        "reader_receipt_sha256": receipt["receipt_sha256"],
        "genome_sha256": receipt["genome_sha256"],
        "thesis_ok": receipt["thesis_ok"],
        "heartbeat": {
            "tempo_bpm": genome["time_map"]["tempo_bpm"],
            "phrase_beats": genome["time_map"]["phrase_beats"],
            "phrase_starts": genome["time_map"]["phrase_starts"],
        },
        "atlas": receipt["counts"],
        "leave_one_out": {
            "audible_same_time_sources": receipt["execution"]["audible_same_time_recurrence_sources"],
            "metrics": receipt["metrics"]["recurrence_leave_one_out"],
        },
        "diagnostic": {
            "reference_derived_unique_audio_used": receipt["execution"]["reference_derived_unique_audio_used"],
            "publication_eligible": receipt["execution"]["publication_eligible"],
            "metrics": receipt["metrics"]["song_genome_diagnostic"],
            "foreground_start_seconds": genome["arms"]["residual"]["foreground_start_seconds"],
        },
        "negative_controls": comparisons,
        "gates": receipt["gates"],
    }
    proof = reader_seal(proof, "proof_sha256")
    (output / "THESIS_PROOF.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# EarCrate cephalopod reader — first-30 thesis proof",
        "",
        f"- Thesis gate: **{'PASS' if proof['thesis_ok'] else 'FAIL'}**",
        f"- Tempo: **{proof['heartbeat']['tempo_bpm']:.6f} BPM**",
        f"- Non-trivial phrase heartbeat: **{proof['heartbeat']['phrase_beats']} beats**",
        f"- Canonical events: **{proof['atlas']['canonical_events']}**",
        f"- Exact instances: **{proof['atlas']['instances']}**",
        f"- Recurrent events: **{proof['atlas']['recurrent_events']}**",
        f"- Recurrent instances: **{proof['atlas']['recurrent_instances']}**",
        f"- Audible recurrence instances using their own same-time PCM: **{proof['leave_one_out']['audible_same_time_sources']}**",
        f"- Unique foreground entrance: **{proof['diagnostic']['foreground_start_seconds']:.6f} s**",
        "",
        "## Raw correspondence",
        "",
        "| Artifact | Onset correlation | Mel-frame cosine | Chroma cosine | Waveform correlation |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, row in proof["negative_controls"].items():
        metrics = row["metrics"]
        lines.append(
            f"| {label} | {metrics['onset_envelope_correlation']:.4f} | {metrics['mel_frame_cosine_mean']:.4f} | "
            f"{metrics['chroma_frame_cosine_mean']:.4f} | {metrics['raw_waveform_correlation']:.4f} |"
        )
    for label, metrics in (
        ("recurrence leave-one-out", proof["leave_one_out"]["metrics"]),
        ("SongGenome diagnostic", proof["diagnostic"]["metrics"]),
    ):
        lines.append(
            f"| {label} | {metrics['onset_envelope_correlation']:.4f} | {metrics['mel_frame_cosine_mean']:.4f} | "
            f"{metrics['chroma_frame_cosine_mean']:.4f} | {metrics['raw_waveform_correlation']:.4f} |"
        )
    lines += [
        "",
        "The leave-one-out render replays each audible recurrent cell from a different occurrence; unique cells are silent.",
        "The diagnostic then adds two explicitly named same-time residual objects: the one-time drop transition and the foreground SourcePhrase candidate.",
        "It is therefore a song-reading proof, not a publication-eligible reconstruction.",
        "",
        "## Boundary",
        "",
        "- No invented MIDI chord or sustain contributes audio.",
        "- No symbolic cue contributes PCM.",
        "- No reference-conditioned mastering is used to pass correspondence.",
        "- The unique residual is reference-derived and blocks publication until replaced by eligible material.",
    ]
    (output / "THESIS_PROOF.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if proof["thesis_ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
