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

from . import rounds

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


def _reviewable(issues):
    """Manual review is only for placeholder failures that survived repair.

    Everything else is unfinished translation: untranslated segments, partial
    files, missing files, marker/structure errors. Those belong in the
    translation queue, never in the manual-review registry.
    """
    return bool(issues) and all(issue["kind"] == "placeholder"
                                for issue in issues)


def _kind_summary(issues):
    counts = {}
    for issue in issues:
        counts[issue["kind"]] = counts.get(issue["kind"], 0) + 1
    return ", ".join("%d %s" % (count, kind)
                     for kind, count in sorted(counts.items()))


def add(cfg, name, ci):
    """Register a chunk's problems, replacing any earlier rows for it.

    Only chunks whose every remaining issue is a placeholder failure are
    accepted. A chunk with untranslated segments, a partial/missing file, or
    structural damage is unfinished translation and must go back to the
    translator instead.
    """
    name = _resolve(cfg, name)
    fresh = collect(cfg, name, ci)
    if fresh and not _reviewable(fresh):
        raise SystemExit(
            "%s chunk %d is not finished (%s); keep translating and run "
            "`check --chunk` again. Manual review only accepts placeholder "
            "failures after the repair rounds." % (name, ci,
                                                    _kind_summary(fresh)))
    rows = [row for row in read(cfg)
            if not (row["file"] == name and row["chunk"] == ci)]
    rows.extend(fresh)
    write(cfg, rows)
    rounds.clear(cfg, name, ci)
    return fresh


def recheck(cfg):
    """Clear the registry, re-check recorded chunks, keep what still fails.

    Placeholder-only failures stay registered. Any other issue means the chunk
    is unfinished, so all its rows are dropped and `next` hands it back to the
    translator.
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
        if not _reviewable(issues):
            requeued.append((name, ci, len(issues)))
            continue
        remaining.extend(issues)
    write(cfg, remaining)
    for name, ci, count in requeued:
        rounds.clear(cfg, name, ci)
        print("  requeued %s chunk %d for translation (%d issue(s))"
              % (name, ci, count))
    for name, ci in cleared:
        rounds.clear(cfg, name, ci)
        print("  cleared %s chunk %d" % (name, ci))
    return remaining
