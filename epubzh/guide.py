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


def _contract(cfg, stem, chunk, dst, state, last, nseg):
    check_cmd = "%s check --config %s %s --chunk %d --fix" % (
        _module_cmd(cfg), shlex.quote(str(cfg.path)), stem, chunk)

    if state == "repair":
        first = (
            "1. 输出文件已有完整译文，但自检未通过。你的第一条动作必须是运行自检：\n"
            "   `%s`\n"
            "   不要先写文件，不要整块重写，也不要重新编号。`--fix` 只处理\n"
            "   机械错误；仍报错的段按第 4 条只改那一段。" % check_cmd
        )
        second = (
            "2. 只改自检点名的段。`placeholder mismatch` 的 `extra` 标记直接删除；\n"
            "   源文是单独 `⟦n⟧` 时不要补 `⟦/n⟧`。占位符编号每段独立，不要跨段比较。"
        )
    elif state == "broken":
        first = (
            "1. 输出文件已存在但段号不连续。你的第一条动作必须是运行自检修复：\n"
            "   `%s`\n"
            "   不要先写文件，不要整块重写。`--fix` 会自动删除重复段、删除越界段、\n"
            "   按段号排序；修复后若仍报段号不连续，才进入第 2 条。" % check_cmd
        )
        second = (
            "2. 修复后只处理自检报错的段，不要通读或重写整块。报\n"
            "   `missing seg N: insert between ...` 时按第 4 条在指定位置插入；\n"
            "   只有文件结构严重损坏、无法按段号修复时，才用 `write` 重写整个\n"
            "   分块为正确的 1..%d 段。" % nseg
        )
    elif state == "partial":
        first = (
            "1. 输出文件已有第 1–%d 段。第一条动作是 `read` 文件末尾，确认最后一段\n"
            "   编号 %d；不要 `write` 覆盖已有内容，也不要从第 1 段重写。"
            % (last, last)
        )
        second = (
            "2. 从 `@@@SEG %03d@@@` 开始追加：`edit` 的 oldString 只取最后一段\n"
            "   （标记行 + 译文），newString = oldString + 换行 + 新段，绝对不要重复\n"
            "   最后一段。`edit` 返回 identical / 找不到 / 多处匹配时不要重试；先\n"
            "   `read` 完整文件，再 `write` 覆盖，确保第 1–%d 段原样保留。"
            % (last + 1, last)
        )
    elif state == "done":
        first = (
            "1. 输出文件已存在，段号完整且自检通过。默认不要重写；只有用户明确\n"
            "   要求重译时，才先删除输出文件再重新翻译。"
        )
        second = (
            "2. 如果用户要求局部修改，只改指定段落，不要通读或重写整块。"
        )
    else:
        first = (
            "1. 你的第一条动作必须是写输出文件；不要复述任务、不要解释思路、不要输出\n"
            "   「我将翻译」「Actually, I'll ...」这类句子。"
        )
        second = (
            "2. 优先一次 `write` 写完整个分块，不要习惯性边写边追加。只有 `write` 明显\n"
            "   被截断时才追加；追加前先 `read` 文件末尾，确认最后一段编号 n：\n"
            "   - oldString 只取最后一段（`@@@SEG nnn@@@` + 该段译文），不要带上倒数第二段。\n"
            "   - newString = oldString + 换行 + 新段，绝对不要在 newString 里重复最后一段。\n"
            "   - `edit` 返回 identical / 找不到 oldString / 多处匹配时，不要重试，直接\n"
            "     `write` 覆盖整个分块。"
        )

    return """## 执行契约（务必遵守，违反任何一条都会导致返工）

%s

%s
3. 运行自检（`--fix` 会自动修复机械错误）：
   `%s`
4. 自检报错按类型处理，不要通读整块：
   - `segment markers`：`--fix` 会自动删除重复段、删除越界段、按段号排序；
     仍报 `missing seg N: insert between ... and ...` 时，用 `edit` 在指定的
     两个标记之间插入 `@@@SEG NNN@@@` 和该段译文，**不要追加到文件末尾**。
   - 报 `kept the last copy of ambiguous seg N` 时，检查这一段是否正确；不确定就
     只重写这一段。
   - `placeholder mismatch` / `stray placeholder` / `missing space`：只改报错的
     那一段。`extra` 里的标记一律删除；成对的多余标记整对删除，不要通过重新编号
     来修复。占位符编号是每段独立的，不同段可以重复使用 `⟦0⟧`、`⟦1⟧`、`⟦2⟧`；
     不要跨段比较编号，也不要为了全局唯一改编号。
     `edit` 的 oldString 必须是该段当前完整文本（标记行 + 译文），newString 是
     修正后的同一段。
   - `not translated`：先判断这一段是否真的需要翻译。人名、机构、地址、代码、URL、
     应保留原文的标题等，原样保留，并在该段内单独加一行 `@@@VERBATIM@@@`（段首或
     段尾都可以）；确实需要翻译的，只改报错的这一段。
   - `file missing`：只报其他分块时忽略并结束。
5. 同一段同一个错误连续 2 次 `check` 仍出现，停止修改并只回复一行
   `失败：<段号 + 原因>`；不要继续猜。
6. 同一分块最多 3 轮自检；仍不过就只回复一行 `失败：<原因>` 并结束。
7. 完成后只回复一行：`已写入 %s；check OK`
""" % (first, second, check_cmd, dst)


