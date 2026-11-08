#!/usr/bin/env python3
"""Chunk files: writing translation jobs, parsing finished translations."""
import hashlib
import json
import re
from collections import Counter, namedtuple
from pathlib import Path

from . import markup, spacing

SEG_RE = re.compile(r"^@@@\s*SEG\s*0*(\d+)\s*@@@\s*$")
VERBATIM_RE = re.compile(r"^@@@\s*VERBATIM\s*@@@$")
ALIGNED_MARK_RE = re.compile(r"@@@\s*(TEXT|SPAN\s+0*(\d+))\s*@@@")
ALIGNED_TAG_RE = re.compile(r"@@@\s*TEXT\s*@@@")
CJK_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# Upper bound for the span-placement search. Real segments have a handful of
# ambiguous occurrences; this only stops a pathological file from hanging.
MAX_ALIGNED_NODES = 200000


class AlignedError(ValueError):
    """A part of an aligned segment cannot be restored deterministically."""


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def chunk_name(stem, index):
    return "%s.%02d.txt" % (stem, index)


def write_chunks(cfg, name, segs, chunks):
    out = []
    stem = Path(name).stem
    for ci, chunk in enumerate(chunks):
        path = cfg.segs_dir / chunk_name(stem, ci + 1)
        lines = []
        lines.append("### FILE: %s" % name)
        lines.append("### CHUNK: %d/%d" % (ci + 1, len(chunks)))
        lines.append("### SEGMENTS: %d" % len(chunk))
        if ci > 0:
            previous = markup.strip_placeholders(chunks[ci - 1][-1].text).strip()
            lines.append("### PRECEDING TEXT (context only, do NOT translate):")
            lines.append(previous[:400])
        lines.append("###")
        for k, seg in enumerate(chunk):
            lines.append("@@@SEG %03d@@@" % (k + 1))
            lines.append(seg.text)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out.append(path)
    return out


def load_marked(path):
    """Return [(number, text, verbatim)] from a seg or translation file.

    A `@@@VERBATIM@@@` line anywhere inside a segment body marks text the
    translator deliberately kept in the source language (code, commands,
    listing output, names, addresses). It is checked for placeholders like any
    other segment but is exempt from the "not translated" and spacing rules.
    The marker line itself is not part of the visible text.
    """
    raw = Path(path).read_text(encoding="utf-8")
    parts, cur = [], None
    for line in raw.splitlines():
        stripped = line.strip()
        m = SEG_RE.match(stripped)
        if m:
            if cur is not None:
                parts.append(cur)
            cur = [int(m.group(1)), [], False]
        elif cur is not None:
            if VERBATIM_RE.match(stripped):
                cur[2] = True
            else:
                cur[1].append(line)
    if cur is not None:
        parts.append(cur)
    return [(n, " ".join(x.strip() for x in body if x.strip()), keep)
            for n, body, keep in parts]


def _ranges(nums):
    """Compact rendering of sorted numbers: 1-5, 8, 11-29."""
    if not nums:
        return ""
    out = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
        start = prev = n
    out.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ", ".join(out)


def _marker_problems(parts, nseg, path=None, chunk=None):
    """Point at duplicated, missing, and out-of-range segment markers."""
    counts = Counter(p[0] for p in parts)
    dup = sorted(n for n, c in counts.items() if c > 1)
    missing = [n for n in range(1, nseg + 1) if n not in counts]
    extra = sorted(n for n in counts if n < 1 or n > nseg)
    bits = []
    if dup:
        bits.append("duplicated: %s" % _ranges(dup))
    if missing:
        bits.append("missing: %s" % _ranges(missing))
    if extra:
        bits.append("out of range: %s" % _ranges(extra))
    detail = "; ".join(bits) or "order is wrong"
    if dup:
        # Show the duplicated body and its line numbers so the extra marker is
        # easy to delete without reading the whole chunk.
        first = next(p for p in parts if p[0] == dup[0])
        body = " ".join(first[1].split())[:60]
        where = ""
        if path is not None:
            try:
                lines = [i for i, line in enumerate(
                    Path(path).read_text(encoding="utf-8").splitlines(), 1)
                    if (SEG_RE.match(line.strip())
                        and int(SEG_RE.match(line.strip()).group(1)) == dup[0])]
                if lines:
                    where = " at line %s" % ", ".join(str(i) for i in lines)
            except OSError:
                pass
        if body:
            detail += " | seg %d%s: %s" % (dup[0], where, body)
    if missing:
        # A missing middle segment must be inserted in place. Name the exact
        # neighbours and show the source so a weak model does not append it
        # at the end of the file.
        present = sorted(n for n in counts if 1 <= n <= nseg)
        for num in missing[:3]:
            prev = max([n for n in present if n < num], default=None)
            nxt = min([n for n in present if n > num], default=None)
            left = "@@@SEG %03d@@@" % prev if prev else "file start"
            right = "@@@SEG %03d@@@" % nxt if nxt else "file end"
            src = ""
            if chunk is not None and 1 <= num <= len(chunk):
                src = "; src: %s" % _clip(chunk[num - 1].text, 300)
            detail += (" | missing seg %d: insert between %s and %s%s"
                       % (num, left, right, src))
        if len(missing) > 3:
            detail += " | ... %d more missing" % (len(missing) - 3)
    return "segment markers != 1..%d (%s)" % (nseg, detail)


