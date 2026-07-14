#!/usr/bin/env python3
"""Deterministic single-file build: package -> dist/earcrate.py (same UX as ever)."""
import re, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "earcrate"
ORDER = ["tastespec/profiles.py", "core/deps.py", "core/util.py", "core/wavinfo.py", "analyze/decode.py", "deck/dsp.py",
         "deck/transform.py", "deck/lattice.py", "ear/readiness.py", "judge/audio.py",
         "deck/harmony.py", "core/config.py", "analyze/features.py", "librarian/ingest.py",
         "providers/__init__.py", "providers/artifacts.py", "providers/stems.py", "providers/retrieval.py", "plan/math.py", "study/reference.py", "app.py", "ui/server.py", "selftest.py", "cli.py"]
STRIP = re.compile(r"^(from|import) earcrate[.\s]")
import base64
html_b64 = base64.b64encode((PKG / "ui/static/index.html").read_bytes()).decode("ascii")
profiles_b64 = {f.stem: base64.b64encode(f.read_bytes()).decode("ascii")
                for f in sorted((ROOT / "profiles").glob("*.json")) if f.stem != "tastespec.schema"}
parts = ["#!/usr/bin/env python3\nfrom __future__ import annotations\n# Auto-built from the earcrate package. Do not edit; edit the package.\nimport base64 as _b64\n"]
for rel in ORDER:
    src = (PKG / rel).read_text(encoding="utf-8")
    lines = [l for l in src.split("\n") if not STRIP.match(l) and not l.startswith("from __future__")]
    body = "\n".join(lines)
    if rel == "tastespec/profiles.py":
        body = body.replace("EMBEDDED_PROFILES: Dict[str, str] = {}",
                            "EMBEDDED_PROFILES: Dict[str, str] = " + repr(profiles_b64))
    if rel == "ui/server.py":
        body = body.replace(
            'HTML_PAGE = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")  # single-file build inlines this',
            'HTML_PAGE = _b64.b64decode("' + html_b64 + '").decode("utf-8")')
    if rel == "app.py":
        body = body.replace("# --- librarian attachment", "# librarian functions are inline above in single-file build\n# --- librarian attachment")
    parts.append(f"\n# ===== {rel} =====\n" + body)
out = "\n".join(parts)
if "if __name__" not in out.split("# ===== cli.py =====")[-1]:
    out += '\nif __name__ == "__main__":\n    import sys\n    sys.exit(main())\n'
# Stamp the package content hash so the single-file header matches the package
# header and the Pages installer button (same formula in .github/workflows/pages.yml).
_h = hashlib.sha256()
for _f in sorted(PKG.rglob("*.py")) + [PKG / "ui" / "static" / "index.html"]:
    _h.update(_f.read_bytes())
out = out.replace('"__BUILD_STAMP__"', f'"{_h.hexdigest()[:7]}"')
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
(dist / "earcrate.py").write_text(out, encoding="utf-8")
print("built dist/earcrate.py sha256", hashlib.sha256(out.encode()).hexdigest()[:16])
