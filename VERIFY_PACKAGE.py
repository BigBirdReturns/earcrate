#!/usr/bin/env python3
import compileall, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
ok = compileall.compile_dir(str(ROOT / "earcrate"), quiet=1)
checks = {"package_compiles": bool(ok),
          "one_changelog": (ROOT / "CHANGELOG.md").exists() and not list(ROOT.glob("PATCH_NOTES_*.txt")),
          "static_ui": (ROOT / "earcrate/ui/static/index.html").exists()}
r = subprocess.run([sys.executable, str(ROOT / "build/make_singlefile.py")], capture_output=True, text=True)
checks["singlefile_builds"] = r.returncode == 0
if checks["singlefile_builds"]:
    t = subprocess.run([sys.executable, str(ROOT / "dist/earcrate.py"), "--self-test"], capture_output=True, text=True, timeout=600)
    checks["singlefile_selftest"] = "SELF_TEST_OK" in (t.stdout + t.stderr)
g = subprocess.run([sys.executable, str(ROOT / "tests/test_gates.py")], capture_output=True, text=True)
checks["gates"] = g.returncode == 0
print(json.dumps({"ok": all(checks.values()), "checks": checks, "package": ROOT.name}, indent=2))
sys.exit(0 if all(checks.values()) else 1)