def _split_blocks(path):
    """Split a translation file into preamble and per-segment raw blocks.

    Unlike ``load_marked`` this keeps blank lines and original spacing, so a
    repaired file can be written back without reformatting the translator's
    output.
    """
    raw = Path(path).read_text(encoding="utf-8")
    preamble, blocks, cur = [], [], None
    for line in raw.splitlines():
        m = SEG_RE.match(line.strip())
        if m:
            if cur is not None:
                blocks.append(cur)
            cur = [int(m.group(1)), [line]]
        elif cur is None:
            if line.strip():
                preamble.append(line)
        else:
            cur[1].append(line)
    if cur is not None:
        blocks.append(cur)
    return preamble, blocks


def _block_body(lines):
    """Single-line body text used to compare duplicate segment copies."""
    body = []
    for line in lines[1:]:
        stripped = line.strip()
        if stripped and not VERBATIM_RE.match(stripped):
            body.append(stripped)
    return " ".join(body)


RepairReport = namedtuple("RepairReport",
                          "removed dropped reordered ambiguous placeholders")


def _remove_extra_single_closes(lines, seg):
    """Remove ``⟦/n⟧`` when ``n`` is an unpaired single marker.

    The source proves that ``⟦n⟧`` has no closing marker in this segment, so
    every ``⟦/n⟧`` is a mechanical mistake. Removing it cannot change the
    visible translation. Extra paired markers stay untouched because choosing
    which pair to delete would require guessing at meaning.
    """
    body = "\n".join(lines[1:])
    got = [m.group(0) for m in markup.PH_RE.finditer(body)]
    want = []
    for item in seg.items:
        want.append(item["open"])
        if item["kind"] == "pair":
            want.append(item["close"])
    remaining = list(got)
    for marker in want:
        if marker in remaining:
            remaining.remove(marker)
    singles = {item["open"] for item in seg.items
               if item["kind"] == "single"}
    removed = []
    for marker in remaining:
        match = markup.PH_RE.fullmatch(marker)
        if not (match and match.group(1).startswith("/")):
            continue
        number = int(match.group(1)[1:])
        if markup.placeholder(number) not in singles or marker not in body:
            continue
        body = body.replace(marker, "", 1)
        removed.append(marker)
    if not removed:
        return lines, []
    return [lines[0]] + (body.split("\n") if body else []), removed


LITERAL_SPAN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'’&:!?()/+\-]*$")
PAGEBREAK_RE = re.compile(r'epub:type="pagebreak"|role="doc-pagebreak"')
EMPTY_ANCHOR_RE = re.compile(r'^<a\b[^>]*\bid="[^"]+"[^>]*/>$')


def _invisible_anchor(item):
    """True for anchors the reader never sees in the rendered text.

    Pagebreaks and empty ``<a id=.../>`` targets are navigation anchors. Their
    exact word position barely matters visually, so keeping them at an
    approximate place beats dropping them and blocking the chunk. Visible
    elements such as ``<br/>`` and ``<img/>`` are never auto-placed.
    """
    html = (item.get("open_html") or "").strip()
    return bool(PAGEBREAK_RE.search(html) or EMPTY_ANCHOR_RE.match(html))


def _pair_inner(text, marker, close):
    """Text between a pair of placeholders in the source segment."""
    start = text.find(marker)
    if start < 0:
        return ""
    end = text.find(close, start + len(marker))
    if end < 0:
        return ""
    inner = text[start + len(marker):end].strip()
    if markup.PLACEHOLDER_OPEN in inner:
        return ""
    return inner


def _word_bounded(text, start, end, inner):
    if inner[:1].isalnum() and start and text[start - 1].isalnum():
        return False
    if inner[-1:].isalnum() and end < len(text) and text[end].isalnum():
        return False
    return True


def _literal_span(text, inner):
    """Offset of a unique, word-bounded occurrence of ``inner`` in ``text``."""
    if len(inner) < 2 or not LITERAL_SPAN_RE.match(inner) \
            or not re.search("[A-Za-z]", inner):
        return None
    hits, start = [], 0
    while True:
        at = text.find(inner, start)
        if at < 0:
            break
        if _word_bounded(text, at, at + len(inner), inner):
            hits.append(at)
        start = at + 1
    return hits[0] if len(hits) == 1 else None


