"""Shared evidence primitives: identity, seals, receipts, boundaries.

This package exists because these invariants have already survived more than one
concrete use, not because the names sound general. Every piece here was implemented
at least twice inside A1-07 alone, and the duplicates disagreed or went stale at
least once:

* the tree digest was written twice and misclassified its file set twice;
* the sealed body-free receipt was written four times;
* a verdict bound to an object identity was written twice;
* the ledger transition was hand-applied three times and produced a stale seal
  every time.

The musical machinery -- arrangement graphs, performance realizers, frontier
builders -- is deliberately absent. Those still carry A1-07's assumptions about
placing recorded clips against a phrase map, and A1-02 exists to challenge exactly
those assumptions. See `docs/EXTRACTION_BOUNDARY.md`.
"""

from .identity import ObjectIdentity, canonical_json_bytes, seal, sha256_bytes, \
    sha256_file, validate_seal
from .receipts import EvidenceError, load_sealed, seal_field, verify_body_free

__all__ = [
    "EvidenceError",
    "ObjectIdentity",
    "canonical_json_bytes",
    "load_sealed",
    "seal",
    "seal_field",
    "sha256_bytes",
    "sha256_file",
    "validate_seal",
    "verify_body_free",
]
