from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.deck.harmony import *
@dataclass
class Config:
    master_root: Path
    working_root: Path
    stems_root: Path
    playlists_root: Path
    agent_root: Path
    sample_rate: int = DEFAULT_SAMPLE_RATE
    workers: int = 0
    seed: int = 1337
    analysis_seconds: int = DEFAULT_ANALYSIS_SECONDS
    # Which registered StemProvider render selects. "noop" (the shipped default)
    # means "use the registered default provider" (get("stems") with no name), so
    # a box with no GPU never separates. Set to "demucs" (or an env override
    # EARCRATE_STEMS=demucs) to opt a real GPU box into stem separation.
    stem_provider: str = "noop"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "master_root": str(self.master_root),
            "working_root": str(self.working_root),
            "stems_root": str(self.stems_root),
            "playlists_root": str(self.playlists_root),
            "agent_root": str(self.agent_root),
            "sample_rate": self.sample_rate,
            "workers": self.workers,
            "seed": self.seed,
            "analysis_seconds": self.analysis_seconds,
            "stem_provider": self.stem_provider,
        }


