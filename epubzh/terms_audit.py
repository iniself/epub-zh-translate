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


def audit(cfg, only=None, strict=False, limit=20, terms=None):
    """Compare the glossary against translated segments.

    Returns (rows, total_hits, total_checked). ``rows`` is
    [(misses, hits, english, variants, samples)] sorted by misses.
    """
    if terms is None:
        from . import guide
        terms = guide.read_terms(cfg)
    pairs = list(load_pairs(cfg, only=only))
    checked = []
    for en, zh, _note in terms:
        variants = split_variants(zh)
        if not variants:
            continue
        keep = all(v.lower() == en.lower() for v in variants)
        pattern = term_pattern(en)
        hits, misses, samples = 0, 0, []
        for name, ci, num, src, dst in pairs:
            plain = markup.strip_placeholders(src)
            if not pattern.search(plain):
                continue
            if keep:
                ok = en.lower() in dst.lower()
            else:
                ok = any(v in dst for v in variants)
            if ok:
                hits += 1
            else:
                misses += 1
                if len(samples) < 3:
                    samples.append("%s chunk %d seg %d: %s" %
                                   (Path(name).stem, ci, num, dst[:80]))
        if hits + misses >= 5:
            checked.append((misses, hits, en, variants, samples))
    checked.sort(key=lambda r: (-r[0], r[2].lower()))

    total_hits = sum(r[1] for r in checked)
    total_miss = sum(r[0] for r in checked)
    total = total_hits + total_miss
    print("terminology audit: %d term(s), %d segment(s)"
          % (len(checked), total))
    print("  matched %d, missed %d (%.1f%% hit)"
          % (total_hits, total_miss,
             100.0 * total_hits / total if total else 100.0))
    print("")
    print("%-26s %-18s %6s %6s %6s" % ("english", "expected chinese",
                                       "seen", "hit", "miss"))
    for misses, hits, en, variants, _ in checked[:limit]:
        print("%-26s %-18s %6d %6d %6d"
              % (en[:26], "、".join(variants)[:18], hits + misses, hits, misses))
    if checked and checked[0][0]:
        print("\nsamples:")
        for misses, hits, en, variants, samples in checked[:limit]:
            for s in samples:
                print("  !! [%s] 期望「%s」 — %s"
                      % (en, "、".join(variants), s))
    if total_miss:
        print("\nhint: 术语表是约定，不是硬校验。合理变体请补进 %s，"
              "确认漏译再改 work/zh/。" % cfg.terms_tsv)
    return checked, total_hits, total


def refresh_introduced(cfg):
    from . import guide
    values = guide.introduced(cfg, refresh=True)
    print("refreshed %s (%d term(s))" % (cfg.introduced_tsv, len(values)))
    return values
