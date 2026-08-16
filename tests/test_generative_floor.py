from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from typing import Callable

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


def _assert_raises(error_type: type[BaseException], message_fragment: str, fn: Callable[[], object]) -> None:
    try:
        fn()
    except error_type as exc:
        assert message_fragment in str(exc), f"expected {message_fragment!r} in {exc!r}"
        return
    except Exception as exc:
        raise AssertionError(f"expected {error_type.__name__}, received {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"expected {error_type.__name__}")


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
    _assert_raises(ValidationError, "pin at least one", lambda: validate_generation_request(broken))


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


def test_portable_music_server_contract_checks_install_and_runs_inline_audio(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        received: dict = {}

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send(self, value: dict) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send({"status": "ok", "loaded_models": ["ace_step_v15"], "worker_count": 1})
            elif self.path == "/api/models":
                self._send({"models": [{"id": "ace_step_v15", "name": "ACE-Step v1.5", "env": "ace_step_v15", "install_status": "installed", "env_installed": True, "weights_installed": True}]})
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            Handler.received = json.loads(self.rfile.read(length).decode("utf-8"))
            self._send({
                "status": "completed",
                "audio_base64": base64.b64encode(b"RIFF-http-generated-audio").decode("ascii"),
                "sample_rate": 48000,
                "duration_sec": 1.0,
                "inference_time_sec": 0.25,
                "format": "wav",
                "model": "ace_step_v15",
                "entry_id": "entry-test-001",
            })

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        catalog = load_json(CATALOG_PATH)
        provider = _provider(catalog, "ace-step-1.5")
        asset = _fake_asset(tmp_path)
        adapter = {
            "kind": "http_json",
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "health_endpoint": "/health",
            "models_endpoint": "/api/models",
            "model_id": "ace_step_v15",
            "require_model_installed": True,
            "endpoint": "/api/music/{model_id}",
            "request_template": {"duration": 1.0, "output_format": "wav", "skip_post_process": True, "model_params": {"task_type": "text2music"}},
            "timeout_seconds": 60,
        }
        probe = probe_provider(provider, local_override={"adapter": adapter, "model_assets": [asset], "execution_host_probe_sha256": "e" * 64})
        assert probe["ready"] is True
        assert probe["evidence"]["host_model"]["weights_installed"] is True
        assert probe["evidence"]["execution_host_probe_sha256"] == "e" * 64
        request_value = build_generation_request(
            provider_id="ace-step-1.5", task_mode="text_to_music", model_repository="ace-step/ACE-Step-1.5",
            model_revision="commit:test", model_assets=[{key: asset[key] for key in ("name", "sha256", "bytes")}],
            seed=99, prompt={"caption": "short test"},
        )
        receipt = execute_generation_request(
            request_value, provider=provider, probe=probe, local_adapter=adapter,
            private_source_paths={}, output_directory=tmp_path / "http-run", node_identity={"node_id": "test-node"},
        )
        assert receipt["outcome"] == "observed"
        assert receipt["execution"]["response_metadata"]["entry_id"] == "entry-test-001"
        assert Handler.received["model_params"]["task_type"] == "text2music"
        assert "params" not in Handler.received
        assert Handler.received["skip_post_process"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    import earcrate.reference_zero as rz

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
