"""Tests for the combined build preflight report."""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from epubzh import assemble, terms_audit


def make_cfg():
    return SimpleNamespace(work_dir=Path("/tmp/work"))


def term_row(name="agent"):
    return (1, 0, name, ["智能体"], ["c chunk 1 seg 1: expected 智能体 — 代理"])


def review_row(file="c.xhtml", chunk=1, seg=2):
    return {"file": file, "chunk": chunk, "seg": seg, "kind": "structure",
            "source": "", "translation": "", "detail": "file missing"}


class CombinedBuildReportTest(unittest.TestCase):
    def run_build(self, terms=(), pending=(), errors=(), problems=()):
        cfg = make_cfg()
        out = io.StringIO()
        with mock.patch.object(assemble, "_build_tree",
                               return_value=list(errors)) as tree, \
                mock.patch.object(assemble, "apply_metadata"), \
                mock.patch.object(assemble, "install_css"), \
                mock.patch.object(assemble, "validate_tree",
                                  return_value=list(problems)), \
                mock.patch.object(assemble, "package") as package, \
                mock.patch.object(terms_audit, "violations",
                                  return_value=list(terms)), \
                mock.patch.object(assemble.review, "read",
                                  return_value=list(pending)), \
                redirect_stdout(out):
            ok = assemble.build(cfg)
        return ok, out.getvalue(), tree, package

    def test_terminology_and_review_are_reported_together(self):
        ok, out, tree, package = self.run_build(
            terms=[term_row()], pending=[review_row()])
        self.assertFalse(ok)
        tree.assert_called_once()
        package.assert_not_called()
        self.assertIn("build stopped", out)
        self.assertIn("terminology: 1 term(s)", out)
        self.assertIn("agent", out)
        self.assertIn("manual review: 1 item(s)", out)
        self.assertIn("c.xhtml chunk 1 seg 2: structure", out)

    def test_merge_and_structure_problems_join_the_same_report(self):
        ok, out, _, package = self.run_build(
            errors=[("c.xhtml", "chunk 2: missing segment 3")],
            problems=["c.xhtml: missing target ch2.xhtml"])
        self.assertFalse(ok)
        package.assert_not_called()
        self.assertIn("merge: 1 file(s) failed", out)
        self.assertIn("chunk 2: missing segment 3", out)
        self.assertIn("structure: 1 problem(s)", out)
        self.assertIn("missing target ch2.xhtml", out)

    def test_a_clean_trial_build_packages_the_book(self):
        ok, out, _, package = self.run_build()
        self.assertTrue(ok)
        package.assert_called_once()
        self.assertNotIn("build stopped", out)

    def test_a_structure_problem_still_blocks_packaging(self):
        ok, out, _, package = self.run_build(
            problems=["c.xhtml: XML error: not well-formed"])
        self.assertFalse(ok)
        package.assert_not_called()
        self.assertIn("structure: 1 problem(s)", out)


if __name__ == "__main__":
    unittest.main()
