#!/usr/bin/env python3
"""Chunk files: writing translation jobs, parsing finished translations."""
import hashlib
import json
import re
from collections import Counter
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


def _marker_problems(parts, nseg):
    """Point at duplicated, missing, and out-of-range segment markers."""
    counts = Counter(p[0] for p in parts)
    dup = sorted(n for n, c in counts.items() if c > 1)
    missing = [n for n in range(1, nseg + 1) if n not in counts]
    extra = sorted(n for n in counts if n < 1 or n > nseg)
    bits = []
    if dup:
        bits.append("duplicated: %s" % ", ".join(str(n) for n in dup))
    if missing:
        bits.append("missing: %s" % ", ".join(str(n) for n in missing))
    if extra:
        bits.append("out of range: %s" % ", ".join(str(n) for n in extra))
    detail = "; ".join(bits) or "order is wrong"
    if dup:
        # Show the duplicated body so the extra marker is easy to find.
        first = next(p for p in parts if p[0] == dup[0])
        body = " ".join(first[1].split())[:60]
        if body:
            detail += " | seg %d: %s" % (dup[0], body)
    return "segment markers != 1..%d (%s)" % (nseg, detail)


def parse_translation(path, nseg):
    """Parse a translated chunk file into (texts, verbatim flags, error)."""
    path = Path(path)
    if not path.exists():
        return None, None, "file missing"
    parts = load_marked(path)
    if not parts:
        return None, None, "no markers found"
    if [p[0] for p in parts] != list(range(1, nseg + 1)):
        return None, None, _marker_problems(parts, nseg)
    return [p[1] for p in parts], [p[2] for p in parts], None


def read_index(cfg):
    if not cfg.index_file.exists():
        raise SystemExit("index not found: %s (run `epubzh extract`)" % cfg.index_file)
    return json.loads(cfg.index_file.read_text(encoding="utf-8"))


def chunk_counts(cfg, name, info=None):
    """Per-chunk segment counts, from the index or recomputed from source."""
    if info is None:
        info = read_index(cfg)[name]
    counts = info.get("chunk_segments")
    if counts and len(counts) == info["chunks"]:
        return list(counts)
    src = cfg.source_dir / cfg.text_dir / name
    work, _ = markup.atomize(src.read_text(encoding="utf-8"), cfg.protect_blocks)
    segs = markup.extract_segments(work, require_latin=cfg.require_latin)
    cl = markup.chunk_segments(segs, cfg.words_per_chunk,
                               cfg.max_segments_per_chunk)
    return [len(c) for c in cl]


def chunk_state(path, nseg):
    """Classify one translation file: pending / partial / broken / done.

    ``partial`` means segments 1..k are present and correctly numbered, but
    the chunk continues; a translator can append the rest instead of
    retranslating.
    """
    path = Path(path)
    if not path.exists():
        return "pending", 0
    nums = [p[0] for p in load_marked(path)]
    if nums == list(range(1, nseg + 1)):
        return "done", nseg
    if nums and nums == list(range(1, len(nums) + 1)) and len(nums) < nseg:
        return "partial", len(nums)
    return "broken", len(nums)


def status(cfg):
    index = read_index(cfg)
    rows = []
    cap = ("max %d segments" % cfg.max_segments_per_chunk
           if cfg.max_segments_per_chunk else "no segment cap")
    print("profile: %s (%d words per chunk, %s)"
          % (cfg.profile, cfg.words_per_chunk, cap))
    for name in sorted(index):
        info = index[name]
        counts = chunk_counts(cfg, name, info)
        stem = Path(name).stem
        done = partial = broken = 0
        for ci, nseg in enumerate(counts, 1):
            state, _ = chunk_state(cfg.zh_dir / chunk_name(stem, ci), nseg)
            if state == "done":
                done += 1
            elif state == "partial":
                partial += 1
            elif state == "broken":
                broken += 1
        rows.append((name, done, len(counts), info["segments"], info["words"],
                     partial, broken))
    total_done = sum(r[1] for r in rows)
    total_chunks = sum(r[2] for r in rows)
    total_partial = sum(r[5] for r in rows)
    total_broken = sum(r[6] for r in rows)
    print("%-38s %7s %8s %10s" % ("file", "chunks", "segments", "words"))
    for name, done, nchunks, segs, words, partial, broken in rows:
        mark = "done" if done == nchunks else "%d/%d" % (done, nchunks)
        flags = []
        if partial:
            flags.append("%dp" % partial)
        if broken:
            flags.append("%db" % broken)
        if flags:
            mark += " " + " ".join(flags)
        print("%-38s %7s %8d %10d" % (name, mark, segs, words))
    print("\nchunks: %d/%d translated" % (total_done, total_chunks))
    if total_partial or total_broken:
        print("needs attention: %d partial, %d broken" % (total_partial, total_broken))
    return total_done, total_chunks


def _clip(text, limit=110):
    """Single-line excerpt used in error messages."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _context(seg, translated):
    """Source and target excerpt so a translator can fix the line directly."""
    return "\n    src: %s\n    got: %s" % (_clip(seg.text), _clip(translated))


def check_file(cfg, name, only_chunk=None):
    """Check translated chunks of one source file. Returns problems.

    ``only_chunk`` restricts validation to a single chunk, so a translator
    working on one chunk does not see "file missing" for its siblings.
    """
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
    chunks = markup.chunk_segments(segs, cfg.words_per_chunk,
                                   cfg.max_segments_per_chunk)
    problems = []
    if len(chunks) != info["chunks"]:
        problems.append("%s: chunk count changed (%d vs %d), re-run extract"
                        % (name, len(chunks), info["chunks"]))
    if only_chunk is not None:
        if only_chunk < 1 or only_chunk > len(chunks):
            return problems + ["%s: chunk must be 1..%d" % (name, len(chunks))]
        targets = [(only_chunk, chunks[only_chunk - 1])]
    else:
        targets = list(enumerate(chunks, 1))
    for ci, chunk in targets:
        path = cfg.zh_dir / chunk_name(stem, ci)
        translated, verbatim, err = parse_translation(path, len(chunk))
        if err:
            problems.append("%s chunk %d: %s" % (name, ci, err))
            continue
        # Report chunk-local numbers, matching the @@@SEG nnn@@@ markers the
        # translator edits. Document-wide numbers would point at the wrong
        # line whenever the chunk is not the first one.
        offset = chunk[0].index
        width = len(chunk)
        for seg, text, keep in zip(chunk, translated, verbatim):
            pos = seg.index + 1 - offset
            bad = seg.check(text)
            if bad:
                problems.append("%s chunk %d seg %d/%d: %s%s"
                                % (name, ci, pos, width, bad,
                                   _context(seg, text)))
                continue
            if keep:
                continue
            body = markup.strip_placeholders(text).strip()
            short = len(body) <= 3 and " " not in body
            if cfg.require_latin and re.search("[A-Za-z]", seg.text) \
                    and not CJK_RE.search(body) and not short:
                problems.append("%s chunk %d seg %d/%d: not translated%s"
                                % (name, ci, pos, width,
                                   _context(seg, text)))
            missing = spacing.has_missing_space(text)
            if missing:
                problems.append("%s chunk %d seg %d/%d: missing space around Latin, "
                                "e.g. %s%s"
                                % (name, ci, pos, width, missing[:3],
                                   _context(seg, text)))
    return problems
