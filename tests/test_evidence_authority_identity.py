"""Gates for the authority/event identity split.

The defect these prevent has now occurred twice at two different levels: a render
receipt sealed `rendered_at`, and the master manifest sealed `earcrate_git_head`. In
both cases a change that could not touch the audio moved the identity another receipt
was citing. The first time it was excused with an addendum; this is the schema fix.

The core proof is a matrix. Changing context must move only the event identity.
Changing anything that can alter the object must move the authority identity and
break every claim resting on it.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate.album import acceptance as acc  # noqa: E402
from earcrate.evidence import manifest as ev  # noqa: E402
from earcrate.evidence.identity import ObjectIdentity, seal  # noqa: E402

PCM = "b467e224808285c6e0f6e1e90c8b8b3908322ffa6471472e8e01f1415ea0b785"
CONTAINER = "f821ce65c9e406a014f56aedd50c53d67e6539cd61af5a18d0f0bf42eea0312d"


def _authority() -> dict:
    return {
        "track_id": "A1-07",
        "descent_id": "a1-07-full-form-v1",
        "audio_affecting_tree_digest": "2" * 64,
        "renderer_identity": {"ffmpeg_version": "ffmpeg version 8.1.2"},
        "command_contract": {"stages": ["linear_gain"], "gain_db": 2.5,
                             "limiter_allowed": False, "dither_allowed": False},
        "canonical_pcm_sha256": PCM,
        "container_sha256": CONTAINER,
        "determinism": {"classification": "bit_exact_across_executions"},
    }


def _context() -> dict:
    return {"earcrate_git_head": "a" * 40, "rendered_at": "2026-08-17T20:00:00Z",
            "hostname": "BAM-Desktop", "execution_id": "run-1"}


def _manifest(**overrides) -> dict:
    authority = _authority()
    context = _context()
    authority.update(overrides.pop("authority", {}))
    context.update(overrides.pop("context", {}))
    return ev.build("earcrate_test_master_manifest", authority, context)


def _verdict(pcm: str = PCM, container: str = CONTAINER, verdict: str = acc.ACCEPT) -> dict:
    return {
        "kind": "earcrate_a1_07_master_acceptance_verdict",
        "track_id": "A1-07",
        "verdict": verdict,
        "audited": {"canonical_pcm_sha256": pcm, "container_sha256": container},
        "authority": {"human_review": True, "reopens_timing_law": False,
                      "reopens_arrangement": False, "reopens_mix": False},
    }


def _accept(manifest: dict) -> dict:
    return acc.build_receipt(track_id="A1-07", descent="a1-07-full-form-v1",
                             master_id="a1-07-master-v1", verdict=_verdict(),
                             manifest=manifest)


# --- the matrix -------------------------------------------------------------------

def test_changing_only_the_git_head_moves_the_event_and_not_the_authority():
    """The exact defect: a commit that cannot touch the audio must not invalidate it."""
    first = _manifest()
    second = _manifest(context={"earcrate_git_head": "b" * 40})

    assert first["authority_sha256"] == second["authority_sha256"]
    assert first["event_sha256"] != second["event_sha256"]

    before, after = _accept(first), _accept(second)
    assert before["bound_authority"]["authority_sha256"] == \
        after["bound_authority"]["authority_sha256"]
    assert before["state"]["accepted_album_master"] is True
    assert after["state"]["accepted_album_master"] is True
    # Only the audit context differs between the two receipts.
    assert before["provenance_context"]["event_sha256"] != \
        after["provenance_context"]["event_sha256"]


def test_changing_only_rendered_at_moves_the_event_and_not_the_authority():
    first = _manifest()
    second = _manifest(context={"rendered_at": "2026-09-01T00:00:00Z"})
    assert first["authority_sha256"] == second["authority_sha256"]
    assert first["event_sha256"] != second["event_sha256"]
    assert _accept(second)["state"]["accepted_album_master"] is True


def test_changing_the_audio_affecting_digest_moves_the_authority():
    """Qualification rests on authority, so this must break it."""
    first = _manifest()
    second = _manifest(authority={"audio_affecting_tree_digest": "9" * 64})
    assert first["authority_sha256"] != second["authority_sha256"]

    # An acceptance sealed against the old authority no longer describes this manifest.
    stale = _accept(first)
    assert stale["bound_authority"]["authority_sha256"] != second["authority_sha256"]


def test_changing_the_canonical_pcm_breaks_acceptance():
    moved = _manifest(authority={"canonical_pcm_sha256": "c" * 64})
    assert moved["authority_sha256"] != _manifest()["authority_sha256"]
    with pytest.raises(acc.AcceptanceValidationError, match="one exact object"):
        _accept(moved)


def test_changing_the_delivered_container_breaks_acceptance():
    moved = _manifest(authority={"container_sha256": "d" * 64})
    with pytest.raises(acc.AcceptanceValidationError, match="one exact object"):
        _accept(moved)


# --- schema discipline ------------------------------------------------------------

def test_context_fields_may_not_hide_inside_authority():
    """Not 'hash everything except two known volatile keys' -- classify explicitly."""
    with pytest.raises(ev.ManifestSchemaError, match="earcrate_git_head"):
        ev.build("k", {**_authority(), "earcrate_git_head": "a" * 40}, _context())
    # Including when buried several levels down.
    with pytest.raises(ev.ManifestSchemaError, match="hostname"):
        ev.build("k", {**_authority(), "env": {"deep": {"hostname": "x"}}}, _context())


def test_an_unclassified_field_is_refused_rather_than_assumed():
    """The next volatile field must fail loudly instead of silently joining authority."""
    value = _manifest()
    value["extra_field"] = {"whatever": 1}
    with pytest.raises(ev.ManifestSchemaError, match="unclassified"):
        ev.validate(value)


def test_a_tampered_authority_or_event_fails_validation():
    value = _manifest()
    tampered = deepcopy(value)
    tampered["authority"]["canonical_pcm_sha256"] = "e" * 64
    with pytest.raises(ev.ManifestSchemaError, match="authority_sha256 mismatch"):
        ev.validate(tampered)

    tampered = deepcopy(value)
    tampered["context"]["hostname"] = "another-host"
    with pytest.raises(ev.ManifestSchemaError, match="event_sha256 mismatch"):
        ev.validate(tampered)


# --- legacy handling --------------------------------------------------------------

def _legacy() -> dict:
    from earcrate.evidence.identity import seal as _seal
    return _seal({"kind": "earcrate_a1_07_master_manifest", "schema_version": 1,
                  "earcrate_git_head": "a" * 40,
                  "master": {"canonical_pcm_sha256": PCM, "container_sha256": CONTAINER}},
                 "master_manifest_sha256")


def test_a_bare_legacy_manifest_is_refused():
    """Its seal moves with the head, so it is not a durable identity."""
    with pytest.raises(ev.ManifestSchemaError, match="explicit migration receipt"):
        ev.resolve_authority(_legacy())
    with pytest.raises(acc.AcceptanceValidationError, match="explicit migration receipt"):
        acc.build_receipt(track_id="A1-07", descent="d", master_id="m",
                          verdict=_verdict(), manifest=_legacy())


def test_a_migration_receipt_resolves_the_legacy_seal_to_durable_authority():
    legacy = _legacy()
    migrated = _manifest()
    receipt = ev.migration_receipt(
        legacy, legacy_seal_field="master_manifest_sha256", migrated=migrated,
        reason="the only identity sealed execution context",
        unchanged={"canonical_pcm_sha256": PCM, "container_sha256": CONTAINER})

    assert ev.resolve_authority(legacy, migration=receipt) == migrated["authority_sha256"]

    accepted = acc.build_receipt(track_id="A1-07", descent="d", master_id="m",
                                 verdict=_verdict(), manifest=legacy, migration=receipt)
    assert accepted["bound_authority"]["authority_sha256"] == migrated["authority_sha256"]
    assert accepted["state"]["accepted_album_master"] is True

    # A migration receipt for a different manifest must not be usable here.
    other = dict(legacy)
    other["master_manifest_sha256"] = "0" * 64
    with pytest.raises(ev.ManifestSchemaError, match="different manifest"):
        ev.resolve_authority(other, migration=receipt)


def test_a_revision_verdict_never_accepts():
    manifest = _manifest()
    receipt = acc.build_receipt(track_id="A1-07", descent="d", master_id="m",
                                verdict=_verdict(verdict=acc.REVISE), manifest=manifest)
    assert receipt["verdict"] == acc.REVISE
    assert receipt["state"]["accepted_album_master"] is False
    assert receipt["state"]["accepted_album_masters"] == 0


# --- the landed A1-07 migration ---------------------------------------------------

def test_the_landed_a1_07_migration_receipt_states_what_did_not_change():
    path = ROOT / "proofs" / "album_one" / "a1-07-master-manifest-v2-migration.public.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["durable_predicate"] == "authority_sha256"
    assert value["audit_only"] == "event_sha256"
    assert value["unchanged"]["canonical_pcm_sha256"] == PCM
    assert value["unchanged"]["container_sha256"] == CONTAINER
    assert value["unchanged"]["audio_recut"] is False
    assert value["legacy"]["seal"] and value["migrated"]["authority_sha256"]
    # The historical seal is retained as evidence, not rewritten.
    assert "keeps its seal" in value["history"]
    assert seal({k: v for k, v in value.items() if k != "receipt_sha256"},
                "receipt_sha256")["receipt_sha256"] == value["receipt_sha256"]


def test_the_landed_acceptance_still_names_the_same_object():
    """Whatever the schema does, the accepted object may not move."""
    acceptance = json.loads((ROOT / "proofs" / "album_one" /
                             "a1-07-master-acceptance-v1.public.json").read_text("utf-8"))
    migration = json.loads((ROOT / "proofs" / "album_one" /
                            "a1-07-master-manifest-v2-migration.public.json").read_text("utf-8"))
    audited = ObjectIdentity(
        canonical_pcm_sha256=acceptance["audited_object"]["canonical_pcm_sha256"],
        container_sha256=acceptance["audited_object"]["container_sha256"])
    assert audited.matches(ObjectIdentity(migration["unchanged"]["canonical_pcm_sha256"],
                                          migration["unchanged"]["container_sha256"]))
