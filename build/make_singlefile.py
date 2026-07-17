#!/usr/bin/env python3
"""Deterministic single-file build: package -> dist/earcrate.py (same UX as ever)."""
import re, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "earcrate"
ORDER = ["tastespec/profiles.py", "tastespec/remix_builder.py", "core/deps.py", "core/util.py", "core/wavinfo.py", "analyze/decode.py", "deck/dsp.py",
         "deck/transform.py", "deck/lattice.py", "ear/readiness.py", "ear/taste_ranker.py", "judge/audio.py",
         "deck/harmony.py", "core/config.py", "analyze/features.py", "analyze/beat_features.py", "librarian/ingest.py",
         "providers/__init__.py", "providers/artifacts.py", "providers/stems.py", "providers/transform.py", "providers/beats.py", "providers/retrieval.py", "providers/workqueue.py", "plan/math.py", "plan/transitions.py", "materials/regions.py", "study/reference.py", "study/musicbrainz.py", "remix/external.py",
         "project/model.py", "project/policy.py", "project/store.py", "project/bridge.py", "project/commands.py", "project/export.py", "project/runtime.py",
         "app.py", "ui/server.py", "selftest.py", "cli.py"]
STRIP = re.compile(r"^(from|import) earcrate[.\s]")
# COMPLETENESS GUARD: every real package module must be concatenated. A module
# left out of ORDER strips its importers' `from earcrate.x import y` lines and
# then NameErrors at CALL time — only in the single-file build, invisible to
# package-mode gates (this is exactly how providers/transform+beats and
# ear/taste_ranker went missing). Refuse to build an incomplete dist.
_all_py = {str(p.relative_to(PKG)).replace("\\", "/") for p in PKG.rglob("*.py")}
_excluded = {p for p in _all_py if p.endswith("__init__.py") or p == "__main__.py"}
_excluded.discard("providers/__init__.py")   # carries the provider registry; stays in ORDER
_missing = _all_py - set(ORDER) - _excluded
if _missing:
    raise SystemExit("package modules missing from the single-file ORDER (dist would NameError at call time): "
                     + ", ".join(sorted(_missing)))
import base64
html_b64 = base64.b64encode((PKG / "ui/static/index.html").read_bytes()).decode("ascii")
profiles_b64 = {f.stem: base64.b64encode(f.read_bytes()).decode("ascii")
                for f in sorted((ROOT / "profiles").glob("*.json")) if f.stem != "tastespec.schema"}
parts = ["#!/usr/bin/env python3\nfrom __future__ import annotations\n# Auto-built from the earcrate package. Do not edit; edit the package.\nimport base64 as _b64\n"]
INDENTED_EARCRATE = re.compile(r"^\s+(from|import) earcrate[.\s]")
_FROM_IMPORT = re.compile(r"^from earcrate[\w.]* import (.+)$")


def _alias_assignments(line):
    """A stripped `from earcrate.x import a, b as c` loses the alias `c` even after
    x is concatenated (only `b` exists in the flat namespace). Emit `c = b` in the
    import's place so aliased names survive the strip."""
    m = _FROM_IMPORT.match(line)
    if not m:
        return []
    out = []
    for item in m.group(1).split(","):
        item = item.strip()
        if " as " in item:
            orig, new = [x.strip() for x in item.split(" as ", 1)]
            out.append(f"{new} = {orig}")
    return out


for rel in ORDER:
    src = (PKG / rel).read_text(encoding="utf-8")
    lines = []
    for l in src.split("\n"):
        if l.startswith("from __future__"):
            continue
        if STRIP.match(l):
            lines.extend(_alias_assignments(l))   # keep aliased names alive
            continue
        lines.append(l)
    # A function-level `from earcrate.` import survives the column-0 strip and then
    # raises ModuleNotFoundError in the standalone dist — only at call time, only in
    # the single-file build, invisible to package-mode gates. Refuse to build it:
    # hoist the import to module top level instead (ORDER guarantees definition order).
    bad = [f"{rel}:{i+1}: {l.strip()}" for i, l in enumerate(lines) if INDENTED_EARCRATE.match(l)]
    if bad:
        raise SystemExit("indented earcrate imports would break the single-file dist at call time:\n  " + "\n  ".join(bad))
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
