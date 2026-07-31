from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys

from earcrate.floor.release_governance import floor_open_blind_review_campaign


def test_governance_v2_is_public_and_single_file_packaged() -> None:
    import earcrate.floor as floor

    assert floor.floor_open_blind_review_campaign is floor_open_blind_review_campaign
    root = Path(__file__).resolve().parent.parent
    build = subprocess.run(
        [sys.executable, str(root / "build" / "make_singlefile.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    built = root / "dist" / "earcrate.py"
    namespace = runpy.run_path(
        str(built),
        run_name="earcrate_governance_v2_probe",
    )
    assert callable(namespace["floor_open_blind_review_campaign"])
