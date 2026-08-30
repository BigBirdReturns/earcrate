#!/usr/bin/env python3
"""Focused gates for the interpreter-bound Demucs process provider."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.providers.artifacts import ArtifactStore
from earcrate.providers.stems import (
    DemucsProcessStemProvider,
    probe_demucs_process_python,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wav(path: Path, seconds: float = 0.25, frequency: float = 220.0) -> None:
    sr = 16000
    t = np.arange(int(sr * seconds), dtype=np.float32) / sr
    audio = (0.1 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
    sf.write(path, audio, sr, subtype="PCM_16")


class FakeExecution:
    def __init__(self, python_path: Path):
        self.python_path = python_path
        self.probes = 0
        self.separations = 0

    def probe(self, argv, *, env, timeout):
        self.probes += 1
        assert argv[0] == str(self.python_path)
        assert "VIRTUAL_ENV" not in env
        body = {
            "python_executable": str(self.python_path),
            "python_version": "3.13.0",
            "torch": True,
            "torch_version": "2.6.0+cu124",
            "demucs": True,
            "demucs_version": "4.1.0",
            "cuda": True,
            "gpu_name": "Fixture RTX 4060",
            "ready": True,
            "error": None,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

    def separate(self, argv, *, env, timeout):
        self.separations += 1
        assert argv[0] == str(self.python_path)
        output = Path(argv[argv.index("-o") + 1])
        track = output / "htdemucs" / "fixture"
        track.mkdir(parents=True, exist_ok=True)
        for index, role in enumerate(("vocals", "drums", "bass", "other")):
            _wav(track / (role + ".wav"), frequency=110.0 * (index + 1))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def _fixture(tmp: Path):
    python_path = tmp / ("python.exe" if os.name == "nt" else "python")
    python_path.write_bytes(b"fixture-heavy-python")
    source = tmp / "source.wav"
    _wav(source, seconds=0.5)
    executor = FakeExecution(python_path)
    settings = {
        "schema_version": 1,
        "provider_id": "demucs_process",
        "python_path": str(python_path),
        "python_sha256": _sha(python_path),
        "python_version": "3.13.0",
        "torch_version": "2.6.0+cu124",
        "demucs_version": "4.1.0",
        "gpu_name": "Fixture RTX 4060",
        "model": "htdemucs",
        "shifts": 0,
        "overlap": 0.10,
        "segment_seconds": 6.0,
    }
    settings_path = tmp / "stem_provider.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    store = ArtifactStore(tmp / "l3")
    provider = DemucsProcessStemProvider(
        store=store,
        settings_path=settings_path,
        runner=executor.separate,
        probe_runner=executor.probe,
    )
    return provider, executor, source, store, settings_path, python_path


def test_process_provider_proves_real_roles_and_cache(tmp_path=None):
    tmp = Path(tmp_path or tempfile.mkdtemp(prefix="process-stem-gate-"))
    provider, executor, source, store, _settings, _python = _fixture(tmp)
    first = provider.separate("pcm-fixture", str(source), ["drums", "bass", "other", "no_vocals"])
    assert first["available"] is True
    assert first["cached"] is False
    assert set(first["stems"]) == {"drums", "bass", "other", "no_vocals"}
    assert executor.probes == 1
    assert executor.separations == 1
    for role, key in first["stems"].items():
        held = store.get(key)
        assert held is not None and len(held["data"]) > 44
        assert held["meta"]["provider"] == "demucs_process"
        assert held["meta"]["source_identity"] == "pcm-fixture"
        info = sf.info(io.BytesIO(held["data"]))
        assert info.frames > 0

    second = provider.separate("pcm-fixture", str(source), ["drums", "bass", "other", "no_vocals"])
    assert second["available"] is True
    assert second["cached"] is True
    assert second["stems"] == first["stems"]
    assert executor.probes == 1
    assert executor.separations == 1


def test_process_provider_refuses_changed_interpreter(tmp_path=None):
    tmp = Path(tmp_path or tempfile.mkdtemp(prefix="process-stem-hash-gate-"))
    provider, executor, source, _store, _settings, python_path = _fixture(tmp)
    python_path.write_bytes(b"substituted-heavy-python")
    result = provider.separate("pcm-fixture", str(source), ["drums"])
    assert result["available"] is False
    assert "interpreter" in result["reason"].lower()
    assert executor.probes == 0
    assert executor.separations == 0


def test_process_provider_refuses_missing_receipt(tmp_path=None):
    tmp = Path(tmp_path or tempfile.mkdtemp(prefix="process-stem-missing-gate-"))
    source = tmp / "source.wav"
    _wav(source)
    provider = DemucsProcessStemProvider(
        store=ArtifactStore(tmp / "l3"),
        settings_path=tmp / "absent.json",
    )
    result = provider.separate("pcm-fixture", str(source), ["drums"])
    assert result["available"] is False
    assert "missing" in result["reason"].lower()


def test_process_probe_binds_interpreter_hash(tmp_path=None):
    tmp = Path(tmp_path or tempfile.mkdtemp(prefix="process-stem-probe-gate-"))
    python_path = tmp / ("python.exe" if os.name == "nt" else "python")
    python_path.write_bytes(b"fixture-heavy-python")
    executor = FakeExecution(python_path)
    receipt = probe_demucs_process_python(
        python_path,
        _sha(python_path),
        runner=executor.probe,
    )
    assert receipt["ready"] is True
    assert receipt["python_sha256"] == _sha(python_path)
    assert receipt["python_sha256_after"] == _sha(python_path)
    assert receipt["gpu_name"] == "Fixture RTX 4060"


def main() -> int:
    failed = 0
    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        try:
            fn()
            print("PASS " + name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("SUMMARY %d/%d process-stem gates passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
