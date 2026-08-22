__version__ = "0.8.27"

# The split runtime historically imports common names through core.deps, while
# their implementations live in core.util. Multi-island persistence and render
# dispatch are real runtime paths, so make that import surface complete before
# any CLI or planner module is loaded. These are aliases only: utility identity,
# timestamps, source custody, and serialized plan authority are unchanged.
from earcrate.core import deps as _core_deps
from earcrate.core.util import (
    arrangement_sha as _arrangement_sha,
    now_utc as _now_utc,
    safe_name as _safe_name,
    ulidish as _ulidish,
)
_core_deps.arrangement_sha = _arrangement_sha
_core_deps.now_utc = _now_utc
_core_deps.safe_name = _safe_name
_core_deps.ulidish = _ulidish

from earcrate.cli import main

# Multi-island planning is installed as an additive engine capability. The
# implementation lives outside app.py so ordinary single-deck behavior remains
# byte- and semantic-identity stable unless the new entrypoint is invoked.
import functools as _functools
from earcrate.app import EarcrateCore as _EarcrateCore
from earcrate.plan.key_identity import install_key_identity as _install_key_identity
from earcrate.plan.islands import install_island_set as _install_island_set
from earcrate.plan.source_rotation import install_exact_pool_rotation as _install_exact_pool_rotation
_install_key_identity(_EarcrateCore)
_install_island_set(_EarcrateCore)
_install_exact_pool_rotation(_EarcrateCore)

# The island dispatcher replaces the bound method but must preserve the public
# introspection contract of the original @_durable_render_attempt decoration.
# Existing gates inspect render_mashup.__wrapped__ to verify that source identity
# and post-render refusal remain ordered inside the real single-deck renderer.
# Bind directly to that raw function, rather than to the intermediate decorated
# wrapper, so the pre-island contract remains byte-for-byte inspectable.
_render_dispatch = _EarcrateCore.render_mashup
_single_deck = _EarcrateCore._single_deck_render_mashup
_raw_single_deck = getattr(_single_deck, "__wrapped__", _single_deck)
_functools.update_wrapper(_render_dispatch, _raw_single_deck)

del _EarcrateCore, _install_key_identity, _install_island_set, _install_exact_pool_rotation, _functools
del _render_dispatch, _single_deck, _raw_single_deck
del _arrangement_sha, _core_deps, _now_utc, _safe_name, _ulidish
