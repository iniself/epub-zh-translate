"""Tests for the aligned TEXT/SPAN translation format."""
import tempfile
import unittest
from pathlib import Path

from epubzh import chunks, markup

PH = markup.placeholder
CLOSE = markup.close_placeholder
PAGEBREAK = '<span epub:type="pagebreak" id="p1"></span>'


def make_seg(pairs=(), singles=(), text="", single_html=None):
    items = [{"kind": "pair", "open": PH(n), "close": CLOSE(n),
              "open_html": "<em>", "close_html": "</em>"} for n in pairs]
    items += [{"kind": "single", "open": PH(n), "close": None,
               "open_html": (single_html or {}).get(n, "")}
              for n in singles]
    return markup.Segment(0, 0, text, items)


def aligned(text, spans):
    parts = ["@@@TEXT@@@", text]
    for number, body in spans:
        parts += ["@@@SPAN %d@@@" % number, body]
    return "\n".join(parts)


class AssembleTest(unittest.TestCase):
    def test_pair_at_start_and_end(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧ world")
        self.assertEqual(
            chunks._assemble_aligned(seg, aligned("你好世界", [(0, "你好")])),
            "⟦0⟧你好⟦/0⟧世界")

    def test_pair_in_the_middle(self):
        seg = make_seg(pairs=(0,), text="Say ⟦0⟧hello⟦/0⟧ now")
        self.assertEqual(
            chunks._assemble_aligned(seg, aligned("现在说你好吧", [(0, "你好")])),
            "现在说⟦0⟧你好⟦/0⟧吧")

    def test_nested_pairs_keep_source_nesting(self):
        seg = make_seg(pairs=(0, 1),
                       text="⟦0⟧a ⟦1⟧b⟦/1⟧ c⟦/0⟧")
        body = aligned("甲 乙 丙", [(0, "甲 乙 丙"), (1, "乙")])
        self.assertEqual(chunks._assemble_aligned(seg, body),
                         "⟦0⟧甲 ⟦1⟧乙⟦/1⟧ 丙⟦/0⟧")

    def test_adjacent_pairs_stay_ordered(self):
        seg = make_seg(pairs=(0, 1), text="⟦0⟧a⟦/0⟧⟦1⟧b⟦/1⟧")
        body = aligned("甲乙", [(0, "甲"), (1, "乙")])
        self.assertEqual(chunks._assemble_aligned(seg, body),
                         "⟦0⟧甲⟦/0⟧⟦1⟧乙⟦/1⟧")

    def test_repeated_span_text_uses_source_order(self):
        seg = make_seg(pairs=(0, 1),
                       text="⟦0⟧a⟦/0⟧ and ⟦1⟧a⟦/1⟧")
        body = aligned("甲 和 甲", [(0, "甲"), (1, "甲")])
        self.assertEqual(chunks._assemble_aligned(seg, body),
                         "⟦0⟧甲⟦/0⟧ 和 ⟦1⟧甲⟦/1⟧")

    def test_single_marker_stays_in_text(self):
        seg = make_seg(pairs=(0,), singles=(1,),
                       text="⟦0⟧Hi⟦/0⟧ ⟦1⟧there",
                       single_html={1: PAGEBREAK})
        body = aligned("你好 ⟦1⟧世界", [(0, "你好")])
        self.assertEqual(chunks._assemble_aligned(seg, body),
                         "⟦0⟧你好⟦/0⟧ ⟦1⟧世界")

    def test_missing_invisible_single_is_replaced(self):
        seg = make_seg(pairs=(0,), singles=(1,),
                       text="⟦0⟧Hi⟦/0⟧ ⟦1⟧there",
                       single_html={1: PAGEBREAK})
        body = aligned("你好世界", [(0, "你好")])
        out = chunks._assemble_aligned(seg, body)
        self.assertIn("⟦1⟧", out)
        self.assertIsNone(seg.check(out))

    def test_missing_span_is_reported(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(seg, aligned("你好", []))
        self.assertIn("missing SPAN 0", str(ctx.exception))

    def test_unknown_span_is_reported(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(seg, aligned("你好", [(7, "你好")]))
        self.assertIn("unknown SPAN 7", str(ctx.exception))

    def test_span_not_in_text_is_reported(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(seg, aligned("你好", [(0, "您好")]))
        self.assertIn("does not appear verbatim", str(ctx.exception))

    def test_span_text_must_be_exact(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(seg, aligned("你好 世界", [(0, "你好世界")]))
        self.assertIn("does not appear verbatim", str(ctx.exception))

    def test_paired_marker_inside_text_is_rejected(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(
                seg, aligned("⟦0⟧你好⟦/0⟧", [(0, "你好")]))
        self.assertIn("must not contain the paired marker", str(ctx.exception))

    def test_conflicting_nesting_is_rejected(self):
        seg = make_seg(pairs=(0, 1),
                       text="⟦0⟧a ⟦1⟧b⟦/1⟧ c⟦/0⟧")
        body = aligned("甲乙丙", [(0, "甲乙"), (1, "丙")])
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(seg, body)
        self.assertIn("nesting", str(ctx.exception))

    def test_reordered_siblings_are_rejected(self):
        seg = make_seg(pairs=(0, 1), text="⟦0⟧a⟦/0⟧ ⟦1⟧b⟦/1⟧")
        body = aligned("乙甲", [(0, "甲"), (1, "乙")])
        with self.assertRaises(chunks.AlignedError):
            chunks._assemble_aligned(seg, body)

    def test_empty_span_is_rejected(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(seg, aligned("你好", [(0, "")]))
        self.assertIn("empty", str(ctx.exception))

    def test_footnote_marker_pair_does_not_swallow_the_paragraph(self):
        # Regression shape: ⟦0⟧*1⟦/0⟧ followed by prose and a single anchor.
        seg = make_seg(pairs=(0,), singles=(1,),
                       text="⟦0⟧*1⟦/0⟧ Some critics disagreed. ⟦1⟧Later studies "
                            "suggest otherwise.",
                       single_html={1: PAGEBREAK})
        body = aligned("*1 有些批评者不同意。⟦1⟧后来的研究表明并非如此。",
                       [(0, "*1")])
        out = chunks._assemble_aligned(seg, body)
        self.assertTrue(out.startswith("⟦0⟧*1⟦/0⟧ 有些批评者"))
        self.assertIn("。⟦1⟧后来的研究", out)
        self.assertIsNone(seg.check(out))

    def test_duplicate_text_block_is_rejected(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        body = "@@@TEXT@@@\n你好\n@@@TEXT@@@\n你好\n@@@SPAN 0@@@\n你好"
        with self.assertRaises(chunks.AlignedError) as ctx:
            chunks._assemble_aligned(seg, body)
        self.assertIn("duplicated @@@TEXT@@@", str(ctx.exception))


class ParseTranslationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, text):
        path = self.tmp / "chunk.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_aligned_file_returns_assembled_inline_text(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧ world")
        path = self.write("@@@SEG 001@@@\n"
                          + aligned("你好世界", [(0, "你好")]) + "\n")
        texts, keeps, err = chunks.parse_translation(path, 1, [seg])
        self.assertIsNone(err)
        self.assertEqual(texts, ["⟦0⟧你好⟦/0⟧世界"])
        self.assertEqual(keeps, [False])

    def test_aligned_parse_error_names_the_segment(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        path = self.write("@@@SEG 001@@@\n"
                          + aligned("你好", [(0, "您好")]) + "\n")
        _, _, err = chunks.parse_translation(path, 1, [seg])
        self.assertIn("seg 1/1", err)
        self.assertIn("does not appear verbatim", err)

    def test_verbatim_segment_uses_the_source(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        path = self.write("@@@SEG 001@@@\n@@@VERBATIM@@@\n")
        texts, keeps, err = chunks.parse_translation(path, 1, [seg])
        self.assertIsNone(err)
        self.assertEqual(texts, [seg.text])
        self.assertEqual(keeps, [True])

    def test_inline_file_is_unchanged(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        path = self.write("@@@SEG 001@@@\n⟦0⟧你好⟦/0⟧\n")
        texts, keeps, err = chunks.parse_translation(path, 1, [seg])
        self.assertIsNone(err)
        self.assertEqual(texts, ["⟦0⟧你好⟦/0⟧"])

    def test_aligned_mode_rejects_inline_file(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        path = self.write("@@@SEG 001@@@\n⟦0⟧你好⟦/0⟧\n")
        _, _, err = chunks.parse_translation(path, 1, [seg], mode="aligned")
        self.assertIn("expected aligned format", err)

    def test_aligned_mode_allows_a_pure_verbatim_chunk(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        path = self.write("@@@SEG 001@@@\n@@@VERBATIM@@@\n")
        texts, keeps, err = chunks.parse_translation(
            path, 1, [seg], mode="aligned")
        self.assertIsNone(err)
        self.assertEqual(texts, [seg.text])
        self.assertEqual(keeps, [True])

    def test_partial_aligned_file_is_reported(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧")
        path = self.write("@@@SEG 001@@@\n"
                          + aligned("你好", [(0, "你好")]) + "\n")
        _, _, err = chunks.parse_translation(path, 2, [seg, seg])
        self.assertIn("partial", err)

    def test_repair_does_not_rewrite_aligned_placeholders(self):
        seg = make_seg(pairs=(0,), text="⟦0⟧Hello⟦/0⟧ ⟦1⟧ x")
        path = self.write("@@@SEG 001@@@\n"
                          + aligned("你好 ⟦1⟧ x", [(0, "你好")]) + "\n")
        before = path.read_text(encoding="utf-8")
        chunks.repair_chunk(path, [seg])
        self.assertEqual(path.read_text(encoding="utf-8"), before)


class TailTest(unittest.TestCase):
    def test_aligned_tail_skips_span_text(self):
        from epubzh import guide
        path = Path(tempfile.mkdtemp()) / "chunk.txt"
        path.write_text(
            "@@@SEG 001@@@\n@@@TEXT@@@\n" + "这是正文。" * 10 + "\n"
            "@@@SPAN 0@@@\n" + "区间文字。" * 10 + "\n", encoding="utf-8")
        tail = guide._tail(path)
        self.assertIn("这是正文", tail)
        self.assertNotIn("区间文字", tail)


if __name__ == "__main__":
    unittest.main()
