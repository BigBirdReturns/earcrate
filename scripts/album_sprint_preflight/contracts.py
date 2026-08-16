from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import PreflightError, load, require_seal


def campaign_and_contract(campaign_path: Path, preflight_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    campaign = load(campaign_path)
    preflight = load(preflight_path)
    require_seal(campaign, "contract_sha256")
    require_seal(preflight, "preflight_contract_sha256")
    if preflight.get("kind") != "earcrate_album_sprint_executable_preflight":
        raise PreflightError("wrong executable preflight kind")
    if preflight.get("base_campaign_sha256") != campaign.get("contract_sha256"):
        raise PreflightError("executable preflight belongs to another campaign")
    if preflight.get("sprint_id") != campaign.get("sprint_id"):
        raise PreflightError("campaign and preflight sprint IDs disagree")
    campaign_tracks = {str(row.get("track_id")): row for row in campaign.get("tracks") or []}
    preflight_tracks = preflight.get("tracks") or {}
    expected = [f"A1-{index:02d}" for index in range(1, 8)]
    if list(campaign_tracks) != expected or list(preflight_tracks) != expected:
        raise PreflightError("campaign and preflight must declare A1-01 through A1-07 in order")
    return campaign, preflight
