#!/usr/bin/env python3
"""Translation guide rendering and per-chunk subagent prompts."""
import csv
import os
import re
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config as config_mod
from . import chunks, markup, review, toc

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


# Both files are injected into later chunk prompts once they have content. The
# templates stay as unfilled placeholders: writing them keeps the expected
# location and format visible, and lets `next` notice that the first chapter is
# done but the style notes were never filled in.
STYLE_TEMPLATE = """# 风格基准

<!-- 第一章全部通过后填写：10–15 组英中对照例句，加上硬决定（人称、术语首次
出现格式、标点、句长）。填好后本文件会自动进入后续每个分块的提示词。 -->
"""

DECISIONS_TEMPLATE = """# 已定译法与风格决定

<!-- 每章通过后追加：本章新定的译法、句式取舍，以及放弃某个方案的原因。
填好后本文件会自动进入后续每个分块的提示词。 -->
"""

# A one-line placeholder should not clear the gate: require a few real lines.
MIN_STYLE_LINES = 3
MIN_DECISION_LINES = 1


def seed_notes(cfg):
    """Create empty style/decisions templates when they are missing."""
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    for path, template in ((cfg.style_file, STYLE_TEMPLATE),
                           (cfg.decisions_file, DECISIONS_TEMPLATE)):
        if not path.exists():
            path.write_text(template, encoding="utf-8")


def note_content_count(text):
    """Count real content lines, ignoring headings, blanks, and comments."""
    count, in_comment = 0, False
    for line in text.splitlines():
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
    return count


def note_is_filled(path, minimum=1):
    """True when a notes file has at least ``minimum`` content lines."""
    if not path.exists():
        return False
    try:
        return note_content_count(path.read_text(encoding="utf-8")) >= minimum
    except OSError:
        return False


def render_guide_text(cfg):
    """Rendered guide text plus the number of glossary rows."""
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
    return text, len(terms)


def render_guide(cfg):
    text, count = render_guide_text(cfg)
    cfg.guide_file.write_text(text, encoding="utf-8")
    print("wrote %s (%d terms)" % (cfg.guide_file, count))


def refresh_guide(cfg, quiet=False):
    """Keep TRANSLATION_GUIDE.md in sync with the template and the glossary.

    The full-profile prompt embeds this file, so a stale copy silently undoes
    template fixes (for example the placeholder wording). Refreshing is safe:
    the guide is generated data, and all hand edits belong in terms.tsv,
    style.md, or decisions.md.
    """
    if not cfg.terms_tsv.exists():
        return False
    text, count = render_guide_text(cfg)
    if cfg.guide_file.exists() \
            and cfg.guide_file.read_text(encoding="utf-8") == text:
        return False
    cfg.guide_file.write_text(text, encoding="utf-8")
    if not quiet:
        print("refreshed %s (%d terms)" % (cfg.guide_file, count))
    return True


def _ps_quote(value):
    """Quote a value for PowerShell, where single quotes are doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def quote_path(value):
    """Quote a path for the command syntax of the current platform."""
    if os.name == "nt":
        return _ps_quote(value)
    return shlex.quote(str(value))


def _module_cmd(cfg):
    package_root = Path(__file__).resolve().parent.parent
    if os.name == "nt":
        wrapper = package_root / "epubzh-run.cmd"
        if wrapper.exists():
            return "& %s" % _ps_quote(wrapper)
        return "$env:PYTHONPATH = %s; python -m epubzh" % _ps_quote(package_root)
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
    raw = path.read_text(encoding="utf-8")
    aligned = bool(chunks.ALIGNED_TAG_RE.search(raw))
    body = []
    in_text = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("@@@SEG") or stripped.startswith("###"):
            in_text = False
            continue
        if aligned:
            if chunks.ALIGNED_TAG_RE.match(stripped):
                in_text = True
                continue
            if stripped.startswith("@@@SPAN"):
                in_text = False
                continue
            if not in_text or not stripped:
                continue
        elif stripped == "@@@VERBATIM@@@":
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


def _aligned_rules():
    """Output contract for the aligned mode (model writes no pair markers)."""
    return """## 对齐模式输出格式

