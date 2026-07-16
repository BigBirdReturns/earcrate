from earcrate.core.deps import *
"""The TransformProvider seam — high-quality time-stretch / pitch-shift.

The render hot path stretches and pitch-shifts clips. The DEFAULT is the
existing phase vocoder (``librosa.effects.time_stretch`` / ``pitch_shift``):
selecting nothing changes nothing, byte for byte. Rubber Band is an OPT-IN,
higher-fidelity alternative (the industry-standard independent time/pitch
engine) that a box enables only when both the ``rubberband`` CLI binary and
``pyrubberband`` are present — probed HONESTLY here and surfaced in doctor.

Like the StemProvider seam, this NEVER makes the shipped box worse: a box
without Rubber Band, or one that requests it without the binary installed,
transparently falls back to the phase vocoder. The heavy path is guarded —
importing this module touches neither pyrubberband nor the binary; only a real
rubberband call shells out. Because the two engines produce different samples,
the render transform-cache key carries the effective provider so a phase-vocoder
clip and a Rubber Band clip can never collide.

This is deliberately OPT-IN and does NOT bump ENGINE_VERSION: the default render
is unchanged, so banked renders stay valid. Flipping the default (and taking the
ENGINE_VERSION bump) waits on a real ears verdict on the box.
"""

VALID_TRANSFORM_PROVIDERS = ("phase_vocoder", "rubberband")


def transform_capability() -> Dict[str, Any]:
    """HONEST probe. Reports whether Rubber Band can actually run here. Never
    raises. On the shipped default box (no rubberband binary) ``ready`` is False
    and the phase vocoder is the only provider — the app is fully functional."""
    def _importable(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:
            return False
    pyrb = _importable("pyrubberband")
    binary = shutil.which("rubberband") is not None
    ready = bool(pyrb and binary)
    return {
        "pyrubberband": pyrb,
        "rubberband_bin": binary,
        "ready": ready,
        "default": "phase_vocoder",
        "providers": ["phase_vocoder"] + (["rubberband"] if ready else []),
        "note": ("rubberband high-quality time/pitch ready (opt in with EARCRATE_TRANSFORM=rubberband)"
                 if ready else
                 "rubberband OFF — needs the 'rubberband' CLI binary + pyrubberband; "
                 "default phase_vocoder (librosa) in use"),
    }


def resolve_transform_provider(config: Any = None) -> str:
    """The EFFECTIVE transform provider after the availability fallback.

    Selection order mirrors the stem seam: ``EARCRATE_TRANSFORM`` (env) >
    ``config.transform_provider`` (if a Config ever carries one) > the default
    ``phase_vocoder``. A request for ``rubberband`` on a box that cannot run it
    degrades to ``phase_vocoder`` — honestly, never a crash."""
    selected = (os.environ.get("EARCRATE_TRANSFORM")
                or (getattr(config, "transform_provider", None) if config is not None else None)
                or "phase_vocoder")
    selected = str(selected).strip().lower()
    if selected not in VALID_TRANSFORM_PROVIDERS:
        selected = "phase_vocoder"
    if selected == "rubberband" and not transform_capability()["ready"]:
        return "phase_vocoder"
    return selected


def rubberband_time_stretch(y: "np.ndarray", sr: int, rate: float) -> "np.ndarray":
    """Rubber Band time-stretch. ``rate`` matches librosa's convention (rate>1
    speeds up / shortens). Length is normalized by the caller, as with the phase
    vocoder path."""
    import pyrubberband as prb
    out = prb.time_stretch(np.asarray(y, dtype=np.float32), int(sr), float(rate))
    return np.asarray(out, dtype=np.float32)


def rubberband_pitch_shift(y: "np.ndarray", sr: int, n_steps: float) -> "np.ndarray":
    """Rubber Band pitch-shift by ``n_steps`` semitones (librosa convention)."""
    import pyrubberband as prb
    out = prb.pitch_shift(np.asarray(y, dtype=np.float32), int(sr), float(n_steps))
    return np.asarray(out, dtype=np.float32)
