from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.generative_floor import (
    AUTHORITY_LIMITS,
    ValidationError,
    build_generation_frontier,
    build_generation_request,
    build_public_projection,
    compile_generation_campaign,
    execute_generation_request,
    generated_material_from_receipt,
    load_json,
    material_to_performance_source,
    probe_provider,
    seal,
    validate_generation_request,
    validate_provider_catalog,
)

CATALOG_PATH = ROOT / "configs" / "generative_floor" / "providers.v1.json"
CAMPAIGN_PATH = ROOT / "configs" / "generative_floor" / "beggin-suno-bones.v1.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_asset(tmp_path: Path) -> dict:
    path = tmp_path / "model.bin"
    path.write_bytes(b"model-weights-test")
    return {"name": path.name, "path": str(path), "sha256": _sha(path), "bytes": path.stat().st_size}


def _provider(catalog: dict, provider_id: str) -> dict:
    return next(dict(row) for row in catalog["providers"] if row["provider_id"] == provider_id)


def test_catalog_names_generation_bones_without_granting_authority() -> None:
    catalog = load_json(CATALOG_PATH)
    assert validate_provider_catalog(catalog) == catalog["catalog_sha256"]
    providers = {row["provider_id"]: row for row in catalog["providers"]}
    assert {"ace-step-1.5", "songgeneration-2", "heartmula", "yue", "diffrhythm", "muse", "songecho", "portable-music-server"}.issubset(providers)
    assert {"cover", "repaint", "complete", "vocal_to_bgm", "lego", "extract"}.issubset(set(providers["ace-step-1.5"]["capabilities"]))
    assert providers["heartmula"]["capabilities"] == ["lyrics_to_song", "text_to_music", "segment_generation"]
    for provider in providers.values():
        assert provider["authority"] == AUTHORITY_LIMITS


def test_generation_request_requires_exact_model_assets_and_seed(tmp_path: Path) -> None:
    asset = _fake_asset(tmp_path)
    request = build_generation_request(
        provider_id="ace-step-1.5",
        task_mode="complete",
        model_repository="ace-step/ACE-Step-1.5",
        model_revision="commit:test",
        model_assets=[{key: asset[key] for key in ("name", "sha256", "bytes")}],
        seed=77,
        prompt={"caption": "modern live band following a rubato lead vocal"},
        conditioning=[{"source_id": "lead", "container_sha256": "a" * 64, "role": "lead_vocal"}],
    )
    assert validate_generation_request(request) == request["request_sha256"]
    broken = dict(request)
    broken.pop("request_sha256")
    broken["model"] = {"repository": "x", "revision": "y", "assets": []}
    broken = seal(broken)
    with pytest.raises(ValidationError, match="pin at least one"):
        validate_generation_request(broken)


def test_campaign_selects_only_probed_capable_providers(tmp_path: Path) -> None:
    catalog = load_json(CATALOG_PATH)
    campaign_spec = load_json(CAMPAIGN_PATH)
    asset = _fake_asset(tmp_path)
    provider = _provider(catalog, "ace-step-1.5")
    probe = probe_provider(provider, local_override={"adapter": {"kind": "command", "argv": [sys.executable, "-c", "print('ok')"]}, "model_assets": [asset]})
    assert probe["ready"] is True
    plan = compile_generation_campaign(catalog=catalog, campaign_spec=campaign_spec, provider_probes=[probe])
    assert plan["summary"]["ready"] >= 5
    ace_complete = next(row for row in plan["tasks"] if row["task_id"] == "GF01-ace-complete-frankie")
    assert ace_complete["selected_provider_id"] == "ace-step-1.5"
    heart = next(row for row in plan["tasks"] if row["task_id"] == "GF09-heartmula-section-control")
    assert heart["status"] == "blocked"


def test_command_provider_produces_receipt_and_generated_material(tmp_path: Path) -> None:
    catalog = load_json(CATALOG_PATH)
    provider = _provider(catalog, "ace-step-1.5")
    asset = _fake_asset(tmp_path)
    source = tmp_path / "lead.wav"
    source.write_bytes(b"RIFF-private-source")
    source_sha = _sha(source)
    probe = probe_provider(provider, local_override={"adapter": {"kind": "command", "argv": [sys.executable, "-c", "print('ok')"]}, "model_assets": [asset]}, node_identity={"node_id": "test-node"})
    request = build_generation_request(
        provider_id="ace-step-1.5", task_mode="complete", model_repository="ace-step/ACE-Step-1.5", model_revision="commit:test",
        model_assets=[{key: asset[key] for key in ("name", "sha256", "bytes")}], seed=42,
        prompt={"caption": "modern rhythm section"}, conditioning=[{"source_id": "lead", "container_sha256": source_sha, "role": "lead_vocal"}],
    )
    writer = "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'RIFF-generated-audio')"
    output = tmp_path / "run"
    receipt = execute_generation_request(
        request, provider=provider, probe=probe,
        local_adapter={"kind": "command", "argv": [sys.executable, "-c", writer, "{output_dir}/take.wav"], "timeout_seconds": 60},
        private_source_paths={"lead": source}, output_directory=output, node_identity={"node_id": "test-node"},
        gpu_identity={"device": "0", "gpu_uuid_sha256": "b" * 64},
    )
    assert receipt["outcome"] == "observed"
    assert receipt["artifacts"][0]["name"] == "take.wav"
    assert receipt["authority"] == AUTHORITY_LIMITS
    material = generated_material_from_receipt(receipt, artifact_sha256=receipt["artifacts"][0]["sha256"], role="accompaniment", musical_function="modern band following source vocal", generation_strategy="generative_reperformance")
    assert material["status"] == "candidate_unreviewed"
    source_row = material_to_performance_source(material, source_id="generated-band-001")
    assert source_row["source_kind"] == "generated_material"
    assert source_row["container_sha256"] == receipt["artifacts"][0]["sha256"]


