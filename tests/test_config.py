"""Regression tests for chunk size presets."""
import tempfile
import unittest
from pathlib import Path

from epubzh import config


class DefaultConfigTest(unittest.TestCase):
    def test_generated_config_defaults_to_inline(self):
        root = Path(tempfile.mkdtemp())
        path = root / "book.toml"
        config.write_default(path, str(root / "book.epub"), "书名")
        text = path.read_text(encoding="utf-8")
        self.assertIn("[translate]", text)
        self.assertIn('mode = "inline"', text)
        self.assertEqual(config.load(path).mode, "inline")

    def test_generated_config_accepts_aligned(self):
        root = Path(tempfile.mkdtemp())
        path = root / "book.toml"
        config.write_default(path, str(root / "book.epub"), "书名",
                             mode="aligned")
        self.assertIn('mode = "aligned"',
                      path.read_text(encoding="utf-8"))
        self.assertEqual(config.load(path).mode, "aligned")


class ProfileTest(unittest.TestCase):
    def test_presets_shrink_from_large_to_tiny(self):
        order = ["large", "small", "mini", "tiny"]
        words = [config.PROFILES[name]["words_per_chunk"] for name in order]
        caps = [config.PROFILES[name]["max_segments_per_chunk"] for name in order]
        self.assertEqual(words, sorted(words, reverse=True))
        self.assertEqual(caps[0], 0)  # large has no segment cap
        self.assertEqual(caps[1:], sorted(caps[1:], reverse=True))

    def test_weak_model_presets(self):
        self.assertEqual(config.PROFILES["mini"],
                         {"words_per_chunk": 1000, "max_segments_per_chunk": 25})
        self.assertEqual(config.PROFILES["tiny"],
                         {"words_per_chunk": 600, "max_segments_per_chunk": 20})

    def test_check_profile_accepts_every_preset(self):
        for name in ("large", "small", "mini", "tiny"):
            self.assertEqual(config.check_profile(name), name)

    def test_check_profile_rejects_unknown_names(self):
        with self.assertRaises(SystemExit):
            config.check_profile("huge")

    def test_check_mode_defaults_to_inline(self):
        self.assertEqual(config.check_mode(None), "inline")
        self.assertEqual(config.check_mode(""), "inline")

    def test_check_mode_accepts_aligned(self):
        self.assertEqual(config.check_mode("aligned"), "aligned")
        self.assertEqual(config.check_mode(" ALIGNED "), "aligned")

    def test_check_mode_rejects_unknown_names(self):
        with self.assertRaises(SystemExit):
            config.check_mode("tagged")


if __name__ == "__main__":
    unittest.main()
