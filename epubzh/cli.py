#!/usr/bin/env python3
"""Command line interface for the epubzh pipeline."""
import argparse
import sys
from pathlib import Path

from . import assemble, chunks, config, guide, markup, pre, toc


def cmd_init(args):
    cfg_path = Path(args.config).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise SystemExit("source not found: %s" % source)
    if source.is_dir():
        source_dir = str(source)
    else:
        source_dir = args.source_dir
    source_title = args.source_title or args.title
    config.write_default(cfg_path, source, args.title, author=args.author,
                         work=args.work, output=args.output, source_dir=source_dir,
                         source_title=source_title)
    cfg = config.load(cfg_path)
    guide.seed_terms(cfg)
    if source.is_file():
        assemble.unpack(cfg)
    wrapper = Path(__file__).resolve().parent.parent / "epubzh-run"
    tool = str(wrapper) if wrapper.exists() else "python3 -m epubzh"
    print("\ncreated %s" % cfg_path)
    print("next:")
    print("  %s extract --config %s" % (tool, cfg_path))
    print("  %s guide   --config %s" % (tool, cfg_path))
    print("  edit %s" % cfg.terms_tsv)


def cmd_unpack(args):
    cfg = config.load(args.config)
    assemble.unpack(cfg, force=args.force)


def cmd_extract(args):
    cfg = config.load(args.config)
    if not cfg.source_dir.exists():
        raise SystemExit("source_dir not found: %s (run `epubzh unpack`)" % cfg.source_dir)
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
        work, atoms = markup.atomize(source)
        segs = markup.extract_segments(
            work, warn=lambda w, n=name: warnings.append((n, w)),
            require_latin=cfg.require_latin)
        rebuilt = markup.apply_segments(work, segs, [s.text for s in segs])
        if rebuilt != work:
            raise SystemExit("round-trip mismatch: %s (extraction is unsafe)" % name)
        chunk_list = markup.chunk_segments(segs, cfg.words_per_chunk)
        chunks.write_chunks(cfg, name, segs, chunk_list)
        words = sum(markup.segment_words(s.text) for s in segs)
        index[name] = {
            "segments": len(segs),
            "chunks": len(chunk_list),
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
    print("files=%d segments=%d chunks=%d words=%d"
          % (len(index), total_segs, total_chunks, total_words))
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
    all_problems = []
    for name in names:
        problems = chunks.check_file(cfg, name)
        all_problems.extend(problems)
        mark = "OK" if not problems else "%d problem(s)" % len(problems)
        print("%-38s %s" % (name, mark))
    print("\n%d file(s), %d problem(s)" % (len(names), len(all_problems)))
    for problem in all_problems[:80]:
        print("  !!", problem)
    if len(all_problems) > 80:
        print("  ... %d more" % (len(all_problems) - 80))
    return 1 if all_problems else 0


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
    guide.prompt(cfg, args.name, args.chunk)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="epubzh",
        description="Markup-safe EPUB translation pipeline (no third-party deps).")
    parser.add_argument("--config", default="book.toml",
                        help="config file (default: book.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create book.toml, unpack source, seed glossary")
    p.add_argument("--source", required=True, help="source .epub file or directory")
    p.add_argument("--title", required=True, help="translated book title")
    p.add_argument("--author", default="", help="author name")
    p.add_argument("--source-title", default="", help="title in the source language")
    p.add_argument("--work", default="work", help="work directory")
    p.add_argument("--output", default="", help="output epub path")
    p.add_argument("--source-dir", default="src", help="unpack directory for source epub")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("unpack", help="unpack the source epub")
    p.add_argument("--force", action="store_true", help="replace a non-empty source dir")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("extract", help="extract translatable segments and chunk them")
    p.add_argument("--force", action="store_true", help="re-extract even if work exists")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("status", help="show translation progress")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="validate translated chunks")
    p.add_argument("only", nargs="?", help="only files whose name contains this string")
    p.set_defaults(func=cmd_check)

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

    p = sub.add_parser("prompt", help="print a subagent prompt for a chunk")
    p.add_argument("name", nargs="?", help="file stem, for example chapter-1")
    p.add_argument("--chunk", type=int, help="chunk number (1-based)")
    p.set_defaults(func=cmd_prompt)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Accept --config after the subcommand as well as before it.
    if "--config" in argv:
        i = argv.index("--config")
        pair = argv[i:i + 2]
        del argv[i:i + 2]
        argv = pair + argv
    else:
        for i, value in enumerate(argv):
            if value.startswith("--config="):
                argv.insert(0, argv.pop(i))
                break
    parser = build_parser()
    args = parser.parse_args(argv)
    needs_config = args.command != "init" and not (
        args.command == "verify" and getattr(args, "epub", None))
    if needs_config and not Path(args.config).exists():
        parser.error("config not found: %s (run `epubzh init` first)" % args.config)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
