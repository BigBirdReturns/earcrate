from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "earcrate_crate_currency_repair.py"
SPEC = importlib.util.spec_from_file_location("crate_currency_repair", SCRIPT)
assert SPEC and SPEC.loader
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE files(
          id TEXT PRIMARY KEY, present INTEGER,
          audio_sha256_scope TEXT, audio_sha256 TEXT,
          audio_generation INTEGER
        );
        CREATE TABLE features(file_id TEXT);
        CREATE TABLE loops(
          id TEXT PRIMARY KEY, file_id TEXT,
          source_audio_sha256 TEXT, source_audio_generation INTEGER
        );
        CREATE TABLE ear_atoms(
          id TEXT PRIMARY KEY, loop_id TEXT, file_id TEXT,
          taste_profile TEXT, ear_role TEXT, status TEXT
        );
        """
    )
    return db


def test_historical_approved_atom_does_not_count_as_current() -> None:
    db = database()
    db.execute("INSERT INTO files VALUES('f',1,'full','pcm-new',2)")
    db.execute("INSERT INTO loops VALUES('l','f','pcm-old',1)")
    db.execute(
        "INSERT INTO ear_atoms VALUES('a','l','f','girl_talk_v1','DRUM_BREAK','approved')")
    report = repair.audit(db, "girl_talk_v1")
    assert report["counts"]["atoms_profile_approved_total"] == 1
    assert report["counts"]["atoms_profile_active_approved"] == 0
    assert report["blocker"] == "no_loops_on_active_source_generation"


def test_matching_generation_makes_approved_atom_current() -> None:
    db = database()
    db.execute("INSERT INTO files VALUES('f',1,'full','pcm',3)")
    db.execute("INSERT INTO loops VALUES('l','f','pcm',3)")
    db.execute(
        "INSERT INTO ear_atoms VALUES('a','l','f','girl_talk_v1','DRUM_BREAK','approved')")
    report = repair.audit(db, "girl_talk_v1")
    assert report["currency_ready"] is True
    assert report["blocker"] is None
    assert report["counts"]["atoms_profile_active_approved"] == 1
    assert report["active_role_counts"] == {"DRUM_BREAK": 1}


def test_active_candidates_are_not_silently_promoted() -> None:
    db = database()
    db.execute("INSERT INTO files VALUES('f',1,'full','pcm',0)")
    db.execute("INSERT INTO loops VALUES('l','f','pcm',0)")
    db.execute(
        "INSERT INTO ear_atoms VALUES('a','l','f','girl_talk_v1','DRUM_BREAK','candidate')")
    report = repair.audit(db, "girl_talk_v1")
    assert report["counts"]["atoms_profile_active"] == 1
    assert report["counts"]["atoms_profile_active_approved"] == 0
    assert report["blocker"] == "active_profile_atoms_exist_but_none_are_approved"
