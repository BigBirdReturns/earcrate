"""Local estate inventory, reconciliation, hardware, and provider acceptance."""

from earcrate.estate.discover import redact_estate_inventory, scan_estate
from earcrate.estate.homelab import (
    audit_homelab,
    capture_homelab_node,
    decide_homelab_target,
    homelab_catalog,
    homelab_sweep,
    propose_homelab_campaign,
    record_homelab_audition,
    record_homelab_stage,
)
from earcrate.estate.model import default_estate_policy, estate_architecture
from earcrate.estate.plan import apply_estate_plan, propose_estate_plan, rollback_estate_apply, verify_estate_apply
from earcrate.estate.rig import capture_rig_capabilities, propose_local_acceptance_campaign

__all__ = [
    "estate_architecture",
    "default_estate_policy",
    "scan_estate",
    "redact_estate_inventory",
    "propose_estate_plan",
    "apply_estate_plan",
    "rollback_estate_apply",
    "verify_estate_apply",
    "capture_rig_capabilities",
    "propose_local_acceptance_campaign",
    "homelab_catalog",
    "capture_homelab_node",
    "audit_homelab",
    "propose_homelab_campaign",
    "record_homelab_stage",
    "record_homelab_audition",
    "decide_homelab_target",
    "homelab_sweep",
]
