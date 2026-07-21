"""Session-wide isolation of the app-global workspace pointer.

Why this file exists (2026-07-20 incident): the operator's machine sets a real
``EARCRATE_HOME=S:\\EarCrate-Workspace``. ``visible_app_dir()`` honors that env
var first, so the app-global pointer is a REAL file holding the real 216k-atom
workspace. ``tests/test_gates.py`` tried to sandbox it with
``os.environ.setdefault("EARCRATE_HOME", mkdtemp())`` — but ``setdefault`` is a
no-op precisely when a real ``EARCRATE_HOME`` already exists, which is the only
case that needed sandboxing. A gate then called ``configure_workspace()`` on a
``mkdtemp`` workspace, and the resulting pointer body
``{"config_json": "S:\\Temp\\tmpgrmogjz9\\agent\\config.json"}`` was written
over the operator's real pointer. Every later CLI run silently resolved an empty
database: ``approved_atom_pool`` returned 0 of 216,034 approved atoms, with no
error anywhere — the failure only surfaced as "no usable approved EarAtoms".

The fix is unconditional and belongs at the session boundary, before any test
imports a module that reads the env: force EARCRATE_HOME to a per-run temp dir,
always, overriding whatever the machine has. Individual tests that set their own
EARCRATE_HOME still work — they override this within their own scope.

Belt and suspenders: ``EarcrateCore.configure_workspace`` separately refuses to
write a temp-dir config path into a non-temp pointer (see app.py), which covers
entry points pytest never sees.
"""
import os
import tempfile

import pytest


def _isolate_home() -> str:
    home = tempfile.mkdtemp(prefix="earcrate_test_home_")
    os.environ["EARCRATE_HOME"] = home  # assignment, NEVER setdefault
    return home


# Applied at import time as well as via the fixture: module-level code in test
# files (test_gates.py builds paths at import) runs before any fixture does.
_SESSION_HOME = _isolate_home()


@pytest.fixture(autouse=True, scope="session")
def _earcrate_home_sandbox():
    """Guarantee the sandbox for the whole session, even if a test leaked a
    restore of the machine's real EARCRATE_HOME back into os.environ."""
    os.environ["EARCRATE_HOME"] = _SESSION_HOME
    yield
    os.environ["EARCRATE_HOME"] = _SESSION_HOME


# Every EARCRATE_* var that app code mutates as a side effect of merely
# constructing EarcrateCore. `_seed_from_machine_defaults` calls
# ``os.environ.setdefault("EARCRATE_STEMS", ...)`` and the same for
# EARCRATE_CACHE_ROOT, so one test that exercises auto-seed silently changes the
# stem provider for every test that runs after it — which is how a passing
# auto-seed test broke two unrelated "provider is noop" gates downstream. Snapshot
# and restore around each test so ordering cannot change any test's meaning.
_LEAKY_VARS = ("EARCRATE_STEMS", "EARCRATE_CACHE_ROOT", "EARCRATE_DEFAULTS", "EARCRATE_HOME")


@pytest.fixture(autouse=True)
def _earcrate_env_isolation():
    saved = {k: os.environ.get(k) for k in _LEAKY_VARS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
