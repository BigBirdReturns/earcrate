from earcrate.core.deps import *
"""The BeatProvider seam — real beat/downbeat/section detection.

The analysis path derives beats with ``librosa.beat.beat_track``, downbeats with
a max-RMS phase heuristic, and sections by an every-4th-downbeat energy split.
Both reviewers (and ``docs/OSS_INTEGRATION_AUDIT.md``) flagged this as the
biggest perceptual gap: weak downbeat confidence, no real phrase/section
functions. This seam lets a box replace that heuristic path with a proper MIR
model — **allin1** (All-In-One Music Structure Analyzer: one model returns
beats + downbeats + tempo + functional segments) is the recommended backend.

Exactly like the Stem and Transform seams, this NEVER makes the shipped box
worse. The DEFAULT is ``librosa`` — selecting nothing changes the analysis byte
for byte. A real backend is OPT-IN via ``EARCRATE_BEATS=allin1``, and a box that
requests it without the model installed falls back to librosa, honestly, never a
crash. Heavy imports are GUARDED: importing this module touches neither allin1
nor torch; only a real detect call loads them.

IMPORTANT — provider ⇒ analysis identity. allin1 and librosa produce different
grids, so switching the beat provider requires a ``analyze --force`` re-analyze
(or an ANALYZER_VERSION bump) so a librosa-analyzed file's cached grid is not
served for an allin1 run. This module does not silently mix the two.

STATUS: the allin1 adapter is written to allin1's documented API and is
UNVERIFIED until a box with allin1 installed runs it (the demucs pattern). The
default+fallback+probe contract is what the gate pins here.
"""

VALID_BEAT_PROVIDERS = ("librosa", "allin1")


def beat_capability() -> Dict[str, Any]:
    """HONEST probe. Never raises. On the shipped box (no allin1) ``ready`` is
    False and librosa is the only provider — analysis is fully functional."""
    def _importable(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:
            return False
    allin1_ok = _importable("allin1")
    torch_ok = _importable("torch")
    ready = bool(allin1_ok)
    return {
        "allin1": allin1_ok,
        "torch": torch_ok,
        "ready": ready,
        "default": "librosa",
        "providers": ["librosa"] + (["allin1"] if ready else []),
        "note": ("allin1 beat/downbeat/section model ready (opt in with EARCRATE_BEATS=allin1, "
                 "then re-analyze with --force)"
                 if ready else
                 "allin1 OFF — needs `pip install allin1` (+ torch); default librosa beat_track in use"),
    }


def resolve_beat_provider(config: Any = None) -> str:
    """The EFFECTIVE beat provider after the availability fallback. Order mirrors
    the other seams: ``EARCRATE_BEATS`` (env) > ``config.beat_provider`` > the
    default ``librosa``. A request for a backend that cannot run degrades to
    ``librosa`` — never a crash, never a silent wrong grid."""
    selected = (os.environ.get("EARCRATE_BEATS")
                or (getattr(config, "beat_provider", None) if config is not None else None)
                or "librosa")
    selected = str(selected).strip().lower()
    if selected not in VALID_BEAT_PROVIDERS:
        selected = "librosa"
    if selected == "allin1" and not beat_capability()["ready"]:
        return "librosa"
    return selected


def _sections_from_segments(y: "np.ndarray", sr: int, segments: list) -> list:
    """Map allin1 functional segments onto EarCrate's section shape
    ({start,end,label,energy}); energy is measured from the signal so the shape
    matches the librosa path exactly."""
    out = []
    n = int(y.size)
    for seg in segments:
        a = float(getattr(seg, "start", None) if not isinstance(seg, dict) else seg.get("start", 0.0) or 0.0)
        b = float(getattr(seg, "end", None) if not isinstance(seg, dict) else seg.get("end", 0.0) or 0.0)
        label = str(getattr(seg, "label", None) if not isinstance(seg, dict) else seg.get("label", "")) or "verse"
        if b - a < 1.0:
            continue
        i0, i1 = max(0, int(a * sr)), min(n, int(b * sr))
        chunk = y[i0:i1]
        e = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
        out.append({"start": round(a, 3), "end": round(b, 3), "label": label, "energy": round(e, 6)})
    return out


def detect_beats(y: "np.ndarray", sr: int, provider: str) -> Optional[Dict[str, Any]]:
    """Run the selected real beat backend and return an override dict
    ({bpm, bpm_confidence, beats, downbeats, sections, backend}) or None to mean
    "use the existing librosa path". Returns None on ANY failure — a beat backend
    hiccup must degrade to librosa, never fail the file's analysis.

    The allin1 adapter is written to allin1's documented API (result.beats /
    .downbeats / .segments / .bpm) and is rig-verified only.
    """
    provider = str(provider or "librosa").lower()
    if provider != "allin1":
        return None
    try:
        import allin1  # guarded: only loaded when actually opted in and available
        import soundfile as _sf
        import tempfile as _tf
        # allin1.analyze consumes a file path; write the in-memory PCM to a temp wav.
        with _tf.TemporaryDirectory() as td:
            wav = str(Path(td) / "clip.wav")
            _sf.write(wav, np.asarray(y, dtype=np.float32), int(sr))
            res = allin1.analyze(wav)
        beats = np.asarray(list(getattr(res, "beats", []) or []), dtype=np.float32)
        downbeats = np.asarray(list(getattr(res, "downbeats", []) or []), dtype=np.float32)
        segments = list(getattr(res, "segments", []) or [])
        if beats.size < 2:
            return None  # nothing usable — let librosa run
        bpm = float(getattr(res, "bpm", 0.0) or 0.0)
        if bpm <= 0:
            intervals = np.diff(beats)
            bpm = float(60.0 / max(1e-6, float(np.median(intervals)))) if intervals.size else 120.0
        # allin1 downbeats are model-tracked, so confidence is high by construction;
        # still derive a bounded estimate from grid regularity for parity with librosa.
        if beats.size >= 8:
            iv = np.diff(beats)
            conf = float(max(0.0, min(1.0, 1.0 - (np.std(iv) / (np.mean(iv) + 1e-9)))))
        else:
            conf = 0.5
        return {
            "bpm": bpm,
            "bpm_confidence": conf,
            "beats": beats,
            "downbeats": downbeats,
            "sections": _sections_from_segments(np.asarray(y, dtype=np.float32), int(sr), segments),
            "backend": "allin1",
        }
    except Exception:
        return None
