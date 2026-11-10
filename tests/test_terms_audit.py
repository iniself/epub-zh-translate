"""Tests for the glossary audit and the build-time terminology gate."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from epubzh import terms_audit


def make_cfg(terms, pairs):
    """Build a one-chunk project from ``[(source, translation)]``."""
    root = Path(tempfile.mkdtemp())
    segs_dir = root / "segs"
    zh_dir = root / "zh"
    segs_dir.mkdir()
    zh_dir.mkdir()
    source = "".join("@@@SEG %03d@@@\n%s\n" % (i, a)
                     for i, (a, _) in enumerate(pairs, 1))
    target = "".join("@@@SEG %03d@@@\n%s\n" % (i, b)
                     for i, (_, b) in enumerate(pairs, 1))
    (segs_dir / "c.01.txt").write_text(source, encoding="utf-8")
    (zh_dir / "c.01.txt").write_text(target, encoding="utf-8")
    index = {"c.xhtml": {"chunks": 1, "chunk_segments": [len(pairs)]}}
    index_file = root / "index.json"
    index_file.write_text(json.dumps(index), encoding="utf-8")
    terms_tsv = root / "terms.tsv"
    terms_tsv.write_text(terms, encoding="utf-8")
    return SimpleNamespace(index_file=index_file, segs_dir=segs_dir,
                           zh_dir=zh_dir, terms_tsv=terms_tsv)


class TermsAuditTest(unittest.TestCase):
    def test_a_followed_translation_has_no_violation(self):
        cfg = make_cfg("English\t中文\nagent\t智能体\n",
                       [("The agent uses tools.", "智能体使用工具。")])
        self.assertEqual(terms_audit.violations(cfg), [])

    def test_a_different_rendering_is_a_violation(self):
        cfg = make_cfg("English\t中文\nagent\t智能体\n",
                       [("The agent uses tools.", "AI 代理使用工具。")])
        rows = terms_audit.violations(cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "agent")
        self.assertEqual(rows[0][1], 0)

    def test_a_keep_term_still_in_english_is_fine(self):
        cfg = make_cfg("English\t中文\nVIKING\tVIKING\tkeep\n",
                       [("VIKING is a trademark.", "VIKING 是商标。")])
        self.assertEqual(terms_audit.violations(cfg), [])

    def test_a_registered_keep_term_that_was_translated_is_a_violation(self):
        cfg = make_cfg("English\t中文\nVIKING\tVIKING\tkeep\n",
                       [("VIKING is a trademark.", "维京是商标。"),
                        ("VIKING appears twice.", "VIKING 出现了两次。")])
        rows = terms_audit.violations(cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "VIKING")
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[0][1], 1)

    def test_rare_terms_are_hidden_from_the_report_but_not_the_gate(self):
        cfg = make_cfg("English\t中文\nagent\t智能体\n",
                       [("The agent uses tools.", "AI 代理使用工具。")])
        rows, _, total = terms_audit.audit(cfg, quiet=True)
        self.assertEqual(rows, [])
        self.assertEqual(total, 0)
        self.assertEqual(len(terms_audit.violations(cfg)), 1)

    def test_keep_row_with_a_slash_is_not_split(self):
        # Keep rows must not be split on "/" like Chinese variants are.
        cfg = make_cfg("English\t中文\nMCP / Model Context Protocol\t"
                       "MCP / Model Context Protocol\tkeep\n",
                       [("MCP / Model Context Protocol works.",
                         "MCP / Model Context Protocol 可用。")])
        self.assertEqual(terms_audit.violations(cfg), [])


    def test_a_whole_sentence_registered_as_keep_is_a_violation(self):
        sentence = "They saw the pattern and changed their minds."
        cfg = make_cfg("English\t中文\n%s\t%s\tkeep\n" % (sentence, sentence),
                       [(sentence, "他们看到了这种模式，于是改变了主意。")])
        rows = terms_audit.violations(cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], sentence)
        self.assertIn("keep 行不能是整句", rows[0][4][0])

    def test_an_invalid_keep_row_is_reported_even_when_rare(self):
        sentence = "They saw the pattern and changed their minds."
        cfg = make_cfg("English\t中文\n%s\t-\tkeep\n" % sentence,
                       [("Other text.", "其他文本。")])
        rows, _, _ = terms_audit.audit(cfg, quiet=True)
        self.assertEqual([row[2] for row in rows], [sentence])

    def test_a_name_with_initials_is_a_valid_keep_row(self):
        cfg = make_cfg("English\t中文\nGregory M. Walton\t-\tkeep\n",
                       [("Gregory M. Walton wrote it.", "Gregory M. Walton 写了这本书。")])
        self.assertEqual(terms_audit.violations(cfg), [])

    def test_inflected_forms_do_not_require_the_registered_rendering(self):
        # "associates" and "associated" are different words from the registered
        # term; demanding 熟人 in those sentences was a false alarm.
        cfg = make_cfg("English\t中文\nassociate\t熟人\n",
                       [("Junior associates joined.", "初级律师加入了。"),
                        ("The two are associated.", "两者互相关联。"),
                        ("They became associates.", "他们成了合作伙伴。")])
        self.assertEqual(terms_audit.violations(cfg), [])

    def test_the_registered_form_is_still_checked(self):
        cfg = make_cfg("English\t中文\nassociate\t熟人\n",
                       [("He is my associate.", "他是我的同事。"),
                        ("He is my associate.", "他是我的熟人。")])
        rows = terms_audit.violations(cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0][0], rows[0][1]), (1, 1))


if __name__ == "__main__":
    unittest.main()
