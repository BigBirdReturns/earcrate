from __future__ import annotations

"""Complete named catalog of every swept commodity and interoperability target."""

from collections import Counter
from typing import Any, Mapping, Sequence

from earcrate.estate.homelab_common import (
    AUDIO_STAGES,
    CORE_STAGES,
    HOST_STAGES,
    LIBRARY_STAGES,
    OBSERVATION_STAGES,
    RESEARCH_STAGES,
    SERVICE_STAGES,
    STANDARD_STAGES,
    HOMELAB_SCHEMA_VERSION,
    _sha_json,
    homelab_seal,
)


def _req(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "python_any": [], "python_all": [], "executables_any": [],
        "executables_all": [], "asset_tokens": [], "fixture_ids": [],
        "credentials_all": [], "gpu": "none", "audio_device": False,
        "network": "none", "manual_probe": False,
    }
    value.update(overrides)
    return value


def _target(
    target_id: str,
    name: str,
    target_class: str,
    intent: str,
    capabilities: Sequence[str],
    stages: Sequence[str],
    *,
    state: str,
    license_posture: str,
    source_refs: Sequence[str],
    requirements: Mapping[str, Any] | None = None,
    audition_profile: str = "none",
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    value = {
        "target_id": target_id,
        "display_name": name,
        "target_class": target_class,
        "adoption_intent": intent,
        "capabilities": list(capabilities),
        "required_stages": list(stages),
        "current_state": state,
        "license_posture": license_posture,
        "source_refs": list(source_refs),
        "requirements": dict(requirements or _req()),
        "audition_profile": audition_profile,
        "audition_required": any("audition" in stage for stage in stages),
        "notes": list(notes),
    }
    value["target_manifest_sha256"] = _sha_json(value)
    return value


def _caps(text: str) -> list[str]:
    return [part for part in text.split() if part]


def _core(target_id: str, name: str, caps: str, *, packages: Sequence[str] = (), executables: Sequence[str] = (), fixtures: Sequence[str] = ("fixture.synthetic.regression", "fixture.private_library.real"), audition: str = "regression") -> dict[str, Any]:
    return _target(target_id, name, "adopted_core", "retain_or_replace", _caps(caps), CORE_STAGES,
        state="adopted_needs_real_node_receipt", license_posture="current_core_dependency",
        source_refs=("requirements.txt",), requirements=_req(python_any=list(packages), executables_all=list(executables), fixture_ids=list(fixtures)), audition_profile=audition)


def _library(target_id: str, name: str, caps: str, *, packages_any: Sequence[str] = (), packages_all: Sequence[str] = (), executables: Sequence[str] = (), gpu: str = "none", manual: bool = False, source: str = "docs/OSS_INTEGRATION_AUDIT.md", license_posture: str = "dependency_or_provider_review") -> dict[str, Any]:
    return _target(target_id, name, "native_library", "retain_or_replace", _caps(caps), LIBRARY_STAGES,
        state="surveyed_not_locally_accepted", license_posture=license_posture, source_refs=(source,),
        requirements=_req(python_any=list(packages_any), python_all=list(packages_all), executables_all=list(executables), fixture_ids=["fixture.synthetic.regression", "fixture.local_project.real"], gpu=gpu, manual_probe=manual), audition_profile="real_fixture_and_benchmark")


def _provider(target_id: str, name: str, caps: str, *, packages: Sequence[str] = (), executables: Sequence[str] = (), assets: Sequence[str] = (), fixtures: Sequence[str] = ("fixture.pretty_lights.source_audio", "fixture.private_library.real"), gpu: str = "preferred", audio: bool = False, manual: bool = False, license_posture: str = "owner_review_required", sources: Sequence[str] = ("project-session:oss-community-commodity-sweep",)) -> dict[str, Any]:
    return _target(target_id, name, "oss_provider", "candidate_provider", _caps(caps), AUDIO_STAGES if audio else OBSERVATION_STAGES,
        state="surveyed_not_loaded", license_posture=license_posture, source_refs=sources,
        requirements=_req(python_any=list(packages), executables_all=list(executables), asset_tokens=list(assets), fixture_ids=list(fixtures), gpu=gpu, manual_probe=manual), audition_profile="blind_audio" if audio else "downstream_musical")


def _service(target_id: str, name: str, caps: str, *, credentials: Sequence[str] = (), license_posture: str = "service_terms_required", sources: Sequence[str] = ("docs/OSS_INTEGRATION_AUDIT.md",)) -> dict[str, Any]:
    return _target(target_id, name, "external_service", "external_service_candidate", _caps(caps), SERVICE_STAGES,
        state="surveyed_not_authenticated", license_posture=license_posture, source_refs=sources,
        requirements=_req(fixture_ids=["fixture.private_library.real"], credentials_all=list(credentials), network="runtime"), audition_profile="downstream_musical")


def _host(target_id: str, name: str, caps: str, *, executable: str | None = None, license_posture: str = "provider_boundary") -> dict[str, Any]:
    return _target(target_id, name, "external_host", "external_host_candidate", _caps(caps), HOST_STAGES,
        state="architecture_reference_not_auditioned", license_posture=license_posture, source_refs=("docs/DJ_ENGINE_OSS_SWEEP.md",),
        requirements=_req(executables_any=[executable] if executable else [], fixture_ids=["fixture.pretty_lights.source_audio", "fixture.audio_device.physical"], audio_device=True, manual_probe=not bool(executable)), audition_profile="workflow_and_audio")


def _research(target_id: str, name: str, caps: str) -> dict[str, Any]:
    return _target(target_id, name, "research_system", "architecture_reference", _caps(caps), RESEARCH_STAGES,
        state="reference_only_not_executed", license_posture="source_and_license_review", source_refs=("project-session:oss-community-commodity-sweep",),
        requirements=_req(fixture_ids=["fixture.pretty_lights.source_audio", "fixture.private_library.real"], manual_probe=True), audition_profile="research_workflow")


def _commercial(target_id: str, name: str, caps: str) -> dict[str, Any]:
    return _target(target_id, name, "commercial_comparator", "workflow_comparator", _caps(caps), HOST_STAGES,
        state="workflow_reference_not_auditioned", license_posture="commercial_terms_and_manual_workflow", source_refs=("project-session:oss-community-commodity-sweep",),
        requirements=_req(fixture_ids=["fixture.pretty_lights.source_audio", "fixture.audio_device.physical"], audio_device=True, manual_probe=True), audition_profile="workflow_and_audio")


def _standard(target_id: str, name: str, caps: str) -> dict[str, Any]:
    return _target(target_id, name, "interoperability_target", "interoperability_target", _caps(caps), STANDARD_STAGES,
        state="mapping_only_not_roundtrip_proven", license_posture="standard_or_specification", source_refs=("docs/OPEN_MUSIC_EVIDENCE_FLOOR.md",),
        requirements=_req(fixture_ids=["fixture.synthetic.regression", "fixture.local_project.real"], manual_probe=True))


def _catalog_targets() -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = [
        _core("ffmpeg", "FFmpeg / ffprobe", "decode encode media_probe", executables=("ffmpeg", "ffprobe")),
        _core("numpy", "NumPy", "array_runtime float_pcm", packages=("numpy",)),
        _core("scipy", "SciPy", "polyphase_resample signal_processing", packages=("scipy",)),
        _core("soundfile", "SoundFile / libsndfile", "lossless_audio_io", packages=("soundfile",)),
        _core("librosa", "librosa", "chroma mel onsets baseline_beats", packages=("librosa",)),
        _core("mido", "Mido", "midi_codec", packages=("mido",), audition="symbolic_roundtrip"),
        _core("sounddevice-portaudio", "python-sounddevice + PortAudio", "physical_audio_host callback_io", packages=("sounddevice",), fixtures=("fixture.audio_device.physical",), audition="physical_device"),
        _core("pyloudnorm", "pyloudnorm", "integrated_loudness", packages=("pyloudnorm",)),
        _library("mutagen", "Mutagen", "audio_metadata tag_reading", packages_any=("mutagen",)),
        _library("sqlite", "SQLite", "local_authority_store", manual=True, source="docs/LOCAL_ESTATE_ARCHITECTURE.md"),
        _library("torch-torchaudio", "PyTorch + torchaudio", "gpu_model_runtime", packages_all=("torch", "torchaudio"), gpu="preferred", source="requirements.txt"),
        _library("mir-eval", "mir_eval", "standard_mir_evaluation", packages_any=("mir-eval", "mir_eval")),
        _library("ortools", "Google OR-Tools", "bounded_constraint_search", packages_any=("ortools",), source="third_party/components.lock.json"),
        _library("z3-solver", "Z3 Solver", "hard_invariant_satisfiability", packages_any=("z3-solver",), source="third_party/components.lock.json"),
        _library("symusic", "Symusic", "fast_symbolic_materialization soundfont_render", packages_any=("symusic",), source="third_party/components.lock.json"),
        _library("liquidsfz", "LiquidSFZ", "sfz_playback", executables=("liquidsfz",), source="third_party/components.lock.json", license_posture="MPL-2.0_reviewed_weak_copyleft"),
        _library("libsamplerate", "libsamplerate", "arbitrary_resample varispeed", packages_any=("samplerate",), source="docs/DJ_ENGINE_OSS_SWEEP.md"),
        _library("miniaudio", "miniaudio", "native_device_host mixer decode", packages_any=("miniaudio",), source="docs/DJ_ENGINE_OSS_SWEEP.md"),
        _provider("allin1", "All-In-One Music Structure Analyzer", "beats downbeats tempo key functional_sections", packages=("allin1",), fixtures=("fixture.pretty_lights.source_audio", "fixture.flim.target_recording", "fixture.private_library.real"), sources=("docs/OSS_INTEGRATION_AUDIT.md",)),
        _provider("madmom", "madmom", "beats downbeats chords key", packages=("madmom",), sources=("docs/OSS_INTEGRATION_AUDIT.md",)),
        _provider("beatnet", "BeatNet", "realtime_beats downbeats", packages=("beatnet", "BeatNet"), sources=("docs/OSS_INTEGRATION_AUDIT.md",)),
        _provider("beat-this", "Beat This", "beats downbeats", packages=("beat-this", "beat_this"), sources=("third_party/components.lock.json",)),
        _provider("sf-segmenter", "sf_segmenter", "structural_segmentation", packages=("sf-segmenter", "sf_segmenter")),
        _provider("crepe", "CREPE", "monophonic_pitch key_support", packages=("crepe",)),
        _provider("chordino-nnls-chroma", "Chordino / NNLS-Chroma", "time_varying_chords harmony", executables=("sonic-annotator",), assets=("chordino", "nnls-chroma"), sources=("docs/OSS_INTEGRATION_AUDIT.md",)),
        _provider("msaf", "MSAF", "music_structure_analysis", packages=("msaf",), sources=("docs/OSS_INTEGRATION_AUDIT.md",)),
        _provider("basic-pitch", "Spotify Basic Pitch", "notes onsets pitch_bends midi_hypothesis", packages=("basic-pitch",), fixtures=("fixture.pretty_lights.source_audio", "fixture.flim.target_recording", "fixture.children.target_recording"), sources=("requirements.txt", "third_party/components.lock.json", "external:flim-community-symbolic-report")),
        _provider("music2midi", "Music2MIDI", "multi_instrument_transcription midi_hypothesis", assets=("music2midi",), fixtures=("fixture.pretty_lights.source_audio", "fixture.flim.target_recording"), manual=True, sources=("project-session:transcription-sweep", "external:flim-community-symbolic-report")),
        _provider("pop2piano", "Pop2Piano", "piano_reduction midi_hypothesis", assets=("pop2piano",), fixtures=("fixture.flim.target_recording", "fixture.children.target_recording"), manual=True, sources=("project-session:transcription-sweep", "external:flim-community-symbolic-report")),
        _provider("picogen2", "PiCoGen2", "piano_symbolic_generation transcription_candidate", assets=("picogen2",), fixtures=("fixture.flim.target_recording",), manual=True, sources=("project-session:transcription-sweep", "external:flim-community-symbolic-report")),
        _provider("demucs", "Demucs", "stem_separation source_roles", packages=("demucs",), assets=("demucs", "htdemucs"), audio=True, sources=("requirements.txt", "docs/DJ_ENGINE_OSS_SWEEP.md")),
        _provider("uvr", "Ultimate Vocal Remover model families", "stem_separation model_tournament", assets=("uvr", "mdx", "vr-architecture"), audio=True, manual=True),
        _provider("demucs-rs", "demucs-rs", "native_stem_separation", executables=("demucs-rs",), assets=("demucs-rs",), audio=True, manual=True),
        _provider("mert", "MERT", "music_embeddings retrieval", packages=("transformers",), assets=("mert",)),
        _provider("laion-clap", "LAION-CLAP", "audio_text_embeddings retrieval", packages=("laion-clap", "msclap"), assets=("clap",)),
        _provider("muq", "MuQ", "music_embeddings retrieval", packages=("muq",), assets=("muq",)),
        _provider("panako", "Panako", "transformed_audio_fingerprinting", executables=("panako",), gpu="none", license_posture="AGPL_and_patent_review_isolated_only"),
        _provider("chromaprint", "Chromaprint / fpcalc", "recording_fingerprint", executables=("fpcalc",), gpu="none", fixtures=("fixture.private_library.real",), sources=("docs/OSS_INTEGRATION_AUDIT.md",)),
        _provider("signalsmith-stretch", "Signalsmith Stretch", "time_stretch pitch_shift key_lock", executables=("signalsmith-stretch",), assets=("signalsmith",), gpu="none", audio=True, manual=True, sources=("third_party/components.lock.json", "docs/DJ_ENGINE_OSS_SWEEP.md")),
        _provider("rubberband", "Rubber Band / pyrubberband", "time_stretch pitch_shift key_lock", packages=("pyrubberband",), executables=("rubberband",), gpu="none", audio=True, license_posture="GPL_or_commercial_provider_boundary", sources=("docs/OSS_INTEGRATION_AUDIT.md", "docs/DJ_ENGINE_OSS_SWEEP.md")),
        _provider("aubio", "aubio", "onsets pitch tempo features", packages=("aubio",), executables=("aubio",), gpu="none", license_posture="GPLv3_research_provider", sources=("docs/DJ_ENGINE_OSS_SWEEP.md",)),
        _provider("essentia", "Essentia", "key hpcp danceability mir_features", packages=("essentia",), gpu="none", license_posture="AGPL_or_commercial_isolated_provider", sources=("docs/OSS_INTEGRATION_AUDIT.md", "docs/DJ_ENGINE_OSS_SWEEP.md")),
        _provider("pedalboard", "Spotify Pedalboard", "effects plugin_hosting", packages=("pedalboard",), gpu="none", audio=True, license_posture="GPLv3_studio_optional", sources=("docs/DJ_ENGINE_OSS_SWEEP.md",)),
        _service("acoustid", "AcoustID", "fingerprint_lookup recording_identity", credentials=("ACOUSTID_API_KEY",)),
        _service("musicbrainz", "MusicBrainz", "recording_metadata relationships"),
        _service("discogs", "Discogs", "release_metadata catalog_context", credentials=("DISCOGS_TOKEN",)),
        _service("freesound", "Freesound API", "lawful_material descriptor_search similarity", credentials=("FREESOUND_API_KEY",), sources=("project-session:oss-community-commodity-sweep",)),
        _service("tracklib", "Tracklib", "cleared_sample_catalog licensing_workflow", license_posture="commercial_catalog_terms", sources=("project-session:oss-community-commodity-sweep",)),
        _service("selekt", "Selekt", "cleared_material stems midi provenance", license_posture="commercial_catalog_claims_require_independent_audit", sources=("project-session:oss-community-commodity-sweep",)),
        _host("mixxx", "Mixxx", "decks controllers loops key_lock stems", executable="mixxx", license_posture="GPLv2_external_host_only"),
        _host("ableton-link", "Ableton Link", "tempo_phase_sync network_transport_sync", license_posture="GPL_or_proprietary_external_sync_seam"),
        *[_research(*row) for row in [
            ("polymath", "Polymath", "library_to_production_crate stems alignment similarity"),
            ("nendo", "Nendo", "plugin_music_data_framework crate_schema"),
            ("catart", "CataRT", "corpus_concatenative_synthesis descriptor_space"),
            ("skatart", "SKataRT", "corpus_concatenative_performance"),
            ("audiostellar", "AudioStellar", "visual_corpus_exploration similarity_browsing"),
            ("acorex", "ACorEx", "corpus_exploration concatenative_audio"),
            ("automashupper", "AutoMashUpper", "phrase_mashability transform_aware_compatibility"),
            ("automatic-dj-2018", "From raw audio to a seamless mix (2018)", "automatic_dj_pipeline cue_selection global_progression"),
            ("tomi", "TOMI", "structured_clip_composition daw_lowering"),
        ]],
        *[_commercial(*row) for row in [
            ("whosampled", "WhoSampled", "sample_relationship_reference"),
            ("sononym", "Sononym", "sample_similarity_browser"),
            ("fadr", "Fadr", "stem_remix midi_detection two_turntables"),
            ("traktor", "Traktor Pro", "four_decks stems key_lock controllers"),
            ("djstudio", "DJ.Studio", "stem_automation timeline_dj_editing"),
            ("rekordbox", "rekordbox", "dj_library stems performance"),
            ("djay", "djay", "consumer_pro_dj stem_transport"),
        ]],
        *[_standard(*row) for row in [
            ("jams", "JAMS", "annotation_interchange"),
            ("vamp", "Vamp plugin ecosystem", "feature_plugin_adapter"),
            ("musicxml", "MusicXML", "score_interchange"),
            ("mnx", "MNX", "score_interchange"),
            ("midi2-midici", "MIDI 2.0 / MIDI-CI", "performance_and_device_interchange"),
            ("dawproject", "DAWproject", "daw_project_interchange"),
            ("opentimelineio", "OpenTimelineIO", "timeline_interchange"),
            ("clap", "CLAP", "native_dsp_plugin_hosting"),
            ("onnx", "ONNX", "portable_model_execution"),
            ("oci", "OCI", "provider_packaging"),
            ("sigstore", "Sigstore", "artifact_signatures"),
            ("slsa", "SLSA", "supply_chain_provenance"),
            ("ro-crate", "RO-Crate", "portable_research_objects"),
            ("w3c-prov", "W3C PROV", "derivation_graph"),
            ("spdx", "SPDX", "license_expression"),
            ("odrl", "ODRL", "rights_policy_mapping"),
            ("ddex", "DDEX", "music_metadata_interchange"),
            ("c2pa", "C2PA", "content_provenance"),
            ("mirdata", "mirdata", "benchmark_fixture_bridge"),
            ("mirex", "MIREX", "evaluation_campaign_bridge"),
        ]],
    ]
    return targets


def homelab_catalog() -> dict[str, Any]:
    fixtures = [
        {"fixture_id": "fixture.synthetic.regression", "evidence_tier": "synthetic", "expected_sha256": None, "availability_rule": "always_generated_by_tests"},
        {"fixture_id": "fixture.children.score_pdf", "evidence_tier": "authoritative_score", "expected_sha256": "e029e1a3030800d7fb04c9f5163acb9270579a571d57f63eb63787df692d5845", "availability_rule": "exact_hash_required"},
        {"fixture_id": "fixture.children.target_recording", "evidence_tier": "blind_audio_inference", "expected_sha256": None, "availability_rule": "user_supplied_exact_recording"},
        {"fixture_id": "fixture.flim.community_pack", "evidence_tier": "community_symbolic_witness", "expected_sha256": "a7dabd71af884a4933b7e3c8077bc9d5e7b2e69de3fa9d370fd8b592d09cdf52", "availability_rule": "exact_hash_required"},
        {"fixture_id": "fixture.flim.target_recording", "evidence_tier": "blind_audio_inference", "expected_sha256": None, "availability_rule": "user_supplied_exact_recording", "note": "The community-symbolic proof explicitly withheld this recording from Pop2Piano, Music2MIDI, PiCoGen2, Basic Pitch, and the cephalopod."},
        {"fixture_id": "fixture.pretty_lights.source_audio", "evidence_tier": "blind_audio_inference", "expected_sha256": "af3116da67067e2ce2d8f1635471388c371641f63687917948e154c289cef979", "availability_rule": "exact_container_or_decoded_pcm_identity_required"},
        {"fixture_id": "fixture.pretty_lights.release_candidate_main_v1", "evidence_tier": "performance_realization", "expected_sha256": "97bd2d4c3e7a38097956e2000db475e714c7fad67b51782b2905aeecfd8d0f9e", "decoded_pcm_sha256": "5da1bef8526576ca49628de636337e8fe9e100b4e0da7ada0605d164a4298e59", "availability_rule": "external_pack_or_pcm_identity_required"},
        {"fixture_id": "fixture.private_library.real", "evidence_tier": "approved_private_library", "expected_sha256": None, "availability_rule": "inventory_contains_source_audio_and_workspace_policy"},
        {"fixture_id": "fixture.local_project.real", "evidence_tier": "accepted_project_revision", "expected_sha256": None, "availability_rule": "inventory_contains_project_index_and_revision"},
        {"fixture_id": "fixture.audio_device.physical", "evidence_tier": "physical_device", "expected_sha256": None, "availability_rule": "node_receipt_contains_output_device"},
    ]
    targets = _catalog_targets()
    ids = [row["target_id"] for row in targets]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate homelab target ids")
    if len(targets) != 87:
        raise ValueError(f"catalog completeness regression: expected 87 targets, found {len(targets)}")
    if any(not row["required_stages"][-1].endswith("decision") for row in targets):
        raise ValueError("every homelab target requires a terminal decision stage")
    return homelab_seal({
        "schema_version": HOMELAB_SCHEMA_VERSION,
        "kind": "earcrate_homelab_catalog",
        "name": "EarCrate Homelab Provider Arcade v1",
        "cataloged_at": "2026-07-31",
        "mame_mapping": {
            "driver": "target manifest",
            "rom_audit": "package, executable, model, credential, fixture, and license feasibility audit",
            "machine_configuration": "content-addressed homelab node receipt",
            "software_list": "sealed source, score, project, library, and device fixtures",
            "working_imperfect_not_working": "stage receipts, blockers, and terminal disposition",
            "player_experience": "blind or downstream human audition ledger",
        },
        "source_sweeps": [
            {"source": "docs/OSS_INTEGRATION_AUDIT.md", "scope": "MIR, transforms, metadata, identification, evaluation, and data sources"},
            {"source": "docs/DJ_ENGINE_OSS_SWEEP.md", "scope": "transport, stretch, device hosts, synchronization, and licensing"},
            {"source": "docs/OPEN_MUSIC_EVIDENCE_FLOOR.md", "scope": "providers, standards, supply chain, rights, and evaluation"},
            {"source": "third_party/components.lock.json", "scope": "evaluated components"},
            {"source": "project-session:transcription-sweep", "scope": "Pop2Piano, Music2MIDI, PiCoGen2, and transcription candidates"},
            {"source": "project-session:oss-community-commodity-sweep", "scope": "crate, corpus, mashup, DJ, commercial, and research antecedents"},
            {"source": "external:flim-community-symbolic-report", "scope": "recording-withholding boundary"},
        ],
        "fixtures": fixtures,
        "targets": targets,
        "summary": {
            "targets": len(targets),
            "fixtures": len(fixtures),
            "target_classes": dict(sorted(Counter(row["target_class"] for row in targets).items())),
            "audition_required": sum(1 for row in targets if row["audition_required"]),
            "decision_required": len(targets),
        },
        "invariants": [
            "Every swept target remains named until it has an explicit terminal disposition.",
            "Installed, present, runnable, and feasible do not imply loaded, benchmarked, auditioned, or accepted.",
            "Audio-affecting targets cannot be accepted without a human audition ledger.",
            "Commercial and research comparators remain catalogued even when they cannot be automated.",
            "No homelab audit installs software, downloads weights, invokes a service, loads a model, or decodes source audio.",
        ],
    })


def _catalog_target(catalog: Mapping[str, Any], target_id: str) -> dict[str, Any]:
    for row in catalog.get("targets") or []:
        if row.get("target_id") == target_id:
            return dict(row)
    raise ValueError(f"unknown homelab target: {target_id}")


__all__ = ["homelab_catalog", "_catalog_target"]
