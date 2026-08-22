"""Runtime witnesses for island persistence, dispatch, and quality scope.

Private Proof-005 runs exposed three runtime-only defects: incomplete split-runtime
imports, temporary segment paths outside the ordinary renderer's validated root,
and a complete-set flatness veto being applied independently to each tempo-island
slice. These gates execute the real paths and keep slice-local failures distinct
from the dynamic/form authority of the governed whole.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from earcrate.core import deps
from earcrate.core.util import now_utc, safe_name, ulidish
from earcrate.plan.islands import ISLAND_SET_KIND, persist_proposal
from earcrate.plan.island_render import (
    classify_segment_quality_gate,
    install_island_render_dispatch,
    whole_set_form_gate,
    whole_set_quality_gate,
)


_SCHEMA = """
CREATE TABLE mashups(
  id TEXT PRIMARY KEY,
  name TEXT,
  seed INTEGER NOT NULL,
  params_json TEXT NOT NULL,
  arrangement_json TEXT NOT NULL,
  render_path TEXT,
  created_at TEXT NOT NULL,
  engine_version TEXT,
  arrangement_sha TEXT,
  render_report_path TEXT
)
"""


class _RuntimeCore:
    def __init__(self, root: Path):
        self.config = SimpleNamespace(
            working_root=root / "work",
            agent_root=root / "agent",
            sample_rate=8000,
        )
        self.config.working_root.mkdir(parents=True, exist_ok=True)
        self.config.agent_root.mkdir(parents=True, exist_ok=True)
        self.rendered_segment_paths = []
        self.segment_quality_gate = {
            "passed": True,
            "failures": [],
            "warnings": [],
            "metrics": {"rms_std_db": 4.0},
        }
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(_SCHEMA)
        self.db.commit()

    def ensure_config(self):
        return self.config

    def conn(self):
        return self.db

    def write_manifest(self, author, seed, summary, operations):
        path = self.config.agent_root / "manifests" / "island-runtime.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "author": author,
                    "seed": seed,
                    "summary": summary,
                    "operations": operations,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return str(path)

    def render_mashup(self, mashup_id, destination):
        """Stand-in that enforces the ordinary renderer's real destination law."""
        destination = Path(destination).resolve()
        render_root = (self.config.working_root / "renders").resolve()
        try:
            destination.relative_to(render_root)
        except ValueError as exc:
            raise RuntimeError(
                f"render destination escapes working render root: {destination}"
            ) from exc
        row = self.conn().execute(
            "SELECT arrangement_json FROM mashups WHERE id=?", (mashup_id,)
        ).fetchone()
        arrangement = json.loads(row["arrangement_json"]) if row else {}
        assert (arrangement.get("params") or {}).get("post_render_gate") is False
        assert (arrangement.get("params") or {}).get("quality_gate_scope") == "island_slice_of_governed_whole"
        self.rendered_segment_paths.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        samples = np.linspace(-0.25, 0.25, self.config.sample_rate, dtype=np.float32)
        sf.write(str(destination), samples, self.config.sample_rate, subtype="PCM_24")
        stem_path = destination.with_name(destination.stem + ".stem_voice.wav")
        sf.write(str(stem_path), samples, self.config.sample_rate, subtype="PCM_24")
        report_path = destination.with_suffix(".render_report.json")
        report_path.write_text(
            json.dumps(
                {
                    "quality_gate": self.segment_quality_gate,
                    "stems": {"paths": {"voice": str(stem_path)}},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "type": "render_mashup",
            "path": str(destination),
            "report": str(report_path),
            "presented": True,
        }


def _single_island_arrangement() -> dict:
    segment = {
        "bpm": 120.0,
        "target_key": 0,
        "seed": 17,
        "params": {"stem_export": True},
        "sections": [],
    }
    return {
        "kind": ISLAND_SET_KIND,
        "duration_s": 1.0,
        "requested_duration_s": 1.0,
        "islands": [{"island_id": "island-000", "arrangement": segment}],
        "transitions": [],
        "sections": [],
        "global_source_ledger": [
            {
                "source_id": "fixture-source",
                "island_id": "island-000",
                "first_use_s": 0.0,
                "last_use_s": 1.0,
            }
        ],
        "accounting": {"source_reuse": 0},
    }


def _insert_parent(core: _RuntimeCore, mashup_id: str, arrangement: dict, destination: Path) -> None:
    core.conn().execute(
        "INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            mashup_id,
            "runtime-island",
            17,
            "{}",
            json.dumps(arrangement, sort_keys=True),
            str(destination),
            now_utc(),
            "fixture",
            "fixture-arrangement",
        ),
    )
    core.conn().commit()


def test_island_persistence_executes_real_runtime_imports(tmp_path):
    assert deps.now_utc is now_utc
    assert deps.safe_name is safe_name
    assert deps.ulidish is ulidish

    core = _RuntimeCore(tmp_path / "persist")
    result = {
        "arrangement": {"kind": ISLAND_SET_KIND, "sections": []},
        "arrangement_sha256": "a" * 64,
    }
    persist_proposal(core, {"seed": 17, "name": "Runtime Island"}, result)

    row = core.conn().execute("SELECT * FROM mashups WHERE id=?", (result["mashup_id"],)).fetchone()
    assert row is not None
    assert row["arrangement_sha"] == "a" * 64
    assert str(row["created_at"]).endswith("Z")
    assert Path(result["manifest"]).exists()
    assert Path(result["dst"]).parent == core.config.working_root / "renders"


def test_island_render_dispatch_executes_real_runtime_imports(tmp_path):
    class Core(_RuntimeCore):
        pass

    install_island_render_dispatch(Core)
    core = Core(tmp_path / "render")
    assert core.config.working_root.parent == core.config.agent_root.parent
    assert core.config.working_root != core.config.agent_root
    destination = core.config.working_root / "renders" / "runtime-island.wav"
    _insert_parent(core, "parent", _single_island_arrangement(), destination)

    rendered = core.render_mashup("parent", destination)

    assert rendered["presented"] is True
    assert rendered["island_count"] == 1
    assert rendered["source_count"] == 1
    assert rendered["stem_sum_residual"] <= 1e-7
    assert destination.exists()
    assert Path(rendered["report"]).exists()
    assert all(Path(path).exists() for path in rendered["stems"].values())
    parent = core.conn().execute("SELECT render_report_path FROM mashups WHERE id='parent'").fetchone()
    assert parent is not None and Path(parent["render_report_path"]).exists()
    assert core.conn().execute("SELECT COUNT(*) FROM mashups WHERE id LIKE 'isl_%'").fetchone()[0] == 0

    render_root = (core.config.working_root / "renders").resolve()
    assert core.rendered_segment_paths, "dispatcher must invoke the ordinary renderer"
    assert all(path.is_relative_to(render_root) for path in core.rendered_segment_paths)
    assert all(not path.exists() for path in core.rendered_segment_paths)
    assert all(not path.parent.exists() for path in core.rendered_segment_paths)


def test_segment_flatness_is_deferred_and_recorded(tmp_path):
    class Core(_RuntimeCore):
        pass

    install_island_render_dispatch(Core)
    core = Core(tmp_path / "flat-segment")
    core.segment_quality_gate = {
        "passed": False,
        "failures": ["rms_std_db catastrophically low; render is effectively flat"],
        "warnings": [],
        "metrics": {"rms_std_db": 1.27},
    }
    destination = core.config.working_root / "renders" / "flat-segment.wav"
    _insert_parent(core, "parent-flat", _single_island_arrangement(), destination)

    rendered = core.render_mashup("parent-flat", destination)
    assert rendered["presented"] is True
    report = json.loads(Path(rendered["report"]).read_text(encoding="utf-8"))
    segment_gate = report["islands"][0]["segment_quality_gate"]
    assert segment_gate["passed"] is True
    assert segment_gate["fatal_failures"] == []
    assert segment_gate["deferred_to_whole_set"] == [
        "rms_std_db catastrophically low; render is effectively flat"
    ]


def test_segment_nonflat_quality_failure_remains_fatal(tmp_path):
    class Core(_RuntimeCore):
        pass

    install_island_render_dispatch(Core)
    core = Core(tmp_path / "bad-segment")
    core.segment_quality_gate = {
        "passed": False,
        "failures": ["peak below audible floor; render is effectively empty"],
        "warnings": [],
        "metrics": {"peak": 0.0},
    }
    destination = core.config.working_root / "renders" / "bad-segment.wav"
    _insert_parent(core, "parent-bad", _single_island_arrangement(), destination)

    try:
        core.render_mashup("parent-bad", destination)
    except RuntimeError as exc:
        assert "slice-local quality gate" in str(exc)
        assert "peak below audible floor" in str(exc)
    else:
        raise AssertionError("a slice-local hard failure must refuse the whole render")
    assert not destination.exists()
    assert core.conn().execute("SELECT COUNT(*) FROM mashups WHERE id LIKE 'isl_%'").fetchone()[0] == 0


def test_segment_gate_defaults_new_failure_types_to_fatal():
    classified = classify_segment_quality_gate({
        "passed": False,
        "failures": ["future criterion failed"],
        "warnings": [],
        "metrics": {},
    })
    assert classified["passed"] is False
    assert classified["fatal_failures"] == ["future criterion failed"]
    assert classified["deferred_to_whole_set"] == []


def test_whole_set_form_gate_enforces_withholding_entry_exit_and_content_change():
    arrangement = {
        "sections": [
            {"start_s": 0.0, "type": "INTRO", "layers": [
                {"source_track_key": "melody-a", "role": "vocal", "gain_db": 0.0}
            ]},
            {"start_s": 10.0, "type": "BUILD", "layers": [
                {"source_track_key": "melody-b", "role": "vocal", "gain_db": 0.0},
                {"source_track_key": "bass-a", "role": "bass", "gain_db": -2.0},
                {"source_track_key": "drums-a", "role": "drum_anchor", "gain_db": -1.0},
            ]},
            {"start_s": 20.0, "type": "HOLD", "layers": [
                {"source_track_key": "bass-b", "role": "bass", "gain_db": -3.0},
                {"source_track_key": "sustain-a", "role": "harmony", "gain_db": -4.0},
            ]},
            {"start_s": 30.0, "type": "PAYOFF", "layers": [
                {"source_track_key": "melody-c", "role": "vocal", "gain_db": 1.0},
                {"source_track_key": "bass-c", "role": "bass", "gain_db": -1.0},
                {"source_track_key": "drums-c", "role": "drum_anchor", "gain_db": 0.0},
            ]},
            {"start_s": 40.0, "type": "OUTRO", "layers": [
                {"source_track_key": "melody-d", "role": "vocal", "gain_db": -2.0},
                {"source_track_key": "bass-d", "role": "bass", "gain_db": -3.0},
            ]},
        ]
    }
    gate = whole_set_form_gate(arrangement)
    assert gate["passed"] is True
    assert gate["has_withholding"] is True
    assert gate["has_role_entry"] is True
    assert gate["has_role_exit"] is True
    assert gate["every_transition_changes_content"] is True

    repeated = {
        "sections": [
            {"start_s": float(index * 10), "type": "groove", "layers": [
                {"source_track_key": "same", "role": "drum_anchor", "gain_db": 0.0}
            ]}
            for index in range(3)
        ]
    }
    failed = whole_set_form_gate(repeated)
    assert failed["passed"] is False
    assert "whole-set role occupancy never changes" in failed["failures"]
    assert failed["every_transition_changes_content"] is False


def test_whole_set_audio_gate_judges_dynamic_arc_on_assembled_program():
    sr = 4000
    duration = 60
    t = np.arange(sr * duration, dtype=np.float32) / sr
    carrier = np.sin(2.0 * np.pi * 440.0 * t).astype(np.float32)
    flat = carrier * 0.10
    amplitudes = [0.03, 0.09, 0.04, 0.15, 0.05, 0.12, 0.03, 0.17, 0.05, 0.13, 0.04, 0.10]
    dynamic = np.concatenate([
        carrier[index * sr * 5:(index + 1) * sr * 5] * amplitude
        for index, amplitude in enumerate(amplitudes)
    ]).astype(np.float32)
    permissive_spectrum = {
        "rms_std_db": {"target": 5.0, "floor": 1.6},
        "low200_share": {"ceiling_fail": 2.0, "ceiling_warn": 2.0, "floor_warn": -1.0},
        "high3000_share": {"target": 0.0, "floor_warn": -1.0, "floor_fail": -1.0},
    }

    flat_gate = whole_set_quality_gate(flat, sr, duration, permissive_spectrum)
    dynamic_gate = whole_set_quality_gate(dynamic, sr, duration, permissive_spectrum)
    assert flat_gate["passed"] is False
    assert any("effectively flat" in failure for failure in flat_gate["failures"])
    assert dynamic_gate["passed"] is True
