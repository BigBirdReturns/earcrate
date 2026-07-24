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
         "rack/model.py", "rack/demand.py", "rack/binding.py", "rack/binding_stable.py", "rack/sfz.py", "rack/render.py", "rack/render_fix.py", "rack/library.py", "rack/library_fix.py", "rack/multizone.py", "rack/portable.py",
         "midi/anatomy_grid.py", "midi/anatomy_structure.py", "midi/anatomy.py", "midi/arranger.py", "midi/arranger_fix.py",
         "music/model.py", "music/law_context.py", "music/law_voice.py", "music/law_harmony.py", "music/laws.py", "music/equations.py", "music/player_piano.py", "music/heritage.py", "music/director.py", "music/source_phrase_model.py", "music/source_phrase_audio.py",
         "study/reference.py", "study/reference_grid.py", "study/reference_bundle.py", "study/reference_cli.py",
         "live/model.py", "live/operators.py", "live/capabilities.py", "live/instrumentation.py", "live/planner.py", "live/engine.py", "live/runtime.py", "live/crate.py", "live/stream.py", "live/playback.py", "live/performance.py", "live/audio_cli.py", "live/cli.py",
         "midi/cli.py", "plan/math.py", "plan/transitions.py", "materials/regions.py", "study/musicbrainz.py", "remix/external.py", "app.py", "ui/server.py", "selftest.py", "cli.py"]
PROJECT_FILES = [
    "util.py", "model.py", "causal_revision.py", "policy.py", "store.py", "gate8_store.py", "buffalo.py",
    "compiler_source_common.py", "compiler_source_crate.py", "compiler_source_manifest.py",
    "compiler_clip.py", "compiler_deck.py", "compiler_beam.py", "compiler_entry.py",
    "compiler_gate.py", "compiler_legacy.py", "compiler.py", "render.py", "lower.py",
    "export.py", "commands.py", "custody.py", "library.py", "continuation.py",
    "source_execution.py", "cli.py", "gate8_cli.py", "__init__.py",
]
STRIP = re.compile(r"^(from|import) earcrate[.\s]|^from \.")
INDENTED_EARCRATE = re.compile(r"^\s+(from|import) earcrate[.\s]")
PROJECT_STRIP = re.compile(r"^(from\s+\.|from\s+earcrate\.project(?:\.|\s)|import\s+earcrate\.project(?:\.|\s))")
PROJECT_IMPORT_SOURCES = {
    "compiler_entry.py": "from earcrate.project.compiler_clip import _build_clip\nfrom earcrate.project.compiler_deck import _candidate_signature, _select_deck\nfrom earcrate.project.compiler_beam import _beam_search\n",
    "compiler_gate.py": "from earcrate.project.compiler_source_common import HARD_TECHNIQUES\n",
    "compiler_legacy.py": "from earcrate.project.compiler_source_common import EAR_TO_RENDER, prepare_source_asset\n",
    "compiler.py": "from earcrate.project.compiler_entry import compile_project\nfrom earcrate.project.compiler_gate import static_gate\nfrom earcrate.project.compiler_legacy import import_legacy_arrangement\nfrom earcrate.project.compiler_source_common import EAR_TO_RENDER, HARD_TECHNIQUES, prepare_source_asset\nfrom earcrate.project.compiler_source_crate import prepare_crate_sources\nfrom earcrate.project.compiler_source_manifest import load_source_manifest, prepare_manifest_sources\n",
}


def _strip_package_imports(source: str) -> list[str]:
    """Strip complete package-local import statements from concatenated modules."""
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


def _strip_project_imports(source: str) -> str:
    kept: list[str] = []
    skipping = False
    depth = 0
    for line in source.split("\n"):
        if skipping:
            depth += line.count("(") - line.count(")")
            if depth <= 0 and not line.rstrip().endswith("\\"):
                skipping = False
            continue
        if line.startswith("from __future__"):
            continue
        if PROJECT_STRIP.match(line):
            depth = line.count("(") - line.count(")")
            skipping = depth > 0 or line.rstrip().endswith("\\")
            continue
        kept.append(line)
    return "\n".join(kept)


import base64
html_b64 = base64.b64encode((PKG / "ui/static/index.html").read_bytes()).decode("ascii")
profiles_b64 = {f.stem: base64.b64encode(f.read_bytes()).decode("ascii")
                for f in sorted((ROOT / "profiles").glob("*.json")) if f.stem != "tastespec.schema"}
