#!/usr/bin/env python3
"""Assemble the translated EPUB tree, validate it, and package the result."""
import os
import posixpath
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import chunks, markup, pre, spacing, toc


def _well_formed(text):
    try:
        ET.fromstring(text.encode("utf-8"))
        return True
    except ET.ParseError:
        return False


def normalize_sources(cfg):
    """Escape bare '>' in the unpacked source documents (idempotent).

    A literal '>' in a text run is legal XML, but the extractor renders it as
    "&gt;", so the byte-exact round trip fails and extraction is refused.
    Escaping it once, in src/, is XML-equivalent and fixes the whole project.
    Only the documents the pipeline translates are touched; <pre> and
    ``protect_blocks`` content is copied verbatim and is left untouched.
    """
    total = touched = 0
    for path in cfg.html_files():
        original = path.read_text(encoding="utf-8")
        fixed, replaced = markup.normalize_source(original, cfg.protect_blocks)
        if not replaced:
            continue
        if not _well_formed(fixed):
            raise SystemExit(
                "refusing to write %s: document is not well-formed XML "
                "(the pipeline needs well-formed documents)" % path.name)
        path.write_text(fixed, encoding="utf-8")
        total += replaced
        touched += 1
    if touched:
        print("normalize: escaped %d bare '>' in %d file(s)" % (total, touched))
    return total


def unpack(cfg, force=False):
    """Extract the source EPUB into source_dir when source is an archive."""
    if cfg.source.is_dir():
        print("source is already a directory: %s" % cfg.source)
        normalize_sources(cfg)
        return cfg.source
    if not cfg.source.exists():
        raise SystemExit("source not found: %s" % cfg.source)
    if cfg.source_dir.exists() and any(cfg.source_dir.iterdir()):
        if not force:
            print("source_dir already populated: %s (use --force to replace)"
                  % cfg.source_dir)
            normalize_sources(cfg)
            return cfg.source_dir
        shutil.rmtree(cfg.source_dir)
    cfg.source_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cfg.source) as z:
        z.extractall(cfg.source_dir)
    print("unpacked %s -> %s" % (cfg.source, cfg.source_dir))
    normalize_sources(cfg)
    return cfg.source_dir


def _merge_file(cfg, name):
    src_path = cfg.source_dir / cfg.text_dir / name
    source = src_path.read_text(encoding="utf-8")
    work, atoms = markup.atomize(source, cfg.protect_blocks)
    segs = markup.extract_segments(work, require_latin=cfg.require_latin)
    chunk_list = markup.chunk_segments(segs, cfg.words_per_chunk,
                                       cfg.max_segments_per_chunk)
    translations = []
    verbatim_flags = []
    stem = Path(name).stem
    for ci, chunk in enumerate(chunk_list, 1):
        path = cfg.zh_dir / chunks.chunk_name(stem, ci)
        translated, verbatim, err = chunks.parse_translation(
            path, len(chunk), chunk)
        if err:
            return None, "chunk %d: %s" % (ci, err), 0
        for seg, text in zip(chunk, translated):
            bad = seg.check(text)
            if bad:
                return None, "chunk %d seg %d: %s" % (ci, seg.index + 1, bad), 0
        translations.extend(translated)
        verbatim_flags.extend(verbatim)
    merged = markup.apply_segments(work, segs, translations)
    if cfg.spacing:
        merged = spacing.spacing(merged, cfg.protect_tags)
    merged = markup.detatomize(merged, atoms)
    target = cfg.build_dir / cfg.text_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(merged, encoding="utf-8")
    untranslated = 0
    for seg, text, keep in zip(segs, translations, verbatim_flags):
        if keep:
            continue
        body = markup.strip_placeholders(text).strip()
        short = len(body) <= 3 and " " not in body
        if not short and re.search("[A-Za-z]", body) and not re.search(
                "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", body):
            untranslated += 1
    return len(segs), None, untranslated


def assemble(cfg, only=None):
    """Rebuild work/build from the source tree and translated chunks."""
    if not cfg.source_dir.exists():
        raise SystemExit("source_dir not found: %s (run `epubzh unpack`)"
                         % cfg.source_dir)
    index = chunks.read_index(cfg)
    shutil.rmtree(cfg.build_dir, ignore_errors=True)
    cfg.build_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cfg.source_dir, cfg.build_dir)

    report, errors = [], []
    for name in sorted(index):
        if only and only not in name:
            continue
        segs, err, untranslated = _merge_file(cfg, name)
        if err:
            errors.append((name, err))
            report.append((name, "FAIL " + err))
        else:
            note = "" if not untranslated else " untranslated=%d" % untranslated
            report.append((name, "ok segs=%d%s" % (segs, note)))

    for name, status in report:
        print("%-38s %s" % (name, status))
    if only:
        print("\npartial merge (only matched %r); other files remain in source language" % only)
    print("\n%d/%d files merged" % (len(report) - len(errors), len(report)))
    return errors


def install_css(cfg):
    if cfg.css is None:
        return
    css_path = Path(cfg.css)
    if not css_path.exists():
        print("css: missing %s, skipped" % css_path)
        return
    stylesheet = cfg.build_dir / cfg.stylesheet
    if not stylesheet.exists():
        print("css: stylesheet not found: %s" % stylesheet)
        return
    base = stylesheet.read_text(encoding="utf-8")
    extra = css_path.read_text(encoding="utf-8")
    marker = "/* epubzh typography overrides */"
    if marker in base:
        base = base.split(marker)[0].rstrip() + "\n"
    stylesheet.write_text(base + "\n" + marker + "\n" + extra, encoding="utf-8")
    print("css: installed %s" % css_path.name)


