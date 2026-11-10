"""Regression tests for the chunk repair and untranslated-text heuristics."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from epubzh import chunks, markup, spacing


def make_seg(pairs=(), singles=(), text="", single_html=None):
    items = [{"kind": "pair", "open": "\u27e6%d\u27e7" % n,
              "close": "\u27e6/%d\u27e7" % n} for n in pairs]
    items += [{"kind": "single", "open": "\u27e6%d\u27e7" % n,
               "close": None,
               "open_html": (single_html or {}).get(n, "")}
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

    def test_unknown_placeholder_is_removed(self):
        path = self.write(block(1, "\u27e60\u27e7\u7532\u27e6/0\u27e7\u27e69\u27e7")
                          + "\n" + block(2, "\u27e60\u27e7\u4e59\u27e6/0\u27e7"))
        report = chunks.repair_chunk(path, self.chunk)
        self.assertEqual(report.unknown, [(1, ["\u27e69\u27e7"])])
        body = path.read_text(encoding="utf-8")
        self.assertNotIn("\u27e69\u27e7", body)
        self.assertIn("\u27e60\u27e7\u7532\u27e6/0\u27e7", body)


class PlaceholderCollisionTest(unittest.TestCase):
    def test_literal_placeholder_like_text_is_not_reused(self):
        work, _ = markup.atomize("<p>text \u27e60\u27e7 and <em>more</em></p>", [])
        segs = markup.extract_segments(work, require_latin=False)
        self.assertEqual(len(segs), 1)
        text = segs[0].text
        self.assertIn("\u27e60\u27e7", text)
        self.assertIn("\u27e61\u27e7more\u27e6/1\u27e7", text)

    def test_literal_marker_is_text_not_a_placeholder(self):
        work, _ = markup.atomize(
            "<p>输入 \u27e60\u27e7，然后点 <em>开始</em>。</p>", [])
        seg = markup.extract_segments(work, require_latin=False)[0]
        self.assertEqual(seg.literals, {"\u27e60\u27e7": 1})
        self.assertIn("\u27e61\u27e7开始\u27e6/1\u27e7", seg.text)
        self.assertIsNone(seg.check("输入 \u27e60\u27e7，然后点 \u27e61\u27e7开始\u27e6/1\u27e7。"))
        self.assertIsNone(seg.check("输入，然后点 \u27e61\u27e7开始\u27e6/1\u27e7。"))

    def test_repair_keeps_literal_marker_and_removes_only_unknown(self):
        work, _ = markup.atomize(
            "<p>输入 \u27e60\u27e7，然后点 <em>开始</em>。</p>", [])
        seg = markup.extract_segments(work, require_latin=False)[0]
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(
            "@@@SEG 001@@@\n输入 \u27e60\u27e7，然后点 \u27e61\u27e7开始\u27e6/1\u27e7\u27e69\u27e7。\n",
            encoding="utf-8")
        report = chunks.repair_chunk(path, [seg])
        self.assertEqual(report.unknown, [(1, ["\u27e69\u27e7"])])
        body = path.read_text(encoding="utf-8")
        self.assertIn("\u27e60\u27e7", body)
        self.assertNotIn("\u27e69\u27e7", body)


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


class VerbatimMarkerTest(unittest.TestCase):
    def test_marker_after_body_is_recognized(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(block(1, "Ken Huang and Chris Hughes")
                        + "\n@@@VERBATIM@@@\n", encoding="utf-8")
        self.assertEqual(chunks.load_marked(path),
                         [(1, "Ken Huang and Chris Hughes", True)])

    def test_marker_before_body_is_recognized(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(block(1, "@@@VERBATIM@@@\nKen Huang"),
                        encoding="utf-8")
        self.assertEqual(chunks.load_marked(path), [(1, "Ken Huang", True)])


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
                     "Artificial intelligence is transforming security",
                     "Artificial Intelligence and Machine Learning",
                     "AI Security and Risk Management",
                     "Research and Development"):
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


class PlaceholderOrderTest(unittest.TestCase):
    def test_grouped_closes_at_end_are_rejected(self):
        seg = make_seg(pairs=(0, 1))
        bad = seg.check("\u27e60\u27e7\u7532\u27e61\u27e7\u4e59"
                        "\u27e6/0\u27e7\u27e6/1\u27e7")
        self.assertIn("placeholder order mismatch", bad)
        self.assertIn("source order", bad)
        self.assertIn("single markers may move", bad)

    def test_pair_span_cannot_swallow_a_following_single(self):
        seg = make_seg(pairs=(0,), singles=(1,),
                       text="\u27e60\u27e7*1\u27e6/0\u27e7 body "
                            "\u27e61\u27e7 tail")
        good = "\u27e60\u27e7*1\u27e6/0\u27e7 \u6b63\u6587 \u27e61\u27e7 \u5c3e"
        self.assertIsNone(seg.check(good))
        bad = "\u27e60\u27e7*1 \u6b63\u6587 \u27e61\u27e7 \u5c3e\u27e6/0\u27e7"
        self.assertIn("placeholder nesting mismatch", seg.check(bad))
        self.assertIn("\u27e61\u27e7", seg.check(bad))

    def test_pair_span_cannot_eject_a_nested_pair(self):
        seg = make_seg(pairs=(0, 1),
                       text="\u27e60\u27e7a\u27e61\u27e7b\u27e6/1\u27e7c\u27e6/0\u27e7")
        bad = "\u27e60\u27e7\u7532\u27e6/0\u27e7\u27e61\u27e7\u4e59\u27e6/1\u27e7\u4e19"
        self.assertIn("placeholder", seg.check(bad))

    def test_pairs_in_source_order_pass(self):
        seg = make_seg(pairs=(0, 1))
        self.assertIsNone(seg.check("\u27e60\u27e7\u7532\u27e6/0\u27e7"
                                    "\u3001\u27e61\u27e7\u4e59\u27e6/1\u27e7"))

    def test_single_marker_can_move(self):
        seg = make_seg(pairs=(0,), singles=(1,))
        self.assertIsNone(seg.check("\u27e60\u27e7\u7532\u27e6/0\u27e7"
                                    " \u4e59\u27e61\u27e7"))

    def test_nested_pairs_follow_source_text(self):
        seg = make_seg(pairs=(0, 1))
        seg.text = "\u27e60\u27e7\u27e61\u27e7\u7532\u27e6/1\u27e7\u27e6/0\u27e7"
        self.assertIsNone(seg.check(
            "\u27e60\u27e7\u27e61\u27e7\u4e59\u27e6/1\u27e7\u27e6/0\u27e7"))
        bad = seg.check("\u27e60\u27e7\u27e61\u27e7\u4e59\u27e6/0\u27e7\u27e6/1\u27e7")
        self.assertIn("placeholder order mismatch", bad)

    def test_missing_pair_close_gets_placement_hint(self):
        seg = make_seg(pairs=(0,))
        bad = seg.check("\u27e60\u27e7\u7532")
        self.assertIn("missing=", bad)
        self.assertIn("immediately after its translated span", bad)
        self.assertIn("not at the end of the segment", bad)


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


class RestoreLiteralPairsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def run_case(self, source, translated):
        path = self.tmp / "chunk.txt"
        path.write_text(block(1, translated) + "\n", encoding="utf-8")
        fixes = chunks.restore_placeholders(
            path, [make_seg(pairs=(1,), text=source)])
        return fixes, path.read_text(encoding="utf-8")

    def test_wraps_a_literal_that_survived(self):
        fixes, text = self.run_case(
            "\u27e61\u27e7Friends\u27e6/1\u27e7",
            "\u4f60\u6628\u665a\u770b\u300a\u53cb\u8c0a\u300b\uff08Friends\uff09\u4e86\u5417\uff1f")
        self.assertEqual(fixes, [(1, ["Friends"], [])])
        self.assertIn("\uff08\u27e61\u27e7Friends\u27e6/1\u27e7\uff09", text)

    def test_leaves_translated_spans_alone(self):
        fixes, text = self.run_case(
            "waxing \u27e61\u27e7poetic\u27e6/1\u27e7 about",
            "\u8c08\u7740\u8bd7\u610f\u7684\u8bdd\u9898")
        self.assertEqual(fixes, [])
        self.assertNotIn("\u27e61\u27e7", text)

    def test_skips_ambiguous_repeated_literals(self):
        fixes, _ = self.run_case(
            "\u27e61\u27e7Friends\u27e6/1\u27e7",
            "Friends \u548c Friends \u90fd\u662f\u670b\u53cb")
        self.assertEqual(fixes, [])

    def test_respects_word_boundaries(self):
        fixes, _ = self.run_case(
            "\u27e61\u27e7Friends\u27e6/1\u27e7",
            "Friendship \u662f\u53cb\u8c0a")
        self.assertEqual(fixes, [])

    def test_skips_when_a_marker_is_partially_present(self):
        fixes, _ = self.run_case(
            "\u27e61\u27e7Friends\u27e6/1\u27e7",
            "Friends\u27e6/1\u27e7 \u662f\u53cb\u8c0a")
        self.assertEqual(fixes, [])

    def test_ignores_visible_single_markers(self):
        path = self.tmp / "chunk.txt"
        path.write_text(block(1, "\u8bd7\u610f\u7684\u8bdd\u9898") + "\n",
                        encoding="utf-8")
        seg = make_seg(singles=(1,), text="waxing \u27e61\u27e7poetic",
                       single_html={1: "<br/>"})
        self.assertEqual(chunks.restore_placeholders(path, [seg]), [])

    def restore_anchors(self, source, translated, html):
        path = self.tmp / "chunk.txt"
        path.write_text(block(1, translated) + "\n", encoding="utf-8")
        seg = make_seg(singles=sorted(html), text=source, single_html=html)
        fixes = chunks.restore_placeholders(path, [seg])
        return seg, fixes, path.read_text(encoding="utf-8")

    def test_places_a_dropped_pagebreak_anchor(self):
        seg, fixes, text = self.restore_anchors(
            "predict a team\u2019s \u27e60\u27e7success just now",
            "\u9884\u6d4b\u4e00\u652f\u7403\u961f\u7684\u6210\u529f",
            {0: '<span epub:type="pagebreak" id="page_7" '
                'role="doc-pagebreak" title="7"/>'})
        self.assertEqual(len(fixes[0][2]), 1)
        translated = text.split("@@@SEG 001@@@\n", 1)[1].strip()
        self.assertIsNone(seg.check(translated))

    def test_places_a_dropped_endnote_anchor(self):
        seg, fixes, text = self.restore_anchors(
            "just by \u27e60\u27e7counting how often",
            "\u53ea\u9700\u901a\u8fc7\u6570\u6b21\u6570",
            {0: '<a id="EndnotePhraseInText19"/>'})
        self.assertEqual(len(fixes[0][2]), 1)
        translated = text.split("@@@SEG 001@@@\n", 1)[1].strip()
        self.assertIsNone(seg.check(translated))

    def test_placed_anchors_keep_source_order(self):
        _seg, fixes, text = self.restore_anchors(
            "team\u2019s \u27e60\u27e7success just by \u27e61\u27e7counting",
            "\u7403\u961f\u7684\u6210\u529f\u53ea\u9700\u901a\u8fc7\u6570\u6b21\u6570",
            {0: '<span epub:type="pagebreak" id="page_7"/>',
             1: '<a id="EndnotePhraseInText19"/>'})
        placed = [marker for marker, _ in fixes[0][2]]
        self.assertEqual(placed, ["\u27e60\u27e7", "\u27e61\u27e7"])
        self.assertLess(text.index("\u27e60\u27e7"), text.index("\u27e61\u27e7"))

    def test_does_not_split_a_latin_word(self):
        _seg, _fixes, text = self.restore_anchors(
            "the NBA \u27e60\u27e7players touch",
            "NBA \u7403\u5458\u4eec\u4e92\u76f8\u63a5\u89e6",
            {0: '<span epub:type="pagebreak" id="page_7"/>'})
        self.assertNotIn("N\u27e60\u27e7BA", text)
        self.assertNotIn("NB\u27e60\u27e7A", text)


class SpacingRepairTest(unittest.TestCase):
    def test_fix_text_inserts_spaces_without_touching_markers(self):
        text, count = spacing.fix_text("第3章Vibe⟦0⟧中文a")
        self.assertEqual(text, "第 3 章 Vibe⟦0⟧中文 a")
        self.assertEqual(count, 4)

    def test_fix_text_is_idempotent(self):
        text = "第 3 章 Vibe"
        self.assertEqual(spacing.fix_text(text), (text, 0))

    def test_repair_chunk_spaces_inline_segments(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(block(1, "第3章Vibe") + "\n", encoding="utf-8")
        report = chunks.repair_chunk(path, [make_seg(text="ch3 vibe")])
        self.assertEqual(report.spaced, [(1, 3)])
        self.assertEqual(path.read_text(encoding="utf-8").strip(),
                         "@@@SEG 001@@@\n第 3 章 Vibe")

    def test_repair_chunk_leaves_verbatim_segments_alone(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(block(1, "@@@VERBATIM@@@\n第3章") + "\n",
                        encoding="utf-8")
        report = chunks.repair_chunk(path, [make_seg(text="ch3")])
        self.assertEqual(report.spaced, [])
        self.assertIn("第3章", path.read_text(encoding="utf-8"))

    def test_repair_chunk_leaves_aligned_files_alone(self):
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text("@@@SEG 001@@@\n@@@TEXT@@@\n第3章\n"
                        "@@@SPAN 0@@@\nch3\n", encoding="utf-8")
        report = chunks.repair_chunk(path, [make_seg(text="ch3")])
        self.assertEqual(report.spaced, [])
        self.assertIn("第3章", path.read_text(encoding="utf-8"))


class CheckFileSpacingTest(unittest.TestCase):
    def test_check_fixes_spacing_without_reporting_a_problem(self):
        root = Path(tempfile.mkdtemp())
        src_dir = root / "src"
        text_dir = Path("OEBPS")
        (src_dir / text_dir).mkdir(parents=True)
        source = src_dir / text_dir / "c.xhtml"
        source.write_text("<p>Hello 1 world</p>\n", encoding="utf-8")
        index = {"c.xhtml": {"chunks": 1,
                             "sha256": hashlib.sha256(
                                 source.read_bytes()).hexdigest()}}
        (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
        zh_dir = root / "zh"
        zh_dir.mkdir()
        (zh_dir / "c.01.txt").write_text("@@@SEG 001@@@\n第1章\n",
                                         encoding="utf-8")
        cfg = SimpleNamespace(
            index_file=root / "index.json", source_dir=src_dir,
            text_dir=text_dir, protect_blocks=[], require_latin=False,
            words_per_chunk=1200, max_segments_per_chunk=40, zh_dir=zh_dir,
            mode=None, spacing=True)
        self.assertEqual(chunks.check_file(cfg, "c.xhtml", only_chunk=1), [])
        self.assertIn("第 1 章",
                      (zh_dir / "c.01.txt").read_text(encoding="utf-8"))


class VerbatimProseTest(unittest.TestCase):
    def test_prose_is_flagged(self):
        self.assertTrue(chunks._verbatim_is_prose(
            "In toxic cultures, stars get rewarded for results even if they "
            "wreck relationships."))
        self.assertTrue(chunks._verbatim_is_prose(
            "Our leaders require you to worship them at the office altar "
            "every day"))

    def test_code_urls_logs_and_addresses_are_not_flagged(self):
        for text in ("pip install epubzh --upgrade",
                     "https://example.com/a/b?c=d",
                     "DistributedApps.ai, Herndon, VA, USA",
                     "ERROR failed to connect to the server\n"
                     "WARN retrying in five seconds",
                     "def run(config):\n    return config"):
            self.assertFalse(chunks._verbatim_is_prose(text), text)


class UntranslatedSpanTest(unittest.TestCase):
    def test_mixed_translation_with_a_copied_phrase_is_flagged(self):
        source = "They saw the consistent pattern of bad behavior and changed."
        text = "他们看到了 consistent pattern of bad behavior，于是决定改变。"
        self.assertEqual(chunks._untranslated_spans(source, text),
                         ["consistent pattern of bad behavior"])

    def test_several_phrases_are_reported_together(self):
        source = ("They saw the consistent pattern of bad behavior and "
                  "needed immediate feedback from the team.")
        text = ("他们看到了 consistent pattern of bad behavior，也需要 "
                "immediate feedback from the team。")
        found = chunks._untranslated_spans(source, text)
        self.assertIn("consistent pattern of bad behavior", found)
        self.assertTrue(any("immediate feedback" in run for run in found))

    def test_single_words_short_phrases_and_names_are_allowed(self):
        cases = [
            ("Accountability matters.", "accountability 很重要。"),
            ("They left reform cards behind.", "他们留下了 reform cards。"),
            ("Ministry of Foreign Affairs replied.",
             "Ministry of Foreign Affairs 回复了。"),
            ("She read The Catcher in the Rye twice.",
             "她把 The Catcher in the Rye 读了两遍。"),
        ]
        for source, text in cases:
            self.assertEqual(chunks._untranslated_spans(source, text), [], text)

    def test_email_and_url_are_allowed(self):
        source = ("Write to someone@example.com or visit "
                  "https://example.com/docs for details.")
        text = ("写信到 someone@example.com 或访问 https://example.com/docs "
                "了解详情。")
        self.assertEqual(chunks._untranslated_spans(source, text), [])

    def test_name_particles_and_short_titles_are_allowed(self):
        for run in ("van der Waals", "bin Laden", "The Catcher in the Rye",
                    "Ministry of Foreign Affairs"):
            self.assertFalse(chunks._run_is_prose(run), run)

    def test_short_runs_without_function_words_are_names(self):
        for run in ("VIKING ship colophon", "machine learning model",
                    "GPU memory bandwidth", "Penguin Random House LLC"):
            self.assertFalse(chunks._run_is_prose(run), run)

    def test_short_runs_with_function_words_are_prose(self):
        for run in ("pattern of behavior", "feedback from the team",
                    "science of how we change"):
            self.assertTrue(chunks._run_is_prose(run), run)

    def test_long_title_cased_runs_are_treated_as_prose(self):
        self.assertFalse(chunks._run_is_prose(
            "The Team Needs Immediate Feedback Now"))
        self.assertTrue(chunks._run_is_prose(
            "The Team Needs Immediate Feedback From Now"))

    def test_parenthetical_glossary_notes_are_allowed(self):
        source = "They named the consistent pattern of bad behavior."
        text = "他们把这种现象称为持续的不良行为模式（consistent pattern of bad behavior）。"
        self.assertEqual(chunks._untranslated_spans(source, text), [])

    def test_bibliographic_references_may_stay_in_english(self):
        cases = [
            # Journal article: volume (year): pages.
            "Jeffrey A. Hall, “Applying the Ideal Standards Model to Unmet "
            "Expectations and Satisfaction in Friendship,” Studies in "
            "Communication Sciences 14 (2014): 20-28.",
            # Book with a title-cased name that would otherwise look like prose.
            "Erving Goffman, The Presentation of Self in Everyday Life "
            "(Anchor, 1959).",
            # DOI-only reference.
            "Paul Eastwick, “Online Dating: Can Science Find You Love?,” "
            "Science Vs, doi.org/10.1038/s41586-020-0000-0.",
        ]
        for reference in cases:
            source = "They studied it: " + reference
            text = "他们研究过：" + reference
            self.assertEqual(chunks._untranslated_spans(source, text), [],
                             reference)

    def test_citation_parenthesis_may_close_after_volume_and_pages(self):
        source = ("They studied it: Jeffrey A. Hall, “Applying the Ideal "
                  "Standards Model,” Studies in Communication Sciences "
                  "14 (2014): 20-28.")
        text = ("他们研究过：（Jeffrey A. Hall, “Applying the Ideal Standards "
                "Model,” Studies in Communication Sciences 14 (2014): "
                "20-28。）")
        self.assertEqual(chunks._untranslated_spans(source, text), [])

    def test_wrapped_truncated_reference_is_allowed(self):
        source = ("They studied it: Montoya, Horton, and Kirchner, “Is Actual "
                  "Similarity Necessary for Attraction?”")
        text = ("他们研究过：（Montoya, Horton, and Kirchner, “Is Actual "
                "Similarity Necessary for Attraction?”）")
        self.assertEqual(chunks._untranslated_spans(source, text), [])

    def test_reference_whose_run_stops_inside_a_title_is_allowed(self):
        # The longest English run ends mid-title, before the closing quote, so
        # the year and the quote both sit just past the run.
        source = ("He listened to it: Paul Eastwick on Science Vs, podcast, "
                  "season 5, episode 7, “Online Dating: Can Science Find You "
                  "Love?,” October 26, 2018.")
        text = ("他听过这期节目：Paul Eastwick on Science Vs, podcast, season 5, "
                "episode 7, “Online Dating: Can Science Find You Love?,” "
                "2018 年 10 月 26 日。")
        self.assertEqual(chunks._untranslated_spans(source, text), [])

    def test_truncated_reference_without_a_year_is_allowed(self):
        source = ("In many parts of Europe: Kuwabara, Luo, and Sheldon, "
                  "“Multiplex Exchange Relations.”")
        text = ("在欧洲的许多地方：Kuwabara, Luo, and Sheldon, "
                "“Multiplex Exchange Relations.”")
        self.assertEqual(chunks._untranslated_spans(source, text), [])

    def test_english_prose_after_a_colon_is_still_flagged(self):
        source = ("He said: the consistent pattern of bad behavior was obvious "
                  "to everyone.")
        text = "他说：consistent pattern of bad behavior 对所有人都很明显。"
        self.assertEqual(chunks._untranslated_spans(source, text),
                         ["consistent pattern of bad behavior"])

    def test_a_finished_translation_is_clean(self):
        self.assertEqual(
            chunks._untranslated_spans("They changed their minds", "他们改变了主意"),
            [])


class CheckFileResidueTest(unittest.TestCase):
    def make_cfg(self, source_html, translation, terms=None):
        root = Path(tempfile.mkdtemp())
        src_dir = root / "src"
        text_dir = Path("OEBPS")
        (src_dir / text_dir).mkdir(parents=True)
        source = src_dir / text_dir / "c.xhtml"
        source.write_text(source_html, encoding="utf-8")
        index = {"c.xhtml": {"chunks": 1,
                             "sha256": hashlib.sha256(
                                 source.read_bytes()).hexdigest()}}
        (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
        zh_dir = root / "zh"
        zh_dir.mkdir()
        (zh_dir / "c.01.txt").write_text(translation, encoding="utf-8")
        terms_tsv = root / "terms.tsv"
        terms_tsv.write_text(terms or "English\t中文\n", encoding="utf-8")
        return SimpleNamespace(
            index_file=root / "index.json", source_dir=src_dir,
            text_dir=text_dir, protect_blocks=[], require_latin=True,
            words_per_chunk=1200, max_segments_per_chunk=40, zh_dir=zh_dir,
            work_dir=root / "work", terms_tsv=terms_tsv, mode=None,
            spacing=True)

    def test_leftover_english_is_reported_as_untranslated(self):
        cfg = self.make_cfg(
            "<p>They saw the consistent pattern of bad behavior and "
            "changed.</p>\n",
            "@@@SEG 001@@@\n"
            "他们看到了 consistent pattern of bad behavior，于是决定改变。\n")
        problems = chunks.check_file(cfg, "c.xhtml", only_chunk=1)
        self.assertEqual(len(problems), 1)
        self.assertIn("untranslated source text", problems[0])
        issues = chunks.chunk_issues(cfg, "c.xhtml", 1)
        self.assertEqual(issues[0]["kind"], "not-translated")

    def test_verbatim_on_prose_is_reported(self):
        cfg = self.make_cfg(
            "<p>In toxic cultures, stars get rewarded for results even if "
            "they wreck relationships.</p>\n",
            "@@@SEG 001@@@\n@@@VERBATIM@@@\n"
            "In toxic cultures, stars get rewarded for results even if they "
            "wreck relationships.\n")
        problems = chunks.check_file(cfg, "c.xhtml", only_chunk=1)
        self.assertEqual(len(problems), 1)
        self.assertIn("@@@VERBATIM@@@ on a prose segment", problems[0])
        issues = chunks.chunk_issues(cfg, "c.xhtml", 1)
        self.assertEqual(issues[0]["kind"], "other")

    def test_verbatim_on_a_short_name_is_fine(self):
        cfg = self.make_cfg(
            "<p>Ken Huang and Chris Hughes</p>\n",
            "@@@SEG 001@@@\n@@@VERBATIM@@@\nKen Huang and Chris Hughes\n")
        self.assertEqual(chunks.check_file(cfg, "c.xhtml", only_chunk=1), [])

    def test_trademark_boilerplate_is_not_reported(self):
        cfg = self.make_cfg(
            "<p>VIKING and VIKING ship colophon are registered trademarks of "
            "Penguin Random House LLC.</p>\n",
            "@@@SEG 001@@@\n"
            "VIKING 和 VIKING ship colophon（船尾诗）是 Penguin Random House "
            "LLC 的注册商标。\n")
        self.assertEqual(chunks.check_file(cfg, "c.xhtml", only_chunk=1), [])

    def test_registered_keep_term_is_not_reported(self):
        cfg = self.make_cfg(
            "<p>They saw the consistent pattern of bad behavior and "
            "changed.</p>\n",
            "@@@SEG 001@@@\n"
            "他们看到了 consistent pattern of bad behavior，于是决定改变。\n",
            terms=("English\t中文\n"
                   "consistent pattern of bad behavior\t"
                   "consistent pattern of bad behavior\tkeep\n"))
        self.assertEqual(chunks.check_file(cfg, "c.xhtml", only_chunk=1), [])

    def test_keep_term_does_not_hide_the_rest_of_the_segment(self):
        cfg = self.make_cfg(
            "<p>They saw the consistent pattern of bad behavior and needed "
            "immediate feedback from the team.</p>\n",
            "@@@SEG 001@@@\n"
            "他们看到了 consistent pattern of bad behavior，也需要 immediate "
            "feedback from the team。\n",
            terms=("English\t中文\n"
                   "consistent pattern of bad behavior\t"
                   "consistent pattern of bad behavior\tkeep\n"))
        problems = chunks.check_file(cfg, "c.xhtml", only_chunk=1)
        self.assertEqual(len(problems), 1)
        self.assertIn("immediate feedback from the team", problems[0])
        detail = problems[0].split("\n    src:")[0]
        self.assertNotIn("consistent pattern of bad behavior", detail)

    def test_registered_keep_long_title_can_be_verbatim(self):
        title = ("Ordinary Magic: The Science of How We Can Achieve Big "
                 "Change with Small Acts")
        cfg = self.make_cfg(
            "<p>%s</p>\n" % title,
            "@@@SEG 001@@@\n@@@VERBATIM@@@\n%s\n" % title,
            terms="English\t中文\n%s\t%s\tkeep\n" % (title, title))
        self.assertEqual(chunks.check_file(cfg, "c.xhtml", only_chunk=1), [])

    def test_residue_chunk_is_not_counted_as_progress(self):
        cfg = self.make_cfg(
            "<p>They saw the consistent pattern of bad behavior and "
            "changed.</p>\n",
            "@@@SEG 001@@@\n"
            "他们看到了 consistent pattern of bad behavior，于是决定改变。\n")
        self.assertEqual(chunks.progress(cfg), (0, 1))

    def test_a_finished_chunk_still_counts(self):
        cfg = self.make_cfg(
            "<p>They saw the consistent pattern of bad behavior and "
            "changed.</p>\n",
            "@@@SEG 001@@@\n他们看到了这种持续的不良行为模式，于是决定改变。\n")
        self.assertEqual(chunks.progress(cfg), (1, 1))


if __name__ == "__main__":
    unittest.main()
