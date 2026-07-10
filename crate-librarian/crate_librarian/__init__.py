"""crate-librarian — turn a folder of music into a clean, identified, deduped,
organized library with a stable machine-readable contract (library.json).

Standalone and reusable: mutagen for tags, stdlib for everything else. No audio
analysis, no personas, no UI. Any project can consume the library.json it emits;
EarCrate is consumer #1.
"""
from .identity import derive_identity, folder_identity, fix_case, safe_name
from .scan import scan_roots, read_tags, sha256_file, AUDIO_EXTS
from .library import build_library, write_library, read_library, CONTRACT_VERSION
from .organize import organize, plan_organize, rollback, NAME_PATTERNS

__version__ = "0.1.0"
__all__ = [
    "derive_identity", "folder_identity", "fix_case", "safe_name",
    "scan_roots", "read_tags", "sha256_file", "AUDIO_EXTS",
    "build_library", "write_library", "read_library", "CONTRACT_VERSION",
    "organize", "plan_organize", "rollback", "NAME_PATTERNS",
]