def _restore_pair(seg, text):
    """Wrap source spans that survived verbatim, returning (text, literals).

    Fallback only: the source span must be a Latin run that appears exactly
    once in the translation, so the insertion point is unambiguous. The
    resulting marker scope can still be approximate (an italic span around
    ``Friends`` will not cover a translated title), which is why this is not
    the default repair.
    """
    restored = []
    used = set()
    for item in seg.items:
        if item["kind"] != "pair":
            continue
        if item["open"] in text or item["close"] in text:
            continue
        inner = _pair_inner(seg.text, item["open"], item["close"])
        if not inner or inner in used:
            continue
        at = _literal_span(text, inner)
        if at is None:
            continue
        text = (text[:at] + item["open"] + text[at:at + len(inner)]
                + item["close"] + text[at + len(inner):])
        restored.append(inner)
        used.add(inner)
    return text, restored


def _visible_len(text):
    """Length of ``text`` without placeholder markers."""
    return len(markup.PH_RE.sub("", text))


def _visible_index(text, target):
    """Index where the visible-character count of ``text`` reaches target."""
    if target <= 0:
        return 0
    seen, i = 0, 0
    while i < len(text):
        match = markup.PH_RE.match(text, i)
        if match:
            i = match.end()
            continue
        if seen >= target:
            return i
        seen += 1
        i += 1
    return len(text)


BOUNDARY_CHARS = set(" \t\n\r，。、；：！？“”‘’（）《》〈〉—…·,"
                     ".!?;:\"'()[]{}<>")


def _in_placeholder(text, at):
    for match in markup.PH_RE.finditer(text):
        if match.start() < at < match.end():
            return True
    return False


def _is_boundary(text, at):
    if at <= 0 or at >= len(text):
        return True
    return text[at - 1] in BOUNDARY_CHARS or text[at] in BOUNDARY_CHARS


def _safe_insert(text, at):
    """Move an insertion point out of a word and onto a nearby boundary."""
    if at <= 0 or at >= len(text):
        return at
    before, after = text[at - 1], text[at]
    if before.isascii() and before.isalnum() \
            and after.isascii() and after.isalnum():
        while at < len(text) and text[at].isascii() and text[at].isalnum():
            at += 1
        return at
    if _is_boundary(text, at) and not _in_placeholder(text, at):
        return at
    for step in range(1, 9):
        for candidate in (at + step, at - step):
            if 0 <= candidate <= len(text) and _is_boundary(text, candidate) \
                    and not _in_placeholder(text, candidate):
                return candidate
    return at


def _restore_single_anchors(seg, text):
    """Approximately re-place invisible single anchors, returning placements.

    The anchor's relative position in the source sentence is applied to the
    translation. It is a fallback: positions are approximate by design, but
    pagebreak ids and endnote back-links survive instead of disappearing.
    """
    src_visible = _visible_len(seg.text) or 1
    missing = []
    for item in seg.items:
        if item["kind"] != "single" or item["open"] in text:
            continue
        if not _invisible_anchor(item):
            continue
        at = seg.text.find(item["open"])
        if at < 0:
            continue
        missing.append((_visible_len(seg.text[:at]) / src_visible,
                        item["open"]))
    placed = []
    for ratio, marker in sorted(missing):
        target = round(ratio * _visible_len(text))
        at = _safe_insert(text, _visible_index(text, target))
        text = text[:at] + marker + text[at:]
        placed.append((marker, ratio))
    return text, placed


def restore_placeholders(path, chunk):
    """Fallback repair for placeholders the translator dropped.

    Pair markers are re-wrapped only when their source span survived verbatim
    and occurs exactly once. Invisible single anchors (pagebreaks, empty
    ``<a id=.../>`` targets) are re-placed at an approximate relative position.
    Anything else is left for the translator. This is only a safety net: the
    normal path is the model keeping and repairing its own markers.

    Returns ``[(segment number, [wrapped literals], [placed markers])]``.
    """
    path = Path(path)
    if not path.exists():
        return []
    # Aligned files are assembled by ``parse_translation``; the inline
    # heuristics would wrap Latin literals across TEXT/SPAN boundaries and
    # corrupt the file.
    if is_aligned_file(path):
        return []
    preamble, blocks = _split_blocks(path)
    offset = chunk[0].index
    fixes, changed = [], False
    for block in blocks:
        num, lines = block
        if not 1 <= num <= len(chunk):
            continue
        seg = chunk[num - 1]
        if seg.index - offset + 1 != num:
            continue
        text, wrapped = _restore_pair(seg, "\n".join(lines[1:]))
        text, placed = _restore_single_anchors(seg, text)
        if not wrapped and not placed:
            continue
        block[1] = [lines[0]] + text.split("\n")
        fixes.append((num, wrapped, placed))
        changed = True
    if changed:
        out = list(preamble)
        for _, lines in blocks:
            out.extend(lines)
        path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return fixes


