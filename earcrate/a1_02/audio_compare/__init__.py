"""The audio side of A1-02: comparison only, never generation.

This package may read the custody capture, decoded audio, and sealed score-side
projections. It may not write score or performance authority, and nothing that
produces a performance may import it.

The direction matters more than the wall. An audio comparison that could also
generate would make the convergence claim circular: the score branch is sealed as
never having opened a recording, and the performance has to stay that way too. The
comparison may judge a performance afterwards. It may never be how one was made.
"""

from .align import ComparatorError, compare
from .anchors import score_anchors
from .features import bar_features

__all__ = ["ComparatorError", "bar_features", "compare", "score_anchors"]
