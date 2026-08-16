#!/usr/bin/env python3
from __future__ import annotations

"""Build the deterministic source-free EarCrate Homelab organ-factory zipapp."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "dist" / "earcrate-homelab-factory.pyz"
MODULES = (
    "earcrate/estate/homelab_specimens.py",
    "earcrate/estate/homelab_factory.py",
)
OPTIONAL_STORE_MODULES = (
    "earcrate/estate/homelab_common.py",
    "earcrate/estate/_homelab_store_core.py",
    "earcrate/estate/homelab_store.py",
)
DATA_FILES = (
    "configs/homelab_factory/specimen-suite.v1.json",
    "configs/homelab_factory/provider-role-policy.v1.json",
    "configs/homelab_factory/provider-adapters.v1.json",
    "configs/homelab_factory/beggin-timing-config.json",
    "configs/homelab_factory/review-dimensions.json",
    "schemas/earcrate_homelab_cloud_specimens_v1.schema.json",
    "schemas/earcrate_homelab_factory_v1.schema.json",
    "schemas/earcrate_beggin_timing_pass_v1.schema.json",
)


def _write(archive: zipfile.ZipFile, name: str, data: bytes, *, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((0o100755 if executable else 0o100644) & 0xFFFF) << 16
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build(output: Path) -> dict:
    required = [ROOT / value for value in (*MODULES, *DATA_FILES)]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing factory files: " + ", ".join(missing))
    sources: dict[str, bytes] = {
        "__main__.py": b"from earcrate.estate.homelab_factory import factory_cli_main\nraise SystemExit(factory_cli_main())\n",
        "earcrate/__init__.py": b'"""Minimal root for the EarCrate Homelab organ factory."""\n',
        "earcrate/estate/__init__.py": b'"""Factory package."""\n',
    }
    for relative in (*MODULES, *DATA_FILES):
        sources[relative] = (ROOT / relative).read_bytes()
    store_sync_bundled = all((ROOT / relative).is_file() for relative in OPTIONAL_STORE_MODULES)
    if store_sync_bundled:
        for relative in OPTIONAL_STORE_MODULES:
            sources[relative] = (ROOT / relative).read_bytes()
    manifest = {
        "schema_version": 1,
        "kind": "earcrate_homelab_factory_zipapp_manifest",
        "entries": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(sources.items())
        },
        "boundary": {
            "provider_binaries_bundled": False,
            "model_weights_bundled": False,
            "credentials_bundled": False,
            "source_media_bundled": False,
            "derived_audition_audio_bundled": False,
            "standard_library_runtime": True,
            "homelab_store_sync_bundled": store_sync_bundled
        }
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).hexdigest()
    sources["FACTORY_BUILD_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            raw.write(b"#!/usr/bin/env python3\n")
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
                for entry in sorted(sources):
                    _write(archive, entry, sources[entry], executable=entry == "__main__.py")
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, output)
        if os.name != "nt":
            output.chmod(0o755)
            directory = os.open(str(output.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "ok": True,
        "output": str(output),
        "raw_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "bytes": int(output.stat().st_size),
        "manifest_sha256": manifest["manifest_sha256"],
        "entries": len(sources),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    print(json.dumps(build(Path(args.output).expanduser().resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
