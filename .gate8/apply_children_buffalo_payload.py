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
PATCHED_BUILDER_SHA256 = "aea5e59bdf068490f57e9d7fed8805940e07a3f31d97bab89020785d0976d675"
DIRECTOR_AGGREGATE = '"music/player_piano.py", "music/heritage.py", "music/director.py", "music/source_phrase_model.py", "music/source_phrase_audio.py",'
DIRECTOR_COMPONENTS = '"music/player_piano.py", "music/heritage.py", "music/director_validation.py", "music/director_render.py", "music/source_phrase_model.py", "music/source_phrase_audio.py",'
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


def _repair_standalone_director_embedding() -> None:
    """Flatten the director implementation modules, not its import-only facade.

    ``music/director.py`` merely imports ``_validation_all`` and ``_render_all``
    from sibling modules. The single-file builder strips those package imports,
    so flattening the facade leaves a deterministic NameError. The stacked reader
    branch already carried this exact repair in its one-shot integration script;
    the Buffalo payload applies it content-addressedly instead of weakening either
    standalone gate.
    """
    builder = ROOT / "build" / "make_singlefile.py"
    observed = _sha256(builder)
    if observed != EXTRACTED_BUILDER_SHA256:
        raise SystemExit(
            "refusing standalone-director repair against unexpected builder: "
            f"{observed} != {EXTRACTED_BUILDER_SHA256}"
        )
    text = builder.read_text(encoding="utf-8")
    if text.count(DIRECTOR_AGGREGATE) != 1:
        raise SystemExit("single-file director repair point is missing or ambiguous")
    patched = text.replace(DIRECTOR_AGGREGATE, DIRECTOR_COMPONENTS, 1)
    temporary = builder.with_name(f".{builder.name}.children-buffalo-director.tmp")
    temporary.write_text(patched, encoding="utf-8")
    temporary.replace(builder)
    repaired = _sha256(builder)
    if repaired != PATCHED_BUILDER_SHA256:
        raise SystemExit(
            "standalone-director repair produced unexpected builder identity: "
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

    _repair_standalone_director_embedding()

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
