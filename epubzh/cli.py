#!/usr/bin/env python3
"""Command line interface for the epubzh pipeline."""
import argparse
import os
import sys
from pathlib import Path

from . import assemble, chunks, config, guide, markup, pre, terms_audit, toc


def _shown(base, path):
    """Display a path relative to base when it lives there."""
    try:
        rel = os.path.relpath(str(path), str(base))
    except ValueError:
        return str(path)
    return str(path) if rel.startswith("..") else rel


def _source_candidates(base, value, cfg_path, output_abs):
    """Directories to try for a relative --source, in priority order."""
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return [raw]
    dirs = [base, cfg_path.parent.parent, cfg_path.parent]
    if output_abs is not None:
        dirs.append(output_abs.parent)
    seen, out = set(), []
    for d in dirs:
        d = Path(d).expanduser().resolve()
        if d in seen:
            continue
        seen.add(d)
        out.append(d / raw)
    return out


def cmd_init(args):
    base = Path(args.base).expanduser().resolve() if args.base else Path.cwd()
    if not args.base:
        relative = [name for name, value in (("--config", args.config),
                                             ("--source", args.source),
                                             ("--output", args.output))
                    if value and not Path(value).expanduser().is_absolute()]
        if relative:
            raise SystemExit(
                "relative path without --base: %s\n"
                "pass --base \"<session directory>\" (the directory where opencode "
                "was started) or use absolute paths" % ", ".join(relative))

    cfg_path = config.resolve_path(base, args.config)
    safe = (args.title or "translated").replace("/", "-")
    if args.output:
        output_abs = config.resolve_path(base, args.output)
    else:
        output_abs = cfg_path.parent / "out" / ("%s.epub" % safe)

    candidates = _source_candidates(base, args.source, cfg_path, output_abs)
    source = next((p for p in candidates if p.exists()), None)
    if source is None:
        lines = ["source not found: %s" % args.source, "searched:"]
        lines.extend("  %s" % p for p in candidates)
        for directory in dict.fromkeys(p.parent for p in candidates):
            if directory.is_dir():
                epubs = sorted(p.name for p in directory.glob("*.epub"))
                if epubs:
                    lines.append("epubs in %s: %s" % (directory, ", ".join(epubs)))
        lines.append("hint: keep the source epub next to the output file and "
                     "pass only its file name")
        raise SystemExit("\n".join(lines))

    if source.is_dir():
        source_dir_abs = source
    else:
        source_dir_abs = config.resolve_path(cfg_path.parent, args.source_dir)
    work_abs = config.resolve_path(cfg_path.parent, args.work)

    profile = config.check_profile(args.profile)
    print("base    = %s" % base)
    print("config  = %-24s -> %s" % (_shown(base, cfg_path), cfg_path))
    print("source  = %-24s -> %s" % (args.source, source))
    print("output  = %-24s -> %s" % (args.output or "(default)", output_abs))
    print("profile = %s (%d words per chunk, %s)"
          % (profile, config.PROFILES[profile]["words_per_chunk"],
             "max %d segments" % config.PROFILES[profile]["max_segments_per_chunk"]
             if config.PROFILES[profile]["max_segments_per_chunk"]
             else "no segment cap"))
    config.write_default(cfg_path, source, args.title, author=args.author,
                         work=str(work_abs), output=str(output_abs),
                         source_dir=str(source_dir_abs),
                         source_title=args.source_title, profile=profile)
    cfg = config.load(cfg_path)
    guide.seed_terms(cfg)
    if source.is_file():
        assemble.unpack(cfg)
        layout = config.detect_extract_layout(cfg)
        if layout:
            text_dir, extensions = layout
            if text_dir != cfg.text_dir or extensions != cfg.extensions:
                config.set_extract_layout(cfg.path, text_dir, extensions)
                cfg = config.load(cfg_path)
                print("layout  = %s (%s)" % (text_dir, ", ".join(extensions)))
    wrapper = Path(__file__).resolve().parent.parent / "epubzh-run"
    if os.name == "nt":
        wrapper = wrapper.with_name(wrapper.name + ".cmd")
    if wrapper.exists():
        tool = str(wrapper)
        if os.name == "nt":
            tool = "& %s" % guide.quote_path(wrapper)
    else:
        tool = guide._module_cmd(cfg) if os.name == "nt" else "python3 -m epubzh"
    print("\ncreated %s (profile: %s, %d words per chunk%s)"
          % (cfg_path, profile, cfg.words_per_chunk,
             ", max %d segments" % cfg.max_segments_per_chunk
             if cfg.max_segments_per_chunk else ", no segment cap"))
    config_hint = guide.quote_path(cfg_path) if os.name == "nt" else str(cfg_path)
    print("next:")
    print("  %s extract --config %s" % (tool, config_hint))
    print("  %s guide   --config %s" % (tool, config_hint))
    print("  edit %s" % cfg.terms_tsv)


