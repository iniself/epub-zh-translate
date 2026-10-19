#!/usr/bin/env python3
"""Markup-safe extraction of translatable text runs.

The translator must never see or modify structural markup:
- <pre> blocks are replaced by opaque sentinels and stay byte-identical.
- every element tag is copied verbatim from the source.
- only maximal text runs inside block elements are replaced.
- inline markup inside a run is hidden behind placeholders like U+27E6 0 U+27E7.

A literal '>' in a text run is legal XML but is re-rendered as "&gt;", which
breaks the byte-exact round trip, so such runs are escaped up front by
``normalize_source`` instead.
"""
import html
import re

PLACEHOLDER_OPEN = "\u27e6"
PLACEHOLDER_CLOSE = "\u27e7"

BLOCK_TAGS = {
    "html", "head", "body", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "pre", "section", "article", "blockquote", "dl", "dt", "dd", "figure",
    "figcaption", "title", "meta", "link", "colgroup", "col", "caption",
}
INLINE_PAIRED = {
    "a", "em", "strong", "span", "b", "i", "u", "small", "sub", "sup", "cite",
    "q", "mark", "abbr", "dfn", "font", "big", "tt", "time", "s", "del", "ins",
}
INLINE_VERBATIM = {
    "code", "kbd", "samp", "var", "br", "img", "hr", "wbr", "svg", "math",
    "input", "button", "audio", "video", "iframe", "object", "embed", "source",
    "track", "area", "map",
}
VOID_TAGS = {
    "br", "img", "hr", "wbr", "meta", "link", "input", "source", "track",
    "area", "col", "embed",
}

# Tag internals: quoted values may contain '>' (legal XML, and common in code
# snippets stored in attributes), so the scanner has to stay quote aware.
ATTRS_RE = r"(?:\"[^\"]*\"|'[^']*'|[^<>\"'])*"

PRE_RE = re.compile(r"<pre\b" + ATTRS_RE + r">.*?</pre>", re.S)
ATOM_RE = re.compile(r"\x00P(\d+)\x00")
PROTECT_RE = re.compile(r"^<\s*([A-Za-z][A-Za-z0-9:_-]*)\b")
CLASS_RE = re.compile(r"\bclass=\"([^\"]*)\"")
TOKEN_RE = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<![^>]*>|<\?.*?\?>"
    r"|</?[A-Za-z][A-Za-z0-9:_-]*" + ATTRS_RE + r"/?>|\x00P\d+\x00",
    re.S,
)
TAGNAME_RE = re.compile(r"^</?\s*([A-Za-z][A-Za-z0-9:_-]*)")
PH_RE = re.compile(PLACEHOLDER_OPEN + r"(/?\d+)" + PLACEHOLDER_CLOSE)
STRAY_RE = re.compile("[%s%s]" % (PLACEHOLDER_OPEN, PLACEHOLDER_CLOSE))


def _parse_tag(raw):
    m = TAGNAME_RE.match(raw)
    name = (m.group(1).lower() if m else "")
    closing = raw.startswith("</")
    selfclose = raw.endswith("/>") or name in VOID_TAGS
    return name, closing, selfclose


def _match_close(tokens, i, j):
    """Index of the tag closing the element opened at token i (or None)."""
    name, _, _ = _parse_tag(tokens[i][1])
    depth = 1
    k = i + 1
    while k < j:
        kind, raw = tokens[k]
        if kind == "tag" and not ATOM_RE.match(raw):
            nm, closing, selfclose = _parse_tag(raw)
            if nm == name:
                if closing:
                    depth -= 1
                    if depth == 0:
                        return k
                elif not selfclose:
                    depth += 1
        k += 1
    return None


def _protect_matcher(specs):
    """Predicate for start tags matching "tag" or "tag.class" specs."""
    exact, classes = set(), set()
    for spec in specs or ():
        spec = str(spec).strip()
        if not spec:
            continue
        if "." in spec:
            tag, _, cls = spec.partition(".")
            classes.add((tag.lower(), cls))
        else:
            exact.add(spec.lower())
    if not exact and not classes:
        return None

    def match(raw):
        if raw.startswith("</"):
            return None
        m = PROTECT_RE.match(raw)
        if not m:
            return None
        name = m.group(1).lower()
        if name in exact:
            return True
        if not classes:
            return None
        cm = CLASS_RE.search(raw)
        if not cm:
            return None
        return any((name, cls) in classes for cls in cm.group(1).split())

    return match


