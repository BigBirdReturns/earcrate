from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

custody = ROOT / "earcrate" / "project" / "custody.py"
text = custody.read_text(encoding="utf-8")
old = '''def _score_family(score: Mapping[str, Any]) -> str:\n    if str(score.get("schema") or "") == "earcrate/dj-stage-score@1":\n        return "dj_stage_score"\n    if str(score.get("kind") or "") == "earcrate_player_piano_composition":\n        return "player_piano_composition"\n    raise ValidationError("unsupported causal-score artifact family")\n'''
new = '''def _score_family(score: Mapping[str, Any]) -> str:\n    if str(score.get("schema") or "") == "earcrate/dj-stage-score@1":\n        return "dj_stage_score"\n    # Early DJ-director artifacts predate the string schema marker but already\n    # carry the complete v1 stage-score contract.  Accept that exact structural\n    # family so custody can preserve it without rewriting historical bytes.\n    if (\n        int(score.get("schema_version") or 0) == 1\n        and str(score.get("stage_id") or "")\n        and isinstance(score.get("sections"), list)\n        and isinstance(score.get("events"), list)\n        and int(score.get("ticks_per_beat") or 0) > 0\n        and int(score.get("total_ticks") or 0) > 0\n    ):\n        return "dj_stage_score"\n    if str(score.get("kind") or "") == "earcrate_player_piano_composition":\n        return "player_piano_composition"\n    raise ValidationError("unsupported causal-score artifact family")\n'''
if old not in text:
    raise SystemExit("custody score-family patch point is missing")
custody.write_text(text.replace(old, new, 1), encoding="utf-8")

builder = ROOT / "build" / "make_singlefile.py"
text = builder.read_text(encoding="utf-8")
old = '"music/player_piano.py", "music/heritage.py", "music/director.py", "music/source_phrase_model.py", "music/source_phrase_audio.py",'
new = '"music/player_piano.py", "music/heritage.py", "music/director_validation.py", "music/director_render.py", "music/source_phrase_model.py", "music/source_phrase_audio.py",'
if old not in text:
    raise SystemExit("single-file director patch point is missing")
builder.write_text(text.replace(old, new, 1), encoding="utf-8")

Path(__file__).unlink()
workflow = ROOT / ".github" / "workflows" / "apply-song-reader-fixes.yml"
if workflow.exists():
    workflow.unlink()