def cmd_unpack(args):
    cfg = config.load(args.config)
    assemble.unpack(cfg, force=args.force)


def cmd_extract(args):
    cfg = config.load(args.config)
    if getattr(args, "profile", None):
        profile = config.check_profile(args.profile)
        if profile != cfg.profile:
            existing_zh = (list(cfg.zh_dir.glob("*.txt"))
                           if cfg.zh_dir.exists() else [])
            if existing_zh and not args.force:
                raise SystemExit(
                    "refusing to switch profile to %r: %d translated chunk(s) "
                    "exist and would no longer match\n"
                    "(use --force only if you intend to retranslate)"
                    % (profile, len(existing_zh)))
            config.set_profile(cfg.path, profile)
            cfg = config.load(cfg.path)
            print("profile: %s%s" % (
                cfg.profile,
                " (%d words, max %d segments)" % (cfg.words_per_chunk,
                                                  cfg.max_segments_per_chunk)
                if cfg.max_segments_per_chunk
                else " (%d words, no segment cap)" % cfg.words_per_chunk))
    if not cfg.source_dir.exists():
        raise SystemExit("source_dir not found: %s (run `epubzh unpack`)" % cfg.source_dir)
    assemble.normalize_sources(cfg)
    existing = list(cfg.segs_dir.glob("*.txt")) if cfg.segs_dir.exists() else []
    existing_zh = list(cfg.zh_dir.glob("*.txt")) if cfg.zh_dir.exists() else []
    if (existing or existing_zh) and not args.force:
        raise SystemExit("segs/zh already exist; use --force to re-extract "
                         "(old translations may no longer match)")
    files = cfg.html_files()
    if not files:
        raise SystemExit("no %s files under %s" % (cfg.extensions, cfg.source_dir / cfg.text_dir))
    cfg.segs_dir.mkdir(parents=True, exist_ok=True)
    cfg.zh_dir.mkdir(parents=True, exist_ok=True)
    for old in cfg.segs_dir.glob("*.txt"):
        old.unlink()

    index, warnings = {}, []
    total_segs = total_chunks = total_words = 0
    for path in files:
        name = path.name
        source = path.read_text(encoding="utf-8")
        work, atoms = markup.atomize(source, cfg.protect_blocks)
        segs = markup.extract_segments(
            work, warn=lambda w, n=name: warnings.append((n, w)),
            require_latin=cfg.require_latin)
        rebuilt = markup.apply_segments(work, segs, [s.text for s in segs])
        if rebuilt != work:
            raise SystemExit("round-trip mismatch: %s (extraction is unsafe)" % name)
        chunk_list = markup.chunk_segments(segs, cfg.words_per_chunk,
                                           cfg.max_segments_per_chunk)
        chunks.write_chunks(cfg, name, segs, chunk_list)
        words = sum(markup.segment_words(s.text) for s in segs)
        index[name] = {
            "segments": len(segs),
            "chunks": len(chunk_list),
            "chunk_segments": [len(c) for c in chunk_list],
            "words": words,
            "sha256": chunks.file_sha256(path),
        }
        total_segs += len(segs)
        total_chunks += len(chunk_list)
        total_words += words

    import json
    cfg.index_file.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    toc.write_toc_tsv(cfg)
    pre.collect(cfg)
    guide.seed_terms(cfg)
    print("profile=%s files=%d segments=%d chunks=%d words=%d"
          % (cfg.profile, len(index), total_segs, total_chunks, total_words))
    if warnings:
        print("warnings: %d" % len(warnings))
        for name, warning in warnings[:20]:
            print("  %s: %s" % (name, warning))


def cmd_status(args):
    cfg = config.load(args.config)
    chunks.status(cfg)