def repair_chunk(path, chunk):
    """Repair mechanically invalid segment blocks in a translation file.

    Duplicates usually come from a bad edit-append boundary: the last segment
    is included in both oldString and newString. The repair is deterministic:

    * blocks with segment numbers outside ``1..len(chunk)`` are dropped;
    * for repeated numbers, one copy is kept:
      - identical copies collapse to the first;
      - exactly one placeholder-valid copy wins;
      - otherwise the last valid copy is kept (or the last copy when none is
        valid) and reported as ambiguous;
    * remaining blocks are sorted by segment number when out of order;
    * an extra ``⟦/n⟧`` for a single ``⟦n⟧`` is removed (the source has no
      closing marker, so this is unambiguous).

    Missing segments are not invented; ``parse_translation`` reports where
    they belong. Returns a ``RepairReport``.
    """
    path = Path(path)
    if not path.exists():
        return RepairReport([], [], False, [], [])
    aligned = is_aligned_file(path)
    preamble, blocks = _split_blocks(path)
    drop = set()
    removed, dropped, ambiguous = [], [], []

    for i, (num, _) in enumerate(blocks):
        if num < 1 or num > len(chunk):
            drop.add(i)
            dropped.append(num)

    by_num = {}
    for i, (num, _) in enumerate(blocks):
        if i not in drop:
            by_num.setdefault(num, []).append(i)

    for num in sorted(n for n, idxs in by_num.items() if len(idxs) > 1):
        seg = chunk[num - 1]
        cand = by_num[num]
        bodies = [_block_body(blocks[i][1]) for i in cand]
        if aligned:
            valid = [i for i in cand
                     if _aligned_block_ok(seg,
                                          "\n".join(blocks[i][1][1:]))]
        else:
            valid = [i for i, body in zip(cand, bodies)
                     if body and seg.check(body) is None]
        if len(set(bodies)) == 1:
            keep = cand[0]
        elif len(valid) == 1:
            keep = valid[0]
        elif valid:
            keep = valid[-1]
            ambiguous.append(num)
        else:
            keep = cand[-1]
            ambiguous.append(num)
        for i in cand:
            if i != keep:
                drop.add(i)
        removed.append(num)

    kept = [block for i, block in enumerate(blocks) if i not in drop]
    nums = [num for num, _ in kept]
    reordered = nums != sorted(nums)
    if reordered:
        kept.sort(key=lambda block: block[0])

    placeholder_fixes = []
    if not aligned:
        # In aligned mode position fixes would desynchronise SPAN text from
        # TEXT; leave them to the aligned parser and the model instead.
        for block in kept:
            num, lines = block
            if not 1 <= num <= len(chunk):
                continue
            fixed, markers = _remove_extra_single_closes(lines, chunk[num - 1])
            if markers:
                block[1] = fixed
                placeholder_fixes.append((num, markers))

    if drop or reordered or placeholder_fixes:
        out = list(preamble)
        for _, lines in kept:
            out.extend(lines)
        path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    return RepairReport(removed, sorted(set(dropped)), reordered, ambiguous,
                        placeholder_fixes)


def _marker_number(marker):
    match = markup.PH_RE.fullmatch(marker)
    return int(match.group(1)) if match else None


def _pair_spans(seg):
    """[(number, open_index, close_index)] for the source segment's pairs."""
    out = []
    for item in seg.items:
        if item["kind"] != "pair":
            continue
        number = _marker_number(item["open"])
        start = seg.text.find(item["open"])
        end = seg.text.find(item["close"], start + len(item["open"]))
        if number is None or start < 0 or end < 0:
            continue
        out.append((number, start, end + len(item["close"])))
    out.sort(key=lambda pair: pair[1])
    return out


def _aligned_sections(body):
    """Split an aligned segment body into TEXT and numbered SPAN sections.

    The chunk loader joins every line with a space, so this cannot rely on
    line boundaries; the ``@@@...@@@`` markers are the only structure.
    """
    marks = list(ALIGNED_MARK_RE.finditer(body))
    if not marks:
        raise AlignedError("missing @@@TEXT@@@ block")
    if body[:marks[0].start()].strip():
        raise AlignedError("unexpected text before @@@TEXT@@@")
    text = None
    spans = {}
    order = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        content = " ".join(
            part.strip() for part in body[mark.end():end].splitlines()
            if part.strip())
        if mark.group(1).upper() == "TEXT":
            if text is not None:
                raise AlignedError("duplicated @@@TEXT@@@ block")
            text = content
            continue
        if text is None:
            raise AlignedError("@@@SPAN %d@@@ before @@@TEXT@@@"
                               % int(mark.group(2)))
        number = int(mark.group(2))
        if number in spans:
            raise AlignedError("duplicated @@@SPAN %d@@@" % number)
        spans[number] = content
        order.append(number)
    if text is None:
        raise AlignedError("missing @@@TEXT@@@ block")
    if "@@@" in text:
        raise AlignedError("unexpected @@@ marker inside TEXT")
    return text, [(number, spans[number]) for number in order]


