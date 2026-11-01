"""Regression tests for chunk size presets."""
import unittest

from epubzh import config


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


if __name__ == "__main__":
    unittest.main()
