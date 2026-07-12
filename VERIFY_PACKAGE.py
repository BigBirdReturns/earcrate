#!/usr/bin/env python3
import argparse, compileall, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description="Verify the EarCrate release package")
parser.add_argument(
    "--skip-gates",
    action="store_true",
    help="skip the gate suite only when it already passed in this same CI job",
)
args = parser.parse_args()
ok = compileall.compile_dir(str(ROOT / "earcrate"), quiet=1)
checks = {"package_compiles": bool(ok),
          "one_changelog": (ROOT / "CHANGELOG.md").exists() and not list(ROOT.glob("PATCH_NOTES_*.txt")),
          "static_ui": (ROOT / "earcrate/ui/static/index.html").exists()}
r = subprocess.run([sys.executable, str(ROOT / "build/make_singlefile.py")], capture_output=True, text=True)
checks["singlefile_builds"] = r.returncode == 0
if checks["singlefile_builds"]:
    t = subprocess.run([sys.executable, str(ROOT / "dist/earcrate.py"), "--self-test"], capture_output=True, text=True, timeout=600)
    checks["singlefile_selftest"] = "SELF_TEST_OK" in (t.stdout + t.stderr)
skipped = []
if args.skip_gates:
    skipped.append("gates (already passed in this CI job)")
else:
    g = subprocess.run([sys.executable, str(ROOT / "tests/run_gates.py")], capture_output=True, text=True)
    checks["gates"] = g.returncode == 0
print(json.dumps({"ok": all(checks.values()), "checks": checks, "skipped": skipped, "package": ROOT.name}, indent=2))
sys.exit(0 if all(checks.values()) else 1)