def _occurrences(text, needle):
    """Every start index where ``needle`` occurs, including overlaps."""
    hits, start = [], 0
    while True:
        at = text.find(needle, start)
        if at < 0:
            return hits
        hits.append((at, at + len(needle)))
        start = at + 1


def _vis_prefix(text, index):
    return _visible_len(text[:index])


def _aligned_cost(text, candidate, src_open, src_close, src_total, text_total):
    """Integer distance from the occurrence to the source-relative position."""
    start, end = candidate
    return (abs(_vis_prefix(text, start) * src_total - src_open * text_total)
            + abs(_vis_prefix(text, end) * src_total - src_close * text_total))


def _aligned_placements(text, pairs, spans, seg):
    """Choose one occurrence per span, keeping source order and nesting."""
    src_total = _visible_len(seg.text) or 1
    text_total = _visible_len(text) or 1

    candidates = []
    costs = []
    for number, src_open, src_close in pairs:
        body = spans[number]
        if not body:
            raise AlignedError("SPAN %d is empty; it must contain the "
                               "translated text of the paired span" % number)
        hits = _occurrences(text, body)
        if not hits:
            raise AlignedError(
                "SPAN %d text does not appear verbatim in TEXT: %s"
                % (number, _clip(body, 80)))
        src_a = _vis_prefix(seg.text, src_open)
        src_b = _vis_prefix(seg.text, src_close)
        hits.sort(key=lambda hit: _aligned_cost(
            text, hit, src_a, src_b, src_total, text_total))
        candidates.append(hits)
        costs.append(lambda hit, a=src_a, b=src_b: _aligned_cost(
            text, hit, a, b, src_total, text_total))

    order = [pair[0] for pair in pairs]
    best = {"score": None, "assignment": None, "ties": 0}
    nodes = [0]

    def search(index, assignment, score):
        nodes[0] += 1
        if nodes[0] > MAX_ALIGNED_NODES:
            raise AlignedError("too many ambiguous span placements")
        if best["score"] is not None and score > best["score"]:
            return
        if index == len(pairs):
            if best["score"] is None or score < best["score"]:
                best.update(score=score, assignment=list(assignment), ties=1)
            elif score == best["score"]:
                best["ties"] += 1
            return
        src_open = pairs[index][1]
        for candidate in candidates[index]:
            ok = True
            for earlier, chosen in enumerate(assignment):
                earlier_close = pairs[earlier][2]
                if earlier_close <= src_open:
                    if candidate[0] < chosen[1]:
                        ok = False
                        break
                elif not (chosen[0] <= candidate[0]
                          and candidate[1] <= chosen[1]):
                    ok = False
                    break
            if not ok:
                continue
            assignment.append(candidate)
            search(index + 1, assignment, score + costs[index](candidate))
            assignment.pop()

    search(0, [], 0)
    if best["assignment"] is None:
        raise AlignedError(
            "source nesting cannot be reproduced in TEXT; keep the paired "
            "spans in the same order and nesting as the source")
    if best["ties"] > 1:
        raise AlignedError(
            "ambiguous span placement: several equally good positions for "
            "SPAN %s" % ", ".join(str(n) for n in order))
    return dict(zip(order, best["assignment"]))