def _all_docs(root):
    docs = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".html", ".xhtml"):
            docs[str(path.relative_to(root))] = path
    return docs


def _ids(text):
    return set(re.findall(r"\bid=\"([^\"]+)\"", text))


def validate_tree(cfg):
    root = cfg.build_dir
    problems = []
    docs = _all_docs(root)
    ids = {}
    contents = {}
    for name, path in docs.items():
        text = path.read_text(encoding="utf-8")
        contents[name] = text
        ids[name] = _ids(text)
        try:
            ET.fromstring(text.encode("utf-8"))
        except ET.ParseError as exc:
            problems.append("%s: XML error: %s" % (name, exc))
        hits = markup.PH_RE.findall(text)
        stray = markup.STRAY_RE.findall(markup.PH_RE.sub("", text))
        if hits or stray:
            problems.append("%s: leftover placeholders=%d stray=%d"
                            % (name, len(hits), len(stray)))

    files = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    for name, text in contents.items():
        for attr, value in re.findall(r"\b(href|src)=\"([^\"]+)\"", text):
            if value.startswith(("http://", "https://", "mailto:", "tel:",
                                 "data:", "javascript:", "#")):
                if value.startswith("#") and value[1:] not in ids[name]:
                    problems.append("%s: missing local anchor %s" % (name, value))
                continue
            if value.startswith("epubcfi("):
                continue
            target, _, frag = value.partition("#")
            if not target:
                target = name
            else:
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), target))
            if target not in files:
                problems.append("%s: missing target %s" % (name, value))
            elif frag and target in ids and frag not in ids[target]:
                problems.append("%s: missing anchor %s" % (name, value))

    for path in list(root.rglob("*.opf")) + list(root.rglob("*.ncx")):
        try:
            ET.fromstring(path.read_bytes())
        except ET.ParseError as exc:
            problems.append("%s: XML error: %s" % (path.relative_to(root), exc))

    print("validate: %d document(s), %d problem(s)" % (len(docs), len(problems)))
    for p in problems[:40]:
        print("  !!", p)
    if len(problems) > 40:
        print("  ... %d more" % (len(problems) - 40))
    return problems


def package(cfg):
    out = cfg.output
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    mimetype = cfg.build_dir / "mimetype"
    if not mimetype.exists():
        mimetype.write_text("application/epub+zip", encoding="ascii")
    with zipfile.ZipFile(out, "w") as z:
        z.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for base, _, names in os.walk(cfg.build_dir):
            for name in sorted(names):
                full = Path(base) / name
                rel = full.relative_to(cfg.build_dir).as_posix()
                if rel == "mimetype" or rel.startswith("."):
                    continue
                z.write(full, rel, compress_type=zipfile.ZIP_DEFLATED)
    print("epub: %s (%.1f MB)" % (out, out.stat().st_size / 1e6))
    with zipfile.ZipFile(out) as z:
        first = z.infolist()[0]
        print("epub: first entry=%s stored=%s" % (first.filename,
                                                  first.compress_type == 0))
    return out


def verify_epub(path):
    path = Path(path)
    if not path.exists():
        raise SystemExit("epub not found: %s" % path)
    problems = []
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if not infos or infos[0].filename != "mimetype":
            problems.append("mimetype is not the first entry")
        elif infos[0].compress_type != zipfile.ZIP_STORED:
            problems.append("mimetype is compressed")
        try:
            if z.read("mimetype") != b"application/epub+zip":
                problems.append("wrong mimetype content")
        except KeyError:
            problems.append("mimetype entry is missing")
        names = set(z.namelist())
        docs, ids, contents = {}, {}, {}
        for name in names:
            if name.lower().endswith((".html", ".xhtml")):
                text = z.read(name).decode("utf-8", "replace")
                docs[name] = text
                ids[name] = _ids(text)
                contents[name] = text
                try:
                    ET.fromstring(text.encode("utf-8"))
                except ET.ParseError as exc:
                    problems.append("%s: XML error: %s" % (name, exc))
                if markup.PH_RE.search(text) or markup.STRAY_RE.search(
                        markup.PH_RE.sub("", text)):
                    problems.append("%s: leftover placeholder" % name)
        for name in names:
            if name.lower().endswith((".opf", ".ncx")):
                try:
                    ET.fromstring(z.read(name))
                except ET.ParseError as exc:
                    problems.append("%s: XML error: %s" % (name, exc))
        for name, text in contents.items():
            for value in re.findall(r"\b(?:href|src)=\"([^\"]+)\"", text):
                if value.startswith(("#", "http://", "https://", "mailto:",
                                     "tel:", "data:", "javascript:", "epubcfi(")):
                    if value.startswith("#") and value[1:] not in ids[name]:
                        problems.append("%s: missing anchor %s" % (name, value))
                    continue
                target, _, frag = value.partition("#")
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), target)) if target else name
                if target not in names:
                    problems.append("%s: missing target %s" % (name, value))
                elif frag and target in ids and frag not in ids[target]:
                    problems.append("%s: missing anchor %s" % (name, value))
    print("verify: %d entries, %d problem(s)" % (len(infos), len(problems)))
    for p in problems[:40]:
        print("  !!", p)
    return problems


def apply_metadata(cfg):
    toc.localize_toc(cfg)
    toc.localize_nav(cfg)
    toc.localize_metadata(cfg)
    pre.apply(cfg)


def build(cfg, only=None, do_package=True):
    errors = assemble(cfg, only=only)
    apply_metadata(cfg)
    install_css(cfg)
    problems = validate_tree(cfg)
    if errors or problems:
        print("\nbuild stopped: fix the problems above")
        return False
    if do_package and not only:
        package(cfg)
    elif only:
        print("partial build: package skipped")
    return True
