"""A1-02 performance generation: score in, audio out, recording never consulted.

This package may read the score, its annotations, its navigation and — when they
exist — the reconstruction MIDI and an approved rack. It may not import the audio
comparison, accept an audio path, or take a reference digest. The comparison judges a
performance afterwards; it is never how one was made, and the branch independence the
specimen is built on depends on that staying true in the file system rather than in
intention.

What is here today is a harmonic realization, not the note-level performance. The
1,257-note reconstruction MIDI and the four-page score PDF are both unavailable, so
the only score authority in this checkout is the sealed chord vocabulary, the printed
navigation, and the 105-measure traversal. That is enough to make real, deterministic,
score-derived audio — and it is not enough to claim a performance. The receipt says
which of those it is.
"""

from .harmony import HarmonyRealizationError, realize
from .render import render_engineering_audio

__all__ = ["HarmonyRealizationError", "realize", "render_engineering_audio"]