parts = ["#!/usr/bin/env python3\nfrom __future__ import annotations\n# Auto-built from the earcrate package. Do not edit; edit the package.\nimport base64 as _b64\n"]
for rel in ORDER:
    src = (PKG / rel).read_text(encoding="utf-8")
    lines = _strip_package_imports(src)
    bad = [f"{rel}:{i+1}: {line.strip()}" for i, line in enumerate(lines) if INDENTED_EARCRATE.match(line)]
    if bad:
        raise SystemExit("indented earcrate imports would break the single-file dist at call time:\n  " + "\n  ".join(bad))
    body = "\n".join(lines)
    if rel in {"study/reference_cli.py", "live/cli.py", "live/audio_cli.py"}:
        marker = '\nif __name__ == "__main__":'
        if marker in body:
            body = body.split(marker, 1)[0]
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
        replacement = (
            needle
            + "\n    if argv and argv[0] == \"project\":\n        return project_main(argv[1:])"
            + "\n    if argv and argv[0] == \"midi\":\n        return midi_main(argv[1:])"
            + "\n    if argv and argv[0] == \"live\":\n        return live_cli_main(argv[1:])"
            + "\n    if argv and argv[0] == \"live-audio\":\n        return live_audio_cli_main(argv[1:])"
            + "\n    if argv and argv[0] == \"reference\":\n        return reference_cli_main(argv[1:])"
        )
        if needle not in body:
            raise SystemExit("cli.py command dispatch insertion point is missing")
        body = body.replace(needle, replacement, 1)
    parts.append(f"\n# ===== {rel} =====\n" + body)

project_sources = {rel: _strip_project_imports(PROJECT_IMPORT_SOURCES.get(rel, "") + "\n" + (PKG / "project" / rel).read_text(encoding="utf-8")) for rel in PROJECT_FILES}
project_bootstrap = r'''
# ===== project package bootstrap =====
import sys as _project_sys
import types as _project_types
_project_package = _project_types.ModuleType("earcrate.project")
_project_package.__package__ = "earcrate.project"
_project_package.__path__ = []
_project_sys.modules["earcrate.project"] = _project_package
_project_sources = __PROJECT_SOURCES__
_project_seed = dict(globals())
_project_modules = {}
for _project_rel in __PROJECT_FILES__:
    _project_name = "earcrate.project" if _project_rel == "__init__.py" else "earcrate.project." + _project_rel[:-3]
    _project_module = _project_package if _project_rel == "__init__.py" else _project_types.ModuleType(_project_name)
    _project_module.__package__ = "earcrate.project"
    _project_module.__file__ = "<embedded>/earcrate/project/" + _project_rel
    _project_module.__dict__.update(dict(_project_seed))
    _project_sys.modules[_project_name] = _project_module
    exec(compile(_project_sources[_project_rel], _project_module.__file__, "exec"), _project_module.__dict__)
    _project_modules[_project_rel] = _project_module
    _project_seed.update({k: v for k, v in _project_module.__dict__.items() if not k.startswith("__")})
for _project_rel, _project_module in _project_modules.items():
    if _project_rel == "__init__.py":
        continue
    setattr(_project_package, _project_rel[:-3], _project_module)
for _project_rel, _project_module in _project_modules.items():
    for _project_export in getattr(_project_module, "__all__", ()):
        if hasattr(_project_module, _project_export):
            setattr(_project_package, _project_export, getattr(_project_module, _project_export))
project_main = _project_modules["gate8_cli.py"].main
'''.replace("__PROJECT_SOURCES__", repr(project_sources)).replace("__PROJECT_FILES__", repr(PROJECT_FILES))
parts.insert(-1, project_bootstrap)
out = "\n".join(parts)
if "if __name__" not in out.split("# ===== cli.py =====")[-1]:
    out += '\nif __name__ == "__main__":\n    import sys\n    sys.exit(main())\n'
_h = hashlib.sha256()
for _f in sorted(PKG.rglob("*.py")) + [PKG / "ui" / "static/index.html"]:
    _h.update(_f.read_bytes())
out = out.replace('"__BUILD_STAMP__"', f'"{_h.hexdigest()[:7]}"')
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
(dist / "earcrate.py").write_text(out, encoding="utf-8")
print("built dist/earcrate.py sha256", hashlib.sha256(out.encode()).hexdigest()[:16])
