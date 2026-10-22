"""Regression tests for state-aware chunk prompt generation."""
import unittest
from pathlib import Path
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
