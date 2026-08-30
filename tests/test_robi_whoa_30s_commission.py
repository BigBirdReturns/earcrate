from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "earcrate_robi_whoa_30s_v1.py"
CONTRACT = ROOT / "configs" / "commissions" / "robi_whoa_30s_v1.json"


def _module():
    spec = importlib.util.spec_from_file_location("earcrate_robi_whoa_30s_v1", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_robi_commission_binds_exact_source_and_one_owner_candidate():
    contract = _contract()
    assert contract["commission_id"] == "EC-ROBI-WHOA-30S-001"
    assert contract["source"]["container_sha256"] == "50f5937511a8acce65c76a34b216d96d03d1f78ba26708ba8d62a9fe67cd120a"
    assert contract["source"]["analysis_bundle"]["sha256"] == "3c9d8b3142e1931dc758ab6853a4670364a83afddc9c37922eab9c857aa9a667"
    assert contract["target"]["frames"] == 1_440_000
    assert contract["target"]["maximum_owner_candidates"] == 1
    assert contract["private_execution"]["cloud_audio_generation_authorized"] is False


def test_robi_commission_forbids_the_failed_cloud_lineage():
    forbidden = set(_contract()["forbidden_mechanism_ids"])
    assert {
        "cloud_handwritten_synthesis",
        "oscillator_kick",
        "elementary_waveform_808",
        "general_midi_soundfont",
        "old_render_fallback",
        "floor_safe_rescue",
        "multi_option_owner_probe",
    } <= forbidden


def test_execution_request_exposes_real_estate_authorities_only():
    module = _module()
    contract = module.load_contract(CONTRACT)
    receipt = {
        "source": {"artifact_path": "S:/private/Robi.aac", "sha256": contract["source"]["container_sha256"], "bytes": contract["source"]["container_bytes"]},
        "analysis_bundle": {"artifact_path": "S:/private/failure.zip", "sha256": contract["source"]["analysis_bundle"]["sha256"], "bytes": contract["source"]["analysis_bundle"]["bytes"]},
    }
    request = module.build_execution_request(receipt, contract)
    assert request["execution_policy"]["provider_or_crate_absent"] == "refuse_without_audio"
    assert request["execution_policy"]["historical_robi_renders_are_inputs"] is False
    assert request["execution_policy"]["candidate_count_after_machine_triage"] == 1
    assert {row["kind"] for row in request["allowed_authorities"]} == {
        "ace_step_vocal_to_bgm",
        "midi_sag_vocal_to_bgm",
        "source_coherent_crate_rack",
    }


def test_candidate_validator_rejects_multiple_owner_options(tmp_path: Path):
    module = _module()
    manifest = tmp_path / "candidate.json"
    manifest.write_text(json.dumps({
        "schema_version": "earcrate_robi_whoa_candidate_v1",
        "commission_id": "EC-ROBI-WHOA-30S-001",
        "candidate_count": 2,
        "candidate_id": "bad-frontier",
    }), encoding="utf-8")
    with pytest.raises(module.CommissionError, match="exactly one candidate"):
        module.validate_candidate_manifest(manifest, CONTRACT)


def test_candidate_validator_rejects_forbidden_mechanism_before_audio(tmp_path: Path):
    module = _module()
    contract = _contract()
    receipt = tmp_path / "authority.json"
    receipt.write_text(json.dumps({"outcome": "observed", "request_sha256": "a" * 64, "receipt_sha256": "b" * 64, "node_identity_sha256": "c" * 64}), encoding="utf-8")
    manifest = tmp_path / "candidate.json"
    manifest.write_text(json.dumps({
        "schema_version": "earcrate_robi_whoa_candidate_v1",
        "commission_id": contract["commission_id"],
        "candidate_count": 1,
        "candidate_id": "bad-synth",
        "source": {"container_sha256": contract["source"]["container_sha256"]},
        "authority": {
            "kind": "ace_step_vocal_to_bgm",
            "provider_id": "ace-step-1.5",
            "receipt": {"path": receipt.name, "sha256": module.sha256_file(receipt)},
        },
        "construction": {
            "band_is_coherent_body": True,
            "mechanism_ids": ["cloud_handwritten_synthesis"],
            "prohibited_mechanisms_used": ["cloud_handwritten_synthesis"],
        },
    }), encoding="utf-8")
    with pytest.raises(module.CommissionError, match="prohibited mechanisms"):
        module.validate_candidate_manifest(manifest, CONTRACT)


def test_public_projection_contains_no_private_paths():
    module = _module()
    contract = _contract()
    validated = {
        "manifest": {
            "candidate_id": "candidate-001",
            "authority": {"kind": "ace_step_vocal_to_bgm", "provider_id": "ace-step-1.5", "private_path": "S:/secret"},
            "construction": {"mechanism_ids": ["provider_rhythm_body"]},
            "render": {"loop_cycles_printed": 4, "stem_sum_peak_error": 0.0, "boundary_discontinuity_dbfs": -90.0},
        },
        "canonical_frames": 1_440_000,
        "canonical_pcm_sha256": "d" * 64,
    }
    projection = module._public_projection(validated, contract)
    encoded = json.dumps(projection)
    assert "S:/" not in encoded
    assert projection["authority_boundary"]["owner_accepted"] is False
    assert projection["authority_boundary"]["private_paths_included"] is False
