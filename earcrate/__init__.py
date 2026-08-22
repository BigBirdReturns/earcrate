__version__ = "0.8.27"
from earcrate.cli import main

# Multi-island planning is installed as an additive engine capability. The
# implementation lives outside app.py so ordinary single-deck behavior remains
# byte- and semantic-identity stable unless the new entrypoint is invoked.
from earcrate.app import EarcrateCore as _EarcrateCore
from earcrate.plan.islands import install_island_set as _install_island_set
_install_island_set(_EarcrateCore)
del _EarcrateCore, _install_island_set