输入原文里的行内标记分两种：

- `⟦k⟧ ... ⟦/k⟧`：一对标记，代表链接、加粗、脚注等。**不要把这对标记写进译文。**
- `⟦k⟧`（没有对应的 `⟦/k⟧`）：单个标记，原样留在译文里。

每个 `@@@SEG nnn@@@` 按下面的格式输出：

```
@@@SEG 001@@@
@@@TEXT@@@
整段自然连贯的译文，单标记 ⟦2⟧ 放在合适的位置。
@@@SPAN 0@@@
TEXT 中对应原文 ⟦0⟧...⟦/0⟧ 内容的那段文字
@@@SPAN 1@@@
TEXT 中对应原文 ⟦1⟧...⟦/1⟧ 内容的那段文字
```

规则：

1. `TEXT` 是整段的连贯译文，必须一口气写顺，不要为了行内格式把句子切碎。
2. 原文里每一对 `⟦k⟧ ... ⟦/k⟧`，都在 TEXT 之后写一个 `@@@SPAN k@@@`。
3. `SPAN k` 的内容必须逐字出现在 TEXT 中：直接从 TEXT 复制对应文字，不要改写，不要增删空格或标点。
4. 区间的顺序和嵌套必须与原文一致：SPAN 的顺序就是原文开标记的顺序，外层区间包含内层区间。
5. TEXT 里只保留单标记 `⟦k⟧`；不要把成对标记写进 TEXT，也不要输出原文没有的 SPAN。
6. 整段是代码、命令、日志、URL：原文照抄，并在段内单独一行写 `@@@VERBATIM@@@`。
   普通句子一律翻译；`@@@VERBATIM@@@` 不能用来跳过散文，否则自检会报
   `@@@VERBATIM@@@ on a prose segment`。
