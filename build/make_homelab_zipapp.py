#!/usr/bin/env python3
from __future__ import annotations

"""Build the deterministic, standard-library EarCrate Homelab zipapp."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ESTATE = ROOT / "earcrate" / "estate"
DEFAULT_OUTPUT = ROOT / "dist" / "earcrate-homelab.pyz"
REQUIRED_MODULES = {
    "model.py",
    "markers.py",
    "classify.py",
    "inspect.py",
    "traverse.py",
    "scan.py",
    "discover.py",
    "plan.py",
    "hardware.py",
    "campaign.py",
    "rig.py",
    "homelab_common.py",
    "homelab_catalog.py",
    "homelab.py",
    "_homelab_store_core.py",
    "homelab_store.py",
    "homelab_review.py",
    "homelab_ops.py",
    "homelab_cli.py",
    "cli.py",
    "__init__.py",
    "__main__.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_entry(archive: zipfile.ZipFile, name: str, data: bytes, *, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = 0o100755 if executable else 0o100644
    info.external_attr = (mode & 0xFFFF) << 16
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build(output: Path) -> dict:
    missing = sorted(name for name in REQUIRED_MODULES if not (ESTATE / name).is_file())
    if missing:
        raise SystemExit("missing Homelab modules: " + ", ".join(missing))
    schema = ROOT / "schemas" / "earcrate_homelab_v1.schema.json"
    if not schema.is_file():
        raise SystemExit("missing Homelab JSON Schema")

    sources: dict[str, bytes] = {
        "__main__.py": (
            "from earcrate.estate.homelab_cli import homelab_cli_main\n"
            "raise SystemExit(homelab_cli_main())\n"
        ).encode("utf-8"),
        "earcrate/__init__.py": b'"""Minimal root for the standalone Homelab executable."""\n',
        "schemas/earcrate_homelab_v1.schema.json": schema.read_bytes(),
    }
    for name in sorted(REQUIRED_MODULES):
        sources[f"earcrate/estate/{name}"] = (ESTATE / name).read_bytes()
    manifest = {
        "schema_version": 1,
        "kind": "earcrate_homelab_zipapp_manifest",
        "entries": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(sources.items())
        },
        "boundary": {
            "provider_binaries_bundled": False,
            "model_weights_bundled": False,
            "credentials_bundled": False,
            "source_media_bundled": False,
            "standard_library_runtime": True,
        },
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).hexdigest()
    sources["HOMELAB_BUILD_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False) as raw:
        temporary = Path(raw.name)
        raw.write(b"#!/usr/bin/env python3\n")
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
            for name in sorted(sources):
                _write_entry(archive, name, sources[name], executable=name == "__main__.py")
        raw.flush()
        os.fsync(raw.fileno())
    try:
        os.replace(temporary, output)
        if os.name != "nt":
            output.chmod(0o755)
            descriptor = os.open(str(output.parent), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "ok": True,
        "output": str(output),
        "raw_sha256": _sha256(output),
        "bytes": int(output.stat().st_size),
        "manifest_sha256": manifest["manifest_sha256"],
        "entries": len(sources),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    result = build(Path(args.output).expanduser().resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
