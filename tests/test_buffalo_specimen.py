from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import mido

from earcrate.specimen.children import children_compile_score_branch
from earcrate.specimen.convergence import specimen_compare_score_audio
from earcrate.specimen.gate import specimen_build_buffalo_gate
from earcrate.specimen.model import (
    SpecimenError,
    specimen_make_observation,
    specimen_read_json,
    specimen_seal_observation_ledger,
    specimen_sha256_file,
)

PPQ = 192
BAR = PPQ * 4


def _path() -> list[int]:
    order: list[int] = []
    order.extend(range(1, 5))
    order.extend(range(5, 9))
    order.extend(range(5, 8))
    order.append(9)
    order.extend(range(10, 18))
    order.extend(range(10, 18))
    order.extend(range(18, 54))
    order.extend(range(54, 62))
    order.extend(range(54, 60))
    order.extend(range(62, 65))
    order.extend(range(34, 53))
    order.extend(range(65, 70))
    assert len(order) == 105
    return order


def _midi(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("set_tempo", tempo=round(60_000_000 / 130), time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    midi.tracks.append(conductor)
    for name, channel, note in (("Right Hand", 0, 77), ("Left Hand", 1, 49)):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        track.append(mido.Message("note_on", channel=channel, note=note, velocity=88, time=0))
        track.append(mido.Message("note_off", channel=channel, note=note, velocity=0, time=PPQ))
        midi.tracks.append(track)
    midi.save(path)


def _fixture(tmp_path: Path) -> tuple[dict, dict, dict[str, str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    score_pdf = tmp_path / "score.pdf"
    score_pdf.write_bytes(b"%PDF-1.4\nsynthetic Buffalo Gate fixture\n%%EOF\n")
    pdf_sha = specimen_sha256_file(score_pdf)

    annotations = specimen_read_json(root / "specimens" / "children_v1.annotations.json")
    annotations["source_pdf_sha256"] = pdf_sha
    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text(json.dumps(annotations, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    per_measure: dict[str, dict] = {
        str(measure): {"treble": [], "bass": []} for measure in range(1, 70)
    }
    per_measure["1"]["treble"] = [
        {"kind": "note", "midi": 77, "pitch": "F5", "beat": 0.0, "duration": 1.0}
    ]
    per_measure["1"]["bass"] = [
        {"kind": "note", "midi": 49, "pitch": "Db3", "beat": 0.0, "duration": 1.0}
    ]
    occurrence_counts: dict[int, int] = {}
    occurrences = []
    for index, measure in enumerate(_path()):
        occurrence_counts[measure] = occurrence_counts.get(measure, 0) + 1
        occurrences.append(
            {
                "order_index": index,
                "measure": measure,
                "occurrence": occurrence_counts[measure],
                "start_beat": index * 4.0,
            }
        )
    extraction = {
        "schema_version": 1,
        "kind": "children_vector_score_extraction",
        "score": {
            "title": "Children",
            "credited_artist": "Robert Miles",
            "credited_composer": "Roberto Concina",
            "tempo_bpm": 130.0,
            "meter": "4/4",
            "key_signature": "four flats (F minor / A-flat major)",
            "printed_measure_count": 69,
            "linearized_measure_count": 105,
        },
        "source_pdf": {"sha256": pdf_sha},
        "method": {"type": "synthetic test fixture"},
        "measure_events": per_measure,
        "occurrences": occurrences,
        "linear_note_counts": {"treble": 1, "bass": 1},
    }
    extraction_path = tmp_path / "extraction.json"
    extraction_path.write_text(json.dumps(extraction, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    midi_path = tmp_path / "score.mid"
    _midi(midi_path)
    proof = {
        "schema_version": 1,
        "kind": "children_proof_receipt",
        "complete": True,
        "source_pdf_sha256": pdf_sha,
        "printed_measures": 69,
        "linearized_measures": 105,
        "tempo_bpm": 130.0,
        "midi": {"note_count": 2, "instrument_count": 2},
    }
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    mix_score = {
        "schema_version": 1,
        "kind": "earcrate_mix_score",
        "title": "synthetic specimen transport",
        "clock": {"bpm": 130.0, "beats_per_bar": 4, "sample_rate": 8000},
        "end_beat": 4.0,
        "assets": [{"asset_id": "source", "path": "source.wav", "source_bpm": 130.0}],
        "decks": [{"deck_id": "A", "crossfader_side": "A"}],
        "events": [
            {"at_beat": 0.0, "deck_id": "A", "op": "load", "asset_id": "source"},
            {"at_beat": 0.0, "deck_id": "A", "op": "play", "source_beat": 0.0},
        ],
    }
    mix_score_path = tmp_path / "mixscore.json"
    mix_score_path.write_text(json.dumps(mix_score, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    mix_ledger = {
        "schema_version": 1,
        "kind": "earcrate_mix_execution_ledger",
        "complete": True,
        "selected_event_count": 2,
        "executed_event_count": 2,
        "refused_event_count": 0,
        "stem_reconciliation_max_abs": 0.0,
        "master_pcm_f32le_sha256": "0" * 64,
        "stem_pcm_f32le_sha256": {"A": "1" * 64},
        "events": [
            {"event_id": "fixture_load", "op": "load", "status": "executed"},
            {"event_id": "fixture_play", "op": "play", "status": "executed"},
        ],
    }
    mix_ledger_path = tmp_path / "mix.events.json"
    mix_ledger_path.write_text(json.dumps(mix_ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def artifact(artifact_id: str, branch: str, media_kind: str, path: Path, required_for: list[str]) -> dict:
        return {
            "artifact_id": artifact_id,
            "branch": branch,
            "media_kind": media_kind,
            "status": "bound",
            "required_for": required_for,
            "expected_sha256": specimen_sha256_file(path),
            "path_hint": path.name,
        }

    manifest = {
        "schema_version": 1,
        "kind": "earcrate_specimen_manifest",
        "specimen_id": "children_v1",
        "title": "Children synthetic gate fixture",
        "credited_artist": "Robert Miles",
        "credited_composer": "Roberto Concina",
        "rights": {"synthetic_test_fixture": True},
        "artifacts": [
            artifact("score_pdf", "score", "application/pdf", score_pdf, ["score_gate"]),
            artifact("score_annotations", "score", "application/json", annotations_path, ["score_gate"]),
            artifact("score_extraction", "score", "application/json", extraction_path, ["score_gate"]),
            artifact("score_reconstruction_midi", "score", "audio/midi", midi_path, ["score_gate"]),
            artifact("score_proof_receipt", "score", "application/json", proof_path, ["score_gate"]),
            artifact("mix_score", "performance", "application/json", mix_score_path, ["mixscore_realization"]),
            artifact("mix_execution_ledger", "performance", "application/json", mix_ledger_path, ["mixscore_realization"]),
            {
                "artifact_id": "reference_recording",
                "branch": "audio",
                "media_kind": "audio/*",
                "status": "unbound",
                "required_for": ["audio_inference", "cross_modal_convergence"],
                "expected_sha256": None,
                "path_hint": "external",
            },
        ],
        "expected": {
            "tempo_bpm": 130.0,
            "meter": {"numerator": 4, "denominator": 4},
            "key_signature": {"fifths": -4, "tonic_pc": 5, "mode": "minor"},
            "printed_measure_count": 69,
            "performed_measure_count": 105,
            "midi_note_count": 2,
            "midi_instrument_names": ["Right Hand", "Left Hand"],
            "mix_selected_event_count": 2,
            "mix_executed_event_count": 2,
            "mix_refused_event_count": 0,
            "mix_stem_reconciliation_max_abs": 0.0,
        },
        "metadata": {},
    }
    bindings = {
        "score_pdf": str(score_pdf),
        "score_annotations": str(annotations_path),
        "score_extraction": str(extraction_path),
        "score_reconstruction_midi": str(midi_path),
        "score_proof_receipt": str(proof_path),
        "mix_score": str(mix_score_path),
        "mix_execution_ledger": str(mix_ledger_path),
    }
    return manifest, annotations, bindings


def _compile(tmp_path: Path, suffix: str) -> dict:
    manifest, annotations, bindings = _fixture(tmp_path / f"fixture-{suffix}")
    output = tmp_path / f"output-{suffix}"
    return children_compile_score_branch(
        manifest=manifest,
        annotations=annotations,
        bindings=bindings,
        output_dir=output,
        repository_root=Path(__file__).resolve().parent.parent,
    )


def test_children_score_branch_compiles_all_score_side_organs_and_blocks_missing_audio(tmp_path: Path) -> None:
    result = _compile(tmp_path, "one")
    receipt = result["receipt"]
    assert result["ok"] is True and result["complete"] is True
    assert receipt["counts"]["notes"] == 2
    assert receipt["counts"]["printed_measures"] == 69
    assert receipt["counts"]["performed_measures"] == 105
    assert receipt["counts"]["printed_chord_symbols"] == 36
    assert receipt["counts"]["harmony_frames"] > 0
    assert receipt["checks"]["score_midi_note_identity"] is True
    assert receipt["checks"]["mixscore_execution_complete"] is True
    assert receipt["cross_modal_status"] == "blocked"

    root = Path(result["output_dir"])
    gate = specimen_build_buffalo_gate(
        manifest=specimen_read_json(root / "specimen.manifest.bound.json"),
        score_ledger=specimen_read_json(root / "score.observation-ledger.json"),
        score_branch_receipt=specimen_read_json(root / "score.branch.receipt.json"),
    )
    assert gate["ok"] is True
    assert gate["overall_status"] == "blocked"
    assert gate["buffalo_gate_passed"] is False
    statuses = {row["organ_id"]: row["status"] for row in gate["receipt"]["organs"]}
    for organ in ("score_custody", "notation_perception", "form_graph", "harmony_frames", "exact_midi_authority", "mixscore_source_transports"):
        assert statuses[organ] == "passed"
    for organ in ("cephalopod_audio_inference", "cross_modal_convergence", "proof_carrying_adjacent_move", "sealed_rack_realization", "review_patch_circulation", "campaign_evolution"):
        assert statuses[organ] == "blocked"


def test_children_score_branch_is_path_independent_and_deterministic(tmp_path: Path) -> None:
    first = _compile(tmp_path, "first")
    second = _compile(tmp_path, "second")
    for key in ("score_ledger_sha256", "form_graph_sha256", "performance_path_sha256", "answer_key_sha256"):
        assert first["receipt"][key] == second["receipt"][key]


def test_audio_branch_refuses_score_taint() -> None:
    observation = specimen_make_observation(
        specimen_id="children_v1",
        branch="audio",
        kind="tempo",
        address={"scope": "recording"},
        value={"bpm": 130.0},
        confidence=1.0,
        source_artifact_ids=["recording"],
        provider="fixture",
        provider_version="1",
    )
    try:
        specimen_seal_observation_ledger(
            {
                "schema_version": 1,
                "kind": "earcrate_observation_ledger",
                "specimen_id": "children_v1",
                "branch": "audio",
                "inputs": [
                    {
                        "artifact_id": "recording",
                        "branch": "audio",
                        "sha256": "a" * 64,
                        "ancestor_branches": ["audio", "score"],
                    }
                ],
                "observations": [observation],
            }
        )
    except SpecimenError as exc:
        assert "tainted" in str(exc)
    else:
        raise AssertionError("audio branch accepted score-derived ancestry")


def test_independent_audio_ledger_can_converge_only_after_sealing(tmp_path: Path) -> None:
    result = _compile(tmp_path, "convergence")
    score = specimen_read_json(Path(result["output_dir"]) / "score.observation-ledger.json")
    needed = {"tempo", "meter", "key_signature", "performed_note", "performed_harmony"}
    observations = []
    for row in score["observations"]:
        if row["kind"] not in needed:
            continue
        observations.append(
            specimen_make_observation(
                specimen_id="children_v1",
                branch="audio",
                kind=str(row["kind"]),
                address=deepcopy(dict(row["address"])),
                value=deepcopy(row["value"]),
                confidence=0.99,
                source_artifact_ids=["synthetic_recording"],
                provider="independent_audio_fixture",
                provider_version="1",
            )
        )
    audio = specimen_seal_observation_ledger(
        {
            "schema_version": 1,
            "kind": "earcrate_observation_ledger",
            "specimen_id": "children_v1",
            "branch": "audio",
            "inputs": [
                {
                    "artifact_id": "synthetic_recording",
                    "branch": "audio",
                    "sha256": "b" * 64,
                    "ancestor_branches": ["audio"],
                }
            ],
            "observations": observations,
            "metadata": {"score_branch_consulted": False},
        }
    )
    report = specimen_compare_score_audio(score, audio)
    assert report["complete"] is True
    assert report["independence"]["audio_score_taint"] is False
    assert report["passed_metric_count"] == report["required_metric_count"]


def test_buffalo_cli_capability_uses_real_package_entrypoint(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    run = subprocess.run(
        [sys.executable, "-m", "earcrate.specimen", "capability"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["ready"] is True
    assert payload["score_branch"]["form_graph"] is True
    assert "cross-modal convergence" in payload["full_gate_requires"]


def test_buffalo_schema_contracts_and_external_media_boundary(tmp_path: Path) -> None:
    result = _compile(tmp_path, "schemas")
    root = Path(__file__).resolve().parent.parent
    runtime = {
        "earcrate_specimen_manifest_v1.schema.json": specimen_read_json(Path(result["output_dir"]) / "specimen.manifest.bound.json"),
        "earcrate_observation_ledger_v1.schema.json": specimen_read_json(Path(result["output_dir"]) / "score.observation-ledger.json"),
        "earcrate_form_graph_v1.schema.json": specimen_read_json(Path(result["output_dir"]) / "score.form-graph.json"),
        "earcrate_performance_path_v1.schema.json": specimen_read_json(Path(result["output_dir"]) / "score.performance-path.json"),
        "earcrate_score_answer_key_v1.schema.json": specimen_read_json(Path(result["output_dir"]) / "score.answer-key.json"),
        "earcrate_children_score_branch_receipt_v1.schema.json": specimen_read_json(Path(result["output_dir"]) / "score.branch.receipt.json"),
    }
    for filename, value in runtime.items():
        schema = specimen_read_json(root / "schemas" / filename)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["properties"]["schema_version"]["const"] == value["schema_version"] == 1
        assert schema["properties"]["kind"]["const"] == value["kind"]

    manifest = specimen_read_json(root / "specimens" / "children_v1.json")
    assert manifest["rights"]["source_media_committed"] is False
    assert manifest["rights"]["score_media_external"] is True
    assert manifest["rights"]["recording_media_external"] is True
    assert all(not str(row.get("path_hint") or "").startswith("proofs/") for row in manifest["artifacts"])
    proof = specimen_read_json(root / "proofs" / "specimens" / "children_v1.score-side.proof.json")
    assert proof["source_media_bundled"] is False
    assert proof["buffalo_gate"]["overall_status"] == "blocked"
    assert proof["boundary"]["whole_organism_passed"] is False


def test_buffalo_top_level_package_dispatch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    run = subprocess.run(
        [sys.executable, "-m", "earcrate", "buffalo", "capability"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["kind"] == "earcrate_buffalo_gate_capability"
    assert payload["ready"] is True
    assert payload["branch_isolation"]["audio"] == ["audio"]


def test_buffalo_single_file_dispatch_and_embedded_specimen(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    artifact = root / "dist" / "earcrate.py"
    run = subprocess.run(
        [sys.executable, str(artifact), "buffalo", "capability"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    assert payload["ready"] is True
    assert payload["specimen_ids"] == ["children_v1"]
    namespace_run = subprocess.run(
        [sys.executable, str(artifact), "buffalo", "children-bindings", str(tmp_path / "bindings.json")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert namespace_run.returncode == 0, namespace_run.stdout + namespace_run.stderr
    template = specimen_read_json(tmp_path / "bindings.json")
    assert template["specimen_id"] == "children_v1"
    assert "score_pdf" in template["bindings"]


def test_buffalo_harvest_registers_reader_transports_and_cross_organ_gate() -> None:
    from earcrate.music.heritage import music_buffalo_harvest_manifest

    rows = {row["organ"]: row for row in music_buffalo_harvest_manifest()["organs"]}
    assert rows["cephalopod_observation_ledger_and_song_genome"]["disposition"] == "preserve"
    assert rows["mixscore_independent_source_transports"]["disposition"] == "preserve"
    assert rows["cross_organ_specimen_gate"]["disposition"] == "preserve"