7. TEXT 和每个 SPAN 尽量各写一行；需要换行时只在句末换行，不要拆开词语。
8. 译文用中文写完，不要留下成句英文；人名、品牌、术语、代码可以保留。
"""


def _aligned_opening(state, chunk, last):
    if state == "repair":
        return ("**立即开始：先运行 `check --chunk %d --fix` 查看现有译文；"
                "它只修段号、重复段和排序，不会替你补 SPAN。然后只改报错点名的段："
                "把 SPAN 改成 TEXT 里逐字复制的内容，或补上缺失的 SPAN。**" % chunk)
    if state == "partial":
        return ("**立即开始：读完输入文件后先 `read` 输出文件末尾，确认最后一段"
                "编号 %d，再从下一段继续追加。**" % last)
    if state == "broken":
        return ("**立即开始：先运行 `check --chunk %d --fix` 修复段号，"
                "不要先写文件。**" % chunk)
    if state == "done":
        return ("**该分块已完成且自检通过；默认不要重写。只有用户明确要求重译时，"
                "才删除输出文件后重新翻译。**")
    return ("**立即开始：读完输入文件后直接写输出文件。推理放在思考里，"
            "正文第一条消息必须是工具调用。**")


def _aligned_contract(cfg, stem, chunk, dst, state, last, nseg):
    check_cmd = "%s check --config %s %s --chunk %d --fix" % (
        _module_cmd(cfg), quote_path(cfg.path), stem, chunk)
    if state == "repair":
        first = ("1. 输出文件已有完整译文，但自检没通过。第一步动作是运行第 3 条的自检\n"
                 "   命令，不要先写文件，也不要整块重写。")
        second = ("2. 只改自检点名的段。常见错误只有三种：SPAN 文字不是 TEXT 的逐字\n"
                  "   子串、缺少某个 SPAN、TEXT 里混进了成对标记。改完再跑一次自检。")
    elif state == "broken":
        first = ("1. 输出文件段号不连续。第一步动作是运行第 3 条的自检命令，`--fix` 会\n"
                 "   删除重复段、越界段并按段号排序；不要先写文件。")
        second = ("2. 修复后仍缺段时，按报错给的 `insert between ...` 位置插入；只有结构\n"
                  "   严重损坏、无法按段号修复时，才用 `write` 重写整个分块为正确的 1..%d 段。"
                  % nseg)
    elif state == "partial":
        first = ("1. 输出文件已有第 1–%d 段。第一步动作是 `read` 文件末尾，确认最后一段\n"
                 "   编号 %d；不要覆盖已有内容，也不要从第 1 段重写。" % (last, last))
        second = ("2. 从 `@@@SEG %03d@@@` 开始继续追加：`edit` 的 oldString 只取最后一段\n"
                  "   （标记行到最后一个 SPAN 的内容），newString = oldString + 换行 + 新段，\n"
                  "   不要重复最后一段。`edit` 返回 identical / 找不到 / 多处匹配时不要重试，\n"
                  "   直接 `write` 覆盖整个文件，确保第 1–%d 段原样保留。"
                  % (last + 1, last))
    elif state == "done":
        first = "1. 输出文件已存在，段号完整且自检通过。默认不要重写。"
        second = "2. 只有用户明确要求重译时才删除输出文件重来；局部修改只改指定段落。"
    else:
        first = ("1. 第一步动作是 `write` 输出文件。需要推理就在思考里完成，正文第一条\n"
                 "   消息必须是工具调用。")
        second = ("2. 优先一次 `write` 写完整个分块。只有写被截断时才追加：`edit` 的\n"
                  "   oldString 只取最后一段，newString = oldString + 换行 + 新段，不要在\n"
                  "   newString 里重复最后一段。`edit` 返回 identical / 找不到 / 多处匹配时\n"
                  "   不要重试，直接 `write` 覆盖整个分块。")
    return """## 执行契约

%s

%s
3. 写完后运行自检（`--fix` 只处理段号、重复段、越界段、排序、多余闭标记和
   中英文空格，**不会补 SPAN**）：
   `%s`
4. 自检报错时只改报错的那一段，改完再跑一次：
   - `SPAN k text does not appear verbatim in TEXT`：把第 k 个 SPAN 改成 TEXT 里
     对应内容的逐字复制；不要改写 TEXT 来迁就 SPAN。
   - `missing SPAN k`：补一个 `@@@SPAN k@@@`，并按原文顺序放在正确位置。
   - `TEXT must not contain the paired marker`：把该成对标记从 TEXT 删掉，
     对应文字放进 `@@@SPAN k@@@`。
   - `source nesting cannot be reproduced`：保持原文区间的顺序和嵌套，不要移动区间。
   - 报 `not translated` / `untranslated source text` 时，把残留英文译成中文；
     人名、术语、代码可以保留；只有整段确实是代码/日志/URL 才加
     `@@@VERBATIM@@@`。
5. 每轮 = 改 TEXT/SPAN + 自检；用自检末尾的 `N problem(s)` 判断进展，最多 6 轮。
   数字变小就继续，没变小就停（aligned 模式没有 `--placeholders` 兜底）。
   停止时回复一行 `失败：<原因>`，不要问用户。
