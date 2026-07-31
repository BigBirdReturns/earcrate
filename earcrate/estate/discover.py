"""Compatibility surface for read-only local-estate discovery."""

from earcrate.estate.scan import redact_estate_inventory, scan_estate

__all__ = ["scan_estate", "redact_estate_inventory"]
