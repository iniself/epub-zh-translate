#!/usr/bin/env python3
"""Manual-review registry for chunks the translator could not finish.

The registry lets a translation run continue past an unsolvable chunk instead
of stopping the whole book. Each row names one failing segment and carries the
source text, the current translation, and the check message, so a human can fix
it without re-reading the chunk.

Workflow:

* ``add`` registers (or refreshes) the failing segments of one chunk.
* ``recheck`` clears the registry, re-checks every recorded chunk, and writes
  back only the problems that remain. An empty registry means the book is
  ready to build.
"""
import csv
from pathlib import Path

FIELDS = ("file", "chunk", "seg", "kind", "source", "translation", "detail")
FILENAME = "needs_review.tsv"


def path_for(cfg):
    return cfg.work_dir / FILENAME


def _escape(value):
    """Flatten a cell so a row always stays on one TSV line."""
    return (str(value).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _unescape(value):
    out, i = [], 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


def read(cfg):
    """Return registry rows, oldest first; an absent file means no rows."""
    path = path_for(cfg)
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh, delimiter="\t"):
            row = {field: _unescape(raw.get(field) or "") for field in FIELDS}
            row["chunk"] = int(row["chunk"])
            row["seg"] = int(row["seg"])
            rows.append(row)
    return rows


def write(cfg, rows):
    """Replace the registry atomically; an empty list removes the file."""
    path = path_for(cfg)
    if not rows:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    ordered = sorted(rows, key=lambda row: (row["file"], row["chunk"],
                                            row["seg"]))
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        for row in ordered:
            writer.writerow({field: _escape(row.get(field, ""))
                             for field in FIELDS})
    tmp.replace(path)


def _resolve(cfg, value):
    """Map a full name, file stem, or unique substring to an index entry."""
    from . import chunks

    index = chunks.read_index(cfg)
    if value in index:
        return value
    stem_matches = [name for name in index if Path(name).stem == value]
    if len(stem_matches) == 1:
        return stem_matches[0]
    matches = [name for name in index if value in name]
    if not matches:
        raise SystemExit("no file matches %r" % value)
    if len(matches) > 1:
        raise SystemExit("%r matches %d files: %s"
                         % (value, len(matches), ", ".join(sorted(matches))))
    return matches[0]


def collect(cfg, name, ci):
    """Return registry rows for the failing segments of one chunk."""
    from . import chunks

    name = _resolve(cfg, name)
    issues = chunks.chunk_issues(cfg, name, ci)
    return [{"file": name, "chunk": ci, "seg": issue["seg"],
             "kind": issue["kind"], "source": issue["source"],
             "translation": issue["translation"],
             "detail": issue["detail"]} for issue in issues]


def add(cfg, name, ci):
    """Register a chunk's problems, replacing any earlier rows for it.

    Untranslated segments are rejected: they are unfinished work, not a
    manual-review problem. The translator must translate them (or mark
    genuinely verbatim text), so registering can never be used to skip a
    chunk that still contains source-language prose.
    """
    name = _resolve(cfg, name)
    fresh = collect(cfg, name, ci)
    untranslated = [row for row in fresh if row["kind"] == "not-translated"]
    if untranslated:
        raise SystemExit(
            "%s chunk %d: %d segment(s) are not translated; finish them in "
            "work/zh (or mark genuinely verbatim text with @@@VERBATIM@@@). "
            "Untranslated text is translation work, not manual review."
            % (name, ci, len(untranslated)))
    rows = [row for row in read(cfg)
            if not (row["file"] == name and row["chunk"] == ci)]
    rows.extend(fresh)
    write(cfg, rows)
    return fresh


def _needs_translation(issue):
    """True for issues the translator agent must finish, not a human."""
    if issue["kind"] == "not-translated":
        return True
    detail = issue.get("detail", "")
    return detail.startswith(("file missing", "partial:", "no markers found"))


def recheck(cfg):
    """Clear the registry, re-check recorded chunks, keep what still fails.

    Chunks that are not actually translated (untranslated segments, missing or
    partial files, missing markers) are removed from the registry instead of
    being kept: they belong in the translation queue, where `next` hands them
    back to the agent. Only placeholder/structure failures that survived
    repair stay registered for manual review.
    """
    rows = read(cfg)
    if not rows:
        return []
    targets = sorted({(row["file"], row["chunk"]) for row in rows})
    remaining, cleared, requeued = [], [], []
    for name, ci in targets:
        try:
            issues = collect(cfg, name, ci)
        except SystemExit as exc:
            print("  !! %s chunk %d: %s" % (name, ci, exc))
            remaining.extend(row for row in rows
                             if row["file"] == name and row["chunk"] == ci)
            continue
        if not issues:
            cleared.append((name, ci))
            continue
        retry = [issue for issue in issues if _needs_translation(issue)]
        if retry:
            requeued.append((name, ci, len(retry)))
            continue
        remaining.extend(issues)
    write(cfg, remaining)
    for name, ci, count in requeued:
        print("  requeued %s chunk %d for translation (%d issue(s))"
              % (name, ci, count))
    for name, ci in cleared:
        print("  cleared %s chunk %d" % (name, ci))
    return remaining
