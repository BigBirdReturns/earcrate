from __future__ import annotations

"""Portable, content-addressed rack relocation.

RackRevision source paths are execution-local.  ``portable_sample`` records the
bundle-relative source identity so relocation can produce a new sealed rack
without weakening byte/slice verification or pretending the old rack hash is
unchanged.
"""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from earcrate.midi.model import midi_sha256_json

from earcrate.rack.model import (
    RackError,
    rack_atomic_json,
    rack_sample_identity,
    rack_seal_draft,
    rack_sha256_file,
    rack_validate_revision,
)

PORTABLE_BUNDLE_SCHEMA = "earcrate/portable-rack-bundle@1"
PORTABLE_REBASE_SCHEMA = "earcrate/portable-rack-rebase@1"


def _inside(root: Path, relative: str) -> Path:
    rel = Path(str(relative))
    if rel.is_absolute() or ".." in rel.parts:
        raise RackError(f"portable sample path must be bundle-relative: {relative}")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RackError(f"portable sample escapes bundle root: {relative}") from exc
    return candidate


def _draft_from_sealed(rack: Mapping[str, Any]) -> dict[str, Any]:
    rack_validate_revision(rack)
    return {
        "rack_id": str(rack["rack_id"]),
        "name": str(rack["name"]),
        "mode": str(rack["mode"]),
        "metadata": deepcopy(dict(rack.get("metadata") or {})),
        "created_by": deepcopy(dict(rack.get("created_by") or {})),
        "zones": [
            {
                key: deepcopy(value)
                for key, value in dict(zone).items()
                if key != "sample"
            }
            | {
                "sample": {
                    "path": str((zone.get("sample") or {}).get("path") or ""),
                    "start_frame": int((zone.get("sample") or {}).get("start_frame") or 0),
                    "end_frame": int((zone.get("sample") or {}).get("end_frame") or 0),
                }
            }
            for zone in rack["zones"]
        ],
    }


def rack_rebase_portable_revision(
    rack: Mapping[str, Any],
    bundle_root: str | Path,
    *,
    actor: str = "earcrate",
    reason: str = "portable rack relocation",
) -> dict[str, Any]:
    """Resolve bundle-relative samples and seal a relocation child RackRevision."""
    rack_validate_revision(rack)
    root = Path(bundle_root).expanduser().resolve()
    if not root.is_dir():
        raise RackError(f"portable bundle root is not a directory: {root}")
    draft = _draft_from_sealed(rack)
    draft["created_by"] = {"actor": str(actor), "reason": str(reason)}
    draft.setdefault("metadata", {})["parent_rack_sha256"] = str(rack["rack_sha256"])
    draft["metadata"]["portable_bundle_root"] = str(root)
    zone_receipts = []
    for ordinal, (source_zone, draft_zone) in enumerate(zip(rack["zones"], draft["zones"])):
        portable = dict(source_zone.get("portable_sample") or {})
        relative = str(portable.get("relative_path") or "")
        if not relative:
            raise RackError(f"zone {source_zone['zone_id']} has no portable_sample.relative_path")
        source = _inside(root, relative)
        if not source.is_file():
            raise RackError(f"portable sample is missing: {source}")
        expected = source_zone["sample"]
        expected_byte = str(portable.get("byte_sha256") or expected["byte_sha256"])
        expected_slice = str(portable.get("slice_pcm_sha256") or expected["slice_pcm_sha256"])
        if rack_sha256_file(source) != expected_byte:
            raise RackError(f"portable sample byte identity changed: {relative}")
        actual = rack_sample_identity(
            source,
            start_frame=int(expected["start_frame"]),
            end_frame=int(expected["end_frame"]),
        )
        if actual["slice_pcm_sha256"] != expected_slice:
            raise RackError(f"portable sample slice identity changed: {relative}")
        draft_zone["sample"] = {
            "path": str(source),
            "start_frame": int(expected["start_frame"]),
            "end_frame": int(expected["end_frame"]),
        }
        draft_zone["portable_sample"] = {
            "relative_path": relative,
            "byte_sha256": expected_byte,
            "slice_pcm_sha256": expected_slice,
        }
        zone_receipts.append(
            {
                "zone_id": str(source_zone["zone_id"]),
                "relative_path": relative,
                "resolved_path": str(source),
                "byte_sha256": expected_byte,
                "slice_pcm_sha256": expected_slice,
                "ok": True,
            }
        )
    sealed = rack_seal_draft(draft)
    receipt = {
        "schema": PORTABLE_REBASE_SCHEMA,
        "ok": True,
        "bundle_root": str(root),
        "rack_id": str(rack["rack_id"]),
        "parent_rack_sha256": str(rack["rack_sha256"]),
        "rebased_rack_sha256": str(sealed["rack_sha256"]),
        "zone_count": len(zone_receipts),
        "zones": zone_receipts,
    }
    receipt["receipt_sha256"] = midi_sha256_json(receipt)
    return {"rack": sealed, "receipt": receipt}


