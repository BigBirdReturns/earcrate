from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "earcrate_stem_provider_repair.py"
SPEC = importlib.util.spec_from_file_location("earcrate_stem_provider_repair", SCRIPT)
assert SPEC and SPEC.loader
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE files(
          id TEXT PRIMARY KEY, path TEXT, sha256 TEXT, audio_sha256 TEXT,
          audio_sha256_scope TEXT, audio_generation INTEGER, present INTEGER,
          duration_s REAL
        );
        CREATE TABLE loops(
          id TEXT PRIMARY KEY, file_id TEXT, source_audio_sha256 TEXT,
          source_audio_generation INTEGER
        );
        CREATE TABLE ear_atoms(
          id TEXT PRIMARY KEY, loop_id TEXT, file_id TEXT, taste_profile TEXT,
          status TEXT, ear_role TEXT, score REAL
        );
        """
    )
    return db


def _insert_source(db: sqlite3.Connection, path: Path, *, current: bool, status: str,
                   drum: bool = False, bass: bool = False, generation: int = 2) -> None:
    file_id = path.stem
    pcm = f"pcm-{file_id}-current"
    loop_pcm = pcm if current else f"pcm-{file_id}-old"
    loop_generation = generation if current else generation - 1
    db.execute(
        "INSERT INTO files VALUES(?,?,?,?,?,?,?,?)",
        (file_id, str(path), _sha(path), pcm, "full", generation, 1, 30.0),
    )
    roles = []
    if drum:
        roles.append("DRUM_BREAK")
    if bass:
        roles.append("BASS_RIFF")
    if not roles:
        roles.append("BED_CHORD")
    for index, role in enumerate(roles):
        loop_id = f"loop-{file_id}-{index}"
        atom_id = f"atom-{file_id}-{index}"
        db.execute("INSERT INTO loops VALUES(?,?,?,?)", (loop_id, file_id, loop_pcm, loop_generation))
        db.execute(
            "INSERT INTO ear_atoms VALUES(?,?,?,?,?,?,?)",
            (atom_id, loop_id, file_id, repair.PROFILE, status, role, 0.9),
        )
    db.commit()


def test_cuda_wheel_selection_uses_highest_supported_channel() -> None:
    assert repair.parse_cuda_version("CUDA Version: 12.9") == 12.9
    assert repair.choose_cuda_wheel(12.9) == "cu128"
    assert repair.choose_cuda_wheel(12.7) == "cu126"
    assert repair.choose_cuda_wheel(12.0) == "cu118"
    with pytest.raises(repair.RepairError):
        repair.choose_cuda_wheel(11.7)


def test_probe_source_excludes_stale_generation_and_unapproved_atoms(tmp_path: Path) -> None:
    stale = tmp_path / "stale.wav"
    candidate = tmp_path / "candidate.wav"
    current = tmp_path / "current.wav"
    stale.write_bytes(b"stale")
    candidate.write_bytes(b"candidate")
    current.write_bytes(b"current")
    db = _db()
    _insert_source(db, stale, current=False, status="approved", drum=True, bass=True)
    _insert_source(db, candidate, current=True, status="candidate", drum=True, bass=True)
    _insert_source(db, current, current=True, status="approved", drum=True, bass=True)

    selected = repair.select_probe_source(db, repair.PROFILE)

    assert Path(selected["path"]) == current.resolve()
    assert selected["verified_file_sha256"] == _sha(current)


def test_probe_source_refuses_changed_file_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"original")
    db = _db()
    _insert_source(db, source, current=True, status="approved", drum=True, bass=True)
    source.write_bytes(b"changed")

    with pytest.raises(repair.RepairError, match="hash"):
        repair.select_probe_source(db, repair.PROFILE)


def test_config_activation_is_backup_bound_and_reversible(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"stem_provider": "noop", "master_root": "M", "human_field": {"keep": True}}),
        encoding="utf-8",
    )
    original = config_path.read_bytes()

    receipt = repair.activate_provider(config_path, tmp_path / "archive")
    activated = json.loads(config_path.read_text(encoding="utf-8"))

    assert activated["stem_provider"] == "demucs"
    assert activated["human_field"] == {"keep": True}
    backup = Path(receipt["backup"])
    assert backup.read_bytes() == original

    repair.restore_config(config_path, backup)
    assert config_path.read_bytes() == original


def test_public_source_receipt_omits_private_path() -> None:
    receipt = repair.public_source({
        "file_id": "f1", "path": "D:/Private/Song.wav", "duration_s": 30.0,
        "audio_sha256": "pcm", "verified_file_sha256": "file", "approved_atoms": 3,
        "drum_atoms": 1, "bass_atoms": 1,
    })
    assert "path" not in receipt
    assert receipt["filename"] == "Song.wav"
