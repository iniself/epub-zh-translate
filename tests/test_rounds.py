"""Tests for repair-round bookkeeping."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from epubzh import rounds


def make_cfg(root):
    (root / "work").mkdir()
    (root / "zh").mkdir()
    return SimpleNamespace(work_dir=root / "work", zh_dir=root / "zh")


class RoundsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.path = self.cfg.zh_dir / "c.01.txt"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def update(self, problems):
        return rounds.update(self.cfg, "c.xhtml", 1, self.path, problems)

    def test_first_check_counts_one_round(self):
        self.write("a")
        state = self.update(["problem"])
        self.assertEqual(state["rounds"], 1)
        self.assertFalse(state["exhausted"])

    def test_first_stall_exhausts_the_chunk(self):
        self.write("a")
        self.update(["p"])
        state = self.update(["p"])
        self.assertEqual(state["stalled"], 1)
        self.assertTrue(state["exhausted"])

    def test_mark_exhausted_forces_the_state(self):
        self.write("a")
        rounds.mark_exhausted(self.cfg, "c.xhtml", 1)
        state = rounds.get(self.cfg, "c.xhtml", 1)
        self.assertTrue(state["exhausted"])
        state = self.update(["p"])
        self.assertTrue(state["exhausted"])
        self.assertTrue(state["forced"])

    def test_edit_with_fewer_problems_resets_the_stall(self):
        self.write("a")
        self.update(["p1", "p2", "p3"])
        self.assertEqual(self.update(["p1", "p2", "p3"])["stalled"], 1)
        self.write("b")
        state = self.update(["p1", "p2"])
        self.assertEqual(state["rounds"], 2)
        self.assertEqual(state["stalled"], 0)
        self.assertFalse(state["exhausted"])

    def test_six_real_rounds_exhaust_the_chunk(self):
        state = None
        for i in range(1, 7):
            self.write("version %d" % i)
            state = self.update(["p"] * (10 - i))
        self.assertEqual(state["rounds"], 6)
        self.assertTrue(state["exhausted"])

    def test_pass_clears_the_state_file(self):
        self.write("a")
        self.update(["p"])
        self.update([])
        self.assertEqual(rounds.get(self.cfg, "c.xhtml", 1), {})
        self.assertFalse(rounds.path_for(self.cfg).exists())

    def test_clear_removes_one_chunk(self):
        self.write("a")
        self.update(["p"])
        rounds.clear(self.cfg, "c.xhtml", 1)
        self.assertEqual(rounds.get(self.cfg, "c.xhtml", 1), {})

    def test_placeholder_only_classification(self):
        self.assertTrue(rounds.is_placeholder_only(
            ["placeholder mismatch: missing=['⟦0⟧']"]))
        self.assertTrue(rounds.is_placeholder_only(["stray placeholder bracket"]))
        self.assertFalse(rounds.is_placeholder_only(
            ["not translated: Hello world"]))
        self.assertFalse(rounds.is_placeholder_only([]))


if __name__ == "__main__":
    unittest.main()
