#!/usr/bin/env python3
"""Chunk files: writing translation jobs, parsing finished translations."""
import hashlib
import json
import re
from pathlib import Path

from . import markup, spacing

SEG_RE = re.compile(r"^@@@\s*SEG\s*0*(\d+)\s*@@@\s*$")
VERBATIM_RE = re.compile(r"^@@@\s*VERBATIM\s*@@@$")
CJK_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def chunk_name(stem, index):
    return "%s.%02d.txt" % (stem, index)


def write_chunks(cfg, name, segs, chunks):
    out = []
    stem = Path(name).stem
    for ci, chunk in enumerate(chunks):
        path = cfg.segs_dir / chunk_name(stem, ci + 1)
        lines = []
        lines.append("### FILE: %s" % name)
        lines.append("### CHUNK: %d/%d" % (ci + 1, len(chunks)))
        lines.append("### SEGMENTS: %d" % len(chunk))
        if ci > 0:
            previous = markup.strip_placeholders(chunks[ci - 1][-1].text).strip()
            lines.append("### PRECEDING TEXT (context only, do NOT translate):")
            lines.append(previous[:400])
        lines.append("###")
        for k, seg in enumerate(chunk):
            lines.append("@@@SEG %03d@@@" % (k + 1))
            lines.append(seg.text)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out.append(path)
    return out


def load_marked(path):
    """Return [(number, text, verbatim)] from a seg or translation file.

    A `@@@VERBATIM@@@` line as the first line of a segment body marks text the
    translator deliberately kept in the source language (code, commands,
    listing output). It is checked for placeholders like any other segment but
    is exempt from the "not translated" and spacing rules.
    """
    raw = Path(path).read_text(encoding="utf-8")
    parts, cur = [], None
    for line in raw.splitlines():
        stripped = line.strip()
        m = SEG_RE.match(stripped)
        if m:
            if cur is not None:
                parts.append(cur)
            cur = [int(m.group(1)), [], False]
        elif cur is not None:
            if not cur[1] and not cur[2] and VERBATIM_RE.match(stripped):
                cur[2] = True
            else:
                cur[1].append(line)
    if cur is not None:
        parts.append(cur)
    return [(n, " ".join(x.strip() for x in body if x.strip()), keep)
            for n, body, keep in parts]


def parse_translation(path, nseg):
    """Parse a translated chunk file into (texts, verbatim flags, error)."""
    path = Path(path)
    if not path.exists():
        return None, None, "file missing"
    parts = load_marked(path)
    if not parts:
        return None, None, "no markers found"
    nums = [p[0] for p in parts]
    if nums != list(range(1, nseg + 1)):
        return None, None, "segment numbers %s != 1..%d" % (nums[:8], nseg)
    return [p[1] for p in parts], [p[2] for p in parts], None


def read_index(cfg):
    if not cfg.index_file.exists():
        raise SystemExit("index not found: %s (run `epubzh extract`)" % cfg.index_file)
    return json.loads(cfg.index_file.read_text(encoding="utf-8"))


def status(cfg):
    index = read_index(cfg)
    rows = []
    for name in sorted(index):
        info = index[name]
        stem = Path(name).stem
        done = 0
        for ci in range(1, info["chunks"] + 1):
            path = cfg.zh_dir / chunk_name(stem, ci)
            if path.exists():
                done += 1
        rows.append((name, done, info["chunks"], info["segments"], info["words"]))
    total_done = sum(r[1] for r in rows)
    total_chunks = sum(r[2] for r in rows)
    print("%-38s %7s %8s %10s" % ("file", "chunks", "segments", "words"))
    for name, done, chunks, segs, words in rows:
        mark = "done" if done == chunks else "%d/%d" % (done, chunks)
        print("%-38s %7s %8d %10d" % (name, mark, segs, words))
    print("\nchunks: %d/%d translated" % (total_done, total_chunks))
    return total_done, total_chunks


def check_file(cfg, name):
    """Check every translated chunk of one source file. Returns problems."""
    index = read_index(cfg)
    if name not in index:
        return ["%s: not in index" % name]
    info = index[name]
    stem = Path(name).stem
    src_path = cfg.source_dir / cfg.text_dir / name
    if info.get("sha256") and file_sha256(src_path) != info["sha256"]:
        return ["%s: source changed since extract, re-run `epubzh extract --force`"
                % name]
    work, atoms = markup.atomize(src_path.read_text(encoding="utf-8"),
                                 cfg.protect_blocks)
    segs = markup.extract_segments(work, require_latin=cfg.require_latin)
    chunks = markup.chunk_segments(segs, cfg.words_per_chunk)
    problems = []
    if len(chunks) != info["chunks"]:
        problems.append("%s: chunk count changed (%d vs %d), re-run extract"
                        % (name, len(chunks), info["chunks"]))
    for ci, chunk in enumerate(chunks, 1):
        path = cfg.zh_dir / chunk_name(stem, ci)
        translated, verbatim, err = parse_translation(path, len(chunk))
        if err:
            problems.append("%s chunk %d: %s" % (name, ci, err))
            continue
        for seg, text, keep in zip(chunk, translated, verbatim):
            bad = seg.check(text)
            if bad:
                problems.append("%s chunk %d seg %d: %s"
                                % (name, ci, seg.index + 1, bad))
                continue
            if keep:
                continue
            body = markup.strip_placeholders(text).strip()
            short = len(body) <= 3 and " " not in body
            if cfg.require_latin and re.search("[A-Za-z]", seg.text) \
                    and not CJK_RE.search(body) and not short:
                problems.append("%s chunk %d seg %d: not translated -> %s"
                                % (name, ci, seg.index + 1, text[:100]))
            missing = spacing.has_missing_space(text)
            if missing:
                problems.append("%s chunk %d seg %d: missing space around Latin, e.g. %s"
                                % (name, ci, seg.index + 1, missing[:3]))
    return problems
