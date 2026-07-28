#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import lzma
import shutil
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CHUNK_ROOT = ROOT / ".gate8" / "children_buffalo_payload"
ARCHIVE_SHA256 = "1c5c9494a8d794599555094e6aa3d5b1e5af443e75577d57a276849bfc6c0a8f"
PARTS = tuple(f"part{index:02d}" for index in range(6))
EXPECTED_FILES = 25
EXTRACTED_BUILDER_SHA256 = "20d1c89eaf73533356fc0e5eb917bdef8a3e44301648f1400ead5691fb7a64e0"
PATCHED_BUILDER_SHA256 = "750fea60c0a6d9eea29ea9ed6dc656039e9d7f79c3754a9d512d21776a315329"
DIRECTOR_AGGREGATE = '"music/player_piano.py", "music/heritage.py", "music/director.py", "music/source_phrase_model.py", "music/source_phrase_audio.py",'
DIRECTOR_COMPONENTS = '"music/player_piano.py", "music/heritage.py", "music/director_validation.py", "music/director_render.py", "music/source_phrase_model.py", "music/source_phrase_audio.py",'
PROJECT_SOURCES_ANCHOR = "project_sources = {rel: _strip_project_imports("
PACKAGE_INSERT_ANCHOR = "parts.insert(-1, project_bootstrap)\nparts.insert(-1, specimen_bootstrap)"
PROJECT_PACKAGE_ANCHOR = '_project_sys.modules["earcrate.project"] = _project_package\n'
SPECIMEN_PACKAGE_ANCHOR = '_specimen_sys.modules["earcrate.specimen"] = _specimen_package\n'
PROJECT_SEED_ANCHOR = "_project_seed = dict(globals())"
PROJECT_SEED_FILTERED = '_project_seed = {k: v for k, v in globals().items() if not k.startswith("__")}'
SPECIMEN_SEED_ANCHOR = "_specimen_seed = dict(globals())"
SPECIMEN_SEED_FILTERED = '_specimen_seed = {k: v for k, v in globals().items() if not k.startswith("__")}'
FLAT_PACKAGE_BOOTSTRAP = 'flat_package_bootstrap = r\'\'\'\n# ===== flattened package namespace bootstrap =====\n# Project and specimen modules retain explicit cross-organ imports. The ordinary\n# single-file modules above are concatenated into one namespace, so expose that\n# exact namespace through package-shaped module shims before executing the embedded\n# package modules. Shims contain no second implementation; they are import views\n# over the already-loaded authority.\nimport sys as _flat_sys\nimport types as _flat_types\n_flat_seed = {k: v for k, v in globals().items() if not k.startswith("__")}\n_flat_root = _flat_sys.modules.get("earcrate")\nif not isinstance(_flat_root, _flat_types.ModuleType):\n    _flat_root = _flat_types.ModuleType("earcrate")\n    _flat_sys.modules["earcrate"] = _flat_root\n_flat_root.__dict__.update(dict(_flat_seed))\n_flat_root.__package__ = "earcrate"\n_flat_root.__path__ = []\n\ndef _flat_ensure_module(_flat_name, _flat_is_package):\n    _flat_module = _flat_sys.modules.get(_flat_name)\n    if not isinstance(_flat_module, _flat_types.ModuleType):\n        _flat_module = _flat_types.ModuleType(_flat_name)\n        _flat_sys.modules[_flat_name] = _flat_module\n    _flat_module.__dict__.update(dict(_flat_seed))\n    _flat_module.__package__ = _flat_name if _flat_is_package else _flat_name.rpartition(".")[0]\n    _flat_module.__file__ = "<embedded>/" + _flat_name.replace(".", "/") + ".py"\n    if _flat_is_package:\n        _flat_module.__path__ = []\n    _flat_parent_name, _, _flat_child = _flat_name.rpartition(".")\n    _flat_parent = _flat_sys.modules.get(_flat_parent_name)\n    if isinstance(_flat_parent, _flat_types.ModuleType):\n        setattr(_flat_parent, _flat_child, _flat_module)\n    return _flat_module\n\nfor _flat_rel in __FLAT_ORDER__:\n    _flat_components = _flat_rel[:-3].split("/")\n    _flat_is_init = bool(_flat_components and _flat_components[-1] == "__init__")\n    if _flat_is_init:\n        _flat_components = _flat_components[:-1]\n    for _flat_index in range(len(_flat_components)):\n        _flat_name = "earcrate." + ".".join(_flat_components[: _flat_index + 1])\n        _flat_is_package = _flat_index < len(_flat_components) - 1 or _flat_is_init\n        _flat_ensure_module(_flat_name, _flat_is_package)\n\n# Aggregate facades intentionally omitted from concatenation because their imports\n# would be stripped. Preserve their public import paths as views of the same seed.\nfor _flat_alias in ("earcrate.music.director", "earcrate.music.source_phrase"):\n    _flat_ensure_module(_flat_alias, False)\n\'\'\'.replace("__FLAT_ORDER__", repr(ORDER))\n\n'
FORBIDDEN_MEDIA_SUFFIXES = {
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".mid", ".midi",
    ".mp3", ".ogg", ".opus", ".pdf", ".wav", ".wma",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_member_path(name: str) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise SystemExit(f"unsafe payload path: {name!r}")
    target = ROOT.joinpath(*pure.parts)
    resolved = target.resolve()
    root_resolved = ROOT.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SystemExit(f"payload path escapes repository: {name!r}") from exc
    if pure.parts[0] == ".git":
        raise SystemExit("payload may not write .git")
    return target


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{label} patch point is missing or ambiguous")
    return text.replace(old, new, 1)


def _repair_standalone_embedding() -> None:
    """Make cross-organ imports executable in the generated one-file package.

    The director facade is import-only, so its implementation modules are flattened
    directly. Project modules intentionally retain imports into MIDI, music, rack,
    and reader authorities; package-shaped shims expose the already-loaded flattened
    namespace without adding a second implementation or weakening standalone gates.
    """
    builder = ROOT / "build" / "make_singlefile.py"
    observed = _sha256(builder)
    if observed != EXTRACTED_BUILDER_SHA256:
        raise SystemExit(
            "refusing standalone repair against unexpected builder: "
            f"{observed} != {EXTRACTED_BUILDER_SHA256}"
        )
    text = builder.read_text(encoding="utf-8")
    text = _replace_once(text, DIRECTOR_AGGREGATE, DIRECTOR_COMPONENTS, "director")
    text = _replace_once(
        text,
        PROJECT_SOURCES_ANCHOR,
        FLAT_PACKAGE_BOOTSTRAP + PROJECT_SOURCES_ANCHOR,
        "flattened package bootstrap",
    )
    text = _replace_once(
        text,
        PACKAGE_INSERT_ANCHOR,
        "parts.insert(-1, flat_package_bootstrap)\n" + PACKAGE_INSERT_ANCHOR,
        "flattened package insertion",
    )
    text = _replace_once(
        text,
        PROJECT_PACKAGE_ANCHOR,
        PROJECT_PACKAGE_ANCHOR + 'setattr(_project_sys.modules["earcrate"], "project", _project_package)\n',
        "project package attachment",
    )
    text = _replace_once(
        text,
        SPECIMEN_PACKAGE_ANCHOR,
        SPECIMEN_PACKAGE_ANCHOR + 'setattr(_specimen_sys.modules["earcrate"], "specimen", _specimen_package)\n',
        "specimen package attachment",
    )
    text = _replace_once(
        text,
        PROJECT_SEED_ANCHOR,
        PROJECT_SEED_FILTERED,
        "project bootstrap seed isolation",
    )
    text = _replace_once(
        text,
        SPECIMEN_SEED_ANCHOR,
        SPECIMEN_SEED_FILTERED,
        "specimen bootstrap seed isolation",
    )
    temporary = builder.with_name(f".{builder.name}.children-buffalo-standalone.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(builder)
    repaired = _sha256(builder)
    if repaired != PATCHED_BUILDER_SHA256:
        raise SystemExit(
            "standalone repair produced unexpected builder identity: "
            f"{repaired} != {PATCHED_BUILDER_SHA256}"
        )


def main() -> int:
    missing = [name for name in PARTS if not (CHUNK_ROOT / name).is_file()]
    if missing:
        raise SystemExit(f"missing payload chunks: {missing}")
    archive = b"".join((CHUNK_ROOT / name).read_bytes() for name in PARTS)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise SystemExit(f"payload archive SHA-256 mismatch: {digest}")
    try:
        tar_bytes = lzma.decompress(archive)
    except lzma.LZMAError as exc:
        raise SystemExit("payload archive is not valid xz") from exc

    files_written: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as bundle:
        members = bundle.getmembers()
        regular = [member for member in members if member.isfile()]
        if len(regular) != EXPECTED_FILES:
            raise SystemExit(f"payload file count mismatch: {len(regular)} != {EXPECTED_FILES}")
        for member in members:
            if not (member.isdir() or member.isfile()):
                raise SystemExit(f"payload contains unsupported member type: {member.name}")
            target = _safe_member_path(member.name)
            if target.suffix.lower() in FORBIDDEN_MEDIA_SUFFIXES:
                raise SystemExit(f"source media may not enter the repository: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit(f"cannot read payload member: {member.name}")
            data = source.read()
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.children-buffalo.tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
            files_written.append(member.name)

    _repair_standalone_embedding()

    shutil.rmtree(CHUNK_ROOT)
    for ephemeral in (
        ROOT / ".gate8" / "apply_children_buffalo_payload.py",
        ROOT / ".github" / "workflows" / "apply-children-buffalo-gate.yml",
    ):
        ephemeral.unlink(missing_ok=True)

    print(
        f"applied {len(files_written)} Children Buffalo Gate files "
        f"from archive {ARCHIVE_SHA256[:16]}; "
        f"standalone builder {PATCHED_BUILDER_SHA256[:16]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