6. 完成后只回复一行：`已写入 %s；check OK`
""" % (first, second, check_cmd, dst)


SEG_HEAD_RE = re.compile(r"^@@@\s*SEG\s*0*(\d+)\s*@@@\s*$", re.M)


def _single_marker_segments(chunk_text):
    """[(segment number, [markers])] for open markers with no closing pair.

    Self-closing inline tags (anchors, pagebreaks, images) become single
    markers on purpose: the source has no closing tag, so the translation
    must not add one. Naming the segments and their markers spares a weak
    model from scanning the whole chunk to decide which markers need a pair,
    which is where small models tend to start reasoning loops.
    """
    pieces = SEG_HEAD_RE.split(chunk_text)
    out = []
    for i in range(1, len(pieces) - 1, 2):
        number, body = int(pieces[i]), pieces[i + 1]
        closed = {m.group(1)[1:] for m in markup.PH_RE.finditer(body)
                  if m.group(1).startswith("/")}
        singles = []
        for match in markup.PH_RE.finditer(body):
            key = match.group(1)
            if key.startswith("/") or key in closed:
                continue
            if match.group(0) not in singles:
                singles.append(match.group(0))
        if singles:
            out.append((number, singles))
    return out


def _contract(cfg, stem, chunk, dst, state, last, nseg):
    check_cmd = "%s check --config %s %s --chunk %d --fix" % (
        _module_cmd(cfg), quote_path(cfg.path), stem, chunk)

    if state == "repair":
        first = (
            "1. 输出文件已有完整译文，但自检没通过。第一步动作是运行第 3 条的自检\n"
            "   命令，不要先写文件，也不要整块重写。"
        )
        second = (
            "2. 用 `edit`/`write` 只改自检点名的段，改完再跑一次自检。只运行\n"
            "   `--fix` 或 `--placeholders` 不算修复；没有编辑过点名段之前，不得\n"
            "   回复失败，也不得问用户。占位符按原文照抄：原文没有 `⟦/n⟧` 就不要补，\n"
            "   也不要重新编号。"
        )
    elif state == "broken":
        first = (
            "1. 输出文件段号不连续。第一步动作是运行第 3 条的自检命令，`--fix` 会\n"
            "   删除重复段、越界段并按段号排序；不要先写文件。"
        )
        second = (
            "2. 修复后仍缺段时，按报错给的 `insert between ...` 位置插入；只有结构\n"
            "   严重损坏、无法按段号修复时，才用 `write` 重写整个分块为正确的 1..%d 段。"
            % nseg
        )
    elif state == "partial":
        first = (
            "1. 输出文件已有第 1–%d 段。第一步动作是 `read` 文件末尾，确认最后一段\n"
            "   编号 %d；不要覆盖已有内容，也不要从第 1 段重写。" % (last, last)
        )
        second = (
            "2. 从 `@@@SEG %03d@@@` 开始继续追加：`edit` 的 oldString 只取最后一段\n"
            "   （标记行 + 译文），newString = oldString + 换行 + 新段，不要重复最后一段。\n"
            "   `edit` 返回 identical / 找不到 / 多处匹配时不要重试，直接 `write`\n"
            "   覆盖整个文件，确保第 1–%d 段原样保留。" % (last + 1, last)
        )
    elif state == "done":
        first = (
            "1. 输出文件已存在，段号完整且自检通过。默认不要重写。"
        )
        second = (
            "2. 只有用户明确要求重译时才删除输出文件重来；局部修改只改指定段落。"
        )
    else:
        first = (
            "1. 第一步动作是 `write` 输出文件。需要推理就在思考里完成，正文第一条\n"
            "   消息必须是工具调用。"
        )
        second = (
            "2. 优先一次 `write` 写完整个分块。只有写被截断时才追加：`edit` 的\n"
            "   oldString 只取最后一段（`@@@SEG nnn@@@` + 该段译文），newString =\n"
            "   oldString + 换行 + 新段，不要在 newString 里重复最后一段。`edit` 返回\n"
            "   identical / 找不到 / 多处匹配时不要重试，直接 `write` 覆盖整个分块。"
        )

    step3 = ("3. 写完后运行自检（`--fix` 只处理重复段、越界段、段号排序、多余的"
             "单标记闭标记和中英文空格；**缺失的占位符不会被补回**）："
             if state in ("pending", "partial") else
             "3. 自检命令（同上：`--fix` 不补缺失的占位符）：")

    return """## 执行契约

