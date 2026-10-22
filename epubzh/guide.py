#!/usr/bin/env python3
"""Translation guide rendering and per-chunk subagent prompts."""
import csv
import re
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config as config_mod
from . import chunks, markup, toc

# Cap the glossary injected into a small-profile prompt. Large profiles get
# the full guide (and therefore the full glossary) instead.
MAX_PROMPT_TERMS = 60
MAX_INTRODUCED = 80


def read_terms(cfg):
    """Return [(english, chinese, note)] from work/terms.tsv."""
    rows = []
    if not cfg.terms_tsv.exists():
        return rows
    with open(cfg.terms_tsv, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if len(row) < 2:
                continue
            en, zh = row[0].strip(), row[1].strip()
            if not en or en == "English" or not zh:
                continue
            note = row[2].strip() if len(row) > 2 else ""
            rows.append((en, zh, note))
    return rows


def seed_terms(cfg):
    if cfg.terms_tsv.exists():
        return
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.terms_tsv.write_text(
        config_mod.DEFAULT_TERMS.read_text(encoding="utf-8"), encoding="utf-8")
    print("wrote %s (edit this glossary before translating)" % cfg.terms_tsv)


def render_guide(cfg):
    template = config_mod.DEFAULT_GUIDE_TEMPLATE.read_text(encoding="utf-8")
    terms = read_terms(cfg)
    table = "\n".join("| %s | %s |" % (en, zh) for en, zh, _ in terms)
    if not table:
        table = "| （在此填写术语） | |"
    text = (template
            .replace("{{TITLE}}", cfg.title or "（中文书名）")
            .replace("{{SOURCE_TITLE}}", cfg.source_title or cfg.title)
            .replace("{{AUTHOR}}", cfg.author or "（作者）")
            .replace("{{LANGUAGE}}", cfg.language)
            .replace("{{OPEN}}", "\u27e6")
            .replace("{{CLOSE}}", "\u27e7")
            .replace("{{TERMS}}", table)
            .replace("{{WORK}}", str(cfg.work_dir)))
    cfg.guide_file.write_text(text, encoding="utf-8")
    print("wrote %s (%d terms)" % (cfg.guide_file, len(terms)))


def _module_cmd(cfg):
    package_root = Path(__file__).resolve().parent.parent
    return "PYTHONPATH=%s python3 -m epubzh" % shlex.quote(str(package_root))


def _term_pattern(term):
    """Match an English term with its common inflections."""
    body = re.escape(term)
    if term.lower().endswith("y"):
        body = re.escape(term[:-1]) + r"(?:y|ies)"
    return re.compile(r"(?<![A-Za-z])" + body + r"(?:s|es|ed|ing)?(?![A-Za-z])",
                      re.IGNORECASE)


def canonical_term(zh):
    """Preferred Chinese rendering of a glossary cell, notes stripped."""
    return re.split(r"[（(；;、/|]", zh, 1)[0].strip()


def terms_for_chunk(cfg, text):
    """Terms that plausibly occur in this chunk, capped for prompt size."""
    plain = markup.strip_placeholders(text)
    hits = []
    for en, zh, note in read_terms(cfg):
        m = _term_pattern(en).findall(plain)
        if m:
            hits.append((len(m), en, canonical_term(zh) or zh))
    hits.sort(key=lambda x: (-x[0], x[1].lower()))
    return [(en, zh) for _, en, zh in hits[:MAX_PROMPT_TERMS]]


def _read_introduced(path):
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _spine_order(cfg):
    """Source file name -> reading position, taken from the OPF spine.

    File names do not follow reading order (a part frontmatter can sort after
    the chapter it introduces), so term bookkeeping must use the spine.
    """
    try:
        opf = toc.find_opf(cfg)
    except Exception:
        return {}
    if not opf:
        return {}
    try:
        root = ET.fromstring(Path(opf).read_text(encoding="utf-8"))
    except (ET.ParseError, OSError):
        return {}
    manifest = {}
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "item":
            continue
        mid, href = el.get("id"), el.get("href")
        if mid and href:
            manifest[mid] = Path(href).name
    order = {}
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "itemref":
            continue
        name = manifest.get(el.get("idref"))
        if name and name not in order:
            order[name] = len(order)
    return order


def _chunk_rank(path, order):
    """(spine position, chunk number) for a translated chunk file."""
    m = re.match(r"(.+)\.(\d+)$", Path(path).stem)
    if not m:
        return None
    stem, number = m.group(1), int(m.group(2))
    for name, rank in order.items():
        if Path(name).stem == stem:
            return (rank, number)
    return None


def introduced(cfg, refresh=False, exclude=None):
    """English terms already rendered as「中文（English）」in earlier chunks.

    ``exclude`` is the chunk currently being translated. Only chunks that come
    before it in reading order count as earlier context: its own older copy,
    and chapters that follow it, must not be treated as already introduced.
    """
    path = cfg.introduced_tsv
    zh_files = sorted(cfg.zh_dir.glob("*.txt")) if cfg.zh_dir.exists() else []
    if exclude is not None:
        ex = Path(exclude).resolve()
        order = _spine_order(cfg)
        ex_rank = _chunk_rank(ex, order) if order else None
        if ex_rank is not None:
            zh_files = [p for p in zh_files
                        if (_chunk_rank(p, order) or (10 ** 9, 0)) < ex_rank]
        else:
            # Without a usable spine, fall back to excluding the chunk itself.
            zh_files = [p for p in zh_files if p.resolve() != ex]
    if exclude is None and path.exists() and not refresh and zh_files:
        newest = max(p.stat().st_mtime for p in zh_files)
        if path.stat().st_mtime >= newest:
            return _read_introduced(path)
    found = {}
    pat = re.compile(r"[\u4e00-\u9fff][^（(]{0,24}[（(]"
                     r"([A-Za-z][A-Za-z0-9 ._\-/]{0,40})[）)]")
    for p in zh_files:
        for m in pat.finditer(p.read_text(encoding="utf-8")):
            en = " ".join(m.group(1).split())
            if len(en) >= 2 and re.search(r"[A-Za-z]{2}", en):
                found.setdefault(en.lower(), en)
    values = sorted(found.values(), key=str.lower)
    # The cache is only valid for a full scan; a chunk-scoped scan would lose
    # the excluded chunk's terms and poison later prompts.
    if exclude is None:
        path.write_text("".join(v + "\n" for v in values), encoding="utf-8")
    return values


CJK_RE = re.compile("[\u4e00-\u9fff]")


def introduced_for_chunk(cfg, chunk_text, exclude=None):
    """Already-introduced terms that actually occur in this chunk.

    Restricting the list to the current chunk keeps the prompt short and
    actionable instead of dumping every annotation in the book. ``exclude``
    keeps a retranslated chunk from seeing its own old copy.
    """
    plain = markup.strip_placeholders(chunk_text)
    out = []
    for en in introduced(cfg, exclude=exclude):
        if len(en) < 2 or not re.search(r"[A-Za-z]{2}", en):
            continue
        if _term_pattern(en).search(plain):
            out.append(en)
    return out


def _tail(path, limit=500, min_cjk=30):
    """Chinese prose at the end of a translated chunk, for tone matching."""
    if not path.exists():
        return ""
    body = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("@@@SEG") or stripped.startswith("###"):
            continue
        if stripped == "@@@VERBATIM@@@":
            continue
        if stripped:
            body.append(stripped)
    text = markup.PH_RE.sub("", " ".join(body))
    text = re.sub(r"\s+", " ", text).strip()
    if len(CJK_RE.findall(text)) < min_cjk:
        return ""
    return text[-limit:]


def _preceding_translation(cfg, name, chunk, index):
    """Last translated text before this chunk: previous chunk or previous file."""
    stem = Path(name).stem
    if chunk > 1:
        tail = _tail(cfg.zh_dir / chunks.chunk_name(stem, chunk - 1))
        if tail:
            return "上一分块译文结尾", tail
    order = sorted(index)
    if name in order:
        i = order.index(name)
        if i > 0:
            prev = order[i - 1]
            ci = index[prev]["chunks"]
            tail = _tail(cfg.zh_dir / chunks.chunk_name(Path(prev).stem, ci))
            if tail:
                return "上一文件译文结尾", tail
    return None, ""


def _rulecard():
    return config_mod.DEFAULT_RULECARD.read_text(encoding="utf-8")


def _contract(cfg, stem, chunk, dst):
    return """## 执行契约（务必遵守，违反任何一条都会导致返工）

1. 你的第一条动作必须是写输出文件；不要复述任务、不要解释思路、不要输出
   「我将翻译」「Actually, I'll ...」这类句子。
2. 优先一次 `write` 写完整个分块，不要习惯性边写边追加。只有 `write` 明显
   被截断时才追加；追加前先 `read` 文件末尾，确认最后一段编号 n：
   - oldString 只取最后一段（`@@@SEG nnn@@@` + 该段译文），不要带上倒数第二段。
   - newString = oldString + 换行 + 新段，绝对不要在 newString 里重复最后一段。
   - `edit` 返回 identical / 找不到 oldString / 多处匹配时，不要重试，直接
     `write` 覆盖整个分块。
3. 写完运行自检（`--fix` 会自动修复机械错误）：
   `%s check --config %s %s --chunk %d --fix`
4. 自检报错按类型处理，不要通读整块：
   - `segment markers`：`--fix` 会自动删除重复段、删除越界段、按段号排序；
     仍报 `missing seg N: insert between ... and ...` 时，用 `edit` 在指定的
     两个标记之间插入 `@@@SEG NNN@@@` 和该段译文，**不要追加到文件末尾**。
   - 报 `kept the last copy of ambiguous seg N` 时，检查这一段是否正确；不确定就
     只重写这一段。
   - `placeholder mismatch` / `stray placeholder` / `not translated` /
     `missing space`：只改报错的那一段。`edit` 的 oldString 必须是该段当前
     完整文本（标记行 + 译文），newString 是修正后的同一段。
   - `file missing`：只报其他分块时忽略并结束。
5. 同一分块最多 3 轮自检；仍不过就只回复一行 `失败：<原因>` 并结束。
6. 完成后只回复一行：`已写入 %s；check OK`
""" % (_module_cmd(cfg), shlex.quote(str(cfg.path)), stem, chunk, dst)


def _build_prompt(cfg, name, chunk, index, full=False):
    info = index[name]
    stem = Path(name).stem
    src = cfg.segs_dir / chunks.chunk_name(stem, chunk)
    dst = cfg.zh_dir / chunks.chunk_name(stem, chunk)
    nseg = chunks.chunk_counts(cfg, name, info)[chunk - 1]
    chunk_text = src.read_text(encoding="utf-8")
    use_full = full or cfg.profile == "large"

    parts = []
    parts.append("你是资深技术图书译者，正在翻译《%s》的一个分块。" %
                 (cfg.title or "（书名）"))
    parts.append("")
    parts.append("项目目录：%s" % cfg.root)
    parts.append("分块：%s 第 %d/%d 块，共 %d 段" %
                 (stem, chunk, info["chunks"], nseg))
    parts.append("输入文件：%s" % src)
    parts.append("输出文件：%s" % dst)
    parts.append("")
    parts.append("**立即开始：读完输入文件后直接写输出文件，不要先回复计划。**")
    parts.append("")

    if use_full:
        parts.append("## 翻译规范（完整，请逐条执行）")
        parts.append("")
        if cfg.guide_file.exists():
            parts.append(cfg.guide_file.read_text(encoding="utf-8").rstrip())
        else:
            parts.append(_rulecard().rstrip())
            parts.append("")
            parts.append("（完整的 work/TRANSLATION_GUIDE.md 不存在，"
                         "请先运行 `epubzh guide`。）")
        parts.append("")
    else:
        parts.append("## 翻译规则（完整规则卡）")
        parts.append("")
        parts.append(_rulecard().rstrip())
        parts.append("")

        terms = terms_for_chunk(cfg, chunk_text)
        parts.append("## 本块术语（必须统一使用）")
        parts.append("")
        if terms:
            parts.append("| 英文 | 中文 |")
            parts.append("| --- | --- |")
            for en, zh in terms:
                parts.append("| %s | %s |" % (en, zh))
            parts.append("")
            parts.append("表中没有的术语，先查 `%s`，不要自创变体。" % cfg.terms_tsv)
        else:
            parts.append("本块未命中术语表；遇到术语先查 `%s`。" % cfg.terms_tsv)
        parts.append("")

    seen = introduced_for_chunk(cfg, chunk_text, exclude=dst)
    if seen:
        shown = "、".join(seen[:MAX_INTRODUCED])
        if len(seen) > MAX_INTRODUCED:
            shown += " 等 %d 条" % len(seen)
        parts.append("## 已出现过的术语（本块会用到）")
        parts.append("")
        parts.append("以下术语前面已经用「中文（English）」介绍过，本块只用中文，"
                     "不要再附英文：")
        parts.append(shown)
        parts.append("")

    context = []
    style = cfg.style_file
    if style.exists():
        context.append(("风格基准（沿用这些取舍）", style.read_text(encoding="utf-8")[-1200:]))
    decisions = cfg.decisions_file
    if decisions.exists():
        context.append(("已定译法与风格决定", decisions.read_text(encoding="utf-8")[-1200:]))
    label, tail = _preceding_translation(cfg, name, chunk, index)
    if tail:
        context.append((label + "（只用于对齐语气，不要照抄）", tail))
    if context:
        parts.append("## 风格与上文")
        parts.append("")
        for title, body in context:
            parts.append("### %s" % title)
            parts.append(body.rstrip())
            parts.append("")

    state, last = chunks.chunk_state(dst, nseg)
    if state == "partial":
        parts.append("## 这是一个未写完的分块")
        parts.append("")
        parts.append("输出文件已有第 1–%d 段，且编号正确。不要重写这些段落，"
                     "从 `@@@SEG %03d@@@` 开始追加到文件末尾；"
                     "补齐后再按执行契约自检。" % (last, last + 1))
        parts.append("")
    elif state == "broken":
        parts.append("## 已有文件编号异常")
        parts.append("")
        parts.append("输出文件已存在但段号不连续。先运行 `check --chunk %d --fix`；"
                     "修复后仍不连续，再整体重写为正确的 1..%d 段。"
                     % (chunk, nseg))
        parts.append("")

    parts.append(_contract(cfg, stem, chunk, dst).rstrip())
    return "\n".join(parts) + "\n"


def prompt(cfg, name=None, chunk=None, stdout=False, full=False, force=False):
    index = chunks.read_index(cfg)
    if not name:
        pending = []
        for filename in sorted(index):
            stem = Path(filename).stem
            counts = chunks.chunk_counts(cfg, filename, index[filename])
            for ci, nseg in enumerate(counts, 1):
                state, last = chunks.chunk_state(
                    cfg.zh_dir / chunks.chunk_name(stem, ci), nseg)
                if state != "done":
                    pending.append((stem, ci, state, last, nseg))
        if not pending:
            print("all chunks translated")
            return
        print("profile: %s" % cfg.profile)
        print("pending chunks (%d):" % len(pending))
        for stem, ci, state, last, nseg in pending:
            extra = ""
            if state == "partial":
                extra = " (partial %d/%d)" % (last, nseg)
            elif state == "broken":
                extra = " (broken)"
            print("  %s %d%s" % (stem, ci, extra))
        print("\nusage: %s prompt <file-stem> --chunk <n>" % _module_cmd(cfg))
        return

    matches = [f for f in index if Path(f).stem == name or f == name]
    if not matches:
        matches = [f for f in index if name in f]
    if len(matches) != 1:
        raise SystemExit("file %r matches %d entries: %s" % (name, len(matches), matches[:8]))
    filename = matches[0]
    info = index[filename]
    stem = Path(filename).stem
    if chunk is None:
        print("profile: %s (%d words per chunk, %s)" % (
            cfg.profile, cfg.words_per_chunk,
            "max %d segments" % cfg.max_segments_per_chunk
            if cfg.max_segments_per_chunk else "no segment cap"))
        print("%s: %d chunk(s), %d segment(s), %d words" % (
            filename, info["chunks"], info["segments"], info["words"]))
        counts = chunks.chunk_counts(cfg, filename, info)
        for ci, nseg in enumerate(counts, 1):
            state, last = chunks.chunk_state(
                cfg.zh_dir / chunks.chunk_name(stem, ci), nseg)
            extra = " (%d/%d)" % (last, nseg) if state == "partial" else ""
            print("  chunk %d: %s%s" % (ci, state, extra))
        return
    if chunk < 1 or chunk > info["chunks"]:
        raise SystemExit("chunk must be 1..%d" % info["chunks"])

    text = _build_prompt(cfg, filename, chunk, index, full=full)
    if cfg.guide_file.exists() and cfg.terms_tsv.exists() \
            and cfg.terms_tsv.stat().st_mtime > cfg.guide_file.stat().st_mtime:
        print("warning: terms.tsv is newer than TRANSLATION_GUIDE.md; "
              "run `epubzh guide` to refresh the full guide")
    if stdout:
        print(text)
        return
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.prompts_dir / ("%s.%02d.md" % (stem, chunk))
    if path.exists() and not force and path.read_text(encoding="utf-8") == text:
        print("prompt unchanged: %s" % path)
    else:
        path.write_text(text, encoding="utf-8")
        print("wrote %s" % path)
    print("→ 交给一个 subagent 执行（一个分块一个 subagent），"
          "不要在主会话里直接翻译")
    print("→ 自检：%s check --config %s %s --chunk %d --fix"
          % (_module_cmd(cfg), shlex.quote(str(cfg.path)), stem, chunk))
