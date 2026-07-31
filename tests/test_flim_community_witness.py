from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

from earcrate.specimen.community import (
    COMMUNITY_PACK_RECEIPT_KIND,
    community_bind_pack,
    community_validate_report,
)
from earcrate.specimen.flim import (
    FLIM_PROOF_PACK_SHA256,
    FLIM_REQUIRED_PACK_MEMBERS,
    FLIM_SPECIMEN_ID,
    flim_capability,
    flim_load_builtin,
)
from earcrate.specimen.model import SpecimenError, specimen_normalize_manifest, specimen_sha256_file


def _pack(path: Path, *, unsafe: bool = False) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FLIM_REQUIRED_PACK_MEMBERS:
            archive.writestr(f"proof/{name}", f"fixture:{name}\n")
        if unsafe:
            archive.writestr("../escape.txt", "no")


def _manifest_for_pack(manifest: dict, pack: Path) -> dict:
    value = deepcopy(manifest)
    value.pop("manifest_sha256", None)
    for row in value["artifacts"]:
        if row["artifact_id"] == "community_proof_pack":
            row["expected_sha256"] = specimen_sha256_file(pack)
    return value


def test_flim_report_preserves_the_intermediate_evidence_tier() -> None:
    manifest, report = flim_load_builtin()
    assert manifest["specimen_id"] == FLIM_SPECIMEN_ID
    assert manifest["metadata"]["evidence_tier"] == "community_symbolic_witness"
    assert report["proof_pack"]["sha256"] == FLIM_PROOF_PACK_SHA256
    assert report["witness"]["total_midi_note_ons"] == 2012
    assert report["adjacent_move"]["total_midi_note_ons"] == 538
    assert report["transport"]["selected_event_count"] == 6
    assert report["boundary"]["target_recording_bytes_used"] is False
    assert report["boundary"]["blind_audio_inference_used"] is False
    assert report["boundary"]["community_symbolic_sources_used"] is True
    assert report["boundary"]["whole_organism_passed"] is False
    assert community_validate_report(report)["report_sha256"] == report["report_sha256"]
    capability = flim_capability()
    assert capability["whole_organism_passed"] is False
    assert capability["blind_audio_inference_used"] is False


def test_community_pack_binding_is_exact_and_refuses_unsafe_archives(tmp_path: Path) -> None:
    manifest, report = flim_load_builtin()
    safe = tmp_path / "safe.zip"
    _pack(safe)
    receipt = community_bind_pack(
        manifest=_manifest_for_pack(manifest, safe),
        report=report,
        pack_path=safe,
        required_basenames=FLIM_REQUIRED_PACK_MEMBERS,
        output_path=tmp_path / "receipt.json",
    )
    assert receipt["kind"] == COMMUNITY_PACK_RECEIPT_KIND
    assert receipt["checks"]["exact_pack_identity"] is True
    assert receipt["checks"]["required_members_present"] is True
    assert receipt["checks"]["blind_audio_inference_used"] is False
    assert receipt["whole_organism_passed"] is False
    assert len(receipt["required_members"]) == len(FLIM_REQUIRED_PACK_MEMBERS)

    unsafe = tmp_path / "unsafe.zip"
    _pack(unsafe, unsafe=True)
    try:
        community_bind_pack(
            manifest=_manifest_for_pack(manifest, unsafe),
            report=report,
            pack_path=unsafe,
            required_basenames=FLIM_REQUIRED_PACK_MEMBERS,
        )
    except SpecimenError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("community proof-pack custody accepted path traversal")


def test_flim_manifest_and_report_match_their_schemas() -> None:
    root = Path(__file__).resolve().parent.parent
    manifest, report = flim_load_builtin()
    normalized = specimen_normalize_manifest(manifest)
    manifest_schema = json.loads((root / "schemas" / "earcrate_specimen_manifest_v1.schema.json").read_text(encoding="utf-8"))
    report_schema = json.loads((root / "schemas" / "earcrate_community_symbolic_report_v1.schema.json").read_text(encoding="utf-8"))
    receipt_schema = json.loads((root / "schemas" / "earcrate_community_symbolic_pack_receipt_v1.schema.json").read_text(encoding="utf-8"))
    assert manifest_schema["properties"]["evidence_tier"]["enum"]
    assert "symbolic" in manifest_schema["properties"]["artifacts"]["items"]["properties"]["branch"]["enum"]
    assert normalized["metadata"]["evidence_tier"] == report_schema["properties"]["evidence_tier"]["const"]
    assert receipt_schema["properties"]["whole_organism_passed"]["const"] is False


def test_flim_package_and_single_file_expose_the_same_report(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    package = subprocess.run(
        [sys.executable, "-m", "earcrate", "buffalo", "flim-report"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert package.returncode == 0, package.stdout + package.stderr
    package_payload = json.loads(package.stdout)
    assert package_payload["report"]["evidence_tier"] == "community_symbolic_witness"

    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    single = subprocess.run(
        [sys.executable, str(root / "dist" / "earcrate.py"), "buffalo", "flim-report"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert single.returncode == 0, single.stdout + single.stderr
    single_payload = json.loads(single.stdout)
    assert package_payload["report"]["report_sha256"] == single_payload["report"]["report_sha256"]
    assert package_payload["capability"]["proof_pack_sha256"] == single_payload["capability"]["proof_pack_sha256"]