%s

%s
%s
   `%s`
4. 自检报错时只改报错的那一段，改完再跑一次：
   - 占位符按原文照抄；`extra` 里的标记删掉，成对的多余标记整对删除。
   - 报 `placeholder nesting mismatch` 时，说明某个成对区间吞进了或挤出了相邻标记：
     把闭标记移回该区间译文之后，保持原文的嵌套关系。
   - 报 `not translated` / `untranslated source text` 时，把残留英文译成中文；
     人名、术语、代码可以保留；只有整段确实是代码/日志/URL 才加
     `@@@VERBATIM@@@`。
5. 每轮 = 改点名段 + 自检；用自检末尾的 `N problem(s)` 判断进展，最多 6 轮：
   - 数字变小 → 继续下一轮；
   - 没变小 → 只剩占位符错误且没用过兜底时，跑一次
     `check --chunk --fix --placeholders` 再自检；仍没变小就停；
   - 其它错误不要用兜底，直接停。
   停止时回复一行 `失败：<原因>`，不要问用户。
6. 完成后只回复一行：`已写入 %s；check OK`
""" % (first, second, step3, check_cmd, dst)


def _opening(state, chunk, last):
    """State-aware first action, so recovery prompts do not contradict."""
    if state == "repair":
        return ("**立即开始：先运行 `check --chunk %d --fix` 查看现有译文；"
                "它只修段号、重复段和多余闭标记，不会补缺失的占位符。然后按"
                "提示词列出的待修复段补回标记，不要整块重写。**" % chunk)
    if state == "partial":
        return ("**立即开始：读完输入文件后先 `read` 输出文件末尾，确认最后一段"
                "编号 %d，再从下一段继续追加。**" % last)
    if state == "broken":
        return ("**立即开始：先运行 `check --chunk %d --fix` 修复段号，"
                "不要先写文件。**" % chunk)
    if state == "done":
        return ("**该分块已完成且自检通过；默认不要重写。只有用户明确要求重译时，"
                "才删除输出文件后重新翻译。**")
    return ("**立即开始：读完输入文件后直接写输出文件。推理放在思考里，"
            "正文第一条消息必须是工具调用。**")


def _chunk_prompt_state(cfg, filename, chunk, nseg):
    """Numbering state plus a content check for completed chunk files.

    ``chunk_state`` only looks at segment numbers. A file can have all
    segments and still fail a placeholder check; that is a ``repair``
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
    aligned = getattr(cfg, "mode", "inline") == "aligned"
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
    parts.append(_aligned_opening(state, chunk, last) if aligned
                 else _opening(state, chunk, last))
    parts.append("")

    if state in ("repair", "broken"):
        problems = chunks.check_file(cfg, name, only_chunk=chunk, fix=False)
        if problems:
            parts.append("## 本块自检结果（--fix 处理不了这些）")
            parts.append("")
            for problem in problems:
                parts.append("- %s" % problem.replace("\n", "\n  "))
            parts.append("")
            if aligned:
                parts.append("只改上面点名的段：SPAN 必须是 TEXT 里的逐字子串，"
                             "缺失的 SPAN 按原文顺序补上；不要把成对标记写进 TEXT。"
                             "`untranslated source text` 点名的英文词组要译成中文；"
                             "`@@@VERBATIM@@@ on a prose segment` 要删掉标记并翻译。"
                             "`--fix` 只处理段号、重复段和排序，重复运行不会消除这些错误。")
            else:
                parts.append("只改上面点名的段：按原文把缺失的 `⟦n⟧` / `⟦/n⟧` 插回"
                             "对应译文的位置，多余的标记删掉。"
                             "`untranslated source text` 点名的英文词组要译成中文；"
                             "`@@@VERBATIM@@@ on a prose segment` 要删掉标记并翻译。"
                             "`--fix` 只处理段号、重复段和多余闭标记，重复运行不会消除这些错误。")
            parts.append("")

    if aligned:
        parts.append(_aligned_rules().rstrip())
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
    elif use_full:
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

    singles = _single_marker_segments(chunk_text)
    if singles:
        parts.append("## 本块的单占位符（原文就没有闭合标记）")
        parts.append("")
        for number, markers in singles[:20]:
            parts.append("- 第 %d 段：%s" % (number, "、".join(markers)))
        if len(singles) > 20:
            parts.append("- 其余 %d 段同理" % (len(singles) - 20))
        parts.append("")
        parts.append("上面这些标记在原文里没有对应的 `⟦/n⟧`，原样保留即可，"
                     "不要补闭合标记。页码锚点和脚注回跳锚点不可见、最容易漏，"
                     + ("写进 TEXT 时逐段核对，务必保留。" if aligned
                        else "翻译时逐段核对，务必保留。"))
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
    if note_is_filled(style, MIN_STYLE_LINES):
        context.append(("风格基准（沿用这些取舍）", style.read_text(encoding="utf-8")[-1200:]))
    decisions = cfg.decisions_file
    if note_is_filled(decisions, MIN_DECISION_LINES):
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

    parts.append((_aligned_contract(cfg, stem, chunk, dst, state, last, nseg)
                  if aligned else
                  _contract(cfg, stem, chunk, dst, state, last, nseg)).rstrip())
    return "\n".join(parts) + "\n"


