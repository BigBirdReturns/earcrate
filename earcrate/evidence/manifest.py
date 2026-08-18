"""Two identities, because a manifest answers two different questions.

**Authority** is what was rendered, from which stable inputs and code identities.
**Context** is when, where, and at which repository head the event happened.

Collapsing them into one seal is a defect with a track record here. The A1-07 master
manifest embedded `earcrate_git_head`, so its seal moved on every commit — and the
acceptance receipt cited that seal. A changelog line could therefore invalidate the
pointer that says which object the owner accepted. The render receipts had already
taught the same lesson through `rendered_at`, and it was recorded as a private
addendum rather than fixed, so it recurred one level up.

The fix is not "hash everything except the volatile keys we know about". That fails
the next time somebody adds a hostname. Fields are **classified explicitly**:

* authority is a declared block; nothing volatile may appear anywhere inside it;
* context is a declared block;
* an unrecognized top-level key is refused rather than silently assigned.

```text
authority_sha256   canonical digest of the stable authority block alone
event_sha256       canonical digest of authority_sha256 plus the context block
```

A later commit, checkout, timestamp, hostname or execution id produces a different
event identity and leaves the authority identity untouched. That is the whole point:
acceptance binds authority, and audit keeps the event.

One subtlety worth stating, because it is easy to get backwards. Some environment
facts belong in *authority*, not context: the FFmpeg build identity or a model
checkpoint identity can change the output, so they are stable requirements. The
filesystem path to that binary cannot change the output, so it is context.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .identity import canonical_json_bytes, seal, sha256_bytes, validate_seal

SCHEMA_VERSION = 2

TOP_LEVEL_KEYS = frozenset({
    "kind", "schema_version", "authority", "context", "authority_sha256", "event_sha256",
    "migrated_from",
})

# Names that describe *when, where or by whom* something ran. None of them may appear
# anywhere inside an authority block, at any depth. This is a deny-list applied to a
# declared structure, not a substitute for the structure.
CONTEXT_FIELD_NAMES = frozenset({
    "earcrate_git_head", "git_head", "head", "rendered_at", "sealed_at", "created_at",
    "timestamp", "wall_time", "hostname", "host", "username", "user", "session_path",
    "execution_id", "execution_uuid", "run_id", "temp_dir", "tmp_path", "receipt_filename",
    "artifact_path", "workspace", "output_path", "cwd", "pid",
})

# The classification, stated positively so a reader can see what "stable" means here.
AUTHORITY_ADMITS = (
    "track and commission identity",
    "source and binding identities",
    "score or arrangement identity",
    "audio-affecting code or tree identity",
    "declared processing chain and effective parameters",
    "canonical PCM identity",
    "delivered container identity where delivery identity matters",
    "duration and format",
    "signal constraints and the measured result",
    "determinism classification",
    "tool identities that can change the output, such as an encoder build or a model "
    "checkpoint -- but never the path to them",
)

CONTEXT_ADMITS = (
    "repository head, as observation rather than predicate",
    "rendered_at and other wall-clock readings",
    "hostname, username, session path, execution id",
    "temporary paths and receipt filenames",
)


class ManifestSchemaError(RuntimeError):
    pass


def _walk_keys(node: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield str(key), f"{path}/{key}"
            yield from _walk_keys(value, f"{path}/{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _walk_keys(value, f"{path}[{index}]")


def classify(authority: Mapping[str, Any]) -> list[str]:
    """Findings: which volatile fields are hiding inside a stable payload."""
    return [f"{name!r} is execution context but appears in authority at {where}"
            for name, where in _walk_keys(authority)
            if name in CONTEXT_FIELD_NAMES]


def authority_digest(authority: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(dict(authority)))


def event_digest(authority_sha256: str, context: Mapping[str, Any]) -> str:
    """The event is the authority *plus* where and when it happened."""
    return sha256_bytes(canonical_json_bytes(
        {"authority_sha256": authority_sha256, "context": dict(context)}))


def build(kind: str, authority: Mapping[str, Any], context: Mapping[str, Any],
          *, migrated_from: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Assemble a v2 manifest with both identities computed, never copied."""
    problems = classify(authority)
    if problems:
        raise ManifestSchemaError("; ".join(problems))
    value: dict[str, Any] = {
        "kind": kind,
        "schema_version": SCHEMA_VERSION,
        "authority": dict(authority),
        "context": dict(context),
    }
    if migrated_from is not None:
        value["migrated_from"] = dict(migrated_from)
    value["authority_sha256"] = authority_digest(value["authority"])
    value["event_sha256"] = event_digest(value["authority_sha256"], value["context"])
    return value


