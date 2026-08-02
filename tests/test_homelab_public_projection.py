from __future__ import annotations

import json
from pathlib import Path

from earcrate.estate.homelab_common import HOMELAB_SCHEMA_VERSION, homelab_seal, homelab_validate_seal
from earcrate.estate.homelab_ops import export_public_store
from earcrate.estate.homelab_redact import project_public_object
from earcrate.estate.homelab_store import HomelabStore

ROOT = Path(__file__).resolve().parent.parent


def _node() -> dict:
    return homelab_seal(
        {
            "schema_version": HOMELAB_SCHEMA_VERSION,
            "kind": "earcrate_homelab_node_receipt",
            "captured_at": "2026-08-01T00:00:00Z",
            "catalog_sha256": "1" * 64,
            "rig_sha256": "2" * 64,
            "node_id": "node_test",
            "host": {
                "python_executable": "C:\\Users\\Owner\\EarCrate\\python.exe",
                "working_directory": "/home/owner/earcrate",
                "operator_note": "model cache is at D:\\Models\\MERT\\weights.bin",
                "diagnostic_note": "temporary file lives at /mnt/fast/earcrate/cache.bin",
                "remote_url": "https://example.invalid/provider/status",
                "api_key": "do-not-export-this-api-key",
                "access_token": "do-not-export-this-access-token",
                "review_token_sha256": "f" * 64,
            },
            "roots": [{"path": "D:\\EarCrate\\Models", "exists": True}],
            "nvidia": {},
            "audio_devices": {},
            "python_distributions": {},
            "executables": {
                "ffmpeg": {
                    "available": True,
                    "path": "/usr/bin/ffmpeg",
                    "version": "test",
                }
            },
            "credential_environment_names": ["FREESOUND_API_KEY"],
            "boundary": {
                "provider_process_executed": False,
                "model_loaded": False,
                "network_request_made": False,
                "source_audio_decoded": False,
                "capability_is_not_quality_acceptance": True,
                "credential_values_recorded": False,
            },
        }
    )


def test_public_projection_redacts_windows_posix_embedded_paths_and_secrets() -> None:
    node = _node()
    projection = project_public_object(node)
    homelab_validate_seal(projection)
    text = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    assert "C:\\\\Users" not in text
    assert "D:\\\\EarCrate" not in text
    assert "D:\\\\Models" not in text
    assert "/home/owner/earcrate" not in text
    assert "/usr/bin/ffmpeg" not in text
    assert "/mnt/fast/earcrate" not in text
    assert "do-not-export-this-api-key" not in text
    assert "do-not-export-this-access-token" not in text
    assert projection["payload"]["host"]["api_key"] == "redacted"
    assert projection["payload"]["host"]["access_token"] == "redacted"
    assert projection["payload"]["host"]["review_token_sha256"] == "f" * 64
    assert projection["payload"]["host"]["remote_url"] == "https://example.invalid/provider/status"
    assert projection["payload"]["credential_environment_names"] == ["FREESOUND_API_KEY"]
    assert projection["source_identity"] == node["node_sha256"]
    assert projection["redaction"]["absolute_paths"] == 6
    assert projection["redaction"]["sensitive_fields"] == 2
    assert projection["redaction"]["payload_is_original_authority"] is False


def test_public_export_contains_projections_not_authoritative_node_bytes(tmp_path: Path) -> None:
    node = _node()
    store_root = tmp_path / "store"
    with HomelabStore(store_root) as store:
        store.ingest_object(node, visibility="public")
    result = export_public_store(store_root, tmp_path / "public")
    assert result["authoritative_object_bytes_exported"] is False
    manifest = json.loads((tmp_path / "public" / "manifest.json").read_text(encoding="utf-8"))
    entry = next(row for row in manifest["entries"] if row.get("source_identity") == node["node_sha256"])
    projection = json.loads((tmp_path / "public" / entry["path"]).read_text(encoding="utf-8"))
    homelab_validate_seal(projection)
    assert projection["kind"] == "earcrate_homelab_public_projection"
    assert projection["projection_sha256"] == entry["projection_identity"]
    exported = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    assert "C:\\\\Users" not in exported
    assert "D:\\\\Models" not in exported
    assert "/usr/bin/ffmpeg" not in exported
    assert "/mnt/fast/earcrate" not in exported
    assert "do-not-export-this-api-key" not in exported


def test_public_projection_schema_is_versioned() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "earcrate_homelab_public_projection_v1.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["properties"]["kind"]["const"] == "earcrate_homelab_public_projection"
    assert schema["properties"]["redaction"]["properties"]["payload_is_original_authority"]["const"] is False
