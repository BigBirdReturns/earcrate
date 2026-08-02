from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from earcrate.floor.release_governance import (
    floor_open_blind_review_campaign,
    floor_release_governance_schema_bundle,
    floor_publish_release,
    floor_verify_published_release,
)


def test_governance_v2_is_public_and_single_file_packaged(tmp_path: Path) -> None:
    import earcrate.floor as floor

    assert floor.floor_open_blind_review_campaign is floor_open_blind_review_campaign
    assert floor.floor_publish_release is floor_publish_release
    assert floor.floor_verify_published_release is floor_verify_published_release

    root = Path(__file__).resolve().parent.parent
    schemas = floor_release_governance_schema_bundle()
    assert len(schemas) == 12
    for name, value in schemas.items():
        path = root / "schemas" / name
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8")) == value

    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    package_cli = subprocess.run(
        [sys.executable, "-m", "earcrate", "floor", "release-governance-capability"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert package_cli.returncode == 0, package_cli.stdout + package_cli.stderr
    assert '"publication_is_staged_and_atomic": true' in package_cli.stdout

    schema_cli = subprocess.run(
        [sys.executable, "-m", "earcrate", "floor", "release-governance-schemas", str(tmp_path / "schemas")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert schema_cli.returncode == 0, schema_cli.stdout + schema_cli.stderr
    assert len(list((tmp_path / "schemas").glob("*.schema.json"))) == len(schemas)

    built = root / "dist" / "earcrate.py"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy; "
                f"ns=runpy.run_path({str(built)!r}, run_name='earcrate_governance_v2_probe'); "
                "required=('floor_open_blind_review_campaign','floor_publish_release','floor_verify_published_release'); "
                "assert all(callable(ns[name]) for name in required)"
            ),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr

    singlefile_cli = subprocess.run(
        [sys.executable, str(built), "floor", "release-governance-capability"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert singlefile_cli.returncode == 0, singlefile_cli.stdout + singlefile_cli.stderr
    assert '"private_assignment_authority_committed": true' in singlefile_cli.stdout