def _assemble_aligned(seg, body):
    """Restore pair markers around a model's whole-segment translation.

    The model writes one fluent ``TEXT`` plus the text of every paired span.
    Each span must occur verbatim in ``TEXT``; this function locates the spans,
    checks that their order and nesting match the source, and inserts the
    original placeholder markers.
    """
    text, span_list = _aligned_sections(body)
    spans = dict(span_list)
    pairs = _pair_spans(seg)
    expected = [number for number, _, _ in pairs]
    if sorted(spans) != sorted(expected):
        missing = [n for n in expected if n not in spans]
        extra = sorted(n for n in spans if n not in expected)
        bits = []
        if missing:
            bits.append("missing SPAN %s" % ", ".join(map(str, missing)))
        if extra:
            bits.append("unknown SPAN %s" % ", ".join(map(str, extra)))
        raise AlignedError("paired span mismatch: %s" % "; ".join(bits))

    pair_items = {}
    for item in seg.items:
        if item["kind"] == "pair":
            pair_items[_marker_number(item["open"])] = item
    pair_markers = set()
    for number in expected:
        pair_markers.add(pair_items[number]["open"])
        pair_markers.add(pair_items[number]["close"])
    singles = {item["open"] for item in seg.items
               if item["kind"] == "single"}
    for match in markup.PH_RE.finditer(text):
        marker = match.group(0)
        if marker in pair_markers:
            raise AlignedError(
                "TEXT must not contain the paired marker %s; put its text in "
                "the matching @@@SPAN@@@ block instead" % marker)
        if marker not in singles:
            raise AlignedError("TEXT contains unknown marker %s" % marker)

    placements = _aligned_placements(text, pairs, spans, seg)
    events = []
    for number in expected:
        start, end = placements[number]
        item = pair_items[number]
        events.append((start, seg.text.find(item["open"]), item["open"]))
        events.append((end, seg.text.find(item["close"]), item["close"]))
    events.sort(key=lambda event: (event[0], event[1]))

    out, pos = [], 0
    for at, _, marker in events:
        out.append(text[pos:at])
        out.append(marker)
        pos = at
    out.append(text[pos:])
    assembled = "".join(out)
    # Visible single markers must be kept by the model. Invisible anchors
    # (pagebreaks, empty id targets) may be dropped; re-place those by the
    # source-relative position, exactly as the inline fallback does.
    assembled, _ = _restore_single_anchors(seg, assembled)
    return assembled


def _aligned_block_ok(seg, body):
    if any(VERBATIM_RE.match(line.strip()) for line in body.splitlines()):
        return True
    try:
        text = _assemble_aligned(seg, body)
    except AlignedError:
        return False
    return seg.check(text) is None


