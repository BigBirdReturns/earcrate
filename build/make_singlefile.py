#!/usr/bin/env python3
"""Deterministic single-file build: package -> dist/earcrate.py (same UX as ever)."""
import re, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "earcrate"
ORDER = ["tastespec/profiles.py", "tastespec/remix_builder.py", "core/deps.py", "core/util.py", "core/wavinfo.py", "analyze/decode.py", "deck/dsp.py",
         "deck/transform.py", "deck/lattice.py", "ear/readiness.py", "judge/audio.py",
         "deck/harmony.py", "core/config.py", "analyze/features.py", "analyze/beat_features.py", "librarian/ingest.py",
         "providers/__init__.py", "providers/artifacts.py", "providers/notes.py", "providers/stems.py", "providers/retrieval.py", "providers/workqueue.py",
         "midi/model.py", "midi/codec.py", "midi/render.py",
         "rack/model.py", "rack/demand.py", "rack/binding.py", "rack/binding_stable.py", "rack/sfz.py", "rack/render.py", "rack/render_fix.py", "rack/library.py", "rack/library_fix.py", "rack/multizone.py",
         "midi/anatomy_grid.py", "midi/anatomy_structure.py", "midi/anatomy.py", "midi/arranger.py", "midi/cli.py",
         "plan/math.py", "plan/transitions.py", "materials/regions.py", "study/reference.py", "study/musicbrainz.py", "remix/external.py", "app.py", "ui/server.py", "selftest.py", "cli.py"]
STRIP = re.compile(r"^(from|import) earcrate[.\s]")
INDENTED_EARCRATE = re.compile(r"^\s+(from|import) earcrate[.\s]")


def _strip_package_imports(source: str) -> list[str]:
    """Strip complete package-local import statements from concatenated modules.

    A parenthesized import spans several physical lines. Removing only the first
    line leaves its indented names and closing parenthesis as invalid standalone
    syntax. Track parenthesis depth so the complete statement is removed.
    """
    kept: list[str] = []
    skipping = False
    depth = 0
    continued = False
    for line in source.split("\n"):
        if skipping:
            depth += line.count("(") - line.count(")")
            continued = line.rstrip().endswith("\\")
            if depth <= 0 and not continued:
                skipping = False
            continue
        if line.startswith("from __future__"):
            continue
        if STRIP.match(line):
            depth = line.count("(") - line.count(")")
            continued = line.rstrip().endswith("\\")
            skipping = depth > 0 or continued
            continue
        kept.append(line)
    return kept


import base64
html_b64 = base64.b64encode((PKG / "ui/static/index.html").read_bytes()).decode("ascii")
profiles_b64 = {f.stem: base64.b64encode(f.read_bytes()).decode("ascii")
                for f in sorted((ROOT / "profiles").glob("*.json")) if f.stem != "tastespec.schema"}
parts = ["#!/usr/bin/env python3\nfrom __future__ import annotations\n# Auto-built from the earcrate package. Do not edit; edit the package.\nimport base64 as _b64\n"]
for rel in ORDER:
    src = (PKG / rel).read_text(encoding="utf-8")
    lines = _strip_package_imports(src)
    # A function-level `from earcrate.` import survives the column-0 strip and then
    # raises ModuleNotFoundError in the standalone dist. Hoist such imports instead.
    bad = [f"{rel}:{i+1}: {line.strip()}" for i, line in enumerate(lines) if INDENTED_EARCRATE.match(line)]
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
    if rel == "cli.py":
        needle = "    argv = list(sys.argv[1:] if argv is None else argv)"
        replacement = needle + "\n    if argv and argv[0] == \"midi\":\n        return midi_main(argv[1:])"
        if needle not in body:
            raise SystemExit("cli.py MIDI dispatch insertion point is missing")
        body = body.replace(needle, replacement, 1)
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