def test_frontier_deduplicates_audio_and_preserves_incumbent_control(tmp_path: Path) -> None:
    receipt = seal({
        "schema_version": 1, "kind": "earcrate_generation_run_receipt", "recorded_at": "2026-08-11T00:00:00Z",
        "request_sha256": "a" * 64, "provider_id": "ace-step-1.5", "provider_repository": {}, "task_mode": "complete",
        "model": {"repository": "x", "revision": "y", "assets": [{"name": "m", "sha256": "b" * 64, "bytes": 1}]},
        "seed": 1, "node": {}, "gpu": {}, "probe_sha256": "c" * 64, "outcome": "observed", "refusal": None,
        "conditioning": [], "artifacts": [{"name": "take.wav", "sha256": "d" * 64, "bytes": 20, "media_kind": "audio/wav"}],
        "execution": {}, "rights_scope": {"private_local_analysis": True, "public_upload_allowed": False}, "authority": dict(AUTHORITY_LIMITS),
    })
    one = generated_material_from_receipt(receipt, artifact_sha256="d" * 64, role="band", musical_function="one", generation_strategy="generative_reperformance")
    two = generated_material_from_receipt(receipt, artifact_sha256="d" * 64, role="band", musical_function="duplicate", generation_strategy="hybrid")
    frontier = build_generation_frontier([one, two], incumbent={"container_sha256": "e" * 64, "label": "reference-zero-v5"}, maximum_options=4)
    assert frontier["entries"][0]["kind"] == "incumbent_control"
    assert len(frontier["entries"]) == 2


def test_public_projection_removes_local_paths_and_secrets() -> None:
    request = seal({
        "schema_version": 1, "kind": "earcrate_generation_request", "created_at": "2026-08-11T00:00:00Z",
        "provider_id": "ace-step-1.5", "task_mode": "complete",
        "model": {"repository": "x", "revision": "y", "assets": [{"name": "m", "sha256": "a" * 64, "bytes": 1}]},
        "seed": 1, "prompt": {"caption": "test"}, "conditioning": [], "output_contract": {},
        "rights_scope": {"private_local_analysis": True, "public_upload_allowed": False, "publication_permission": False},
        "private_model_path": "S:\\Models\\ace-step", "api_token": "secret-value", "parent_request_sha256": None,
        "authority": dict(AUTHORITY_LIMITS),
    })
    projection = build_public_projection([request])
    text = json.dumps(projection)
    assert "S:\\\\Models" not in text
    assert "secret-value" not in text
    assert projection["boundary"]["source_audio_exported"] is False


def test_portable_music_server_is_a_host_not_model_authority() -> None:
    catalog = load_json(CATALOG_PATH)
    host = _provider(catalog, "portable-music-server")
    assert host["provider_class"] == "commodity_host"
    assert host["authority"] == AUTHORITY_LIMITS
    assert host["default_adapter"]["base_url"] == "http://127.0.0.1:9150"


def test_generated_material_source_enters_reference_zero_score() -> None:
    rz = pytest.importorskip("earcrate.reference_zero")
    source = {
        "source_id": "generated-band-001", "container_sha256": "f" * 64, "canonical_pcm_sha256": None,
        "source_kind": "generated_material", "generated_material_sha256": "e" * 64,
        "generation_receipt_sha256": "d" * 64, "provider_id": "ace-step-1.5", "task_mode": "complete",
        "role": "accompaniment", "musical_function": "modern band following source vocal",
    }
    score = rz.seal({
        "schema_version": 1, "kind": "earcrate_performance_score", "created_at": "2026-08-11T00:00:00Z",
        "timeline": {"sample_rate": 48000, "channels": 2, "duration_samples": 48000}, "sources": [source],
        "tracks": [{"track_id": "generated-band", "clips": [{"clip_id": "generated-band-clip", "source_id": "generated-band-001", "source_start_sample": 0, "source_end_sample": 48000, "target_start_sample": 0, "tempo_scale": 1.0, "pitch_semitones": 0.0, "gain_db": 0.0, "pan": 0.0, "fade_in_samples": 0, "fade_out_samples": 0}]}],
        "command_history": [], "master": {"gain_db": 0.0, "codec": "pcm_s24le"}, "authority": {"allow_unused_sources": False},
    })
    assert rz.validate_performance_score(score) == score["score_sha256"]