def is_aligned_file(path):
    """True when a translation file uses the aligned TEXT/SPAN format."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(ALIGNED_TAG_RE.search(raw))


def parse_translation(path, nseg, chunk=None, mode=None):
    """Parse a translated chunk file into (texts, verbatim flags, error).

    ``mode="aligned"`` requires every non-verbatim segment to use the
    ``@@@TEXT@@@`` format. Without ``mode`` the format is auto-detected, so
    existing projects keep working unchanged.
    """
    path = Path(path)
    if not path.exists():
        return None, None, "file missing"
    aligned = is_aligned_file(path)
    require_aligned = mode == "aligned"
    parts = load_marked(path)
    if not parts:
        return None, None, "no markers found"
    nums = [p[0] for p in parts]
    if nums != list(range(1, nseg + 1)):
        if nums == list(range(1, len(nums) + 1)) and len(nums) < nseg:
            return None, None, ("partial: segments 1..%d of %d; append from %d"
                                % (len(nums), nseg, len(nums) + 1))
        return None, None, _marker_problems(parts, nseg, path, chunk)
    texts, keeps = [], []
    for number, body, keep in parts:
        if keep:
            if chunk is None or not 1 <= number <= len(chunk):
                return None, None, "verbatim segment needs its source chunk"
            text = chunk[number - 1].text
        elif require_aligned and not aligned:
            return None, None, (
                "seg %d/%d: expected aligned format (@@@TEXT@@@ + one "
                "@@@SPAN k@@@ per paired marker); retranslate this chunk"
                % (number, nseg))
        elif aligned:
            if chunk is None or not 1 <= number <= len(chunk):
                return None, None, "aligned segment needs its source chunk"
            try:
                text = _assemble_aligned(chunk[number - 1], body)
            except AlignedError as exc:
                return None, None, "seg %d/%d: %s" % (number, nseg, exc)
        else:
            text = body
        texts.append(text)
        keeps.append(keep)
    return texts, keeps, None



def read_index(cfg):
    if not cfg.index_file.exists():
        raise SystemExit("index not found: %s (run `epubzh extract`)" % cfg.index_file)
    return json.loads(cfg.index_file.read_text(encoding="utf-8"))


def chunk_counts(cfg, name, info=None):
    """Per-chunk segment counts, from the index or recomputed from source."""
    if info is None:
        info = read_index(cfg)[name]
    counts = info.get("chunk_segments")
    if counts and len(counts) == info["chunks"]:
        return list(counts)
    src = cfg.source_dir / cfg.text_dir / name
    work, _ = markup.atomize(src.read_text(encoding="utf-8"), cfg.protect_blocks)
    segs = markup.extract_segments(work, require_latin=cfg.require_latin)
    cl = markup.chunk_segments(segs, cfg.words_per_chunk,
                               cfg.max_segments_per_chunk)
    return [len(c) for c in cl]


def chunk_state(path, nseg):
    """Classify one translation file: pending / partial / broken / done.

    ``partial`` means segments 1..k are present and correctly numbered, but
    the chunk continues; a translator can append the rest instead of
    retranslating.
    """
    path = Path(path)
    if not path.exists():
        return "pending", 0
    nums = [p[0] for p in load_marked(path)]
    if nums == list(range(1, nseg + 1)):
        return "done", nseg
    if nums and nums == list(range(1, len(nums) + 1)) and len(nums) < nseg:
        return "partial", len(nums)
    return "broken", len(nums)


def progress(cfg):
    """Return (done, total) chunk counts for the whole project."""
    index = read_index(cfg)
    done = total = 0
    for name in index:
        info = index[name]
        counts = chunk_counts(cfg, name, info)
        stem = Path(name).stem
        for ci, nseg in enumerate(counts, 1):
            total += 1
            state, _ = chunk_state(cfg.zh_dir / chunk_name(stem, ci), nseg)
            if state == "done":
                done += 1
    return done, total


def status(cfg):
    index = read_index(cfg)
    rows = []
    cap = ("max %d segments" % cfg.max_segments_per_chunk
           if cfg.max_segments_per_chunk else "no segment cap")
    print("profile: %s (%d words per chunk, %s)"
          % (cfg.profile, cfg.words_per_chunk, cap))
    for name in sorted(index):
        info = index[name]
        counts = chunk_counts(cfg, name, info)
        stem = Path(name).stem
        done = partial = broken = 0
        for ci, nseg in enumerate(counts, 1):
            state, _ = chunk_state(cfg.zh_dir / chunk_name(stem, ci), nseg)
            if state == "done":
                done += 1
            elif state == "partial":
                partial += 1
            elif state == "broken":
                broken += 1
        rows.append((name, done, len(counts), info["segments"], info["words"],
                     partial, broken))
    total_done = sum(r[1] for r in rows)
    total_chunks = sum(r[2] for r in rows)
    total_partial = sum(r[5] for r in rows)
    total_broken = sum(r[6] for r in rows)
    print("%-38s %7s %8s %10s" % ("file", "chunks", "segments", "words"))
    for name, done, nchunks, segs, words, partial, broken in rows:
        mark = "done" if done == nchunks else "%d/%d" % (done, nchunks)
        flags = []
        if partial:
            flags.append("%dp" % partial)
        if broken:
            flags.append("%db" % broken)
        if flags:
            mark += " " + " ".join(flags)
        print("%-38s %7s %8d %10d" % (name, mark, segs, words))
    print("\nchunks: %d/%d translated" % (total_done, total_chunks))
    if total_partial or total_broken:
        print("needs attention: %d partial, %d broken" % (total_partial, total_broken))
    return total_done, total_chunks


def _clip(text, limit=110):
    """Single-line excerpt used in error messages."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _context(seg, translated, limit=110):
    """Source and target excerpt so a translator can fix the line directly.

    Placeholder failures get the full text (``limit=0``): a clipped excerpt
    hides exactly the marker that must be removed or restored.
    """
    clip = (lambda t: t) if not limit else (lambda t: _clip(t, limit))
    return "\n    src: %s\n    got: %s" % (clip(" ".join(seg.text.split())),
                                          clip(" ".join(translated.split())))


def _looks_translatable(body):
    """True when a Latin-only segment is prose, not a name/label/address.

    Names ("Chris Hughes"), affiliations ("DistributedApps.ai, Herndon, VA,
    USA"), labels ("Figure 1.1"), and URLs are not prose. Flagging them as
    "not translated" makes small models edit them in place, get an identical
    edit, and loop. Longer all-capitalized runs are still treated as prose.
    """
    words = body.split()
    if not words or not any(re.search("[A-Za-z]", w) for w in words):
        return False
    if re.fullmatch(r"(?:https?://|www\.|mailto:|doi:\s*)\S+", body,
                    re.IGNORECASE):
        return False
    if not any(w[:1].islower() for w in words):
        # A short capitalized run, or a comma-separated address, is a proper
        # noun rather than an untranslated sentence.
        return len(words) > 4 and "," not in body
    return True


