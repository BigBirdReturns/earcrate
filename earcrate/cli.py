from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.selftest import *
def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
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
        print(json.dumps(core.configure_workspace(data), ensure_ascii=False, indent=2))
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

