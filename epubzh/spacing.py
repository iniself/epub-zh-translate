#!/usr/bin/env python3
"""Insert spaces between CJK and Latin text (pangu style)."""
import re

from . import markup

CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3005\u3007"
ASCII = "0-9A-Za-z"


def _parse(raw):
    m = markup.TAGNAME_RE.match(raw)
    name = (m.group(1).lower() if m else "")
    return name, raw.startswith("</"), raw.endswith("/>") or name in markup.VOID_TAGS


def spacing(work, protect_tags=None):
    """Insert a space between CJK and latin/number runs.

    Text inside <pre> and configured inline tags is never modified.
    """
    protect = set(protect_tags or [])
    verbatim = protect - {"pre"}
    tokens = markup.tokenize(work)
    pieces = []            # [kind, raw, block, protect]
    depth_pre = 0
    depth_v = 0
    for kind, raw in tokens:
        if kind == "text":
            pieces.append(["text", raw, False, depth_pre > 0 or depth_v > 0])
            continue
        if markup.ATOM_RE.match(raw) or raw.startswith("<!"):
            pieces.append(["tag", raw, True, True])
            continue
        name, closing, selfclose = _parse(raw)
        block = name not in markup.INLINE_PAIRED | markup.INLINE_VERBATIM
        if name == "pre":
            depth_pre = max(0, depth_pre + (-1 if closing else (0 if selfclose else 1)))
        if name in verbatim:
            depth_v = max(0, depth_v + (-1 if closing else (0 if selfclose else 1)))
        pieces.append(["tag", raw, block, depth_pre > 0 or depth_v > 0])

    # 1) inside each unprotected text piece
    for pc in pieces:
        if pc[0] == "text" and not pc[3]:
            t = re.sub(" {2,}", " ", pc[1])
            t = re.sub("([" + CJK + "])([" + ASCII + "])", "\\1 \\2", t)
            t = re.sub("([" + ASCII + "])([" + CJK + "])", "\\1 \\2", t)
            pc[1] = t

    # 2) across tags: one insertion decision per gap between content pieces
    atoms = []      # [content_piece_index, [trailing inline piece indices]]
    for i, pc in enumerate(pieces):
        if pc[0] == "text" and pc[1].strip() == "":
            if atoms:
                atoms[-1][1].append(i)
            continue
        if pc[0] == "text" or pc[2]:
            atoms.append([i, []])
        elif atoms:
            atoms[-1][1].append(i)

    for k in range(len(atoms) - 2, -1, -1):
        ai, bi = atoms[k][0], atoms[k + 1][0]
        pa, pb = pieces[ai], pieces[bi]
        a = pa[1].rstrip()[-1:] if pa[0] == "text" else ""
        b = pb[1].lstrip()[:1] if pb[0] == "text" else ""
        if not a or not b:
            continue
        if not ((re.match("[" + CJK + "]", a) and re.match("[" + ASCII + "]", b))
                or (re.match("[" + ASCII + "]", a) and re.match("[" + CJK + "]", b))):
            continue
        if bi == ai + 1:                    # no gap to hold the space
            if pa[0] == "text" and not pa[3] and not pa[1].endswith(" "):
                pa[1] = pa[1] + " "
            elif pb[0] == "text" and not pb[3] and not pb[1].startswith(" "):
                pb[1] = " " + pb[1]
            continue
        # place the space outside both elements: right after the last closing tag
        ins = ai + 1
        for idx in range(ai + 1, bi):
            if pieces[idx][0] == "tag" and pieces[idx][1].startswith("</"):
                ins = idx + 1
        if (pa[0] == "text" and pa[1].endswith(" ")) or \
                (pb[0] == "text" and pb[1].startswith(" ")) or \
                any(pieces[idx][0] == "text" and pieces[idx][1].strip() == ""
                    for idx in range(ai + 1, bi)):
            continue                      # already separated by whitespace
        pieces.insert(ins, ["text", " ", False, True])
    return "".join(p[1] for p in pieces)


def has_missing_space(text):
    """Return matches where CJK and Latin touch without a space."""
    body = markup.PH_RE.sub(" ", text)
    return re.findall("[A-Za-z0-9][\u4e00-\u9fff]|[\u4e00-\u9fff][A-Za-z0-9]", body)
