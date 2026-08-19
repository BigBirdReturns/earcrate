"""A1-02 score authority v2: derived from the recovered PDF, with new identities.

The historical extraction, reconstruction MIDI and MixScore are gone. Their identities
are known and their bytes are not, and a regenerated object may never claim a digest
that belonged to something else. So everything here is new, and says so.
"""

from .compile_midi import CompileError, compile_performed, semantic_identity, to_midi_ledger
from .extraction import ExtractionError, extract, hard_gate

__all__ = ["CompileError", "ExtractionError", "compile_performed", "extract",
           "hard_gate", "semantic_identity", "to_midi_ledger"]
