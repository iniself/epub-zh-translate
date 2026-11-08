"""Tests for shell-safe command hints."""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from epubzh import cli, guide


class HintLinesTest(unittest.TestCase):
    def test_quotes_a_config_path_with_spaces_and_parentheses(self):
        lines = cli._hint_lines("epubzh-run", "/books/World(zh)-project/book.toml")
        self.assertEqual(
            lines[0],
            "epubzh-run extract --config '/books/World(zh)-project/book.toml'")
        self.assertIn(
            "guide   --config '/books/World(zh)-project/book.toml'", lines[1])

    def test_leaves_a_plain_config_path_unquoted(self):
        lines = cli._hint_lines("epubzh-run", "/books/mybook-zh-project/book.toml")
        self.assertIn("--config /books/mybook-zh-project/book.toml", lines[0])
        self.assertNotIn("'", lines[0])

    def test_uses_powershell_quotes_on_windows(self):
        with mock.patch.object(guide, "os", SimpleNamespace(name="nt")):
            lines = cli._hint_lines(
                "& 'C:\\skill\\epubzh-run.cmd'",
                "C:\\Books\\World(zh)-project\\book.toml")
        self.assertIn(
            "--config 'C:\\Books\\World(zh)-project\\book.toml'", lines[0])


class NextActionTest(unittest.TestCase):
    def run_next(self, state, last=0):
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"), path="/tmp/book.toml")
        target = ("chapter.html", 1, state, last, 4)
        out = io.StringIO()
        with mock.patch.object(cli.config, "load", return_value=cfg), \
                mock.patch.object(cli.guide, "next_target", return_value=target), \
                mock.patch.object(cli.guide, "prompt",
                                  return_value="/tmp/prompt.md"), \
                mock.patch.object(cli.chunks, "progress", return_value=(1, 3)), \
                mock.patch.object(cli.guide, "_module_cmd",
                                  return_value="epubzh-run"), \
                mock.patch.object(cli.guide, "quote_path",
                                  return_value="'/tmp/book.toml'"), \
                redirect_stdout(out):
            cli.cmd_next(SimpleNamespace(config="/tmp/book.toml",
                                         resume=False, brief=False))
        return out.getvalue()

    def test_pending_action_prefers_a_subagent(self):
        out = self.run_next("pending")
        self.assertIn("dispatch one subagent", out)
        self.assertIn("do not run a project-wide check", out)

    def test_action_keeps_main_session_fallback(self):
        for state in ("pending", "partial", "repair", "broken"):
            self.assertIn("main session", self.run_next(state), state)

    def test_partial_action_resumes_from_the_next_segment(self):
        self.assertIn("append from segment 3", self.run_next("partial", last=2))


if __name__ == "__main__":
    unittest.main()
