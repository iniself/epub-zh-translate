#!/usr/bin/env python3
"""Translation guide rendering and per-chunk subagent prompts."""
import csv
from pathlib import Path

from . import config as config_mod
from . import chunks


def read_terms(cfg):
    rows = []
    if not cfg.terms_tsv.exists():
        return rows
    with open(cfg.terms_tsv, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if len(row) >= 2 and row[0].strip() and row[0].strip() != "English":
                rows.append((row[0].strip(), row[1].strip()))
    return rows


def seed_terms(cfg):
    if cfg.terms_tsv.exists():
        return
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.terms_tsv.write_text(
        config_mod.DEFAULT_TERMS.read_text(encoding="utf-8"), encoding="utf-8")
    print("wrote %s (edit this glossary before translating)" % cfg.terms_tsv)


def render_guide(cfg):
    template = config_mod.DEFAULT_GUIDE_TEMPLATE.read_text(encoding="utf-8")
    terms = read_terms(cfg)
    table = "\n".join("| %s | %s |" % (en, zh) for en, zh in terms)
    if not table:
        table = "| （在此填写术语） | |"
    text = (template
            .replace("{{TITLE}}", cfg.title or "（中文书名）")
            .replace("{{SOURCE_TITLE}}", cfg.source_title or cfg.title)
            .replace("{{AUTHOR}}", cfg.author or "（作者）")
            .replace("{{LANGUAGE}}", cfg.language)
            .replace("{{OPEN}}", "\u27e6")
            .replace("{{CLOSE}}", "\u27e7")
            .replace("{{TERMS}}", table)
            .replace("{{WORK}}", str(cfg.work_dir)))
    cfg.guide_file.write_text(text, encoding="utf-8")
    print("wrote %s (%d terms)" % (cfg.guide_file, len(terms)))


def _module_cmd(cfg):
    package_root = Path(__file__).resolve().parent.parent
    return ("PYTHONPATH=\"%s\" python3 -m epubzh" % package_root)


def prompt(cfg, name=None, chunk=None):
    index = chunks.read_index(cfg)
    if not name:
        pending = []
        for filename in sorted(index):
            stem = Path(filename).stem
            for ci in range(1, index[filename]["chunks"] + 1):
                if not (cfg.zh_dir / chunks.chunk_name(stem, ci)).exists():
                    pending.append((stem, ci))
        if not pending:
            print("all chunks translated")
            return
        print("pending chunks (%d):" % len(pending))
        for stem, ci in pending:
            print("  %s %d" % (stem, ci))
        print("\nusage: %s prompt <file-stem> --chunk <n>" % _module_cmd(cfg))
        return

    matches = [f for f in index if Path(f).stem == name or f == name]
    if not matches:
        matches = [f for f in index if name in f]
    if len(matches) != 1:
        raise SystemExit("file %r matches %d entries: %s" % (name, len(matches), matches[:8]))
    filename = matches[0]
    info = index[filename]
    stem = Path(filename).stem
    if chunk is None:
        print("%s: %d chunk(s), %d segment(s), %d words" % (
            filename, info["chunks"], info["segments"], info["words"]))
        for ci in range(1, info["chunks"] + 1):
            done = "done" if (cfg.zh_dir / chunks.chunk_name(stem, ci)).exists() else "pending"
            print("  chunk %d: %s" % (ci, done))
        return
    if chunk < 1 or chunk > info["chunks"]:
        raise SystemExit("chunk must be 1..%d" % info["chunks"])
    src = cfg.segs_dir / chunks.chunk_name(stem, chunk)
    dst = cfg.zh_dir / chunks.chunk_name(stem, chunk)
    print("""你是资深技术图书译者。请翻译一个 EPUB 文本分块。

项目目录：%s
翻译规范：%s
输入文件：%s
输出文件：%s

步骤：
1. 先完整阅读翻译规范，严格按其中的格式、占位符和术语要求执行。
2. 阅读输入文件，只翻译 @@@SEG nnn@@@ 标记之间的正文。
3. 只输出 @@@SEG nnn@@@ 标记和对应译文，写入输出文件，不要输出解释。
4. 写完后在项目目录运行以下命令自检并修正：
   %s check --config %s %s

注意：占位符（%s / %s）必须原样保留；代码、命令等整段保留原文的内容照抄，并在
段标记下一行加 @@@VERBATIM@@@；中英文之间加空格。
完成后只回复：已写入 %s，自检通过/剩余问题。""" % (
        cfg.root, cfg.guide_file, src, dst, _module_cmd(cfg), cfg.path, stem,
        "\u27e6", "\u27e7", dst))
