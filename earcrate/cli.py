from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.selftest import *


def _pop_json_out(args: List[str]) -> str:
    """Pull an optional `--json-out <path>` out of an argv slice so the caller's
    argparse never sees it. This is a machine-result affordance for verification
    tooling: the command's exact result dict is written to that file (in addition
    to being printed), so a reader never has to scrape it out of noisy stdout."""
    if "--json-out" in args:
        i = args.index("--json-out")
        val = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        return val
    return ""


def _emit(result: Any, json_out: str) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if json_out:
        p = Path(json_out).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    print(text)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "project":
        _json_out = _pop_json_out(argv)
        pp = argparse.ArgumentParser(prog="earcrate project", description="Compile, inspect, edit, render and export immutable EarCrate projects")
        sub = pp.add_subparsers(dest="project_command", required=True)
        cp = sub.add_parser("compile", help="compile the existing EarAtom crate through bounded candidate search")
        cp.add_argument("--profile", default="girl_talk_v1")
        cp.add_argument("--seconds", type=float, default=120.0)
        cp.add_argument("--name", default="EarCrate Set")
        cp.add_argument("--seed", type=int, default=0)
        cp.add_argument("--bpm", type=float, default=0.0)
        cp.add_argument("--candidate-count", type=int, default=0)
        cp.add_argument("--render", action="store_true", help="execute the resulting guarded project manifest")
        sub.add_parser("list", help="list visible project records")
        sp = sub.add_parser("show", help="show a project and revision")
        sp.add_argument("project_id"); sp.add_argument("--revision", default="")
        hp = sub.add_parser("history", help="show append-only project command history")
        hp.add_argument("project_id")
        ep = sub.add_parser("edit", help="apply a typed immutable project command")
        ep.add_argument("project_id"); ep.add_argument("--command", required=True, help="command JSON file")
        up = sub.add_parser("undo"); up.add_argument("project_id")
        rp2 = sub.add_parser("redo"); rp2.add_argument("project_id")
        rcp = sub.add_parser("recompile", help="recompile unlocked decisions around human locks")
        rcp.add_argument("project_id"); rcp.add_argument("--seed", type=int, default=0); rcp.add_argument("--candidate-count", type=int, default=0)
        rnp = sub.add_parser("render", help="render the active or named revision through explicit premaster/master stages")
        rnp.add_argument("project_id"); rnp.add_argument("--revision", default=""); rnp.add_argument("--dst", default="")
        pvp = sub.add_parser("preview", help="audition an exact beat-range crop from the verified project render")
        pvp.add_argument("project_id"); pvp.add_argument("--revision", default=""); pvp.add_argument("--start-beat", type=float, default=0.0); pvp.add_argument("--duration-beats", type=float, default=16.0); pvp.add_argument("--dst", default="")
        xp = sub.add_parser("export", help="export EDL, Reaper RPP and the live score sheet")
        xp.add_argument("project_id"); xp.add_argument("--revision", default=""); xp.add_argument("--destination", default="")
        ip = sub.add_parser("import", help="import a legacy arrangement JSON as a first-class project")
        ip.add_argument("arrangement"); ip.add_argument("--name", default="Imported EarCrate Project"); ip.add_argument("--project-id", default="")
        ap = sub.add_parser("acceptance", help="drive the full integrated project lifecycle in an isolated workspace")
        ap.add_argument("--destination", required=True)
        pn = sub.add_parser("piano", help="the player piano: an unattended, bounded, kill-safe compile->render->keep/discard loop over the configured library")
        pn.add_argument("--personas", default="girl_talk_v1", help="comma-separated persona ids to rotate through")
        pn.add_argument("--iterations", type=int, default=8, help="hard cap on compile/render attempts")
        pn.add_argument("--keeps", type=int, default=0, help="stop early once this many sets pass the gate (0 = no keep cap)")
        pn.add_argument("--seconds", type=float, default=0.0, help="stop early after this much wall-clock (0 = no time cap)")
        pn.add_argument("--target-seconds", type=float, default=120.0, help="target length of each compiled set")
        pn.add_argument("--seed", type=int, default=0, help="base seed; iteration i uses seed+i")
        pn.add_argument("--run-id", default="", help="resume/append to a prior run receipt with this id")
        pn.add_argument("--no-resume", action="store_true", help="ignore any prior receipt for this run-id and start fresh")
        ns = pp.parse_args(argv[1:])
        if ns.project_command == "acceptance":
            destination = Path(ns.destination).expanduser().resolve()
            old_home = os.environ.get("EARCRATE_HOME")
            os.environ["EARCRATE_HOME"] = str(destination / "home")
            try:
                core = EarcrateCore()
                result = core.project_acceptance(str(destination))
            finally:
                if old_home is None:
                    os.environ.pop("EARCRATE_HOME", None)
                else:
                    os.environ["EARCRATE_HOME"] = old_home
            _emit(result, _json_out)
            return 0
        core = EarcrateCore()
        if ns.project_command == "compile":
            data = {"taste_profile": ns.profile, "target_seconds": ns.seconds, "name": ns.name}
            if ns.seed: data["seed"] = ns.seed
            if ns.bpm: data["bpm"] = ns.bpm
            if ns.candidate_count: data["candidate_count"] = ns.candidate_count
            result = core.project_proposal(data)
            if ns.render:
                result["execute"] = core.execute_manifest(result["manifest"], apply=True)
        elif ns.project_command == "list": result = core.project_list()
        elif ns.project_command == "show": result = core.project_show(ns.project_id, ns.revision)
        elif ns.project_command == "history": result = core.project_history(ns.project_id)
        elif ns.project_command == "edit":
            result = core.project_edit(ns.project_id, json.loads(Path(ns.command).read_text(encoding="utf-8")))
        elif ns.project_command == "undo": result = core.project_undo(ns.project_id)
        elif ns.project_command == "redo": result = core.project_redo(ns.project_id)
        elif ns.project_command == "recompile":
            data = {}
            if ns.seed: data["seed"] = ns.seed
            if ns.candidate_count: data["candidate_count"] = ns.candidate_count
            result = core.project_recompile(ns.project_id, data)
        elif ns.project_command == "render":
            result = core.project_render(ns.project_id, Path(ns.dst).resolve() if ns.dst else None, ns.revision)
        elif ns.project_command == "preview":
            result = core.project_preview(ns.project_id, start_beat=ns.start_beat, duration_beats=ns.duration_beats,
                                          dst=Path(ns.dst).resolve() if ns.dst else None, revision_sha=ns.revision)
        elif ns.project_command == "export":
            result = core.project_export(ns.project_id, ns.destination, ns.revision)
        elif ns.project_command == "import":
            arrangement = json.loads(Path(ns.arrangement).read_text(encoding="utf-8"))
            result = core.project_import_arrangement(arrangement, name=ns.name, project_id=ns.project_id,
                                                     created_by={"actor": "cli", "reason": "legacy_arrangement_import"})
        elif ns.project_command == "piano":
            result = core.project_piano(
                personas=[p.strip() for p in str(ns.personas).split(",") if p.strip()],
                max_iterations=ns.iterations, max_keeps=ns.keeps, max_seconds=ns.seconds,
                target_seconds=ns.target_seconds, seed_base=ns.seed,
                run_id=ns.run_id, resume=not ns.no_resume)
        _emit(result, _json_out)
        return 0
    if argv and argv[0] == "judge":
        jp = argparse.ArgumentParser(prog="earcrate judge", description="Judge a render against the v1.1 reference gates")
        jp.add_argument("render")
        jp.add_argument("--ref", default="")
        ns = jp.parse_args(argv[1:])
        print(json.dumps(judge_audio_file(Path(ns.render), Path(ns.ref) if ns.ref else None), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] in {"manifest", "execute-manifest"}:
        mp = argparse.ArgumentParser(prog="earcrate manifest", description="Dry-run or apply a manifest through the guarded executor")
        mp.add_argument("path")
        mp.add_argument("--apply", action="store_true", help="write outputs; default is dry-run only")
        ns = mp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.execute_manifest(ns.path, apply=ns.apply), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "rollback":
        rp = argparse.ArgumentParser(prog="earcrate rollback", description="Dry-run or apply rollback archival for generated outputs")
        rp.add_argument("--apply", action="store_true", help="archive generated outputs; default is dry-run only")
        rp.add_argument("--manifest-id", default="", help="only rollback records for one manifest id")
        rp.add_argument("--limit", type=int, default=0, help="maximum rollback records to process, 0 means all")
        ns = rp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.rollback_outputs(manifest_id=ns.manifest_id, limit=ns.limit, apply=ns.apply), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] in {"ear-crate", "taste-crate"}:
        ep = argparse.ArgumentParser(prog="earcrate ear-crate", description="Build the TasteSpec ear crate from extracted loops")
        ep.add_argument("--limit", type=int, default=0)
        ep.add_argument("--force", action="store_true")
        ep.add_argument("--profile", default="girl_talk_v1")
        ep.add_argument("--previews", action="store_true", help="write short audition WAVs under working_root/ear_crate/previews")
        ns = ep.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.build_ear_crate(limit=ns.limit, force=ns.force, taste_profile=ns.profile, write_previews=ns.previews), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "workspace-candidates":
        tp = argparse.ArgumentParser(prog="earcrate workspace-candidates", description="Scout and rank workspace folder candidates")
        tp.add_argument("--music", default="", help="Music/source folder to enforce separation against")
        ns = tp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.workspace_candidates(ns.music), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "ingest":
        ip = argparse.ArgumentParser(prog="earcrate ingest", description="Copy multiple source folders into the managed library (manifest-gated)")
        ip.add_argument("sources", nargs="+")
        ip.add_argument("--apply", action="store_true", help="write copies; default is dry-run plan")
        ns = ip.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.ingest_sources({"sources": ns.sources, "apply": ns.apply}), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "organize":
        op_ = argparse.ArgumentParser(prog="earcrate organize", description="Copy library into Artist/Album/NN Title with amended tags (manifest-gated)")
        op_.add_argument("--apply", action="store_true")
        op_.add_argument("--limit", type=int, default=0)
        ns = op_.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.organize_and_retag({"apply": ns.apply, "limit": ns.limit}), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "train-ranker":
        _json_out = _pop_json_out(argv)
        tp = argparse.ArgumentParser(prog="earcrate train-ranker", description="Train the opt-in taste ranker (M4) from your approve/reject judgments for a persona. Writes a model artifact + receipt; enable it with EARCRATE_RANKER=on.")
        tp.add_argument("--profile", default="girl_talk_v1")
        tp.add_argument("--min-examples", type=int, default=8, help="minimum labelled atoms (both classes) required to train")
        ns = tp.parse_args(argv[1:])
        core = EarcrateCore()
        _emit(core.train_taste_ranker(ns.profile, min_examples=ns.min_examples), _json_out)
        return 0
    if argv and argv[0] == "rank":
        rp = argparse.ArgumentParser(prog="earcrate rank", description="Rank the ear crate by the persona's selection priorities (curation surface)")
        rp.add_argument("--profile", default="girl_talk_v1")
        rp.add_argument("--limit", type=int, default=40)
        ns = rp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.rank_crate(ns.profile, ns.limit), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "taste-readiness":
        tp = argparse.ArgumentParser(prog="earcrate taste-readiness", description="Audit whether the ear crate can satisfy a TasteSpec profile")
        tp.add_argument("--profile", default="girl_talk_v1")
        tp.add_argument("--seconds", type=float, default=120)
        ns = tp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.taste_readiness(ns.profile, ns.seconds), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "taste-graph":
        gp = argparse.ArgumentParser(prog="earcrate taste-graph", description="Build deterministic TasteSpec compatibility edges")
        gp.add_argument("--profile", default="girl_talk_v1")
        gp.add_argument("--seconds", type=float, default=120)
        gp.add_argument("--bpm", type=float, default=0.0)
        ns = gp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.build_compatibility_graph(ns.profile, ns.seconds, ns.bpm), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "configure":
        _json_out = _pop_json_out(argv)
        cp = argparse.ArgumentParser(prog="earcrate configure", description="Set the music folder + workspace (persists; run this once before scan/organize/deepclean)")
        cp.add_argument("--music", required=True, help="your music/source folder (read-only source)")
        cp.add_argument("--workspace", default="", help="workspace folder; default is a visible sibling of the music folder")
        cp.add_argument("--workers", type=int, default=None)
        cp.add_argument("--analysis-seconds", type=int, default=0)
        ns = cp.parse_args(argv[1:])
        data = {"music_folder": ns.music, "workspace_folder": ns.workspace}
        if ns.workers is not None: data["workers"] = ns.workers
        if ns.analysis_seconds: data["analysis_seconds"] = ns.analysis_seconds
        core = EarcrateCore()
        _emit(core.configure_workspace(data), _json_out)
        return 0
    if argv and argv[0] == "scan":
        core = EarcrateCore()
        print(json.dumps(core.scan(), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "analyze":
        ap = argparse.ArgumentParser(prog="earcrate analyze", description="Compute BPM/key/energy/vocal features for scanned tracks")
        ap.add_argument("--limit", type=int, default=0)
        ap.add_argument("--force", action="store_true")
        ns = ap.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.analyze(limit=ns.limit, force=ns.force), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "deepclean":
        dp = argparse.ArgumentParser(prog="earcrate deepclean", description="Listen to each file's audio graph: separate real songs from silence/static/corrupt; find empty + art-only folders. Assessment only (nothing moved).")
        dp.add_argument("--root", default="", help="folder to assess; default is the configured music folder")
        dp.add_argument("--limit", type=int, default=0, help="assess only the first N audio files (0 = all)")
        ns = dp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.deep_clean_scan({"root": ns.root, "limit": ns.limit}), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "reorganize":
        rp = argparse.ArgumentParser(prog="earcrate reorganize", description="Reorganize the source IN PLACE into Artist/Album/NN-Title. Dry-run plan by default; --apply moves (journaled, reversible).")
        rp.add_argument("--root", default="", help="folder to reorganize; default is the configured music folder")
        rp.add_argument("--apply", action="store_true", help="execute the moves; default is a dry-run plan")
        rp.add_argument("--signature", default="", help="approved plan signature from a prior dry-run (apply refuses if the library changed)")
        ns = rp.parse_args(argv[1:])
        core = EarcrateCore()
        data = {"root": ns.root, "apply": ns.apply}
        if ns.signature: data["signature"] = ns.signature
        print(json.dumps(core.reorganize_source(data), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "reorganize-rollback":
        rp = argparse.ArgumentParser(prog="earcrate reorganize-rollback", description="Undo a reorganize using its journal. Dry-run preview by default; --apply to move files back.")
        rp.add_argument("journal")
        rp.add_argument("--apply", action="store_true", help="execute the undo; default is a dry-run preview")
        ns = rp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.rollback_reorganize({"journal": ns.journal, "apply": ns.apply}), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "identify":
        ip = argparse.ArgumentParser(prog="earcrate identify", description="Propose real identities via AcoustID/MusicBrainz (needs fpcalc on PATH + a free key). Dry-run; nothing written.")
        ip.add_argument("--key", default="", help="AcoustID client key (or set EARCRATE_ACOUSTID_KEY)")
        ip.add_argument("--limit", type=int, default=0)
        ns = ip.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.identify_tracks({"api_key": ns.key, "limit": ns.limit}), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "apply-identities":
        ap = argparse.ArgumentParser(prog="earcrate apply-identities", description="Rewrite tags from identify's AcoustID proposals. Dry-run by default; --apply writes (reversible).")
        ap.add_argument("--min-score", type=float, default=0.85, help="only apply matches at or above this AcoustID score")
        ap.add_argument("--proposals", default="", help="proposals JSON path (default: workspace agent/identify_proposals.json)")
        ap.add_argument("--apply", action="store_true", help="write tags; default is a dry-run preview")
        ap.add_argument("--signature", default="")
        ns = ap.parse_args(argv[1:])
        core = EarcrateCore()
        d = {"min_score": ns.min_score, "apply": ns.apply}
        if ns.proposals: d["proposals_path"] = ns.proposals
        if ns.signature: d["signature"] = ns.signature
        print(json.dumps(core.apply_identities(d), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "identify-rollback":
        rp = argparse.ArgumentParser(prog="earcrate identify-rollback", description="Restore the tags a retag pass overwrote, using its journal. Dry-run preview by default; --apply to rewrite tags back.")
        rp.add_argument("journal")
        rp.add_argument("--apply", action="store_true", help="execute the undo; default is a dry-run preview")
        ns = rp.parse_args(argv[1:])
        core = EarcrateCore()
        print(json.dumps(core.rollback_identities({"journal": ns.journal, "apply": ns.apply}), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "doctor":
        # Lightweight capability + workspace health report. Exits non-zero when a
        # required check (ffmpeg/ffprobe, workspace roots, sqlite) fails, so setup
        # scripts and CI can gate on it. Stem/GPU capability is reported but is
        # informational only (a box with no GPU is healthy). Unlike --self-test,
        # this runs NO synthetic render, and it works before a workspace exists.
        _json_out = _pop_json_out(argv)
        dp = argparse.ArgumentParser(prog="earcrate doctor", description="Report environment + workspace health (ffmpeg, roots, sqlite, stem/GPU capability). Exits non-zero if a required check fails.")
        dp.parse_args(argv[1:])
        core = EarcrateCore()
        try:
            report = core.doctor()
        except RuntimeError as exc:
            # No workspace configured yet: still answer the first-run question
            # ("is ffmpeg here?") instead of crashing on ensure_config().
            tool_checks = [{"name": t, "ok": shutil.which(t) is not None, "detail": shutil.which(t) or "missing"}
                           for t in ("ffmpeg", "ffprobe")]
            report = {"ok": all(x["ok"] for x in tool_checks), "configured": False,
                      "reason": str(exc), "checks": tool_checks,
                      "hint": "run 'earcrate configure --music <folder>' to enable the workspace + sqlite checks"}
        _emit(report, _json_out)
        return 0 if report.get("ok") else 1
    parser = argparse.ArgumentParser(prog="earcrate", description="earcrate: local-first layered mashup engine; only auditioned material exists to the composer")
    parser.add_argument("--serve", action="store_true", help="start local UI server")
    parser.add_argument("--no-browser", action="store_true", help="do not open browser")
    parser.add_argument("--port", type=int, default=0, help="local port, 0 means random")
    parser.add_argument("--self-test", action="store_true", help="run synthetic end-to-end test")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    serve(open_browser=not args.no_browser, port=args.port)
    return 0


if __name__ == "__main__":
    import multiprocessing as _mp
    _mp.freeze_support()  # required for spawn-based workers in frozen Windows builds
    raise SystemExit(main())

