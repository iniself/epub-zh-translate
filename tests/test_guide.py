"""Regression tests for state-aware chunk prompt generation."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from epubzh import guide


CFG = SimpleNamespace(path=Path("/tmp/book.toml"))
DST = Path("/tmp/chunk.txt")


def contract(state, last=0, nseg=10):
    return guide._contract(CFG, "chapter", 1, DST, state, last, nseg)


class OpeningTest(unittest.TestCase):
    def test_pending_starts_by_writing(self):
        text = guide._opening("pending", 1, 0)
        self.assertIn("直接写输出文件", text)
        self.assertNotIn("check --chunk", text)

    def test_partial_starts_by_reading_tail_then_appending(self):
        text = guide._opening("partial", 1, 4)
        self.assertIn("read", text)
        self.assertIn("编号 4", text)
        self.assertIn("继续追加", text)
        self.assertNotIn("直接写输出文件", text)

    def test_broken_starts_by_running_check_fix(self):
        text = guide._opening("broken", 1, 0)
        self.assertIn("先运行 `check --chunk 1 --fix`", text)
        self.assertIn("不要先写文件", text)
        self.assertNotIn("直接写输出文件", text)

    def test_repair_starts_by_checking_existing_translation(self):
        text = guide._opening("repair", 1, 0)
        self.assertIn("先运行 `check --chunk 1 --fix`", text)
        self.assertIn("不要整块重写", text)
        self.assertNotIn("直接写输出文件", text)

    def test_done_does_not_rewrite_by_default(self):
        text = guide._opening("done", 1, 0)
        self.assertIn("默认不要重写", text)
        self.assertNotIn("直接写输出文件", text)


class ContractTest(unittest.TestCase):
    def test_pending_writes_whole_chunk_first(self):
        text = contract("pending")
        self.assertIn("第一条动作必须是写输出文件", text)
        self.assertIn("优先一次 `write` 写完整个分块", text)
        self.assertNotIn("第一条动作必须是运行自检修复", text)

    def test_partial_appends_without_rewriting(self):
        text = contract("partial", last=2)
        self.assertIn("第一条动作是 `read` 文件末尾", text)
        self.assertIn("从 `@@@SEG 003@@@` 开始追加", text)
        self.assertNotIn("优先一次 `write` 写完整个分块", text)
        self.assertNotIn("第一条动作必须是写输出文件", text)

    def test_broken_runs_check_fix_before_writing(self):
        text = contract("broken")
        self.assertIn("第一条动作必须是运行自检修复", text)
        self.assertIn("不要先写文件", text)
        self.assertNotIn("优先一次 `write` 写完整个分块", text)
        self.assertNotIn("第一条动作必须是写输出文件", text)
        self.assertIn("正确的 1..10 段", text)

    def test_repair_runs_check_fix_without_overwriting(self):
        text = contract("repair")
        self.assertIn("第一条动作必须是运行自检", text)
        self.assertIn("不要整块重写", text)
        self.assertIn("不要重新编号", text)
        self.assertNotIn("第一条动作必须是写输出文件", text)

    def test_contracts_explain_per_segment_numbering(self):
        for state in ("pending", "repair", "partial", "broken"):
            text = contract(state, last=2)
            self.assertIn("每段独立", text)
            self.assertIn("不要跨段比较", text)

    def test_same_error_stops_after_two_rounds(self):
        text = contract("pending")
        self.assertIn("连续 2 次", text)
        self.assertIn("停止修改", text)


class NextTargetTest(unittest.TestCase):
    def test_skips_done_chunks_and_returns_repair(self):
        index = {"chapter.html": {"chunks": 2}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"))
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1, 1]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  side_effect=[("done", 1), ("repair", 1)]):
            target = guide.next_target(cfg)
        self.assertEqual(target, ("chapter.html", 2, "repair", 1, 1))

    def test_existing_work_wins_over_starting_a_new_chunk(self):
        index = {"chapter.html": {"chunks": 2}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"))
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1, 1]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  side_effect=[("pending", 0), ("repair", 1)]):
            target = guide.next_target(cfg)
        self.assertEqual(target, ("chapter.html", 2, "repair", 1, 1))

    def test_returns_none_when_everything_is_done(self):
        index = {"chapter.html": {"chunks": 1}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"))
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  return_value=("done", 1)):
            self.assertIsNone(guide.next_target(cfg))


if __name__ == "__main__":
    unittest.main()
