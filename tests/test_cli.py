"""Tests for shell-safe command hints."""
import unittest
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


if __name__ == "__main__":
    unittest.main()
