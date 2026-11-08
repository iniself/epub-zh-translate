"""Regression tests for state-aware chunk prompt generation."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from epubzh import chunks, guide


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
        self.assertIn("第一步动作是 `write` 输出文件", text)
        self.assertIn("优先一次 `write` 写完整个分块", text)
        self.assertNotIn("运行第 3 条的自检", text)

    def test_partial_appends_without_rewriting(self):
        text = contract("partial", last=2)
        self.assertIn("第一步动作是 `read` 文件末尾", text)
        self.assertIn("从 `@@@SEG 003@@@` 开始继续追加", text)
        self.assertNotIn("优先一次 `write` 写完整个分块", text)
        self.assertNotIn("第一步动作是 `write` 输出文件", text)

    def test_broken_runs_check_fix_before_writing(self):
        text = contract("broken")
        self.assertIn("第一步动作是运行第 3 条的自检", text)
        self.assertIn("不要先写文件", text)
        self.assertNotIn("优先一次 `write` 写完整个分块", text)
        self.assertNotIn("第一步动作是 `write` 输出文件", text)
        self.assertIn("正确的 1..10 段", text)

    def test_repair_runs_check_fix_without_overwriting(self):
        text = contract("repair")
        self.assertIn("第一步动作是运行第 3 条的自检", text)
        self.assertIn("不要整块重写", text)
        self.assertIn("不要重新编号", text)
        self.assertNotIn("第一步动作是 `write` 输出文件", text)

    def test_rulecard_explains_per_segment_numbering(self):
        # Per-segment numbering is stated once, in the rule card, instead of
        # being repeated in every contract variant.
        text = guide._rulecard()
        self.assertIn("每段独立", text)
        self.assertIn("不要跨段比较", text)

    def test_contracts_do_not_ask_the_model_to_self_check(self):
        for state in ("pending", "repair", "partial", "broken"):
            text = contract(state, last=2)
            self.assertNotIn("自我纠错", text)
            self.assertNotIn("提交前自检", text)
            self.assertIn("check", text)

    def test_repair_contract_says_fix_cannot_restore_placeholders(self):
        text = contract("repair")
        self.assertIn("不补缺失的占位符", text)

    def test_contract_explains_nesting_mismatch(self):
        text = contract("repair")
        self.assertIn("placeholder nesting mismatch", text)
        self.assertIn("闭标记移回", text)

    def test_repair_opening_says_fix_cannot_restore_placeholders(self):
        text = guide._opening("repair", 1, 0)
        self.assertIn("不会补缺失的占位符", text)

    def test_placeholder_escalation_only_for_placeholder_errors(self):
        text = contract("repair")
        self.assertIn("--placeholders", text)
        self.assertIn("其它错误", text)
        self.assertIn("不要问用户", text)

    def test_repair_requires_an_edit_before_failure(self):
        text = contract("repair")
        self.assertIn("只运行", text)
        self.assertIn("不算修复", text)
        self.assertIn("没有编辑过", text)
        self.assertIn("不得", text)

    def test_repair_stops_when_problem_count_stops_shrinking(self):
        text = contract("pending")
        self.assertIn("最多 6 轮", text)
        self.assertIn("数字变小", text)
        self.assertIn("没变小", text)
        self.assertIn("失败：<原因>", text)


class RefreshGuideTest(unittest.TestCase):
    def cfg(self, root):
        cfg = SimpleNamespace(
            title="书名", source_title="Original", author="Author",
            language="zh-CN", work_dir=root, terms_tsv=root / "terms.tsv",
            guide_file=root / "TRANSLATION_GUIDE.md")
        cfg.terms_tsv.write_text("English\t中文\t\n", encoding="utf-8")
        return cfg

    def test_rewrites_a_stale_guide(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.cfg(Path(tmp))
            cfg.guide_file.write_text("old template", encoding="utf-8")
            self.assertTrue(guide.refresh_guide(cfg, quiet=True))
            self.assertNotIn("old template",
                             cfg.guide_file.read_text(encoding="utf-8"))

    def test_keeps_a_fresh_guide(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.cfg(Path(tmp))
            guide.refresh_guide(cfg, quiet=True)
            self.assertFalse(guide.refresh_guide(cfg, quiet=True))

    def test_skips_when_the_glossary_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.cfg(Path(tmp))
            cfg.terms_tsv.unlink()
            self.assertFalse(guide.refresh_guide(cfg, quiet=True))


class SingleMarkerTest(unittest.TestCase):
    def test_names_the_segments_and_markers(self):
        text = ("@@@SEG 001@@@\n⟦0⟧a⟦/0⟧ ⟦1⟧b\n"
                "@@@SEG 002@@@\n⟦0⟧plain\n")
        self.assertEqual(guide._single_marker_segments(text),
                         [(1, ["⟦1⟧"]), (2, ["⟦0⟧"])])

    def test_paired_markers_are_not_reported(self):
        text = "@@@SEG 001@@@\n⟦0⟧a⟦/0⟧\n"
        self.assertEqual(guide._single_marker_segments(text), [])


class BriefPromptTest(unittest.TestCase):
    def cfg(self):
        return SimpleNamespace(path=Path("/tmp/book.toml"),
                               segs_dir=Path("/tmp/segs"),
                               zh_dir=Path("/tmp/zh"))

    def build(self, state, last=0):
        index = {"chapter.html": {"chunks": 1}}
        with mock.patch.object(guide.chunks, "chunk_counts", return_value=[5]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  return_value=(state, last)):
            return guide._build_brief_prompt(self.cfg(), "chapter.html", 1, index)

    def test_brief_prompt_keeps_only_the_mechanics(self):
        text = self.build("pending")
        self.assertIn("第一步是 `write` 输出文件", text)
        self.assertIn("check --config", text)
        self.assertIn("@@@SEG 001@@@", text)
        self.assertNotIn("翻译规则卡", text)
        self.assertNotIn("自我纠错", text)
        self.assertNotIn("提交前自检", text)

    def test_brief_prompt_resumes_partial_without_overwriting(self):
        text = self.build("partial", last=2)
        self.assertIn("第 3 段继续追加", text)
        self.assertIn("不要覆盖已有内容", text)

    def test_brief_prompt_repairs_before_writing(self):
        text = self.build("repair")
        self.assertIn("先运行规则 3 里的自检命令", text)
        self.assertNotIn("第一步是 `write` 输出文件", text)

    def test_aligned_brief_prompt_has_the_span_format(self):
        cfg = self.cfg()
        cfg.mode = "aligned"
        index = {"chapter.html": {"chunks": 1}}
        with mock.patch.object(guide.chunks, "chunk_counts", return_value=[5]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  return_value=("pending", 0)):
            text = guide._build_brief_prompt(cfg, "chapter.html", 1, index)
        self.assertIn("@@@TEXT@@@", text)
        self.assertIn("@@@SPAN 0@@@", text)
        self.assertIn("逐字", text)
        self.assertIn("check --config", text)


class AlignedContractTest(unittest.TestCase):
    def contract(self, state, last=0):
        return guide._aligned_contract(CFG, "chapter", 1, DST, state, last, 10)

    def test_rules_describe_text_and_span_blocks(self):
        text = guide._aligned_rules()
        self.assertIn("@@@TEXT@@@", text)
        self.assertIn("@@@SPAN k@@@", text)
        self.assertIn("逐字", text)
        self.assertIn("不要把成对标记写进 TEXT", text)
        self.assertIn("@@@VERBATIM@@@", text)

    def test_contract_points_at_aligned_errors(self):
        text = self.contract("repair")
        self.assertIn("does not appear verbatim", text)
        self.assertIn("missing SPAN", text)
        self.assertIn("不会补 SPAN", text)
        self.assertNotIn("缺失的占位符", text)

    def test_repair_does_not_tell_the_model_to_add_pair_markers(self):
        text = self.contract("repair")
        self.assertNotIn("把缺失的 `⟦n⟧`", text)
        self.assertIn("只改自检点名的段", text)

    def test_aligned_opening_runs_check_for_repair(self):
        text = guide._aligned_opening("repair", 2, 0)
        self.assertIn("check --chunk 2 --fix", text)
        self.assertIn("不会替你补 SPAN", text)
        self.assertNotIn("直接写输出文件", text)


class NextTargetTest(unittest.TestCase):
    def test_skips_done_chunks_and_returns_repair(self):
        index = {"chapter.html": {"chunks": 2}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"),
                              work_dir=Path("/tmp/missing-work"))
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1, 1]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  side_effect=[("done", 1), ("repair", 1)]):
            target = guide.next_target(cfg)
        self.assertEqual(target, ("chapter.html", 2, "repair", 1, 1))

    def test_strict_order_starts_first_pending_before_later_repair(self):
        index = {"chapter.html": {"chunks": 2}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"),
                              work_dir=Path("/tmp/missing-work"))
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1, 1]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  side_effect=[("pending", 0), ("repair", 1)]):
            target = guide.next_target(cfg)
        self.assertEqual(target, ("chapter.html", 1, "pending", 0, 1))

    def test_resume_prioritizes_existing_work(self):
        index = {"chapter.html": {"chunks": 2}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"),
                              work_dir=Path("/tmp/missing-work"))
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1, 1]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  side_effect=[("pending", 0), ("repair", 1)]):
            target = guide.next_target(cfg, resume=True)
        self.assertEqual(target, ("chapter.html", 2, "repair", 1, 1))

    def test_returns_none_when_everything_is_done(self):
        index = {"chapter.html": {"chunks": 1}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"),
                              work_dir=Path("/tmp/missing-work"))
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1]), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  return_value=("done", 1)):
            self.assertIsNone(guide.next_target(cfg))

    def test_skips_chunks_registered_for_manual_review(self):
        index = {"chapter.html": {"chunks": 2}}
        cfg = SimpleNamespace(zh_dir=Path("/tmp/zh"),
                              work_dir=Path("/tmp/missing-work"))
        rows = [{"file": "chapter.html", "chunk": 1, "seg": 1,
                 "kind": "placeholder", "source": "", "translation": "",
                 "detail": ""}]
        with mock.patch.object(guide.chunks, "read_index", return_value=index), \
                mock.patch.object(guide.chunks, "chunk_counts",
                                  return_value=[1, 1]), \
                mock.patch.object(guide.review, "read", return_value=rows), \
                mock.patch.object(guide, "_chunk_prompt_state",
                                  return_value=("repair", 1)):
            target = guide.next_target(cfg, resume=True)
        self.assertEqual(target, ("chapter.html", 2, "repair", 1, 1))


class StyleNoteTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.cfg = SimpleNamespace(
            work_dir=self.root / "work",
            zh_dir=self.root / "zh",
            style_file=self.root / "work" / "style.md",
            decisions_file=self.root / "work" / "decisions.md")

    def test_seed_notes_writes_templates_and_keeps_edits(self):
        guide.seed_notes(self.cfg)
        self.assertEqual(self.cfg.style_file.read_text(encoding="utf-8"),
                         guide.STYLE_TEMPLATE)
        self.assertEqual(self.cfg.decisions_file.read_text(encoding="utf-8"),
                         guide.DECISIONS_TEMPLATE)
        self.cfg.style_file.write_text("# 风格基准\n\n- 用「你」\n",
                                       encoding="utf-8")
        guide.seed_notes(self.cfg)
        self.assertIn("用「你」",
                      self.cfg.style_file.read_text(encoding="utf-8"))

    def test_template_is_not_filled_but_an_edit_is(self):
        guide.seed_notes(self.cfg)
        self.assertFalse(guide.note_is_filled(self.cfg.style_file))
        self.cfg.style_file.write_text(guide.STYLE_TEMPLATE + "\n- 示例\n",
                                       encoding="utf-8")
        self.assertTrue(guide.note_is_filled(self.cfg.style_file))
        self.assertFalse(guide.note_is_filled(self.root / "missing.md"))

    def test_short_note_does_not_clear_the_style_gate(self):
        guide.seed_notes(self.cfg)
        self.cfg.style_file.write_text(
            "# 风格基准\n\n<!-- 说明 -->\n\n- 用「你」\n", encoding="utf-8")
        self.assertFalse(guide.note_is_filled(self.cfg.style_file,
                                              guide.MIN_STYLE_LINES))
        self.cfg.style_file.write_text(
            "# 风格基准\n\n- 用「你」\n- 短句为主\n- 数字用半角\n",
            encoding="utf-8")
        self.assertTrue(guide.note_is_filled(self.cfg.style_file,
                                             guide.MIN_STYLE_LINES))

    def test_hint_ignores_single_chunk_files_and_returns_first_chapter(self):
        index = {"front.html": {"chunks": 1}, "chapter.html": {"chunks": 2},
                 "later.html": {"chunks": 2}}
        guide.seed_notes(self.cfg)
        with mock.patch.object(guide.chunks, "chunk_counts",
                               side_effect=lambda cfg, name, info: [1] * info["chunks"]), \
                mock.patch.object(guide.chunks, "chunk_state",
                                  return_value=("done", 1)):
            self.assertEqual(guide.style_note_hint(self.cfg, index),
                             "chapter.html")

    def test_hint_disappears_after_style_is_filled(self):
        index = {"chapter.html": {"chunks": 2}}
        guide.seed_notes(self.cfg)
        with mock.patch.object(guide.chunks, "chunk_counts",
                               return_value=[1, 1]), \
                mock.patch.object(guide.chunks, "chunk_state",
                                  return_value=("done", 1)):
            self.assertEqual(guide.style_note_hint(self.cfg, index),
                             "chapter.html")
        self.cfg.style_file.write_text(
            "# 风格基准\n\n- 用「你」\n- 短句为主\n- 数字用半角\n",
            encoding="utf-8")
        self.assertIsNone(guide.style_note_hint(self.cfg, index))

    def test_hint_waits_for_a_fully_done_chapter(self):
        index = {"chapter.html": {"chunks": 2}}
        guide.seed_notes(self.cfg)
        with mock.patch.object(guide.chunks, "chunk_counts",
                               return_value=[1, 1]), \
                mock.patch.object(guide.chunks, "chunk_state",
                                  side_effect=[("done", 1), ("partial", 1)]):
            self.assertIsNone(guide.style_note_hint(self.cfg, index))


class DecisionsNoteTest(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.cfg = SimpleNamespace(
            work_dir=root / "work",
            zh_dir=root / "zh",
            style_file=root / "work" / "style.md",
            decisions_file=root / "work" / "decisions.md")
        self.cfg.zh_dir.mkdir(parents=True)
        guide.seed_notes(self.cfg)
        self.stamp = 1_700_000_000

    def add_chunks(self, stem, count, stamp=None):
        stamp = self.stamp if stamp is None else stamp
        for ci in range(1, count + 1):
            path = self.cfg.zh_dir / chunks.chunk_name(stem, ci)
            path.write_text("@@@SEG 001@@@\n译文\n", encoding="utf-8")
            os.utime(path, (stamp, stamp))

    def entry(self, count):
        return {"chunks": count, "chunk_segments": [1] * count}

    def test_empty_template_points_at_the_finished_chapter(self):
        self.add_chunks("009_chapter", 2)
        index = {"009_chapter.xhtml": self.entry(2)}
        self.assertEqual(guide.decisions_note_hint(self.cfg, index=index),
                         "009_chapter.xhtml")

    def test_appending_after_the_chapter_clears_the_hint(self):
        self.add_chunks("009_chapter", 2)
        self.cfg.decisions_file.write_text("# 已定译法\n\n- 用「你」\n",
                                           encoding="utf-8")
        os.utime(self.cfg.decisions_file,
                 (self.stamp + 100, self.stamp + 100))
        index = {"009_chapter.xhtml": self.entry(2)}
        self.assertIsNone(guide.decisions_note_hint(self.cfg, index=index))

    def test_decisions_older_than_the_chapter_still_trigger(self):
        self.cfg.decisions_file.write_text("# 已定译法\n\n- 用「你」\n",
                                           encoding="utf-8")
        os.utime(self.cfg.decisions_file,
                 (self.stamp - 100, self.stamp - 100))
        self.add_chunks("009_chapter", 2)
        index = {"009_chapter.xhtml": self.entry(2)}
        self.assertEqual(guide.decisions_note_hint(self.cfg, index=index),
                         "009_chapter.xhtml")

    def test_only_chapters_before_the_current_target_count(self):
        self.add_chunks("009_chapter", 2)
        self.add_chunks("010_next", 1)
        index = {"009_chapter.xhtml": self.entry(2),
                 "010_next.xhtml": self.entry(2)}
        self.assertEqual(
            guide.decisions_note_hint(self.cfg, before="010_next.xhtml",
                                      index=index),
            "009_chapter.xhtml")
        self.assertIsNone(
            guide.decisions_note_hint(self.cfg, before="009_chapter.xhtml",
                                      index=index))

    def test_reports_the_earliest_unrecorded_chapter(self):
        self.add_chunks("009_chapter", 2, stamp=self.stamp)
        self.add_chunks("010_chapter", 2, stamp=self.stamp + 100)
        self.cfg.decisions_file.write_text("# 已定译法\n\n- 用「你」\n",
                                           encoding="utf-8")
        os.utime(self.cfg.decisions_file,
                 (self.stamp - 100, self.stamp - 100))
        index = {"009_chapter.xhtml": self.entry(2),
                 "010_chapter.xhtml": self.entry(2)}
        self.assertEqual(guide.decisions_note_hint(self.cfg, index=index),
                         "009_chapter.xhtml")

    def test_single_chunk_files_never_trigger(self):
        self.add_chunks("cover", 1)
        index = {"cover.xhtml": self.entry(1)}
        self.assertIsNone(guide.decisions_note_hint(self.cfg, index=index))


if __name__ == "__main__":
    unittest.main()
