"""Regression tests for the chunk repair and untranslated-text heuristics."""
import tempfile
import unittest
from pathlib import Path

from epubzh import chunks, markup


def make_seg(pairs=(), singles=(), text=""):
    items = [{"kind": "pair", "open": "\u27e6%d\u27e7" % n,
              "close": "\u27e6/%d\u27e7" % n} for n in pairs]
    items += [{"kind": "single", "open": "\u27e6%d\u27e7" % n}
              for n in singles]
    return markup.Segment(0, 0, text, items)


def block(num, text):
    return "@@@SEG %03d@@@\n%s" % (num, text)


class RepairChunkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.chunk = [make_seg(pairs=(0,)), make_seg(pairs=(0,))]

    def write(self, text):
        path = self.tmp / "chunk.txt"
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def test_identical_duplicates_collapse_to_first(self):
        path = self.write(block(1, "\u7532") + "\n" + block(1, "\u7532")
                          + "\n" + block(2, "\u27e60\u27e7\u4e59\u27e6/0\u27e7"))
        report = chunks.repair_chunk(path, self.chunk)
        self.assertEqual(report.removed, [1])
        self.assertEqual(report.ambiguous, [])
        self.assertEqual(path.read_text(encoding="utf-8").count("@@@SEG 001@@@"), 1)

    def test_keeps_the_only_valid_copy(self):
        path = self.write(block(1, "\u27e60\u27e7\u7532\u27e7")
                          + "\n" + block(1, "\u27e60\u27e7\u7532\u27e6/0\u27e7")
                          + "\n" + block(2, "\u27e60\u27e7\u4e59\u27e6/0\u27e7"))
        report = chunks.repair_chunk(path, self.chunk)
        self.assertEqual(report.removed, [1])
        self.assertEqual(report.ambiguous, [])
        body = path.read_text(encoding="utf-8")
        self.assertIn("\u27e60\u27e7\u7532\u27e6/0\u27e7", body)
        self.assertNotIn("\u27e60\u27e7\u7532\u27e7", body)

    def test_ambiguous_duplicates_keep_last_copy(self):
        path = self.write(block(1, "\u27e60\u27e7\u7532\u27e7")
                          + "\n" + block(1, "\u27e61\u27e7\u4e59\u27e6/1\u27e7")
                          + "\n" + block(2, "\u27e60\u27e7\u4e19\u27e6/0\u27e7"))
        report = chunks.repair_chunk(path, self.chunk)
        self.assertEqual(report.removed, [1])
        self.assertEqual(report.ambiguous, [1])
        body = path.read_text(encoding="utf-8")
        self.assertEqual(body.count("@@@SEG 001@@@"), 1)
        self.assertIn("\u27e61\u27e7\u4e59\u27e6/1\u27e7", body)
        self.assertNotIn("\u27e60\u27e7\u7532\u27e7", body)

    def test_out_of_range_blocks_are_dropped(self):
        path = self.write(block(0, "\u27e60\u27e7\u5e9f\u27e6/0\u27e7")
                          + "\n" + block(1, "\u27e60\u27e7\u7532\u27e6/0\u27e7")
                          + "\n" + block(2, "\u27e60\u27e7\u4e59\u27e6/0\u27e7")
                          + "\n" + block(3, "\u27e60\u27e7\u4e19\u27e6/0\u27e7"))
        report = chunks.repair_chunk(path, self.chunk)
        self.assertEqual(report.dropped, [0, 3])
        self.assertEqual(report.removed, [])
        body = path.read_text(encoding="utf-8")
        self.assertNotIn("@@@SEG 000@@@", body)
        self.assertNotIn("@@@SEG 003@@@", body)

    def test_complete_but_reordered_blocks_are_sorted(self):
        path = self.write(block(2, "\u27e60\u27e7\u4e59\u27e6/0\u27e7")
                          + "\n" + block(1, "\u27e60\u27e7\u7532\u27e6/0\u27e7"))
        report = chunks.repair_chunk(path, self.chunk)
        self.assertTrue(report.reordered)
        body = path.read_text(encoding="utf-8")
        self.assertLess(body.index("@@@SEG 001@@@"), body.index("@@@SEG 002@@@"))


class PlaceholderRepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, text):
        path = self.tmp / "chunk.txt"
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def test_extra_close_for_single_marker_is_removed(self):
        chunk = [make_seg(singles=(2,), text="two")]
        path = self.write(block(1, "\u27e62\u27e7\u7532\u27e6/2\u27e7"))
        report = chunks.repair_chunk(path, chunk)
        self.assertEqual(report.placeholders,
                         [(1, ["\u27e6/2\u27e7"])])
        body = path.read_text(encoding="utf-8")
        self.assertIn("\u27e62\u27e7\u7532", body)
        self.assertNotIn("\u27e6/2\u27e7", body)
        self.assertIsNone(chunk[0].check("\u27e62\u27e7\u7532"))

    def test_extra_pair_is_not_removed_automatically(self):
        chunk = [make_seg(pairs=(3,), text="three")]
        duplicate = ("\u27e63\u27e7\u7532\u27e6/3\u27e7"
                     "\u27e63\u27e7\u4e59\u27e6/3\u27e7")
        path = self.write(block(1, duplicate))
        report = chunks.repair_chunk(path, chunk)
        self.assertEqual(report.placeholders, [])
        self.assertEqual(path.read_text(encoding="utf-8").count("\u27e63\u27e7"), 2)

    def test_safe_close_removed_while_ambiguous_pair_remains(self):
        chunk = [make_seg(pairs=(3,), singles=(2,), text="mixed")]
        body = ("\u27e62\u27e7\u7532\u27e6/2\u27e7"
                "\u27e63\u27e7\u4e59\u27e6/3\u27e7"
                "\u27e63\u27e7\u4e19\u27e6/3\u27e7")
        path = self.write(block(1, body))
        report = chunks.repair_chunk(path, chunk)
        self.assertEqual(report.placeholders,
                         [(1, ["\u27e6/2\u27e7"])])
        fixed = path.read_text(encoding="utf-8")
        self.assertNotIn("\u27e6/2\u27e7", fixed)
        self.assertIn("\u27e63\u27e7\u4e19\u27e6/3\u27e7", fixed)


class TranslatableTest(unittest.TestCase):
    def test_names_and_labels_are_not_flagged(self):
        for text in ("Chris Hughes", "DistributedApps.ai, Herndon, VA, USA",
                     "Figure 1.1", "Email:", "LLM",
                     "https://doi.org/10.1007/978-3-032-02130-4_1"):
            self.assertFalse(chunks._looks_translatable(text), text)

    def test_prose_is_flagged(self):
        for text in ("This is a complete sentence",
                     "Agentic AI uses tools",
                     "AI Agents Are Transforming Security",
                     "Artificial intelligence is transforming security"):
            self.assertTrue(chunks._looks_translatable(text), text)


class MarkerDiagnosticsTest(unittest.TestCase):
    def test_duplicate_reports_line_numbers(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(block(1, "\u7532") + "\n" + block(2, "\u4e59")
                        + "\n" + block(2, "\u4e59") + "\n"
                        + block(3, "\u4e19") + "\n", encoding="utf-8")
        error = chunks.parse_translation(path, 3)[2]
        self.assertIn("duplicated: 2", error)
        self.assertIn("at line 3, 5", error)

    def test_partial_explains_where_to_append(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(block(1, "\u7532") + "\n" + block(2, "\u4e59")
                        + "\n", encoding="utf-8")
        error = chunks.parse_translation(path, 4)[2]
        self.assertIn("partial: segments 1..2 of 4", error)
        self.assertIn("append from 3", error)

    def test_missing_middle_segment_names_insertion_point_and_source(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(block(1, "\u7532") + "\n" + block(2, "\u4e59")
                        + "\n" + block(4, "\u4e01") + "\n", encoding="utf-8")
        chunk = [make_seg(text="one"), make_seg(text="two"),
                 make_seg(text="three"), make_seg(text="four")]
        error = chunks.parse_translation(path, 4, chunk)[2]
        self.assertIn("missing seg 3", error)
        self.assertIn("between @@@SEG 002@@@ and @@@SEG 004@@@", error)
        self.assertIn("src: three", error)


class PlaceholderHintTest(unittest.TestCase):
    def test_extra_close_for_single_marker_gets_hint(self):
        seg = make_seg(singles=(0,))
        bad = seg.check("\u27e60\u27e7\u7532\u27e6/0\u27e7")
        self.assertIn("is a single marker", bad)
        self.assertIn("remove all extra markers", bad)
        self.assertIn("do not renumber placeholders", bad)
        self.assertIn("numbers are per segment", bad)

    def test_same_number_in_different_segments_is_valid(self):
        paired = make_seg(pairs=(2,))
        single = make_seg(singles=(2,))
        self.assertIsNone(paired.check("\u27e62\u27e7\u7532\u27e6/2\u27e7"))
        self.assertIsNone(single.check("\u27e62\u27e7\u4e59"))

    def test_extra_pair_says_delete_whole_pair(self):
        seg = make_seg(pairs=(3, 4))
        bad = seg.check("\u27e63\u27e7\u7532\u27e6/3\u27e7"
                        "\u27e64\u27e7\u4e59\u27e6/4\u27e7"
                        "\u27e64\u27e7\u4e19\u27e6/4\u27e7")
        self.assertIn("remove all extra markers: \u27e64\u27e7, \u27e6/4\u27e7",
                      bad)
        self.assertIn("delete extra pairs as a whole", bad)
        self.assertIn("do not renumber placeholders", bad)

    def test_extra_pair_and_single_close_are_both_named(self):
        seg = make_seg(pairs=(3,), singles=(2,))
        bad = seg.check("\u27e62\u27e7\u7532\u27e6/2\u27e7"
                        "\u27e63\u27e7\u4e59\u27e6/3\u27e7"
                        "\u27e63\u27e7\u4e19\u27e6/3\u27e7")
        self.assertIn("remove all extra markers", bad)
        self.assertIn("\u27e62\u27e7 is a single marker", bad)
        self.assertIn("delete extra pairs as a whole", bad)


if __name__ == "__main__":
    unittest.main()
