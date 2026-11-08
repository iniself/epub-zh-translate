"""Tests for the manual-review registry."""
import hashlib
import json
import tempfile
import unittest
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

    def test_assemble_refuses_a_pending_registry(self):
        review.write(self.cfg, [{
            "file": "c.xhtml", "chunk": 1, "seg": 1, "kind": "placeholder",
            "source": "s", "translation": "t", "detail": "d"}])
        with self.assertRaises(SystemExit):
            assemble.assemble(self.cfg)


if __name__ == "__main__":
    unittest.main()
