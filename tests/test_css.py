"""Tests for the Chinese typography stylesheet installation."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from epubzh import assemble, config

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c1" href="xhtml/a.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
  </spine>
</package>
"""

DOC = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<title>Chapter</title>
<link rel="stylesheet" type="text/css" href="../css/publisher.css"/>
</head>
<body><p>text</p></body>
</html>
"""


def make_tree():
    root = Path(tempfile.mkdtemp()) / "build"
    opf = root / "OEBPS" / "book.opf"
    opf.parent.mkdir(parents=True)
    opf.write_text(OPF, encoding="utf-8")
    docs = root / "OEBPS" / "xhtml"
    docs.mkdir()
    (docs / "a.xhtml").write_text(DOC, encoding="utf-8")
    sub = docs / "sub"
    sub.mkdir()
    (sub / "b.xhtml").write_text(DOC, encoding="utf-8")
    css = root / "OEBPS" / "css"
    css.mkdir()
    (css / "publisher.css").write_text("body { margin: 0 }", encoding="utf-8")
    overrides = Path(tempfile.mkdtemp()) / "zh-overrides.css"
    overrides.write_text("body { line-height: 1.75 }", encoding="utf-8")
    cfg = SimpleNamespace(build_dir=root, css=overrides,
                          stylesheet="OEBPS/css/publisher.css")
    return cfg, root


class InstallCssTest(unittest.TestCase):
    def test_overrides_are_a_separate_file_linked_everywhere(self):
        cfg, root = make_tree()
        assemble.install_css(cfg)
        target = root / "OEBPS" / "css" / "epubzh-zh.css"
        self.assertTrue(target.exists())
        self.assertIn("line-height", target.read_text(encoding="utf-8"))
        for doc in (root / "OEBPS" / "xhtml" / "a.xhtml",
                    root / "OEBPS" / "xhtml" / "sub" / "b.xhtml"):
            text = doc.read_text(encoding="utf-8")
            self.assertEqual(text.count('data-epubzh="zh"'), 1)
        # the publisher stylesheet is untouched
        self.assertEqual(
            (root / "OEBPS" / "css" / "publisher.css").read_text(encoding="utf-8"),
            "body { margin: 0 }")
        opf = (root / "OEBPS" / "book.opf").read_text(encoding="utf-8")
        self.assertIn('id="epubzh-zh-css"', opf)
        self.assertEqual(opf.count('id="epubzh-zh-css"'), 1)

    def test_running_twice_does_not_duplicate_links_or_items(self):
        cfg, root = make_tree()
        assemble.install_css(cfg)
        assemble.install_css(cfg)
        text = (root / "OEBPS" / "xhtml" / "a.xhtml").read_text(encoding="utf-8")
        self.assertEqual(text.count('data-epubzh="zh"'), 1)
        opf = (root / "OEBPS" / "book.opf").read_text(encoding="utf-8")
        self.assertEqual(opf.count('id="epubzh-zh-css"'), 1)

    def test_inline_only_book_still_gets_the_stylesheet(self):
        root = Path(tempfile.mkdtemp()) / "build"
        opf = root / "OEBPS" / "book.opf"
        opf.parent.mkdir(parents=True)
        opf.write_text(OPF, encoding="utf-8")
        docs = root / "OEBPS" / "xhtml"
        docs.mkdir()
        (docs / "a.xhtml").write_text(
            '<html><head><title>x</title><style>p{color:red}</style></head>'
            "<body><p>x</p></body></html>", encoding="utf-8")
        overrides = Path(tempfile.mkdtemp()) / "zh-overrides.css"
        overrides.write_text("body { line-height: 1.75 }", encoding="utf-8")
        cfg = SimpleNamespace(build_dir=root, css=overrides, stylesheet="")
        assemble.install_css(cfg)
        self.assertTrue((root / "OEBPS" / "epubzh-zh.css").exists())
        text = (docs / "a.xhtml").read_text(encoding="utf-8")
        self.assertIn('href="../epubzh-zh.css"', text)


class DetectStylesheetTest(unittest.TestCase):
    def make_source(self, docs):
        root = Path(tempfile.mkdtemp()) / "src"
        (root / "OEBPS" / "xhtml").mkdir(parents=True)
        for name, text in docs.items():
            (root / "OEBPS" / "xhtml" / name).write_text(text, encoding="utf-8")
        return SimpleNamespace(source_dir=root)

    def test_last_link_wins(self):
        doc = ('<html><head>'
               '<link rel="stylesheet" href="../css/base.css"/>'
               '<link rel="stylesheet" href="../css/main.css"/>'
               '</head><body/></html>')
        cfg = self.make_source({"a.xhtml": doc})
        self.assertEqual(config.detect_stylesheet(cfg),
                         "OEBPS/css/main.css")

    def test_falls_back_to_the_manifest(self):
        cfg = self.make_source({"a.xhtml": "<html><head></head><body/></html>"})
        (cfg.source_dir / "OEBPS" / "book.opf").write_text(
            '<package><manifest>'
            '<item id="s" href="css/only.css" media-type="text/css"/>'
            '</manifest></package>', encoding="utf-8")
        self.assertEqual(config.detect_stylesheet(cfg),
                         "OEBPS/css/only.css")

    def test_inline_only_book_returns_empty(self):
        cfg = self.make_source(
            {"a.xhtml": "<html><head><style>p{}</style></head></html>"})
        self.assertEqual(config.detect_stylesheet(cfg), "")

    def test_set_stylesheet_rewrites_only_the_typography_line(self):
        path = Path(tempfile.mkdtemp()) / "book.toml"
        path.write_text('[extract]\nstylesheet = "keep"\n\n'
                        '[typography]\nspacing = true\nstylesheet = ""\n',
                        encoding="utf-8")
        config.set_stylesheet(path, "OEBPS/css/main.css")
        text = path.read_text(encoding="utf-8")
        self.assertIn('stylesheet = "keep"', text)
        self.assertIn('stylesheet = "OEBPS/css/main.css"', text)


if __name__ == "__main__":
    unittest.main()
