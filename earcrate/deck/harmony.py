from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.judge.audio import *
def krumhansl_key(chroma: np.ndarray) -> Tuple[int, int, float]:
    if chroma.size == 0:
        return 0, 1, 0.0
    vec = np.mean(chroma, axis=1)
    if float(np.sum(vec)) <= 0:
        return 0, 1, 0.0
    vec = vec / (np.linalg.norm(vec) + 1e-9)
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float32)
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float32)
    major = major / np.linalg.norm(major)
    minor = minor / np.linalg.norm(minor)
    scores = []
    for root in range(12):
        scores.append((float(np.dot(vec, np.roll(major, root))), root, 1))
        scores.append((float(np.dot(vec, np.roll(minor, root))), root, 0))
    scores.sort(reverse=True, key=lambda x: x[0])
    best, second = scores[0], scores[1]
    conf = max(0.0, min(1.0, (best[0] - second[0] + 0.02) * 8))
    return int(best[1]), int(best[2]), float(conf)


from earcrate.deck.dsp import pitch_distance  # moved to dsp (cycle-free) in v0.7.2


def compatible_pitch_shift(loop_key: Optional[int], target_key: Optional[int], budget: int, strictness: int) -> Optional[int]:
    if loop_key is None or target_key is None:
        return 0
    raw = pitch_distance(loop_key, target_key)
    candidates = [raw, raw - 12, raw + 12]
    candidates = sorted(candidates, key=lambda x: abs(x))
    for c in candidates:
        if abs(c) <= budget:
            if strictness >= 80 and abs(c) not in (0, 3, 4, 5, 7):
                continue
            return int(c)
    return None