def validate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse anything that is not exactly this schema, including new keys."""
    if int(value.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ManifestSchemaError(
            f"schema_version is {value.get('schema_version')!r}, expected {SCHEMA_VERSION}")
    unknown = set(value) - TOP_LEVEL_KEYS
    if unknown:
        raise ManifestSchemaError(
            f"unclassified top-level field(s): {sorted(unknown)}. Every field is either "
            "stable authority or execution context; add it to one of them deliberately.")
    for required in ("authority", "context", "authority_sha256", "event_sha256"):
        if required not in value:
            raise ManifestSchemaError(f"missing {required}")

    problems = classify(value["authority"])
    if problems:
        raise ManifestSchemaError("; ".join(problems))

    observed = authority_digest(value["authority"])
    if observed != value["authority_sha256"]:
        raise ManifestSchemaError(
            f"authority_sha256 mismatch: declared {value['authority_sha256']}, "
            f"observed {observed}")
    expected_event = event_digest(value["authority_sha256"], value["context"])
    if expected_event != value["event_sha256"]:
        raise ManifestSchemaError(
            f"event_sha256 mismatch: declared {value['event_sha256']}, "
            f"observed {expected_event}")
    return dict(value)


def migration_receipt(legacy: Mapping[str, Any], *, legacy_seal_field: str,
                      migrated: Mapping[str, Any], reason: str,
                      unchanged: Mapping[str, Any]) -> dict[str, Any]:
    """State the mapping from a legacy sealed manifest to its durable authority.

    The historical manifest is not edited. Its seal stays valid evidence of the
    original event; this receipt is what makes the durable identity resolvable from
    it, so a public receipt citing the legacy seal does not become a dangling pointer.
    """
    validate_seal(legacy, legacy_seal_field)
    validate(migrated)
    return seal({
        "kind": "earcrate_manifest_schema_migration_receipt",
        "schema_version": 1,
        "legacy": {
            "seal_field": legacy_seal_field,
            "seal": legacy[legacy_seal_field],
            "schema_version": legacy.get("schema_version"),
            "kind": legacy.get("kind"),
        },
        "migrated": {
            "kind": migrated["kind"],
            "schema_version": migrated["schema_version"],
            "authority_sha256": migrated["authority_sha256"],
            "event_sha256": migrated["event_sha256"],
        },
        "unchanged": dict(unchanged),
        "reason": reason,
        "authority_note": (
            "The legacy seal remains valid evidence of the original event. The durable "
            "predicate for qualification and acceptance is authority_sha256."),
    }, "receipt_sha256")


def resolve_authority(value: Mapping[str, Any], *,
                      migration: Mapping[str, Any] | None = None) -> str:
    """The durable identity, from a v2 manifest or a legacy one plus its migration.

    A bare legacy manifest is refused. Its seal is unstable by construction, so
    accepting it directly would let a commit invalidate an acceptance -- and inferring
    the mapping silently is exactly how the original defect survived this long.
    """
    if int(value.get("schema_version") or 0) == SCHEMA_VERSION:
        return validate(value)["authority_sha256"]

    if migration is None:
        raise ManifestSchemaError(
            f"legacy manifest (schema_version={value.get('schema_version')!r}) needs an "
            "explicit migration receipt; its own seal includes execution context and is "
            "not a durable identity")
    validate_seal(migration, "receipt_sha256")
    if migration.get("kind") != "earcrate_manifest_schema_migration_receipt":
        raise ManifestSchemaError(f"not a migration receipt: {migration.get('kind')}")

    legacy = migration.get("legacy") or {}
    field = str(legacy.get("seal_field") or "")
    if not field or value.get(field) != legacy.get("seal"):
        raise ManifestSchemaError(
            "the migration receipt describes a different manifest than the one supplied")
    return str((migration.get("migrated") or {})["authority_sha256"])
