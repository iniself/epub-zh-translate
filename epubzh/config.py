#!/usr/bin/env python3
"""Configuration loading and project paths."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import tomllib
from urllib.parse import unquote


DEFAULT_CSS = Path(__file__).parent / "defaults" / "zh-overrides.css"
DEFAULT_GUIDE_TEMPLATE = Path(__file__).parent / "defaults" / "guide-template.md"
DEFAULT_TERMS = Path(__file__).parent / "defaults" / "terms.tsv"
DEFAULT_RULECARD = Path(__file__).parent / "defaults" / "rulecard.md"

# Chunk size presets. "large" is the default and keeps the historical chunk
# numbering. "small", "mini" and "tiny" are non-default presets that differ
# only in size, from more context to smaller writes. In every capped preset
# the word limit governs prose and the segment cap governs short-segment
# content such as tables and lists.
PROFILES = {
    "large": {"words_per_chunk": 2600, "max_segments_per_chunk": 0},
    "small": {"words_per_chunk": 1200, "max_segments_per_chunk": 40},
    "mini": {"words_per_chunk": 1000, "max_segments_per_chunk": 25},
    "tiny": {"words_per_chunk": 600, "max_segments_per_chunk": 20},
}
DEFAULT_PROFILE = "large"


def check_profile(value):
    """Return a canonical profile name or raise SystemExit."""
    name = (value or DEFAULT_PROFILE).strip().lower()
    if name not in PROFILES:
        raise SystemExit("unknown profile %r: choose one of %s"
                         % (value, ", ".join(sorted(PROFILES))))
    return name


TRANSLATE_MODES = ("inline", "aligned")


def check_mode(value):
    """Return a canonical translation mode or raise SystemExit."""
    mode = (value or "inline").strip().lower()
    if mode not in TRANSLATE_MODES:
        raise SystemExit("unknown translate mode %r: choose one of %s"
                         % (value, ", ".join(TRANSLATE_MODES)))
    return mode


def _p(base, value):
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


def resolve_path(base, value):
    """Resolve a CLI path against base. Absolute values always win."""
    return _p(Path(base).expanduser(), value)


def relpath_or_abs(path, root):
    """Prefer a path relative to root so book.toml stays portable.

    Falls back to an absolute path when the two locations are on different
    volumes (Windows) or when relpath is not meaningful.
    """
    target = Path(path).expanduser().resolve()
    try:
        return os.path.relpath(str(target), str(Path(root).expanduser().resolve()))
    except ValueError:
        return str(target)


def _as_list(value, default):
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [value]
    return list(value)


@dataclass
class Config:
    path: Path
    root: Path
    source: Path
    source_dir: Path
    work_dir: Path
    output: Path
    title: str
    source_title: str
    author: str
    translator: str
    language: str
    publisher: str
    text_dir: str = "OEBPS/Text"
    extensions: list = field(default_factory=lambda: [".html"])
    skip: set = field(default_factory=set)
    protect_blocks: list = field(default_factory=list)
    profile: str = DEFAULT_PROFILE
    mode: str = "inline"
    words_per_chunk: int = PROFILES[DEFAULT_PROFILE]["words_per_chunk"]
    max_segments_per_chunk: int = PROFILES[DEFAULT_PROFILE]["max_segments_per_chunk"]
    require_latin: bool = True
    spacing: bool = True
    protect_tags: list = field(default_factory=list)
    css: Path | None = None
    stylesheet: str = ""
    toc_labels: dict = field(default_factory=dict)
    contents_file: str = "contents.html"
    raw: dict = field(default_factory=dict)

    # ---- paths ---------------------------------------------------------
    @property
    def segs_dir(self):
        return self.work_dir / "segs"

    @property
    def zh_dir(self):
        return self.work_dir / "zh"

    @property
    def build_dir(self):
        return self.work_dir / "build"

    @property
    def index_file(self):
        return self.segs_dir / "_index.json"

    @property
    def toc_tsv(self):
        return self.work_dir / "toc.tsv"

    @property
    def pre_json(self):
        return self.work_dir / "pre.json"

    @property
    def terms_tsv(self):
        return self.work_dir / "terms.tsv"

    @property
    def guide_file(self):
        return self.work_dir / "TRANSLATION_GUIDE.md"

    @property
    def prompts_dir(self):
        return self.work_dir / "prompts"

    @property
    def style_file(self):
        return self.work_dir / "style.md"

    @property
    def decisions_file(self):
        return self.work_dir / "decisions.md"

    @property
    def introduced_tsv(self):
        return self.work_dir / "introduced.tsv"

    @property
    def oebps(self):
        return self.build_dir / "OEBPS"

    def text_path(self, rel_name):
        return self.source_dir / self.text_dir / rel_name

    def html_files(self):
        base = self.source_dir / self.text_dir
        if not base.is_dir():
            return []
        out = []
        for p in sorted(base.iterdir()):
            if p.is_file() and p.suffix.lower() in self.extensions:
                if p.name in self.skip:
                    continue
                out.append(p)
        return out


def load(config_path="book.toml"):
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit("config not found: %s (run `epubzh init` first)" % path)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    root = path.parent
    book = raw.get("book", {})
    if not book.get("source"):
        raise SystemExit("[book].source is required in %s" % path)
    source = _p(root, book["source"])
    source_dir_value = book.get("source_dir")
    if source_dir_value:
        source_dir = _p(root, source_dir_value)
    elif source.is_dir():
        source_dir = source
    else:
        source_dir = root / "src"

    title = book.get("title", "")
    output_value = book.get("output") or ("out/%s.epub" % (title or "translated"))
    output = _p(root, output_value)

    extract = raw.get("extract", {})
    translate = raw.get("translate", {})
    typography = raw.get("typography", {})
    toc = raw.get("toc", {})
    profile = check_profile(extract.get("profile"))
    mode = check_mode(translate.get("mode"))
    preset = PROFILES[profile]
    css_value = typography.get("css", "default")
    if css_value in (None, "", "none", "false"):
        css = None
    elif css_value == "default":
        css = DEFAULT_CSS
    else:
        css = _p(root, css_value)

    protect = typography.get("protect_tags")
    if protect is None:
        protect = ["pre", "code", "kbd", "samp", "var", "tt"]
    elif isinstance(protect, str):
        protect = [protect]

    cfg = Config(
        path=path,
        root=root,
        source=source,
        source_dir=source_dir,
        work_dir=_p(root, book.get("work", "work")),
        output=output,
        title=title,
        source_title=book.get("source_title", ""),
        author=book.get("author", ""),
        translator=book.get("translator", ""),
        language=book.get("language", "zh-CN"),
        publisher=book.get("publisher", ""),
        text_dir=extract.get("text_dir", "OEBPS/Text"),
        extensions=[e if str(e).startswith(".") else "." + str(e)
                    for e in _as_list(extract.get("extensions"), [".html"])],
        skip=set(_as_list(extract.get("skip"), [])),
        protect_blocks=_as_list(extract.get("protect_blocks"), []),
        profile=profile,
        mode=mode,
        words_per_chunk=int(extract.get("words_per_chunk",
                                        preset["words_per_chunk"])),
        max_segments_per_chunk=int(extract.get("max_segments_per_chunk",
                                               preset["max_segments_per_chunk"])),
        require_latin=bool(extract.get("require_latin", True)),
        spacing=bool(typography.get("spacing", True)),
        protect_tags=list(protect),
        css=css,
        stylesheet=typography.get("stylesheet", ""),
        toc_labels=dict(toc.get("labels", {})),
        contents_file=toc.get("contents_file", "contents.html"),
        raw=raw,
    )
    return cfg


def toml_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def write_default(path, source, title, author="", work="work", output="",
                  source_dir="src", source_title="", profile=DEFAULT_PROFILE,
                  mode="inline"):
    path = Path(path)
    if path.exists():
        raise SystemExit("refusing to overwrite existing config: %s" % path)
    profile = check_profile(profile)
    mode = check_mode(mode)
    preset = PROFILES[profile]
    root = path.parent
    # Store paths relative to the project directory: no home directory in
    # book.toml, and the project stays movable.
    source = relpath_or_abs(source, root)
    source_dir = relpath_or_abs(source_dir, root)
    work = relpath_or_abs(work, root)
    if not output:
        safe = (title or "translated").replace("/", "-")
        output = "out/%s.epub" % safe
    else:
        output = relpath_or_abs(output, root)
    text = """[book]