def check_file(cfg, name, only_chunk=None, fix=True, skip_missing=False,
               placeholders=False):
    """Check translated chunks of one source file. Returns problems.

    ``only_chunk`` restricts validation to a single chunk, so a translator
    working on one chunk does not see "file missing" for its siblings.
    ``skip_missing`` omits chunks that have not been started yet; project-wide
    checks use it so expected missing files do not drown out real errors.
    ``fix`` (default on) first removes duplicate segment blocks produced by a
    bad append boundary, and extra closing markers for single placeholders.
    Ambiguous duplicates are reported instead of guessed at. Pass ``fix=False``
    for a read-only check. ``placeholders`` additionally re-wraps pair markers
    whose source text survived verbatim; it is a fallback for ``--fix``, which
    cannot restore missing placeholders on its own.
    """
    index = read_index(cfg)
    if name not in index:
        return ["%s: not in index" % name]
    info = index[name]
    stem = Path(name).stem
    src_path = cfg.source_dir / cfg.text_dir / name
    if info.get("sha256") and file_sha256(src_path) != info["sha256"]:
        return ["%s: source changed since extract, re-run `epubzh extract --force`"
                % name]
    work, atoms = markup.atomize(src_path.read_text(encoding="utf-8"),
                                 cfg.protect_blocks)
    segs = markup.extract_segments(work, require_latin=cfg.require_latin)
    chunks = markup.chunk_segments(segs, cfg.words_per_chunk,
                                   cfg.max_segments_per_chunk)
    problems = []
    if len(chunks) != info["chunks"]:
        problems.append("%s: chunk count changed (%d vs %d), re-run extract"
                        % (name, len(chunks), info["chunks"]))
    if only_chunk is not None:
        if only_chunk < 1 or only_chunk > len(chunks):
            return problems + ["%s: chunk must be 1..%d" % (name, len(chunks))]
        targets = [(only_chunk, chunks[only_chunk - 1])]
    else:
        targets = list(enumerate(chunks, 1))
    for ci, chunk in targets:
        path = cfg.zh_dir / chunk_name(stem, ci)
        if skip_missing and not path.exists():
            continue
        if fix:
            report = repair_chunk(path, chunk)
            if report.dropped:
                print("  fixed %s chunk %d: dropped out-of-range seg %s"
                      % (name, ci, _ranges(report.dropped)))
            if report.removed:
                print("  fixed %s chunk %d: removed duplicate seg %s"
                      % (name, ci, _ranges(report.removed)))
            if report.reordered:
                print("  fixed %s chunk %d: reordered segments"
                      % (name, ci))
            if report.ambiguous:
                print("  !! %s chunk %d: kept the last copy of ambiguous "
                      "seg %s; verify this segment"
                      % (name, ci, _ranges(report.ambiguous)))
            if report.placeholders:
                detail = "; ".join(
                    "seg %d: %s" % (num, ", ".join(markers))
                    for num, markers in report.placeholders)
                print("  fixed %s chunk %d: removed extra single-marker "
                      "close(s) %s" % (name, ci, detail))
        if placeholders:
            fixes = restore_placeholders(path, chunk)
            for num, wrapped, placed in fixes:
                if wrapped:
                    print("  fixed %s chunk %d: auto-wrapped seg %d around %s"
                          % (name, ci, num,
                             ", ".join('"%s"' % word for word in wrapped)))
                if placed:
                    print("  fixed %s chunk %d: auto-placed seg %d anchor(s) %s"
                          % (name, ci, num,
                             ", ".join("%s ~%d%%" % (marker, round(ratio * 100))
                                       for marker, ratio in placed)))
            if fixes:
                print("  note: placeholder position may be approximate; "
                      "verify the rendered formatting")
        translated, verbatim, err = parse_translation(
            path, len(chunk), chunk, mode=getattr(cfg, "mode", None))
        if err:
            problems.append("%s chunk %d: %s" % (name, ci, err))
            continue
        # Report chunk-local numbers, matching the @@@SEG nnn@@@ markers the
        # translator edits. Document-wide numbers would point at the wrong
        # line whenever the chunk is not the first one.
        offset = chunk[0].index
        width = len(chunk)
        for seg, text, keep in zip(chunk, translated, verbatim):
            pos = seg.index + 1 - offset
            bad = seg.check(text)
            if bad:
                full = "placeholder" in bad or "stray" in bad
                problems.append("%s chunk %d seg %d/%d: %s%s"
                                % (name, ci, pos, width, bad,
                                   _context(seg, text, 0 if full else 110)))
                continue
            if keep:
                continue
            body = markup.strip_placeholders(text).strip()
            short = len(body) <= 3 and " " not in body
            if cfg.require_latin and re.search("[A-Za-z]", seg.text) \
                    and not CJK_RE.search(body) and not short \
                    and _looks_translatable(body):
                problems.append(
                    "%s chunk %d seg %d/%d: not translated (if this is a "
                    "name, address, code, or heading that should stay in the "
                    "source language, add a separate @@@VERBATIM@@@ line in "
                    "this segment)%s"
                    % (name, ci, pos, width, _context(seg, text)))
            missing = spacing.has_missing_space(text)
            if missing:
                problems.append("%s chunk %d seg %d/%d: missing space around Latin, "
                                "e.g. %s%s"
                                % (name, ci, pos, width, missing[:3],
                                   _context(seg, text)))
    return problems