def atomize(src, protect=()):
    """Replace protected blocks with sentinels; return (work, atoms).

    <pre> blocks are always protected. ``protect`` adds "tag" or "tag.class"
    specs for elements that must stay byte-identical as well, which covers code
    blocks that publishers style with their own classes instead of <pre>.
    """
    atoms = []

    def rep(m):
        atoms.append(m.group(0))
        return "\x00P%d\x00" % (len(atoms) - 1)

    work = PRE_RE.sub(rep, src)
    match = _protect_matcher(protect)
    if match is None:
        return work, atoms
    tokens = tokenize(work)
    offsets, pos = [], 0
    for _, txt in tokens:
        offsets.append((pos, pos + len(txt)))
        pos += len(txt)
    spans, i, n = [], 0, len(tokens)
    while i < n:
        kind, raw = tokens[i]
        if kind == "tag" and not ATOM_RE.match(raw) and match(raw):
            _, _, selfclose = _parse_tag(raw)
            end = i if selfclose else _match_close(tokens, i, n)
            if end is not None:
                spans.append((i, end))
                i = end + 1
                continue
        i += 1
    for first, last in reversed(spans):
        start, end = offsets[first][0], offsets[last][1]
        atoms.append(work[start:end])
        work = work[:start] + "\x00P%d\x00" % (len(atoms) - 1) + work[end:]
    return work, atoms


def detatomize(work, atoms):
    def rep(m):
        text = atoms[int(m.group(1))]
        while True:
            inner = ATOM_RE.search(text)
            if not inner:
                return text
            text = (text[:inner.start()] + atoms[int(inner.group(1))]
                    + text[inner.end():])

    return ATOM_RE.sub(rep, work)


def _tag_end(text, i):
    """Index just past the '>' ending the tag that starts at i (quote aware)."""
    j, n, quote = i + 1, len(text), None
    while j < n:
        ch = text[j]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ">":
            return j + 1
        j += 1
    return n