def cmd_check(args):
    cfg = config.load(args.config)
    index = chunks.read_index(cfg)
    names = [n for n in sorted(index) if not args.only or args.only in n]
    if not names:
        raise SystemExit("no files match %r" % args.only)
    if args.chunk and len(names) != 1:
        raise SystemExit("--chunk needs exactly one matching file, matched %d"
                         % len(names))
    skip_missing = (args.only is None and args.chunk is None
                    and not args.all)
    all_problems = []
    results = []
    for name in names:
        problems = chunks.check_file(cfg, name, only_chunk=args.chunk,
                                     fix=args.fix, skip_missing=skip_missing)
        all_problems.extend(problems)
        results.append((name, problems))
    for name, problems in results:
        # A project-wide check should only surface files that need attention;
        # printing every pending file as "OK" drowns out the real problem.
        if not problems and not (args.only or args.chunk):
            continue
        mark = "OK" if not problems else "%d problem(s)" % len(problems)
        if args.chunk:
            mark += " (chunk %d)" % args.chunk
        print("%-38s %s" % (name, mark))
    print("\n%d file(s), %d problem(s)" % (len(names), len(all_problems)))
    if skip_missing:
        print("note: chunks without a translation file are skipped; "
              "use --all to require every chunk")
    for problem in all_problems[:80]:
        print("  !!", problem)
    if len(all_problems) > 80:
        print("  ... %d more" % (len(all_problems) - 80))
    return 1 if all_problems else 0


def cmd_next(args):
    cfg = config.load(args.config)
    target = guide.next_target(cfg, resume=args.resume)
    if not target:
        print("all chunks translated")
        return 0
    filename, chunk, state, last, nseg = target
    stem = Path(filename).stem
    path = guide.prompt(cfg, stem, chunk, quiet=True)
    detail = ""
    if state == "partial":
        detail = " partial %d/%d" % (last, nseg)
    elif state == "repair":
        detail = " needs repair"
    elif state == "broken":
        detail = " broken numbering"
    print("next: %s chunk %d/%d%s" % (filename, chunk, nseg, detail))
    print("prompt: %s" % path)
    print("output: %s" % (cfg.zh_dir / chunks.chunk_name(stem, chunk)))
    print("action: read the prompt and execute only this chunk; "
          "do not run a project-wide check")
    print("check: %s check --config %s %s --chunk %d --fix"
          % (guide._module_cmd(cfg), guide.quote_path(cfg.path), stem, chunk))
    return 0


def cmd_merge(args):
    cfg = config.load(args.config)
    assemble.assemble(cfg, only=args.only)


def cmd_build(args):
    cfg = config.load(args.config)
    ok = assemble.build(cfg, only=args.only, do_package=not args.no_package)
    return 0 if ok else 1


def cmd_verify(args):
    if args.epub:
        target = Path(args.epub).expanduser()
    else:
        cfg = config.load(args.config)
        target = cfg.output
    problems = assemble.verify_epub(target)
    return 1 if problems else 0


def cmd_toc(args):
    cfg = config.load(args.config)
    toc.write_toc_tsv(cfg)


def cmd_pre(args):
    cfg = config.load(args.config)
    pre.collect(cfg)


def cmd_guide(args):
    cfg = config.load(args.config)
    guide.seed_terms(cfg)
    guide.render_guide(cfg)


def cmd_prompt(args):
    cfg = config.load(args.config)
    guide.prompt(cfg, args.name, args.chunk, stdout=args.stdout,
                 full=args.full, force=args.force)


