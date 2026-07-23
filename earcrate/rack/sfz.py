from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from earcrate.rack.model import (
    RackError,
    rack_atomic_json,
    rack_sha256_file,
    rack_validate_revision,
    rack_verify_sources,
)

SFZ_RECEIPT_SCHEMA_VERSION = 1


def _sfz_number(value: float) -> str:
    text = f"{float(value):.9f}".rstrip("0").rstrip(".")
    return text or "0"


def _sfz_path(sample_path: str, output_dir: Path) -> str:
    path = Path(sample_path).expanduser().resolve()
    try:
        relative = os.path.relpath(path, output_dir)
    except ValueError:
        relative = str(path)
    return relative.replace("\\", "/")


def rack_sfz_text(rack: Mapping[str, Any], *, output_dir: str | Path) -> str:
    rack_validate_revision(rack)
    destination_root = Path(output_dir).expanduser().resolve()
    lines = [
        f"// EarCrate RackRevision {rack['rack_id']}",
        f"// rack_sha256={rack['rack_sha256']}",
        "<global>",
        "ampeg_sustain=100",
        "",
    ]
    for zone in rack["zones"]:
        sample = zone["sample"]
        loop = zone["loop"]
        key_lo, key_hi = [int(value) for value in zone["key_range"]]
        vel_lo, vel_hi = [int(value) for value in zone["velocity_range"]]
        trigger_mode = str(zone["trigger_mode"])
        if bool(loop["enabled"]):
            loop_mode = "loop_continuous"
        elif trigger_mode == "one_shot":
            loop_mode = "one_shot"
        else:
            loop_mode = "no_loop"
        lines.extend(
            [
                f"// zone_id={zone['zone_id']} slice_pcm_sha256={sample['slice_pcm_sha256']}",
                "<region>",
                f"sample={_sfz_path(str(sample['path']), destination_root)}",
                f"offset={int(sample['start_frame'])}",
                f"end={int(sample['end_frame']) - 1}",
                f"lokey={key_lo}",
                f"hikey={key_hi}",
                f"lovel={vel_lo}",
                f"hivel={vel_hi}",
                f"pitch_keycenter={int(zone['root_key'])}",
                f"pitch_keytrack={0 if rack['mode'] == 'trigger' else 100}",
                f"tune={_sfz_number(float(zone.get('tune_cents') or 0.0))}",
                f"volume={_sfz_number(float(zone.get('gain_db') or 0.0))}",
                f"pan={_sfz_number(float(zone.get('pan') or 0.0) * 100.0)}",
                f"ampeg_attack={_sfz_number(float(zone.get('attack_ms') or 0.0) / 1000.0)}",
                f"ampeg_release={_sfz_number(float(zone.get('release_ms') or 0.0) / 1000.0)}",
                f"loop_mode={loop_mode}",
            ]
        )
        if bool(loop["enabled"]):
            absolute_loop_start = int(sample["start_frame"]) + int(loop["start_frame"])
            absolute_loop_end = int(sample["start_frame"]) + int(loop["end_frame"]) - 1
            lines.extend(
                [
                    f"loop_start={absolute_loop_start}",
                    f"loop_end={absolute_loop_end}",
                ]
            )
            if int(loop.get("crossfade_frames") or 0) > 0:
                crossfade_seconds = int(loop["crossfade_frames"]) / float(sample["sample_rate"])
                lines.append(f"loop_crossfade={_sfz_number(crossfade_seconds)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def rack_compile_sfz(
    rack: Mapping[str, Any],
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Compile a sealed rack to portable SFZ object code plus a receipt."""
    rack_validate_revision(rack)
    source_receipt = rack_verify_sources(rack)
    destination = Path(output_path).expanduser().resolve()
    receipt_path = destination.with_suffix(destination.suffix + ".receipt.json")
    if not overwrite:
        conflicts = [str(path) for path in (destination, receipt_path) if path.exists()]
        if conflicts:
            raise FileExistsError("refusing to overwrite existing SFZ output(s): " + ", ".join(conflicts))
    if destination.suffix.lower() != ".sfz":
        raise RackError("SFZ output path must end in .sfz")
    text = rack_sfz_text(rack, output_dir=destination.parent)
    _atomic_text(destination, text)
    receipt = {
        "schema_version": SFZ_RECEIPT_SCHEMA_VERSION,
        "kind": "earcrate_sfz_compile",
        "ok": True,
        "rack_id": rack["rack_id"],
        "rack_sha256": rack["rack_sha256"],
        "path": str(destination),
        "sha256": rack_sha256_file(destination),
        "zone_count": len(rack["zones"]),
        "source_verification": source_receipt,
    }
    rack_atomic_json(receipt_path, receipt, overwrite=overwrite)
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_sha256"] = rack_sha256_file(receipt_path)
    return receipt
