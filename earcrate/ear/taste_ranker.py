from earcrate.core.deps import *
from earcrate.core.util import json_dumps, sha256_text
"""The taste ranker — a proposer trained on the owner's own approve/reject calls.

Every human judgment in ``atom_judgments`` is a labelled example. This module
learns a small model over the atom features the engine already computes and uses
it to REORDER the candidate pool so the owner's taste surfaces first. It is a
PROPOSER only: it changes the ORDER candidates are considered in, never which
candidates exist, never a gate outcome, never a policy bound. The measured judge
still disposes. Off by default and identity-when-off.

Deliberately dependency-free: a plain L2-regularized logistic regression trained
by deterministic gradient descent from a zero init (no sklearn, no RNG), so a
given judgments set always yields the same model — the training receipt is
reproducible. The model is a small JSON artifact (feature list + standardization
+ weights), content-addressed by its own hash.
"""

# The ordered numeric atom features the ranker reads. All already computed by
# analysis/ear-crating; a missing one defaults to 0.0. Order is part of the
# model identity — never reorder without retraining.
FEATURES = (
    "score", "hook_score", "bed_score", "floor_score", "bass_score", "spark_score",
    "intelligibility", "low_share", "mid_share", "high_share",
    "loopability", "transient_density", "energy", "vocal_likelihood",
)


def _vec(atom: Dict[str, Any]) -> "np.ndarray":
    return np.array([float(atom.get(k) or 0.0) for k in FEATURES], dtype=np.float64)


def train_ranker(samples: List[Dict[str, Any]], *, iterations: int = 400,
                 lr: float = 0.5, l2: float = 1e-3) -> Dict[str, Any]:
    """Train from labelled atoms. ``samples`` is a list of dicts, each an atom
    feature dict plus an integer ``label`` (1 approved / favorite, 0 rejected).
    Deterministic: zero init + fixed-iteration GD, no randomness. Raises on an
    unlearnable set (no positives or no negatives) rather than emit a degenerate
    model."""
    pos = [s for s in samples if int(s.get("label", 0)) == 1]
    neg = [s for s in samples if int(s.get("label", 0)) == 0]
    if len(pos) < 1 or len(neg) < 1:
        raise ValueError(f"taste ranker needs both approved and rejected examples "
                         f"(have {len(pos)} approved, {len(neg)} rejected)")
    X = np.array([_vec(s) for s in samples], dtype=np.float64)
    y = np.array([int(s.get("label", 0)) for s in samples], dtype=np.float64)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    Xs = (X - mean) / std
    n, d = Xs.shape
    w = np.zeros(d, dtype=np.float64)
    b = 0.0
    for _ in range(int(iterations)):
        z = Xs @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        err = p - y
        gw = (Xs.T @ err) / n + l2 * w
        gb = float(np.mean(err))
        w -= lr * gw
        b -= lr * gb
    model = {
        "kind": "taste_ranker_logreg_v1",
        "features": list(FEATURES),
        "mean": [float(x) for x in mean],
        "std": [float(x) for x in std],
        "weights": [float(x) for x in w],
        "bias": float(b),
        "n_approved": len(pos),
        "n_rejected": len(neg),
        "iterations": int(iterations),
        "lr": float(lr),
        "l2": float(l2),
    }
    model["model_sha"] = sha256_text(json_dumps({k: model[k] for k in
                          ("features", "mean", "std", "weights", "bias")}))[:32]
    return model


def score_atom(model: Dict[str, Any], atom: Dict[str, Any]) -> float:
    """Predicted approval probability in [0,1] for one atom."""
    mean = np.asarray(model["mean"], dtype=np.float64)
    std = np.asarray(model["std"], dtype=np.float64)
    w = np.asarray(model["weights"], dtype=np.float64)
    x = (_vec(atom) - mean) / std
    z = float(x @ w) + float(model["bias"])
    return float(1.0 / (1.0 + np.exp(-z)))


def rank_pool(pool: List[Dict[str, Any]], model: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reorder the pool by predicted approval, highest first. STABLE: ties keep
    their original order, so the output is always a permutation of the input with
    identical membership. A None model is a no-op (returns the pool unchanged)."""
    if not model or not pool:
        return pool
    scored = [(i, score_atom(model, a), a) for i, a in enumerate(pool)]
    # sort by (-predicted, original index) -> deterministic, membership-preserving
    scored.sort(key=lambda t: (-t[1], t[0]))
    out = []
    for _i, s, a in scored:
        a2 = dict(a)
        a2["taste_rank_score"] = round(float(s), 6)
        out.append(a2)
    return out


def ranker_path(agent_root: "Path", taste_profile: str) -> "Path":
    return Path(agent_root) / "rankers" / f"{taste_profile}.json"


def save_ranker(model: Dict[str, Any], path: "Path") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(model), encoding="utf-8")


def load_ranker(path: "Path") -> Optional[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        if list(m.get("features") or []) != list(FEATURES):
            return None  # feature drift -> ignore stale artifact rather than mis-score
        return m
    except Exception:
        return None


def ranker_enabled(config: Any = None) -> bool:
    """Opt-in switch: EARCRATE_RANKER (env, 'on'/'1'/'true') > config.taste_ranker.
    Default OFF — the pool is returned exactly as the retriever produced it."""
    env = os.environ.get("EARCRATE_RANKER")
    if env is not None:
        return str(env).strip().lower() in {"1", "on", "true", "yes"}
    return bool(getattr(config, "taste_ranker", False)) if config is not None else False