def rack_rebase_portable_bundle(
    manifest_path: str | Path,
    bundle_root: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    actor: str = "earcrate",
    reason: str = "portable rack bundle relocation",
) -> dict[str, Any]:
    """Rebase every rack in a portable bundle and emit a new manifest/receipt."""
    manifest_source = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    if str(manifest.get("schema") or "") != PORTABLE_BUNDLE_SCHEMA:
        raise RackError("unsupported portable rack bundle schema")
    root = Path(bundle_root).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite nonempty portable rack output: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    racks_dir = destination / "racks"
    racks_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    rack_manifest = []
    for row_value in manifest.get("rack_revisions") or []:
        row = dict(row_value or {})
        relative = str(row.get("relative_path") or "")
        rack_path = _inside(root, relative)
        rack = json.loads(rack_path.read_text(encoding="utf-8"))
        expected_raw = str(row.get("raw_sha256") or "")
        if expected_raw and rack_sha256_file(rack_path) != expected_raw:
            raise RackError(f"portable rack artifact changed: {relative}")
        result = rack_rebase_portable_revision(rack, root, actor=actor, reason=reason)
        sealed = result["rack"]
        output = racks_dir / f"{sealed['rack_id']}-{sealed['rack_sha256'][:12]}.rack.json"
        rack_atomic_json(output, sealed, overwrite=overwrite)
        rows.append(result["receipt"])
        rack_manifest.append(
            {
                "relative_path": output.relative_to(destination).as_posix(),
                "raw_sha256": rack_sha256_file(output),
                "rack_id": sealed["rack_id"],
                "rack_sha256": sealed["rack_sha256"],
                "zone_count": len(sealed["zones"]),
            }
        )
    new_manifest = deepcopy(dict(manifest))
    new_manifest["bundle_root_relative"] = "."
    new_manifest["rack_revisions"] = rack_manifest
    new_manifest["parent_manifest_raw_sha256"] = rack_sha256_file(manifest_source)
    new_manifest.pop("manifest_sha256", None)
    new_manifest["manifest_sha256"] = midi_sha256_json(new_manifest)
    manifest_output = destination / "portable-rack-bundle.rebased.json"
    rack_atomic_json(manifest_output, new_manifest, overwrite=overwrite)
    receipt = {
        "schema": "earcrate/portable-rack-bundle-rebase@1",
        "ok": True,
        "source_manifest": str(manifest_source),
        "source_manifest_raw_sha256": rack_sha256_file(manifest_source),
        "bundle_root": str(root),
        "output_dir": str(destination),
        "rack_count": len(rows),
        "racks": rows,
        "rebased_manifest_path": str(manifest_output),
        "rebased_manifest_sha256": new_manifest["manifest_sha256"],
    }
    receipt["receipt_sha256"] = midi_sha256_json(receipt)
    receipt_output = destination / "portable-rack-bundle.rebase.receipt.json"
    rack_atomic_json(receipt_output, receipt, overwrite=overwrite)
    return {"manifest": new_manifest, "receipt": receipt, "racks": rack_manifest}


__all__ = [
    "PORTABLE_BUNDLE_SCHEMA",
    "PORTABLE_REBASE_SCHEMA",
    "rack_rebase_portable_revision",
    "rack_rebase_portable_bundle",
]