def cmd_terms_audit(args):
    cfg = config.load(args.config)
    terms_audit.refresh_introduced(cfg)
    rows, hits, total = terms_audit.audit(cfg, only=args.only,
                                          strict=args.strict, limit=args.limit)
    if args.strict and total > hits:
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="epubzh",
        description="Markup-safe EPUB translation pipeline (no third-party deps).")
    parser.add_argument("--config", default="book.toml",
                        help="config file (default: book.toml)")
    parser.add_argument("--base", default=None,
                        help="base directory for relative paths; pass the "
                             "directory where opencode was started instead of "
                             "relying on the current directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create book.toml, unpack source, seed glossary")
    p.add_argument("--base", default=argparse.SUPPRESS,
                   help="session directory used to resolve relative paths "
                        "(see the global --base)")
    p.add_argument("--source", required=True, help="source .epub file or directory")
    p.add_argument("--title", required=True, help="translated book title")
    p.add_argument("--author", default="", help="author name")
    p.add_argument("--source-title", default="", help="title in the source language")
    p.add_argument("--work", default="work", help="work directory")
    p.add_argument("--output", default="", help="output epub path")
    p.add_argument("--source-dir", default="src", help="unpack directory for source epub")
    p.add_argument("--profile", default=config.DEFAULT_PROFILE,
                   choices=sorted(config.PROFILES),
                   help="chunk preset: large (2600 words, no cap) or small "
                        "(1200 words, max 40 segments)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("unpack", help="unpack the source epub")
    p.add_argument("--force", action="store_true", help="replace a non-empty source dir")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("extract", help="extract translatable segments and chunk them")
    p.add_argument("--force", action="store_true", help="re-extract even if work exists")
    p.add_argument("--profile", choices=sorted(config.PROFILES),
                   help="switch the chunk preset before re-extracting")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("status", help="show translation progress")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="validate translated chunks")
    p.add_argument("only", nargs="?", help="only files whose name contains this string")
    p.add_argument("--chunk", type=int, help="validate a single chunk only")
    p.add_argument("--all", action="store_true",
                   help="require every chunk; without it, a project-wide check "
                        "skips chunks that have not been started")
    p.add_argument("--fix", dest="fix", action="store_true", default=True,
                   help="repair duplicate/out-of-range/reordered blocks (default)")
    p.add_argument("--no-fix", dest="fix", action="store_false",
                   help="read-only check, do not repair blocks")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("next", help="show and write the next chunk prompt")
    p.add_argument("--resume", action="store_true",
                   help="resume existing repair/partial/broken chunks first "
                        "instead of following strict reading order")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("terms-audit",
                       help="check glossary terms against translated chunks")
    p.add_argument("--only", help="only files whose name contains this string")
    p.add_argument("--limit", type=int, default=20,
                   help="how many terms to list (default: 20)")
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero when any term is missing")
    p.set_defaults(func=cmd_terms_audit)

    p = sub.add_parser("merge", help="assemble translated files without packaging")
    p.add_argument("--only", help="only merge files whose name contains this string")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("build", help="assemble, validate, and package the epub")
    p.add_argument("--only", help="partial build (no packaging)")
    p.add_argument("--no-package", action="store_true", help="skip zip packaging")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="validate a finished epub")
    p.add_argument("epub", nargs="?", help="epub path (default: config output)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("toc", help="refresh work/toc.tsv, preserving translations")
    p.set_defaults(func=cmd_toc)

    p = sub.add_parser("pre", help="refresh work/pre.json for plain-text <pre> blocks")
    p.set_defaults(func=cmd_pre)

    p = sub.add_parser("guide", help="render work/TRANSLATION_GUIDE.md from the glossary")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("prompt", help="write a subagent prompt for a chunk")
    p.add_argument("name", nargs="?", help="file stem, for example chapter-1")
    p.add_argument("--chunk", type=int, help="chunk number (1-based)")
    p.add_argument("--stdout", action="store_true",
                   help="print the prompt instead of writing work/prompts/")
    p.add_argument("--full", action="store_true",
                   help="use the full translation guide regardless of profile")
    p.add_argument("--force", action="store_true",
                   help="rewrite the prompt file even if it is unchanged")
    p.set_defaults(func=cmd_prompt)
    return parser


def _move_option(argv, name):
    """Move a global option (and its value) in front of the subcommand."""
    if name in argv:
        i = argv.index(name)
        pair = argv[i:i + 2]
        del argv[i:i + 2]
        return pair + argv
    for i, value in enumerate(argv):
        if value.startswith(name + "="):
            argv.insert(0, argv.pop(i))
            break
    return argv


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Accept --base and --config after the subcommand as well as before it.
    for option in ("--base", "--config"):
        argv = _move_option(argv, option)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.base:
        base = Path(args.base).expanduser().resolve()
        args.config = str(config.resolve_path(base, args.config))
        if getattr(args, "epub", None):
            args.epub = str(config.resolve_path(base, args.epub))
    needs_config = args.command != "init" and not (
        args.command == "verify" and getattr(args, "epub", None))
    if needs_config and not Path(args.config).exists():
        parser.error("config not found: %s (run `epubzh init` first)" % args.config)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