source = "%s"
source_dir = "%s"
work = "%s"
output = "%s"
title = "%s"
source_title = "%s"
author = "%s"
translator = "AI 辅助翻译"
language = "zh-CN"
publisher = ""

[extract]
text_dir = "OEBPS/Text"
extensions = [".html"]
skip = []
# Elements kept byte-identical during extraction, as "tag" or "tag.class".
# Use this for code blocks a publisher styles without <pre>.
protect_blocks = []
# Chunk preset: "large" (2600 words, no cap), "small" (1200 words, max 40
# segments), "mini" (1000 words, max 25) or "tiny" (600 words, max 20).
# Decide it before `extract`; changing it later renumbers chunks and
# invalidates existing translations.
profile = "%s"
words_per_chunk = %d
max_segments_per_chunk = %d
require_latin = true

[translate]
# Output format for new chunks: "inline" (default) or "aligned".
# Set this before translating the first chunk. Switching later makes existing
# chunks fail validation, because each book is expected to use one format
# from start to finish.
mode = "%s"

[typography]
spacing = true
css = "default"
# Path of the book's own stylesheet, filled in at init from what the book
# actually links (empty when it styles inline). The Chinese typography
# overrides are installed as a separate file, so an empty value is fine.
stylesheet = ""
protect_tags = ["pre", "code", "kbd", "samp", "var", "tt"]

[toc]
contents_file = "contents.html"

