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


def collect(cfg, name, ci, fix=True):
    """Return registry rows for the failing segments of one chunk.

    ``fix=False`` keeps the call read-only, for hints that must not touch the
    translation.
    """
    from . import chunks

    name = _resolve(cfg, name)
    issues = chunks.chunk_issues(cfg, name, ci, fix=fix)
    return [{"file": name, "chunk": ci, "seg": issue["seg"],
             "kind": issue["kind"], "source": issue["source"],
             "translation": issue["translation"],
             "detail": issue["detail"]} for issue in issues]


# Kinds the registry may hold once repair has run out of ideas. Placeholders
# are mechanical leftovers; untranslated text is what the translator kept
# saying is not text to translate (citations, code, a false positive). Either
# way the chunk is as finished as the translator can make it.
REGISTRABLE = ("placeholder", "not-translated")


def _registrable(issues):
    """True when every remaining issue is content, not unfinished work.

    Structural damage, a partial file, or a missing file means the chunk was
    never finished; those belong in the translation queue, never in the
    manual-review registry.
    """
    return bool(issues) and all(issue["kind"] in REGISTRABLE
                                for issue in issues)


def _kind_summary(issues):
    counts = {}
    for issue in issues:
        counts[issue["kind"]] = counts.get(issue["kind"], 0) + 1
    return ", ".join("%d %s" % (count, kind)
                     for kind, count in sorted(counts.items()))


def can_register(cfg, name, ci, fix=True):
    """True when `review --add` would accept this chunk right now.

    The CLI hints use it so they never suggest a command that will be
    refused: the leftovers must be content and the repair rounds must be
    spent. ``fix=False`` keeps a read-only caller from touching the
    translation.
    """
    try:
        if not _registrable(collect(cfg, name, ci, fix=fix)):
            return False
    except SystemExit:
        return False
    state = rounds.get(cfg, name, ci)
    return bool(state.get("exhausted") or state.get("fallback"))


def add(cfg, name, ci):
    """Register a chunk's problems, replacing any earlier rows for it.

    Two gates protect the registry:

    * every remaining issue must be content (placeholder or untranslated
      text); a partial file or structural damage means the translation is
      unfinished and must go back to the translator;
    * the chunk's repair rounds must be spent (exhausted, or the automatic
      fallback already ran). Registering on the first check hides work the
      translator can still do.
    """
    name = _resolve(cfg, name)
    fresh = collect(cfg, name, ci)
    if fresh and not _registrable(fresh):
        raise SystemExit(
            "%s chunk %d is not finished (%s); keep translating and run "
            "`check --chunk` again. Manual review takes finished chunks only: "
            "partial files and structural damage stay in the queue."
            % (name, ci, _kind_summary(fresh)))
    if fresh:
        state = rounds.get(cfg, name, ci)
        spent = int(state.get("rounds", 0))
        finished = bool(state.get("exhausted") or state.get("fallback"))
        if not finished:
            raise SystemExit(
                "%s chunk %d has used %d of %d repair round(s); keep fixing "
                "and run `check --chunk` again. Manual review is the last "
                "resort, not the first answer."
                % (name, ci, spent, rounds.MAX_ROUNDS))
    rows = [row for row in read(cfg)
            if not (row["file"] == name and row["chunk"] == ci)]
    rows.extend(fresh)
    write(cfg, rows)
    rounds.clear(cfg, name, ci)
    return fresh


def recheck(cfg):
    """Clear the registry, re-check recorded chunks, keep what still fails.

    Placeholder and untranslated failures stay registered: they are content
    the translator has already given up on, so re-checking cannot improve
    them. Any structural issue or partial file means the chunk is unfinished,
    so all its rows are dropped and `next` hands it back to the translator.
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
        if not _registrable(issues):
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
