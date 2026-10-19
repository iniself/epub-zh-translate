#!/usr/bin/env python3
"""Configuration loading and project paths."""
from dataclasses import dataclass, field
from pathlib import Path
import tomllib


DEFAULT_CSS = Path(__file__).parent / "defaults" / "zh-overrides.css"
DEFAULT_GUIDE_TEMPLATE = Path(__file__).parent / "defaults" / "guide-template.md"
DEFAULT_TERMS = Path(__file__).parent / "defaults" / "terms.tsv"


def _p(base, value):
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


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
    words_per_chunk: int = 2600
    require_latin: bool = True
    spacing: bool = True
    protect_tags: list = field(default_factory=list)
    css: Path | None = None
    stylesheet: str = "OEBPS/Styles/stylesheet.css"
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
    typography = raw.get("typography", {})
    toc = raw.get("toc", {})
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
        words_per_chunk=int(extract.get("words_per_chunk", 2600)),
        require_latin=bool(extract.get("require_latin", True)),
        spacing=bool(typography.get("spacing", True)),
        protect_tags=list(protect),
        css=css,
        stylesheet=typography.get("stylesheet", "OEBPS/Styles/stylesheet.css"),
        toc_labels=dict(toc.get("labels", {})),
        contents_file=toc.get("contents_file", "contents.html"),
        raw=raw,
    )
    return cfg


def toml_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def write_default(path, source, title, author="", work="work", output="",
                  source_dir="src", source_title=""):
    path = Path(path)
    if path.exists():
        raise SystemExit("refusing to overwrite existing config: %s" % path)
    if not output:
        safe = (title or "translated").replace("/", "-")
        output = "out/%s.epub" % safe
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
words_per_chunk = 2600
require_latin = true

[typography]
spacing = true
css = "default"
stylesheet = "OEBPS/Styles/stylesheet.css"
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
        toml_escape(author),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
