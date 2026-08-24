"""Public fixture-diversity authority.

The raw axis implementation lives in :mod:`fixture_diversity_core`. Every caller
uses this contract so semantic equivalence, evidence identity, and max-min tie
breaking are enforced at one boundary.
"""
from earcrate.plan.fixture_diversity_contract import *  # noqa: F401,F403
from earcrate.plan.fixture_diversity_contract import __all__