def _build_brief_prompt(cfg, name, chunk, index):
    """Minimal fallback prompt for models that loop on the full contract.

    Long rule lists and self-check duties are what weak models get stuck on:
    they try to verify every placeholder inside their reasoning and never
    reach the write call. This version keeps only the output shape, two
    mechanical rules, and the check command, so there is nothing left to
    deliberate about.
    """
    info = index[name]
    stem = Path(name).stem
    src = cfg.segs_dir / chunks.chunk_name(stem, chunk)
    dst = cfg.zh_dir / chunks.chunk_name(stem, chunk)
    nseg = chunks.chunk_counts(cfg, name, info)[chunk - 1]
    state, last = _chunk_prompt_state(cfg, name, chunk, nseg)
    check_cmd = "%s check --config %s %s --chunk %d --fix" % (
        _module_cmd(cfg), quote_path(cfg.path), stem, chunk)

    if state == "partial":
        start = ("输出文件已有第 1–%d 段。先 `read` 文件末尾，从第 %d 段继续追加，"
                 "不要覆盖已有内容。" % (last, last + 1))
    elif state in ("repair", "broken"):
        start = ("输出文件已存在但自检没通过。先运行规则 3 里的自检命令，只改报错的"
                 "那一段，不要整块重写。")
    elif state == "done":
        start = "这个分块已完成且自检通过，默认不要重写。"
    else:
        start = ("现在开始：第一步是 `write` 输出文件。推理放在思考里，"
                 "正文第一条消息必须是工具调用。")

    if getattr(cfg, "mode", "inline") == "aligned":
        return "\n".join([
            "把输入文件的每一段翻译成简体中文，写入输出文件。",
            "",
            "输入：%s" % src,
            "输出：%s" % dst,
            "",
            start,
            "",
            "格式（每个 SEG 一段）：",
            "",
            "@@@SEG 001@@@",
            "@@@TEXT@@@",
            "整段连贯译文，单标记 ⟦2⟧ 保留在合适位置",
            "@@@SPAN 0@@@",
            "TEXT 中对应原文 ⟦0⟧...⟦/0⟧ 内容的逐字片段",
            "",
            "规则：",
            "1. 原文里每一对 `⟦k⟧ ... ⟦/k⟧`，在 TEXT 后写 `@@@SPAN k@@@`；"
            "SPAN 内容必须能在 TEXT 中逐字找到。",
            "2. TEXT 里只保留单标记 `⟦k⟧`；不要写成对标记，也不要补 `⟦/k⟧`。",
            "3. 区间顺序和嵌套与原文一致。",
            "4. 整段是代码、命令、日志、URL：原文照抄，并在段内加一行 "
            "`@@@VERBATIM@@@`。",
            "5. 写完运行下面的命令，按报错逐段修；只跑 `--fix` 不算修复：",
            "   `%s`" % check_cmd,
            "   每轮只改报错的段，最多 6 轮；按 `N problem(s)` 是否变小决定继续，"
            "没变小就回复 `失败：<原因>`。",
            "6. 通过后回复一行 `已写入 %s；check OK`；失败回复一行 `失败：<原因>`。"
            % dst,
        ]) + "\n"

    return "\n".join([
        "把输入文件的每一段翻译成简体中文，写入输出文件。",
        "",
        "输入：%s" % src,
        "输出：%s" % dst,
        "",
        start,
        "",
        "格式（标记行原样照抄）：",
        "",
        "@@@SEG 001@@@",
        "译文",
        "@@@SEG 002@@@",
        "译文",
        "",
        "规则：",
        "1. `⟦0⟧`、`⟦/0⟧` 是原文的行内格式标记：原文有几个就抄几个，顺序不变。",
        "   编号每段独立，不同段可以都从 `⟦0⟧` 开始。",
        "2. 整段是代码、命令、日志、URL：原文照抄，并在段内加一行 "
        "`@@@VERBATIM@@@`。",
        "3. 写完运行下面的命令，按报错逐段修；缺失的占位符要照原文补回，"
        "`--fix` 不会自动补：",
        "   `%s`" % check_cmd,
        "   每轮只改报错的段，最多 6 轮；按 `N problem(s)` 是否变小决定继续。"
        "只跑 `--fix` 不算修复，没有编辑过不许回复失败；",
        "   数字没变小且只剩占位符错误、还没用过兜底时，才加 `--placeholders`"
        " 再查一次；仍不过就回复 `失败：<原因>`。",
        "4. 通过后回复一行 `已写入 %s；check OK`；失败回复一行 `失败：<原因>`。"
        % dst,
    ]) + "\n"


