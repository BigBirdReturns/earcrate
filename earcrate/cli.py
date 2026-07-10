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

