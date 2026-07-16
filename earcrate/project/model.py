from earcrate.core.deps import *
import dataclasses
from earcrate.core.util import json_dumps, sha256_text, now_utc


class ProjectError(RuntimeError):
    """Base error for the immutable creative-record layer."""


class ProjectValidationError(ProjectError):
    pass


class ProjectConcurrencyError(ProjectError):
    pass


class ProjectNotFoundError(ProjectError):
    pass


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _hash(value: Any) -> str:
    return sha256_text(json_dumps(value))


def _clip_ids(tracks: List[Dict[str, Any]]) -> List[str]:
    return [str(c.get("clip_id") or "") for t in tracks for c in (t.get("clips") or [])]


@dataclass(frozen=True)
class ScoreRevision:
    """Immutable, content-addressed L4 creative record.

    ``score_sha`` identifies the executable musical state. ``revision_sha`` also
    binds ancestry, decisions, locks and receipts, so two revisions that sound
    identical may still preserve distinct human/machine history without lying
    about their audio identity.
    """

    schema_version: int
    project_id: str
    revision_sha: str
    score_sha: str
    parent_revision_sha: Optional[str]
    created_at: str
    created_by: Dict[str, Any]
    intent: Dict[str, Any]
    arrangement: Dict[str, Any]
    source_registry: Dict[str, Dict[str, Any]]
    tracks: List[Dict[str, Any]]
    transitions: List[Dict[str, Any]]
    master_actions: List[Dict[str, Any]]
    decisions: List[Dict[str, Any]]
    locks: List[Dict[str, Any]]
    static_gate_receipt: Dict[str, Any]
    compiler_receipt: Dict[str, Any]

    def score_content(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "intent": _copy(self.intent),
            "arrangement": _copy(self.arrangement),
            "source_registry": _copy(self.source_registry),
            "tracks": _copy(self.tracks),
            "transitions": _copy(self.transitions),
            "master_actions": _copy(self.master_actions),
        }

    def revision_content(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "score_sha": self.score_sha,
            "parent_revision_sha": self.parent_revision_sha,
            "created_by": _copy(self.created_by),
            "decisions": _copy(self.decisions),
            "locks": _copy(self.locks),
            "static_gate_receipt": _copy(self.static_gate_receipt),
            "compiler_receipt": _copy(self.compiler_receipt),
        }

    def computed_score_sha(self) -> str:
        return _hash(self.score_content())

    def computed_revision_sha(self) -> str:
        return _hash(self.revision_content())

    def validate(self) -> None:
        if int(self.schema_version) != 1:
            raise ProjectValidationError(f"unsupported score schema {self.schema_version}")
        if not self.project_id:
            raise ProjectValidationError("project_id is required")
        if self.score_sha != self.computed_score_sha():
            raise ProjectValidationError("score_sha does not match executable score content")
        if self.revision_sha != self.computed_revision_sha():
            raise ProjectValidationError("revision_sha does not match immutable revision content")
        sections = list(self.arrangement.get("sections") or [])
        if not sections:
            raise ProjectValidationError("score has no arrangement sections")
        clips = _clip_ids(self.tracks)
        if any(not c for c in clips):
            raise ProjectValidationError("every clip requires a stable clip_id")
        if len(clips) != len(set(clips)):
            raise ProjectValidationError("duplicate clip_id in score")
        arrangement_clips = [
            str(layer.get("clip_id") or "")
            for sec in sections
            for layer in (sec.get("layers") or [])
        ]
        if sorted(arrangement_clips) != sorted(clips):
            raise ProjectValidationError(
                "canonical tracks do not match the executable arrangement layers"
            )
        known_sources = set(self.source_registry)
        for track in self.tracks:
            if not track.get("track_id") or not track.get("role"):
                raise ProjectValidationError("every track requires track_id and role")
            for clip in track.get("clips") or []:
                if str(clip.get("source_ref_id") or "") not in known_sources:
                    raise ProjectValidationError(
                        f"clip {clip.get('clip_id')} references unknown source"
                    )
                if float(clip.get("duration_beats") or 0.0) <= 0:
                    raise ProjectValidationError(
                        f"clip {clip.get('clip_id')} has nonpositive duration"
                    )
        known_clips = set(clips)
        for transition in self.transitions:
            if not transition.get("transition_id"):
                raise ProjectValidationError("every transition requires transition_id")
            refs = list(transition.get("outgoing_clip_ids") or []) + list(
                transition.get("incoming_clip_ids") or []
            )
            missing = sorted(set(str(x) for x in refs) - known_clips)
            if missing:
                raise ProjectValidationError(
                    f"transition {transition.get('transition_id')} references unknown clips: {missing}"
                )
        profile = self.intent.get("taste_profile") or {}
        if not all(profile.get(k) for k in ("id", "version", "hash", "compiled_policy_sha")):
            raise ProjectValidationError("revision lacks a complete TasteSpec identity")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "revision_sha": self.revision_sha,
            "score_sha": self.score_sha,
            "parent_revision_sha": self.parent_revision_sha,
            "created_at": self.created_at,
            "created_by": _copy(self.created_by),
            "intent": _copy(self.intent),
            "arrangement": _copy(self.arrangement),
            "source_registry": _copy(self.source_registry),
            "tracks": _copy(self.tracks),
            "transitions": _copy(self.transitions),
            "master_actions": _copy(self.master_actions),
            "decisions": _copy(self.decisions),
            "locks": _copy(self.locks),
            "static_gate_receipt": _copy(self.static_gate_receipt),
            "compiler_receipt": _copy(self.compiler_receipt),
        }

    @classmethod
    def build(
        cls,
        *,
        project_id: str,
        parent_revision_sha: Optional[str],
        created_by: Dict[str, Any],
        intent: Dict[str, Any],
        arrangement: Dict[str, Any],
        source_registry: Dict[str, Dict[str, Any]],
        tracks: List[Dict[str, Any]],
        transitions: List[Dict[str, Any]],
        master_actions: Optional[List[Dict[str, Any]]] = None,
        decisions: Optional[List[Dict[str, Any]]] = None,
        locks: Optional[List[Dict[str, Any]]] = None,
        static_gate_receipt: Optional[Dict[str, Any]] = None,
        compiler_receipt: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> "ScoreRevision":
        provisional = cls(
            schema_version=1,
            project_id=str(project_id),
            revision_sha="",
            score_sha="",
            parent_revision_sha=str(parent_revision_sha) if parent_revision_sha else None,
            created_at=str(created_at or now_utc()),
            created_by=_copy(created_by),
            intent=_copy(intent),
            arrangement=_copy(arrangement),
            source_registry=_copy(source_registry),
            tracks=_copy(tracks),
            transitions=_copy(transitions),
            master_actions=_copy(master_actions or []),
            decisions=_copy(decisions or []),
            locks=_copy(locks or []),
            static_gate_receipt=_copy(static_gate_receipt or {}),
            compiler_receipt=_copy(compiler_receipt or {}),
        )
        score_sha = provisional.computed_score_sha()
        with_score = dataclasses.replace(provisional, score_sha=score_sha)
        revision_sha = with_score.computed_revision_sha()
        complete = dataclasses.replace(with_score, revision_sha=revision_sha)
        complete.validate()
        return complete

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ScoreRevision":
        revision = cls(
            schema_version=int(value.get("schema_version") or 0),
            project_id=str(value.get("project_id") or ""),
            revision_sha=str(value.get("revision_sha") or ""),
            score_sha=str(value.get("score_sha") or ""),
            parent_revision_sha=(
                str(value.get("parent_revision_sha"))
                if value.get("parent_revision_sha")
                else None
            ),
            created_at=str(value.get("created_at") or ""),
            created_by=_copy(value.get("created_by") or {}),
            intent=_copy(value.get("intent") or {}),
            arrangement=_copy(value.get("arrangement") or {}),
            source_registry=_copy(value.get("source_registry") or {}),
            tracks=_copy(value.get("tracks") or []),
            transitions=_copy(value.get("transitions") or []),
            master_actions=_copy(value.get("master_actions") or []),
            decisions=_copy(value.get("decisions") or []),
            locks=_copy(value.get("locks") or []),
            static_gate_receipt=_copy(value.get("static_gate_receipt") or {}),
            compiler_receipt=_copy(value.get("compiler_receipt") or {}),
        )
        revision.validate()
        return revision


@dataclass(frozen=True)
class ProjectRecord:
    schema_version: int
    project_id: str
    name: str
    active_revision_sha: str
    revision_history: List[str]
    redo_stack: List[str]
    created_at: str
    updated_at: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "name": self.name,
            "active_revision_sha": self.active_revision_sha,
            "revision_history": list(self.revision_history),
            "redo_stack": list(self.redo_stack),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": _copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ProjectRecord":
        return cls(
            schema_version=int(value.get("schema_version") or 0),
            project_id=str(value.get("project_id") or ""),
            name=str(value.get("name") or ""),
            active_revision_sha=str(value.get("active_revision_sha") or ""),
            revision_history=[str(x) for x in (value.get("revision_history") or [])],
            redo_stack=[str(x) for x in (value.get("redo_stack") or [])],
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            metadata=_copy(value.get("metadata") or {}),
        )