def prompt(cfg, name=None, chunk=None, stdout=False, full=False, force=False,
           quiet=False, brief=False):
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

    if not brief and (full or cfg.profile == "large"):
        # The full prompt embeds the guide, so regenerate it first: a stale
        # copy would undo template fixes on existing projects.
        refresh_guide(cfg, quiet=quiet)
    text = (_build_brief_prompt(cfg, filename, chunk, index) if brief
            else _build_prompt(cfg, filename, chunk, index, full=full))
    if stdout:
        print(text)
        return
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".brief.md" if brief else ".md"
    path = cfg.prompts_dir / ("%s.%02d%s" % (stem, chunk, suffix))
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
              % (_module_cmd(cfg), quote_path(cfg.path), stem, chunk))
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


def _finished_multi_chunks(cfg, index):
    """Multi-chunk files whose chunks all pass the numbering check.

    Single-chunk files are ignored: front matter and part dividers finish
    long before any real chapter, so they say nothing about style stability.
    ``chunk_state`` only looks at segment numbers, which is deliberate; a
    placeholder repair still means the chapter is not finished.
    """
    finished = []
    for filename in _ordered_index(cfg, index):
        counts = chunks.chunk_counts(cfg, filename, index[filename])
        if len(counts) < 2:
            continue
        stem = Path(filename).stem
        states = [chunks.chunk_state(
            cfg.zh_dir / chunks.chunk_name(stem, ci), nseg)[0]
            for ci, nseg in enumerate(counts, 1)]
        if all(state == "done" for state in states):
            finished.append(filename)
    return finished


