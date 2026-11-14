"""Tests for the glossary bootstrap prompt and the candidate import."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from epubzh import guide


def make_cfg(root):
    work = root / "work"
    cfg = SimpleNamespace(
        title="中文书名", source_title="Original Title", author="Author",
        work_dir=work, segs_dir=work / "segs", prompts_dir=work / "prompts",
        terms_tsv=work / "terms.tsv", source_dir=root / "src",
        text_dir="OEBPS", protect_blocks=[], require_latin=True,
        words_per_chunk=1200, max_segments_per_chunk=40)
    work.mkdir(parents=True, exist_ok=True)
    cfg.terms_tsv.write_text("English\t中文\n", encoding="utf-8")
    return cfg


class BootstrapPromptTest(unittest.TestCase):
    def test_lists_the_opening_documents_and_the_output_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            index = {"a.xhtml": {"chunks": 2, "chunk_segments": [3, 4]},
                     "b.xhtml": {"chunks": 1, "chunk_segments": [5]}}
            with mock.patch.object(guide.chunks, "read_index",
                                   return_value=index), \
                    mock.patch.object(guide, "_spine_order", return_value={}):
                path = guide.bootstrap_prompt(cfg)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(path, cfg.prompts_dir / "bootstrap.md")
            self.assertIn("中文书名", text)
            self.assertIn("a.01.txt", text)
            self.assertIn("共 2 块", text)
            self.assertIn("terms.candidates.tsv", text)
            self.assertIn("style-brief.md", text)
            self.assertIn("不要贴原文", text)

    def test_caps_the_document_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            index = {"f%02d.xhtml" % i: {"chunks": 1, "chunk_segments": [2]}
                     for i in range(30)}
            with mock.patch.object(guide.chunks, "read_index",
                                   return_value=index), \
                    mock.patch.object(guide, "_spine_order", return_value={}):
                text = guide.bootstrap_prompt(cfg).read_text(encoding="utf-8")
            self.assertIn("f11.01.txt", text)
            self.assertNotIn("f12.01.txt", text)


class TermsImportTest(unittest.TestCase):
    def write_candidates(self, cfg, text):
        path = cfg.work_dir / "terms.candidates.tsv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_import_validates_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.terms_tsv.write_text("English\t中文\nOpenAI\tOpenAI\tkeep\n",
                                     encoding="utf-8")
            self.write_candidates(cfg, (
                "English\t中文\t备注\n"
                "Sam Altman\t萨姆·奥尔特曼\t\n"
                "openai\tOpenAI\tkeep\n"
                "ChatGPT\tChatGPT\tkeep\n"
                "Really? no.\tReally? no.\tkeep\n"
                "Sora\t\n"))
            added, skipped = guide.import_terms(cfg)
            self.assertEqual([row[0] for row in added],
                             ["Sam Altman", "ChatGPT"])
            self.assertEqual(len(skipped), 3)
            text = cfg.terms_tsv.read_text(encoding="utf-8")
            self.assertIn("Sam Altman\t萨姆·奥尔特曼", text)
            self.assertIn("ChatGPT\tChatGPT\tkeep", text)
            self.assertEqual(text.count("\nOpenAI\t"), 1)
            self.assertNotIn("Sora", text)

    def test_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            self.write_candidates(cfg, "Sam Altman\t萨姆·奥尔特曼\n")
            guide.import_terms(cfg)
            added, skipped = guide.import_terms(cfg)
            self.assertEqual(added, [])
            self.assertEqual(len(skipped), 1)
            self.assertEqual(
                cfg.terms_tsv.read_text(encoding="utf-8").count("Sam Altman"), 1)

    def test_missing_candidates_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            with self.assertRaises(SystemExit) as raised:
                guide.import_terms(cfg)
            self.assertIn("bootstrap", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
