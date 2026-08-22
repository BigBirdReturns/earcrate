"""Runtime witnesses for the two island paths the synthetic planner skipped.

The private Proof-005 fixture reached plan acceptance, then exposed that the
persist and render-dispatch paths imported utility functions from an incomplete
runtime surface. A later private run exposed a second runtime-only defect: the
island dispatcher put segment WAVs beneath agent_root even though the ordinary
renderer accepts destinations only beneath working_root/renders. These gates
execute the real paths and enforce the real root relationship rather than
letting a permissive stub hide it. The repository runner calls tests directly,
so both witnesses accept only the supported ``tmp_path`` fixture.
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
from earcrate.plan.island_render import install_island_render_dispatch


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
        self.rendered_segment_paths.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        samples = np.linspace(-0.25, 0.25, self.config.sample_rate, dtype=np.float32)
        sf.write(str(destination), samples, self.config.sample_rate, subtype="PCM_24")
        stem_path = destination.with_name(destination.stem + ".stem_voice.wav")
        sf.write(str(stem_path), samples, self.config.sample_rate, subtype="PCM_24")
        report_path = destination.with_suffix(".render_report.json")
        report_path.write_text(
            json.dumps({"stems": {"paths": {"voice": str(stem_path)}}}) + "\n",
            encoding="utf-8",
        )
        return {
            "type": "render_mashup",
            "path": str(destination),
            "report": str(report_path),
            "presented": True,
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
    segment = {
        "bpm": 120.0,
        "target_key": 0,
        "seed": 17,
        "params": {"stem_export": True},
        "sections": [],
    }
    arrangement = {
        "kind": ISLAND_SET_KIND,
        "islands": [{"island_id": "island-000", "arrangement": segment}],
        "transitions": [],
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
    _insert_parent(core, "parent", arrangement, destination)

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
