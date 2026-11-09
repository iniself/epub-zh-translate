#!/usr/bin/env python3
"""Terminology consistency audit for translated chunks."""
import re
from pathlib import Path

from . import chunks, markup


def split_variants(zh):
    """Split a glossary cell into acceptable Chinese renderings.

    Notes after a parenthesis are dropped, and alternatives separated by
    ; ； 、 / | are all accepted.
    """
    body = re.split(r"[（(]", zh, 1)[0]
    parts = re.split(r"[；;、/|]", body)
    return [p.strip() for p in parts if p.strip()]


def term_pattern(term):
    """Regex matching an English term and its common inflections."""
    body = re.escape(term)
    if term.lower().endswith("y"):
        body = re.escape(term[:-1]) + r"(?:y|ies)"
    return re.compile(r"(?<![A-Za-z])" + body + r"(?:s|es|ed|ing)?(?![A-Za-z])",
                      re.IGNORECASE)


def load_pairs(cfg, only=None):
    """Yield (file, chunk, seg, source, translation) for translated segments."""
    index = chunks.read_index(cfg)
    names = [n for n in sorted(index) if not only or only in n]
    for name in names:
        info = index[name]
        stem = Path(name).stem
        counts = chunks.chunk_counts(cfg, name, info)
        for ci, _ in enumerate(counts, 1):
            src = cfg.segs_dir / chunks.chunk_name(stem, ci)
            dst = cfg.zh_dir / chunks.chunk_name(stem, ci)
            if not dst.exists():
                continue
            source = chunks.load_marked(src)
            target = chunks.load_marked(dst)
            if len(source) != len(target):
                continue
            for (num, a, keep_a), (_, b, _) in zip(source, target):
                if keep_a:
                    continue
                yield name, ci, num, a, b


def audit(cfg, only=None, strict=False, limit=20, terms=None,
          min_occurrences=5, quiet=False):
    """Compare the glossary against translated segments.

    Returns (rows, total_hits, total_checked). ``rows`` is
    [(misses, hits, english, variants, samples)] sorted by misses.

    A keep row (English column repeated, or note ``keep``) expects the English
    term to survive in the translation; a translate row expects one of its
    Chinese variants. ``min_occurrences`` drops rare terms to keep the report
    readable; the build gate uses 1 so any registered decision is enforced.
    """
    if terms is None:
        from . import guide
        terms = guide.read_terms(cfg)
    from .guide import keep_term_is_valid, term_is_keep
    pairs = list(load_pairs(cfg, only=only))
    checked = []
    for en, zh, note in terms:
        keep = term_is_keep(en, zh, note)
        invalid_keep = keep and not keep_term_is_valid(en)
        if keep:
            variants = [en]
        else:
            variants = split_variants(zh)
            if not variants:
                continue
        pattern = term_pattern(en)
        hits, misses, samples = 0, 0, []
        for name, ci, num, src, dst in pairs:
            plain = markup.strip_placeholders(src)
            if not pattern.search(plain):
                continue
            if invalid_keep:
                # A whole sentence cannot be exempted from translation.
                ok = False
            elif keep:
                ok = bool(pattern.search(markup.strip_placeholders(dst)))
            else:
                ok = any(v in dst for v in variants)
            if ok:
                hits += 1
            else:
                misses += 1
                if len(samples) < 3:
                    if invalid_keep:
                        expected = "译成中文（keep 行不能是整句）"
                    elif keep:
                        expected = "保留英文"
                    else:
                        expected = "、".join(variants)
                    samples.append("%s chunk %d seg %d: 期望 %s — %s"
                                   % (Path(name).stem, ci, num, expected,
                                      dst[:80]))
        if hits + misses >= min_occurrences or invalid_keep:
            checked.append((misses, hits, en, variants, samples))
    checked.sort(key=lambda r: (-r[0], r[2].lower()))

    total_hits = sum(r[1] for r in checked)
    total_miss = sum(r[0] for r in checked)
    total = total_hits + total_miss
    if not quiet:
        print("terminology audit: %d term(s), %d segment(s)"
              % (len(checked), total))
        print("  matched %d, missed %d (%.1f%% hit)"
              % (total_hits, total_miss,
                 100.0 * total_hits / total if total else 100.0))
        print("")
        print("%-26s %-18s %6s %6s %6s" % ("english", "expected",
                                           "seen", "hit", "miss"))
        for misses, hits, en, variants, _ in checked[:limit]:
            label = variants[0] if variants else ""
            print("%-26s %-18s %6d %6d %6d"
                  % (en[:26], label[:18], hits + misses, hits, misses))
        if checked and checked[0][0]:
            print("\nsamples:")
            for misses, hits, en, variants, samples in checked[:limit]:
                for s in samples:
                    print("  !! [%s] %s" % (en, s))
        if total_miss:
            print("\nhint: 术语表是约定，也是 `build` 的硬门槛；可接受译法写进第二列"
                  "（用 `、` 或 `；` 分隔），确认漏译再改 work/zh/。")
    return checked, total_hits, total


def violations(cfg, only=None):
    """Terms whose registered rendering is missing where the source has them."""
    rows, _, _ = audit(cfg, only=only, quiet=True, min_occurrences=1)
    return [row for row in rows if row[0]]


def refresh_introduced(cfg):
    from . import guide
    values = guide.introduced(cfg, refresh=True)
    print("refreshed %s (%d term(s))" % (cfg.introduced_tsv, len(values)))
    return values