[toc.labels]
# Add package-level labels that do not appear in contents.html, for example:
# "copyright" = "版权页"
# "contents" = "目录"
""" % (
        toml_escape(source), toml_escape(source_dir), toml_escape(work),
        toml_escape(output), toml_escape(title), toml_escape(source_title or title),
        toml_escape(author), profile, preset["words_per_chunk"],
        preset["max_segments_per_chunk"], mode,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


KEYS = ("profile", "words_per_chunk", "max_segments_per_chunk")


def _profile_line(key, profile, preset):
    value = {"profile": '"%s"' % profile,
             "words_per_chunk": str(preset["words_per_chunk"]),
             "max_segments_per_chunk": str(preset["max_segments_per_chunk"])}[key]
    return "%s = %s\n" % (key, value)


def detect_extract_layout(cfg):
    """Best guess for (text_dir, extensions) from the unpacked source tree.

    EPUB producers put content documents anywhere: OEBPS/Text, OEBPS/html,
    OPS/xhtml, or the package root. Pick the directory holding the most HTML
    files instead of assuming the EPUB 2 default.
    """
    root = cfg.source_dir
    if not root.is_dir():
        return None
    counts, exts = {}, set()
    for p in root.rglob("*"):
        suffix = p.suffix.lower()
        if not p.is_file() or suffix not in (".html", ".xhtml", ".htm"):
            continue
        exts.add(suffix)
        rel = p.parent.relative_to(root)
        key = "." if str(rel) == "." else rel.as_posix()
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    text_dir = max(counts, key=lambda d: counts[d])
    return text_dir, sorted(exts)


def set_extract_layout(path, text_dir, extensions):
    """Rewrite text_dir and extensions inside the [extract] section."""
    path = Path(path)
    ext_value = "[" + ", ".join('"%s"' % toml_escape(e)
                                for e in extensions) + "]"
    out, in_extract = [], False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_extract = stripped == "[extract]"
        elif in_extract:
            key = stripped.split("=", 1)[0].strip()
            if key == "text_dir":
                line = 'text_dir = "%s"\n' % toml_escape(text_dir)
            elif key == "extensions":
                line = "extensions = %s\n" % ext_value
        out.append(line)
    path.write_text("".join(out), encoding="utf-8")


def detect_stylesheet(cfg):
    """Return the stylesheet the book actually links, or "" when it has none.

    Later links win in CSS, so the last one in the first document that has any
    link is the one worth recording. Inline-only books get an empty value: the
    typography overrides are added as a separate file and do not depend on it.
    """
    root = cfg.source_dir
    if not root.is_dir():
        return ""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".html", ".xhtml", ".htm"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hrefs = re.findall(r'<link\b[^>]*href="([^"]+\.css)"', text, re.IGNORECASE)
        if not hrefs:
            continue
        target = (path.parent / unquote(hrefs[-1])).resolve()
        try:
            return target.relative_to(root.resolve()).as_posix()
        except ValueError:
            return ""
    for opf in sorted(root.rglob("*.opf")):
        text = opf.read_text(encoding="utf-8", errors="ignore")
        for tag in re.findall(r"<(?:\w+:)?item\b[^>]*>", text):
            if "text/css" not in tag.lower():
                continue
            href = re.search(r'href="([^"]+)"', tag)
            if not href:
                continue
            target = (opf.parent / unquote(href.group(1))).resolve()
            try:
                return target.relative_to(root.resolve()).as_posix()
            except ValueError:
                return ""
    return ""


def set_stylesheet(path, stylesheet):
    """Rewrite the stylesheet path inside the [typography] section."""
    path = Path(path)
    out, in_typography = [], False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_typography = stripped == "[typography]"
        elif in_typography and stripped.split("=", 1)[0].strip() == "stylesheet":
            line = 'stylesheet = "%s"\n' % toml_escape(stylesheet)
        out.append(line)
    path.write_text("".join(out), encoding="utf-8")


def set_profile(path, profile):
    """Rewrite the chunk preset inside the [extract] section of book.toml."""
    path = Path(path)
    profile = check_profile(profile)
    preset = PROFILES[profile]
    out, seen = [], set()
    in_extract = extract_seen = False
    insert_at = None
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_extract and insert_at is None:
                insert_at = len(out)
            in_extract = stripped == "[extract]"
            if in_extract:
                extract_seen = True
        if in_extract:
            key = stripped.split("=", 1)[0].strip()
            if key in KEYS:
                seen.add(key)
                indent = line[:len(line) - len(line.lstrip())]
                out.append(indent + _profile_line(key, profile, preset))
                continue
        out.append(line)
    if in_extract and insert_at is None:
        insert_at = len(out)
    missing = [k for k in KEYS if k not in seen]
    if not extract_seen:
        out.append("\n[extract]\n")
        for key in KEYS:
            out.append(_profile_line(key, profile, preset))
    elif missing:
        block = [_profile_line(k, profile, preset) for k in missing]
        if insert_at < len(out) and out[insert_at].lstrip().startswith("["):
            block.append("\n")
        out[insert_at:insert_at] = block
    path.write_text("".join(out), encoding="utf-8")
    return profile
