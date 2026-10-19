#!/usr/bin/env python3
"""Table of contents and package metadata localization."""
import csv
import html
import re
from pathlib import Path
from xml.sax.saxutils import escape

from . import markup

NAV_ITEM_RE = re.compile(r"<item\b[^>]*\bproperties=\"[^\"]*\bnav\b[^\"]*\"[^>]*>")
HREF_RE = re.compile(r"\bhref=\"([^\"]+)\"")
NAV_BLOCK_RE = re.compile(r"<nav\b([^>]*)>(.*?)</nav>", re.S)
NAV_ANCHOR_RE = re.compile(r"(<a\b[^>]*>)(.*?)(</a>)", re.S)
NAV_H1_RE = re.compile(r"(<h1\b[^>]*>)(.*?)(</h1>)", re.S)
PAGE_LIST_RE = re.compile(r"epub:type=\"page-list\"")


def find_ncx(cfg):
    direct = cfg.source_dir / "toc.ncx"
    if direct.exists():
        return direct
    for p in cfg.source_dir.rglob("toc.ncx"):
        return p
    return None


def find_opf(cfg):
    for p in cfg.source_dir.glob("*.opf"):
        return p
    for p in cfg.source_dir.rglob("*.opf"):
        return p
    return None


def find_nav(cfg):
    """Locate the EPUB3 navigation document declared in the OPF manifest."""
    opf = find_opf(cfg)
    if not opf:
        return None
    text = Path(opf).read_text(encoding="utf-8")
    for item in NAV_ITEM_RE.findall(text):
        m = HREF_RE.search(item)
        if not m:
            continue
        target = opf.parent / html.unescape(m.group(1))
        if target.exists():
            return target
    return None


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def nav_labels(path):
    text = Path(path).read_text(encoding="utf-8")
    return [_clean(m.group(1)) for m in
            re.finditer(r"<navLabel>\s*<text>(.*?)</text>", text, re.S)]


def navigation_labels(cfg):
    """Labels of the EPUB3 navigation document (page-list entries excluded).

    The page list holds print page numbers; they must stay verbatim.
    """
    path = find_nav(cfg)
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8")
    labels = [_clean(m.group(2)) for m in NAV_H1_RE.finditer(text)]
    for attrs, body in NAV_BLOCK_RE.findall(text):
        if PAGE_LIST_RE.search(attrs):
            continue
        labels.extend(_clean(m.group(2)) for m in NAV_ANCHOR_RE.finditer(body))
    return [label for label in labels if label]


def write_toc_tsv(cfg):
    """Create or refresh work/toc.tsv, preserving manual translations."""
    ncx = find_ncx(cfg)
    labels = nav_labels(ncx) if ncx else []
    labels.extend(navigation_labels(cfg))
    if not labels:
        print("no toc.ncx or EPUB3 navigation document, skipping toc.tsv")
        return None
    existing = {}
    if cfg.toc_tsv.exists():
        with open(cfg.toc_tsv, encoding="utf-8", newline="") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) >= 2:
                    existing[_clean(row[0])] = row[1].strip()
    seen, rows = set(), []
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        zh = existing.get(label, "") or cfg.toc_labels.get(label, "")
        rows.append((label, zh))
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.toc_tsv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["label_en", "label_zh"])
        writer.writerows(rows)
    empty = sum(1 for _, zh in rows if not zh)
    print("wrote %s (%d labels, %d untranslated)" % (cfg.toc_tsv, len(rows), empty))
    return cfg.toc_tsv


def _block_texts(path, tag):
    text = Path(path).read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r"<%s\b[^>]*>(.*?)</%s>" % (tag, tag), text, re.S):
        value = re.sub(r"\s+", " ", html.unescape(
            re.sub(r"<[^>]+>", "", m.group(1)))).strip()
        out.append(value)
    return out


def contents_pairs(cfg):
    """Map English contents entries to their translated counterparts."""
    source = cfg.source_dir / cfg.text_dir / cfg.contents_file
    target = cfg.build_dir / cfg.text_dir / cfg.contents_file
    if not source.exists() or not target.exists():
        return {}
    for tag in ("p", "li", "div"):
        en = _block_texts(source, tag)
        zh = _block_texts(target, tag)
        if en and len(en) == len(zh):
            return dict(zip(en, zh))
    return {}


def load_toc_map(cfg):
    mapping = {}
    if cfg.toc_tsv.exists():
        with open(cfg.toc_tsv, encoding="utf-8", newline="") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) >= 2 and row[0].strip() and row[1].strip():
                    mapping[_clean(row[0])] = row[1].strip()
    return mapping


