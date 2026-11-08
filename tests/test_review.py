"""Tests for the manual-review registry."""
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from epubzh import assemble, review


def make_cfg(root):
    text_dir = Path("OEBPS")
    source_dir = root / "src"
    (source_dir / text_dir).mkdir(parents=True)
    source = source_dir / text_dir / "c.xhtml"
    source.write_text("<p><em>Hello</em> world</p>\n", encoding="utf-8")
    index = {"c.xhtml": {"chunks": 1, "sha256": hashlib.sha256(
        source.read_bytes()).hexdigest()}}
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    zh_dir = root / "zh"
    zh_dir.mkdir()
    cfg = SimpleNamespace(
        work_dir=root / "work", index_file=root / "index.json",
        source_dir=source_dir, text_dir=text_dir, protect_blocks=[],
        require_latin=False, words_per_chunk=1200,
        max_segments_per_chunk=40, zh_dir=zh_dir, mode=None, spacing=True,
        build_dir=root / "build")
    cfg.work_dir.mkdir()
    return cfg


def write_chunk(cfg, body):
    (cfg.zh_dir / "c.01.txt").write_text("@@@SEG 001@@@\n%s\n" % body,
                                        encoding="utf-8")


def set_source(cfg, html):
    source = cfg.source_dir / cfg.text_dir / "c.xhtml"
    source.write_text(html, encoding="utf-8")
    index = {"c.xhtml": {"chunks": 1, "sha256": hashlib.sha256(
        source.read_bytes()).hexdigest()}}
    cfg.index_file.write_text(json.dumps(index), encoding="utf-8")


class ReviewRegistryTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg(Path(tempfile.mkdtemp()))

    def test_add_records_source_and_translation(self):
        write_chunk(self.cfg, "⟦0⟧你好 世界")
        rows = review.add(self.cfg, "c", 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "placeholder")
        self.assertIn("Hello", rows[0]["source"])
        self.assertIn("你好", rows[0]["translation"])
        self.assertIn("placeholder", rows[0]["detail"])
        self.assertEqual(review.read(self.cfg), rows)

    def test_recheck_clears_a_fixed_chunk(self):
        write_chunk(self.cfg, "⟦0⟧你好 世界")
        review.add(self.cfg, "c", 1)
        self.assertEqual(len(review.recheck(self.cfg)), 1)
        write_chunk(self.cfg, "⟦0⟧你好⟦/0⟧ 世界")
        self.assertEqual(review.recheck(self.cfg), [])
        self.assertFalse(review.path_for(self.cfg).exists())

    def test_add_replaces_previous_rows_for_the_chunk(self):
        write_chunk(self.cfg, "⟦0⟧你好 世界")
        review.add(self.cfg, "c", 1)
        write_chunk(self.cfg, "⟦0⟧你好⟦/0⟧ 世界")
        review.add(self.cfg, "c", 1)
        self.assertEqual(review.read(self.cfg), [])

    def test_cells_keep_tabs_and_newlines(self):
        row = {"file": "c.xhtml", "chunk": 1, "seg": 2, "kind": "other",
               "source": "a\tb", "translation": "甲\n乙", "detail": "x"}
        review.write(self.cfg, [row])
        self.assertEqual(review.read(self.cfg), [row])

    def test_add_refuses_untranslated_segments(self):
        set_source(self.cfg, "<p>Hello world</p>\n")
        self.cfg.require_latin = True
        write_chunk(self.cfg, "Hello world")
        with self.assertRaises(SystemExit):
            review.add(self.cfg, "c", 1)
        self.assertEqual(review.read(self.cfg), [])

    def test_add_refuses_a_partial_chunk(self):
        set_source(self.cfg,
                   "<p>First paragraph.</p>\n<p>Second paragraph.</p>\n")
        write_chunk(self.cfg, "第一段。")
        with self.assertRaises(SystemExit):
            review.add(self.cfg, "c", 1)
        self.assertEqual(review.read(self.cfg), [])

    def test_add_refuses_placeholder_plus_unfinished_segments(self):
        set_source(self.cfg,
                   "<p><em>Hello</em> world</p>\n<p>Another sentence here</p>\n")
        self.cfg.require_latin = True
        write_chunk(self.cfg,
                    "Hello world\n@@@SEG 002@@@\nAnother sentence here")
        with self.assertRaises(SystemExit):
            review.add(self.cfg, "c", 1)
        self.assertEqual(review.read(self.cfg), [])

    def test_registered_chunks_do_not_count_as_progress(self):
        from epubzh import chunks

        set_source(self.cfg, "<p>Hello world</p>\n")
        write_chunk(self.cfg, "你好世界")
        self.assertEqual(chunks.progress(self.cfg), (1, 1))
        review.write(self.cfg, [{
            "file": "c.xhtml", "chunk": 1, "seg": 1, "kind": "placeholder",
            "source": "s", "translation": "t", "detail": "d"}])
        self.assertEqual(chunks.progress(self.cfg), (0, 1))

    def test_recheck_requeues_untranslated_chunk(self):
        set_source(self.cfg, "<p>Hello world</p>\n")
        self.cfg.require_latin = True
        write_chunk(self.cfg, "Hello world")
        review.write(self.cfg, [{
            "file": "c.xhtml", "chunk": 1, "seg": 1, "kind": "not-translated",
            "source": "Hello world", "translation": "Hello world",
            "detail": "not translated"}])
        out = io.StringIO()
        with redirect_stdout(out):
            remaining = review.recheck(self.cfg)
        self.assertEqual(remaining, [])
        self.assertEqual(review.read(self.cfg), [])
        self.assertIn("requeued", out.getvalue())

    def test_recheck_requeues_whole_chunk_when_any_segment_is_unfinished(self):
        set_source(self.cfg,
                   "<p><em>Hello</em> world</p>\n<p>Another sentence here</p>\n")
        self.cfg.require_latin = True
        write_chunk(self.cfg,
                    "Hello world\n@@@SEG 002@@@\nAnother sentence here")
        review.write(self.cfg, [{
            "file": "c.xhtml", "chunk": 1, "seg": 1, "kind": "placeholder",
            "source": "", "translation": "", "detail": "placeholder mismatch"}])
        with redirect_stdout(io.StringIO()):
            remaining = review.recheck(self.cfg)
        self.assertEqual(remaining, [])
        self.assertFalse(review.path_for(self.cfg).exists())

    def test_recheck_requeues_missing_chunk(self):
        review.write(self.cfg, [{
            "file": "c.xhtml", "chunk": 1, "seg": 1, "kind": "structure",
            "source": "", "translation": "", "detail": "file missing"}])
        with redirect_stdout(io.StringIO()):
            remaining = review.recheck(self.cfg)
        self.assertEqual(remaining, [])
        self.assertFalse(review.path_for(self.cfg).exists())

    def test_assemble_refuses_a_pending_registry(self):
        review.write(self.cfg, [{
            "file": "c.xhtml", "chunk": 1, "seg": 1, "kind": "placeholder",
            "source": "s", "translation": "t", "detail": "d"}])
        with self.assertRaises(SystemExit):
            assemble.assemble(self.cfg)


if __name__ == "__main__":
    unittest.main()