def _opaque_end(text, i):
    """Index just past a comment, CDATA, PI or declaration at i, else None."""
    for opener, closer in (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>")):
        if text.startswith(opener, i):
            j = text.find(closer, i)
            return len(text) if j < 0 else j + len(closer)
    if text.startswith("<!", i):
        j = text.find(">", i)
        return len(text) if j < 0 else j + 1
    return None


def escape_bare_gt(text):
    """Escape '>' in text runs; tags, comments, CDATA and PIs stay intact.

    Returns (text, replacements). Attribute values keep their '>' because tags
    are copied verbatim by the extractor.
    """
    out, i, n, count = [], 0, len(text), 0
    while i < n:
        if text[i] == "<":
            end = _opaque_end(text, i)
            if end is None:
                end = _tag_end(text, i)
            out.append(text[i:end])
            i = end
            continue
        j = text.find("<", i)
        j = n if j < 0 else j
        chunk = text[i:j]
        if ">" in chunk:
            count += chunk.count(">")
            chunk = chunk.replace(">", "&gt;")
        out.append(chunk)
        i = j
    return "".join(out), count


def normalize_source(text, protect=()):
    """Escape bare '>' in text runs so extraction can round-trip.

    Publishers sometimes leave '>' unescaped in running text ("score > 0.8").
    That is legal XML, but rendering the translated segment turns it into
    "&gt;", which fails the byte-exact round trip. Escaping it up front is
    XML-equivalent and keeps the rest of the pipeline unchanged. <pre> and
    ``protect`` blocks are copied verbatim by the extractor, so their content
    is left alone, as are comments, CDATA sections and processing instructions.

    Returns (text, replacements); ``text`` is the input when nothing changed.
    """
    work, atoms = atomize(text, protect)
    fixed, count = escape_bare_gt(work)
    if not count:
        return text, 0
    return detatomize(fixed, atoms), count


def tokenize(work):
    """Split atomized markup into a flat token list (verbatim round-trip)."""
    out = []
    pos = 0
    for m in TOKEN_RE.finditer(work):
        if m.start() > pos:
            out.append(("text", work[pos:m.start()]))
        out.append(("tag", m.group(0)))
        pos = m.end()
    if pos < len(work):
        out.append(("text", work[pos:]))
    return out


def xml_escape(text):
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def placeholder(n):
    return "%s%d%s" % (PLACEHOLDER_OPEN, n, PLACEHOLDER_CLOSE)


def close_placeholder(n):
    return "%s/%d%s" % (PLACEHOLDER_OPEN, n, PLACEHOLDER_CLOSE)


def strip_placeholders(text):
    return PH_RE.sub("", text)


def has_stray_brackets(text):
    return bool(STRAY_RE.search(PH_RE.sub("", text)))


class Segment(object):
    __slots__ = ("start", "end", "text", "items", "index")

    def __init__(self, start, end, text, items):
        self.start = start
        self.end = end
        self.text = text
        self.items = items
        self.index = 0

    def check(self, translated):
        """Return None when the placeholder set is intact, else a message."""
        got = [m.group(0) for m in PH_RE.finditer(translated)]
        want = []
        for it in self.items:
            want.append(it["open"])
            if it["kind"] == "pair":
                want.append(it["close"])
        if sorted(got) != sorted(want):
            missing = [w for w in want if w not in got]
            extra = [g for g in got if g not in want]
            return "placeholder mismatch: missing=%s extra=%s" % (missing, extra)
        if has_stray_brackets(translated):
            return "stray placeholder bracket"
        for it in self.items:
            if translated.count(it["open"]) != 1:
                return "duplicated placeholder %s" % it["open"]
            if it["kind"] == "pair":
                if translated.count(it["close"]) != 1:
                    return "duplicated placeholder %s" % it["close"]
                if translated.find(it["open"]) > translated.find(it["close"]):
                    return "reordered placeholder %s" % it["open"]
        return None

    def render(self, translated):
        """Substitute placeholders back and XML-escape the remaining text."""
        mapping = {}
        for it in self.items:
            mapping[it["open"]] = it["open_html"]
            if it["kind"] == "pair":
                mapping[it["close"]] = it["close_html"]
        out, pos = [], 0
        for m in PH_RE.finditer(translated):
            out.append(xml_escape(translated[pos:m.start()]))
            out.append(mapping[m.group(0)])
            pos = m.end()
        out.append(xml_escape(translated[pos:]))
        return "".join(out)


def extract_segments(work, warn=None, require_latin=True):
    """Return the list of text-run segments found in atomized markup."""
    tokens = tokenize(work)
    offsets, pos = [], 0
    for kind, txt in tokens:
        offsets.append((pos, pos + len(txt)))
        pos += len(txt)
    n = len(tokens)

    def visible_text(i, j):
        chunk = "".join(tokens[k][1] for k in range(i, j))
        return html.unescape(re.sub(r"<[^>]*>", "", ATOM_RE.sub("", chunk))).strip()

    segments = []
    run = None

    def add_text(idx, txt):
        run["parts"].append((idx, idx, html.unescape(txt)))

    def add_placeholder(idx, html_raw, last_idx=None):
        k = run["n"]
        run["n"] += 1
        run["parts"].append((idx, last_idx if last_idx is not None else idx,
                             placeholder(k)))
        run["items"].append({"kind": "single", "open": placeholder(k),
                             "close": None, "open_html": html_raw})

    def process_inline(i, j):
        """Consume the inline element starting at token i. Returns next index."""
        kind, raw = tokens[i]
        name, closing, selfclose = _parse_tag(raw)
        if name in INLINE_VERBATIM or selfclose:
            add_placeholder(i, raw)
            return i + 1
        c = _match_close(tokens, i, j)
        if c is None:
            add_placeholder(i, raw)
            return i + 1
        if not visible_text(i + 1, c):
            add_placeholder(i, work[offsets[i][0]:offsets[c][1]], last_idx=c)
            return c + 1
        k = run["n"]
        run["n"] += 1
        run["parts"].append((i, i, placeholder(k)))
        run["items"].append({"kind": "pair", "open": placeholder(k),
                             "close": close_placeholder(k),
                             "open_html": raw, "close_html": tokens[c][1]})
        walk(i + 1, c)
        run["parts"].append((c, c, close_placeholder(k)))
        return c + 1

    def walk(i, j):
        while i < j:
            kind, raw = tokens[i]
            if kind == "text":
                add_text(i, raw)
                i += 1
            else:
                name, _, _ = _parse_tag(raw)
                if ATOM_RE.match(raw) or name not in INLINE_PAIRED:
                    add_placeholder(i, raw)
                    i += 1
                else:
                    i = process_inline(i, j)

    def flush():
        nonlocal run
        if run is None:
            return
        parts = run["parts"]
        while parts and parts[0][2].strip() == "" and not parts[0][2].startswith(
                PLACEHOLDER_OPEN):
            parts.pop(0)
        while parts and parts[-1][2].strip() == "" and not parts[-1][2].startswith(
                PLACEHOLDER_OPEN):
            parts.pop()
        if parts and "".join(p[2] for p in parts).strip():
            text = "".join(p[2] for p in parts)
            visible = PH_RE.sub("", text)
            if require_latin and not re.search(r"[A-Za-z]", visible):
                run = None          # nothing translatable, leave verbatim
                return
            start = offsets[parts[0][0]][0]
            end = offsets[parts[-1][1]][1]
            seg = Segment(start, end, text, run["items"])
            segments.append(seg)
        run = None

    stack = []
    i = 0
    while i < n:
        kind, raw = tokens[i]
        if kind == "tag" and raw.startswith("<!"):
            flush()
            i += 1
            continue
        if kind == "tag" and not ATOM_RE.match(raw):
            name, closing, selfclose = _parse_tag(raw)
            if name in BLOCK_TAGS or (name not in INLINE_PAIRED
                                      and name not in INLINE_VERBATIM):
                flush()
                if name not in BLOCK_TAGS and warn is not None:
                    warn("unknown tag <%s>" % name)
                if not closing and not selfclose:
                    stack.append(name)
                elif closing and stack and stack[-1] == name:
                    stack.pop()
                i += 1
                continue
            if run is None:
                run = {"parts": [], "items": [], "n": 0}
            i = process_inline(i, n)
            continue
        if ATOM_RE.match(raw):
            flush()
            i += 1
            continue
        if run is None:
            run = {"parts": [], "items": [], "n": 0}
        add_text(i, raw)
        i += 1
    flush()
    for k, seg in enumerate(segments):
        seg.index = k
    return segments


def apply_segments(work, segments, translations):
    """Splice rendered translations back into atomized markup."""
    assert len(segments) == len(translations), (len(segments), len(translations))
    out = work
    for seg, tr in sorted(zip(segments, translations),
                          key=lambda p: -p[0].start):
        out = out[:seg.start] + seg.render(tr) + out[seg.end:]
    return out


def segment_words(text):
    return len(PH_RE.sub(" ", text).split())


def chunk_segments(segments, words_per_chunk):
    """Split segments into translation chunks of roughly equal size."""
    chunks, cur, cur_words = [], [], 0
    for s in segments:
        w = segment_words(s.text)
        if cur and cur_words + w > words_per_chunk:
            chunks.append(cur)
            cur, cur_words = [], 0
        cur.append(s)
        cur_words += w
    if cur:
        chunks.append(cur)
    return chunks


def check_markers(src, translated):
    """Validate placeholder markers without a Segment object."""
    src_marks = sorted(m.group(0) for m in PH_RE.finditer(src))
    got = sorted(m.group(0) for m in PH_RE.finditer(translated))
    if src_marks != got:
        return "placeholder mismatch: missing=%s extra=%s" % (
            [m for m in src_marks if m not in got],
            [m for m in got if m not in src_marks])
    if has_stray_brackets(translated):
        return "stray placeholder bracket"
    for m in PH_RE.finditer(translated):
        if m.group(1).startswith("/"):
            num = m.group(1)[1:]
            open_mark = placeholder(num)
            if translated.count(open_mark) != 1:
                return "unpaired closing placeholder %s" % m.group(0)
            if translated.find(open_mark) > m.start():
                return "reordered placeholder %s" % open_mark
    return None
