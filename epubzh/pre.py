#!/usr/bin/env python3
"""Optional translations for plain-text <pre> blocks (LLM output, dialogs)."""
import html
import json
import re

from . import markup

PRE_RE = re.compile(r"<pre\b([^>]*)>(.*?)</pre>", re.S)


def collect(cfg):
    existing = {}
    if cfg.pre_json.exists():
        try:
            data = json.loads(cfg.pre_json.read_text(encoding="utf-8"))
            for rel, items in data.items():
                for item in items:
                    existing[(rel, int(item["index"]))] = item.get("zh", "")
        except (ValueError, KeyError, TypeError):
            print("warning: could not read existing %s, starting fresh" % cfg.pre_json)

    data = {}
    for path in cfg.html_files():
        rel = str(path.relative_to(cfg.source_dir))
        text = path.read_text(encoding="utf-8")
        items = []
        for i, m in enumerate(PRE_RE.finditer(text)):
            inner = m.group(2)
            plain = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip("\n")
            items.append({
                "index": i,
                "source": plain,
                "zh": existing.get((rel, i), ""),
            })
        if items:
            data[rel] = items
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.pre_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    total = sum(len(v) for v in data.values())
    done = sum(1 for v in data.values() for item in v if item.get("zh"))
    print("wrote %s (%d <pre> blocks, %d translated)" % (cfg.pre_json, total, done))


def apply(cfg):
    if not cfg.pre_json.exists():
        return 0
    data = json.loads(cfg.pre_json.read_text(encoding="utf-8"))
    count = 0
    for rel, items in data.items():
        target = cfg.build_dir / rel
        if not target.exists():
            continue
        mapping = {int(item["index"]): item.get("zh", "")
                   for item in items if item.get("zh")}
        if not mapping:
            continue
        text = target.read_text(encoding="utf-8")
        counter = [0]

        def repl(m):
            nonlocal count
            i = counter[0]
            counter[0] += 1
            if i not in mapping:
                return m.group(0)
            count += 1
            return "<pre%s>%s</pre>" % (m.group(1), markup.xml_escape(mapping[i]))

        text = PRE_RE.sub(repl, text)
        target.write_text(text, encoding="utf-8")
    if count:
        print("pre: translated %d block(s)" % count)
    return count