def first_finished_multi_chunk(cfg, index=None):
    """First fully translated multi-chunk file, or ``None``."""
    if index is None:
        index = chunks.read_index(cfg)
    same = _finished_multi_chunks(cfg, index)
    return same[0] if same else None


def style_note_hint(cfg, index=None):
    """Name the file to report when style.md is still empty.

    Returns ``None`` once the notes are filled in, so the hint disappears for
    good after the first edit. It is a reminder, not a build gate: a book with
    no style notes still translates, just less consistently.
    """
    if note_is_filled(cfg.style_file, MIN_STYLE_LINES):
        return None
    return first_finished_multi_chunk(cfg, index)


def _newest_chunk_mtime(cfg, filename, index):
    """Newest mtime among a finished file's translated chunks."""
    stem = Path(filename).stem
    counts = chunks.chunk_counts(cfg, filename, index[filename])
    times = []
    for ci in range(1, len(counts) + 1):
        try:
            times.append((cfg.zh_dir / chunks.chunk_name(stem, ci))
                         .stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else 0.0


def decisions_note_hint(cfg, before=None, index=None):
    """Name the chapter whose decisions should be appended, if any.

    ``before`` is the file of the chunk `next` is about to hand out; only
    finished multi-chunk files before it are considered, so working inside a
    chapter never triggers a reminder about that same chapter.

    The check is time based: decisions.md must be newer than every finished
    chapter. Reporting the *earliest* chapter older than the file catches a
    backlog (two chapters finished while nothing was appended) instead of
    nagging only the latest. Appending once clears the whole backlog without
    keeping extra state. Rewriting an older chunk later can bring the reminder
    back once; that is noise, not a wrong instruction.
    """
    if index is None:
        index = chunks.read_index(cfg)
    finished = _finished_multi_chunks(cfg, index)
    if before is not None:
        order = _ordered_index(cfg, index)
        if before in order:
            limit = order.index(before)
            finished = [name for name in finished
                        if order.index(name) < limit]
    if not finished:
        return None
    path = cfg.decisions_file
    if not note_is_filled(path, MIN_DECISION_LINES):
        return finished[0]
    try:
        stamp = path.stat().st_mtime
        for filename in finished:
            if _newest_chunk_mtime(cfg, filename, index) > stamp:
                return filename
    except OSError:
        return finished[0]
    return None


def next_target(cfg, resume=False):
    """First chunk that still needs work.

    By default this follows reading order: the first chunk that is not done is
    returned, even if a later chunk is broken or partial. That keeps progress
    sequential and avoids a later file jumping ahead of unstarted front matter.

    With ``resume=True``, existing work that needs attention (repair / partial
    / broken) wins over starting a new chunk, because a broken resume point is
    usually the chunk the previous session was working on. Within each group,
    reading order is preserved. Completed files are re-checked (read-only) so a
    chunk with all segment numbers but a placeholder error is returned as
    ``repair`` instead of being skipped as ``done``.
    """
    index = chunks.read_index(cfg)
    registered = {(row["file"], row["chunk"]) for row in review.read(cfg)}
    if not resume:
        for filename in _ordered_index(cfg, index):
            counts = chunks.chunk_counts(cfg, filename, index[filename])
            for ci, nseg in enumerate(counts, 1):
                if (filename, ci) in registered:
                    continue
                state, last = _chunk_prompt_state(cfg, filename, ci, nseg)
                if state != "done":
                    return (filename, ci, state, last, nseg)
        return None

    first_pending = None
    for filename in _ordered_index(cfg, index):
        counts = chunks.chunk_counts(cfg, filename, index[filename])
        for ci, nseg in enumerate(counts, 1):
            if (filename, ci) in registered:
                continue
            state, last = _chunk_prompt_state(cfg, filename, ci, nseg)
            target = (filename, ci, state, last, nseg)
            if state in ("repair", "partial", "broken"):
                return target
            if state == "pending" and first_pending is None:
                first_pending = target
    return first_pending
