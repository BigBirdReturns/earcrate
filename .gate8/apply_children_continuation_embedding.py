from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

builder = ROOT / "build" / "make_singlefile.py"
text = builder.read_text(encoding="utf-8")
old = 'SPECIMEN_FILES = ["model.py", "convergence.py", "children.py", "gate.py", "cli.py", "__init__.py"]'
new = 'SPECIMEN_FILES = ["model.py", "convergence.py", "children.py", "continuation.py", "gate.py", "cli.py", "__init__.py"]'
if text.count(old) != 1:
    raise SystemExit("single-file specimen module list patch point is missing or ambiguous")
builder.write_text(text.replace(old, new, 1), encoding="utf-8")

Path(__file__).unlink()
workflow = ROOT / ".github" / "workflows" / "apply-children-continuation.yml"
workflow.unlink(missing_ok=True)
