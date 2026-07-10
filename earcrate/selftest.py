from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.ui.server import *
def make_sine_fixture(root: Path) -> None:
    sr = DEFAULT_SAMPLE_RATE
    root.mkdir(parents=True, exist_ok=True)
    specs = [
        ("Electro", "Anchor Beat", 100, 60, 36),
        ("Soul", "Vocal Hook", 100, 65, 40),
        ("Rock", "Guitar Bed", 100, 55, 43),
        ("Funk", "Bass Walk", 100, 50, 38),
    ]
    for artist, title, bpm, freq, midi in specs:
        dur = 12.0
        t = np.arange(int(sr * dur)) / sr
        beat = np.zeros_like(t)
        for b in np.arange(0, dur, 60 / bpm):
            i = int(b * sr)
            beat[i:i+800] += np.hanning(min(800, beat.size-i)) * 0.65
        tone = 0.2 * np.sin(2 * np.pi * freq * t) + 0.1 * np.sin(2 * np.pi * freq * 2 * t)
        if "Vocal" in title:
            tone += 0.16 * np.sin(2 * np.pi * 440 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2.5 * t))
        y = (beat + tone).astype(np.float32)
        y = y / max(1e-6, np.max(np.abs(y))) * 0.75
        folder = root / artist / "Fixture Album"
        folder.mkdir(parents=True, exist_ok=True)
        sf.write(str(folder / f"01 {title}.wav"), y, sr, subtype="PCM_16")


def self_test() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="jbgt_selftest_"))
    master = tmp / "music"
    work = tmp / "work"
    agent = tmp / "agent"
    master.mkdir(parents=True, exist_ok=True)
    core = EarcrateCore()
    core.configure({"master_root": str(master), "working_root": str(work), "agent_root": str(agent), "analysis_seconds": 30, "workers": 1})
    doctor = core.doctor()
    print(json.dumps(doctor, indent=2))
    assert doctor.get("ok") is True

    manifest_id = ulidish()
    op_id = ulidish()
    manifest = {
        "manifest_id": manifest_id,
        "created_at": now_utc(),
        "author": "self_test",
        "seed": 1337,
        "summary": "Create self-test playlist",
        "operations": [
            {"op_id": op_id, "type": "create_playlist", "args": {"name": "self test playlist", "entries": [], "format": "m3u8"}, "preconditions": {}}
        ],
    }
    manifest_path = agent / "manifests" / "self-test-playlist.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    playlist_path = work / "playlists" / "self test playlist.m3u8"

    dry = core.execute_manifest(str(manifest_path))
    print(json.dumps({"dry_run": dry}, indent=2))
    assert dry.get("dry_run") is True
    assert not playlist_path.exists()

    applied = core.execute_manifest(str(manifest_path), apply=True)
    print(json.dumps({"apply": applied}, indent=2))
    assert applied.get("dry_run") is False
    assert playlist_path.exists() and playlist_path.read_text(encoding="utf-8").startswith("#EXTM3U")

    rollback_dry = core.rollback_outputs(manifest_id=manifest_id)
    print(json.dumps({"rollback_dry": rollback_dry}, indent=2))
    assert rollback_dry.get("dry_run") is True
    assert playlist_path.exists()

    rollback_apply = core.rollback_outputs(manifest_id=manifest_id, apply=True)
    print(json.dumps({"rollback_apply": rollback_apply}, indent=2))
    assert rollback_apply.get("moved") == 1
    assert not playlist_path.exists()

    bad_manifest = {**manifest, "manifest_id": ulidish(), "operations": [{"op_id": ulidish(), "type": "delete_master", "args": {}, "preconditions": {}}]}
    bad_path = agent / "manifests" / "bad-op.json"
    bad_path.write_text(json.dumps(bad_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        core.execute_manifest(str(bad_path))
        raise AssertionError("bad operation type was not rejected")
    except ValueError:
        pass

    print(f"SELF_TEST_OK {tmp}")
    return 0