def localize_toc(cfg):
    ncx = find_ncx(cfg)
    if not ncx:
        print("toc: no toc.ncx, skipped")
        return []
    target = cfg.build_dir / ncx.relative_to(cfg.source_dir)
    text = target.read_text(encoding="utf-8")
    mapping = contents_pairs(cfg)
    mapping.update(cfg.toc_labels)
    mapping.update(load_toc_map(cfg))

    missing = []

    def repl(m):
        label = _clean(m.group(2))
        new = mapping.get(label)
        if not new:
            missing.append(label)
            new = label
        return "%s<text>%s</text>%s" % (m.group(1), escape(new), m.group(3))

    text = re.sub(r"(<navLabel>\s*)<text>(.*?)</text>(\s*</navLabel>)",
                  repl, text, flags=re.S)
    text = re.sub(r"(<docTitle>\s*)<text>.*?</text>(\s*</docTitle>)",
                  lambda m: "%s<text>%s</text>%s"
                  % (m.group(1), escape(cfg.title), m.group(2)),
                  text, count=1, flags=re.S)
    text = text.replace('xml:lang="eng"', 'xml:lang="%s"' % cfg.language)
    target.write_text(text, encoding="utf-8")
    if missing:
        print("toc: %d labels left untranslated (fill %s)" % (len(missing), cfg.toc_tsv))
    else:
        print("toc: all labels localized")
    return missing


def localize_nav(cfg):
    """Localize the EPUB3 navigation document with the toc.tsv mapping."""
    path = find_nav(cfg)
    if not path:
        print("nav: no EPUB3 navigation document, skipped")
        return []
    target = cfg.build_dir / path.relative_to(cfg.source_dir)
    if not target.exists():
        print("nav: %s missing from the build, skipped" % path.name)
        return []
    text = target.read_text(encoding="utf-8")
    mapping = contents_pairs(cfg)
    mapping.update(cfg.toc_labels)
    mapping.update(load_toc_map(cfg))
    missing = []

    def translated(raw):
        label = _clean(raw)
        new = mapping.get(label)
        if not new:
            if label:
                missing.append(label)
            return None
        return escape(new)

    def repl_h1(m):
        new = translated(m.group(2))
        return m.group(1) + (new if new else m.group(2)) + m.group(3)

    def repl_nav(m):
        attrs, body = m.group(1), m.group(2)
        if PAGE_LIST_RE.search(attrs):
            return m.group(0)

        def repl_a(am):
            new = translated(am.group(2))
            return am.group(1) + (new if new else am.group(2)) + am.group(3)

        return "<nav%s>%s</nav>" % (attrs, NAV_ANCHOR_RE.sub(repl_a, body))

    text = NAV_H1_RE.sub(repl_h1, text)
    text = NAV_BLOCK_RE.sub(repl_nav, text)
    target.write_text(text, encoding="utf-8")
    unique = sorted(set(missing))
    if unique:
        print("nav: %d labels left untranslated (fill %s)"
              % (len(unique), cfg.toc_tsv))
    else:
        print("nav: all labels localized")
    return unique


def localize_metadata(cfg):
    opf = find_opf(cfg)
    if not opf:
        print("metadata: no .opf file, skipped")
        return
    target = cfg.build_dir / opf.relative_to(cfg.source_dir)
    text = target.read_text(encoding="utf-8")
    if cfg.title:
        text, n = re.subn(r"(<dc:title\b[^>]*>).*?(</dc:title>)",
                          lambda m: m.group(1) + escape(cfg.title) + m.group(2),
                          text, count=1, flags=re.S)
        if not n:
            text = re.sub(r"(<metadata[^>]*>)",
                          r"\1\n    <dc:title>%s</dc:title>" % escape(cfg.title),
                          text, count=1)
    text = re.sub(r"(<dc:language\b[^>]*>).*?(</dc:language>)",
                  lambda m: m.group(1) + escape(cfg.language) + m.group(2),
                  text, count=1, flags=re.S)
    if cfg.publisher:
        text = re.sub(r"(<dc:publisher\b[^>]*>).*?(</dc:publisher>)",
                      lambda m: m.group(1) + escape(cfg.publisher) + m.group(2),
                      text, count=1, flags=re.S)
    if cfg.translator and "dc:contributor" not in text:
        text = re.sub(r"(<dc:language\b[^>]*>.*?</dc:language>)",
                      r'\1\n    <dc:contributor id="pub-translator">%s'
                      r'</dc:contributor>\n    <meta refines="#pub-translator"'
                      r' property="role" scheme="marc:relators">trl</meta>'
                      % escape(cfg.translator), text, count=1, flags=re.S)
    target.write_text(text, encoding="utf-8")
    print("metadata: updated")