def _opening(state, chunk, last):
    """State-aware first action, so recovery prompts do not contradict."""
    if state == "repair":
        return ("**立即开始：先运行 `check --chunk %d --fix` 查看并修复现有译文；"
                "文件已有完整译文，不要整块重写；不要先回复计划。**" % chunk)
    if state == "partial":
        return ("**立即开始：读完输入文件后先 `read` 输出文件末尾，确认最后一段"
                "编号 %d，再从下一段继续追加；不要先回复计划。**" % last)
    if state == "broken":
        return ("**立即开始：先运行 `check --chunk %d --fix` 修复段号，"
                "不要先写文件；不要先回复计划。**" % chunk)
    if state == "done":
        return ("**该分块已完成且自检通过；默认不要重写。只有用户明确要求重译时，"
                "才删除输出文件后重新翻译。**")
    return "**立即开始：读完输入文件后直接写输出文件，不要先回复计划。**"


def _chunk_prompt_state(cfg, filename, chunk, nseg):
    """Numbering state plus a content check for completed chunk files.

    ``chunk_state`` only looks at segment numbers. A file can have all
    segments and still fail placeholder/spacing checks; that is a ``repair``
    job, not a fresh translation. Telling a model to "write the whole chunk"
    in that case destroys good work and restarts the loop.
    """
    stem = Path(filename).stem
    dst = cfg.zh_dir / chunks.chunk_name(stem, chunk)
    state, last = chunks.chunk_state(dst, nseg)
    if state == "done":
        problems = chunks.check_file(cfg, filename, only_chunk=chunk, fix=False)
        if problems:
            return "repair", nseg
    return state, last


def _build_prompt(cfg, name, chunk, index, full=False):
    info = index[name]
    stem = Path(name).stem
    src = cfg.segs_dir / chunks.chunk_name(stem, chunk)
    dst = cfg.zh_dir / chunks.chunk_name(stem, chunk)
    nseg = chunks.chunk_counts(cfg, name, info)[chunk - 1]
    chunk_text = src.read_text(encoding="utf-8")
    use_full = full or cfg.profile == "large"
    state, last = _chunk_prompt_state(cfg, name, chunk, nseg)

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
    parts.append(_opening(state, chunk, last))
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

    if state == "partial":
        parts.append("## 这是一个未写完的分块")
        parts.append("")
        parts.append("输出文件已有第 1–%d 段，且编号正确。不要重写这些段落，"
                     "从 `@@@SEG %03d@@@` 开始追加到文件末尾；"
                     "补齐后再按执行契约自检。" % (last, last + 1))
        parts.append("")
    elif state == "repair":
        parts.append("## 已有译文需要修复")
        parts.append("")
        parts.append("输出文件的段号完整，但自检发现了占位符、空格或未翻译问题。"
                     "先运行 `check --chunk %d --fix`；只修改自检点名的段，"
                     "不要整块重写，也不要重新编号。" % chunk)
        parts.append("")
    elif state == "broken":
        parts.append("## 已有文件编号异常")
        parts.append("")
        parts.append("输出文件已存在但段号不连续。先运行 `check --chunk %d --fix`；"
                     "修复后按自检报错只处理对应段：缺段按指定位置插入，"
                     "只有结构严重损坏、无法按段号修复时，才整体重写为正确的 "
                     "1..%d 段。" % (chunk, nseg))
        parts.append("")

    parts.append(_contract(cfg, stem, chunk, dst, state, last, nseg).rstrip())
    return "\n".join(parts) + "\n"


def prompt(cfg, name=None, chunk=None, stdout=False, full=False, force=False,
           quiet=False):
    index = chunks.read_index(cfg)
    if not name:
        pending = []
        for filename in _ordered_index(cfg, index):
            stem = Path(filename).stem
            counts = chunks.chunk_counts(cfg, filename, index[filename])
            for ci, nseg in enumerate(counts, 1):
                state, last = _chunk_prompt_state(cfg, filename, ci, nseg)
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
            elif state == "repair":
                extra = " (repair)"
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
            state, last = _chunk_prompt_state(cfg, filename, ci, nseg)
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
        if not quiet:
            print("prompt unchanged: %s" % path)
    else:
        path.write_text(text, encoding="utf-8")
        if not quiet:
            print("wrote %s" % path)
    if not quiet:
        print("→ 交给一个 subagent 执行（一个分块一个 subagent），"
              "不要在主会话里直接翻译")
        print("→ 自检：%s check --config %s %s --chunk %d --fix"
              % (_module_cmd(cfg), shlex.quote(str(cfg.path)), stem, chunk))
    return path


def _ordered_index(cfg, index):
    """Index keys in OPF spine order, falling back to file name order."""
    order = _spine_order(cfg)
    if not order:
        return sorted(index)

    def key(name):
        rank = order.get(name)
        if rank is None:
            stem = Path(name).stem
            for candidate, candidate_rank in order.items():
                if Path(candidate).stem == stem:
                    rank = candidate_rank
                    break
        return (0, rank if rank is not None else 10 ** 9, name)

    return sorted(index, key=key)


def next_target(cfg):
    """First chunk that still needs work.

    Existing work that needs attention (repair / partial / broken) wins over
    starting a new chunk, because a broken resume point is usually the chunk
    the previous session was working on. Within each group, reading order is
    preserved. Completed files are re-checked (read-only) so a chunk with all
    segment numbers but a placeholder error is returned as ``repair`` instead
    of being skipped as ``done``.
    """
    index = chunks.read_index(cfg)
    first_pending = None
    for filename in _ordered_index(cfg, index):
        counts = chunks.chunk_counts(cfg, filename, index[filename])
        for ci, nseg in enumerate(counts, 1):
            state, last = _chunk_prompt_state(cfg, filename, ci, nseg)
            target = (filename, ci, state, last, nseg)
            if state in ("repair", "partial", "broken"):
                return target
            if state == "pending" and first_pending is None:
                first_pending = target
    return first_pending
