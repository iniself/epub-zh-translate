#!/usr/bin/env python3
"""Repair-round bookkeeping for translated chunks.

The skill used to trust the model to count its own "edit + check" rounds. A
model that edits with Python or sed without running ``check`` can spend an
hour and never reach the fallback. This module records what actually happened:

* a round is counted only when the chunk file changed since the previous
  ``check``;
* checking again without editing is an idle check: it is not a round and does
  not run the fallback; two idle checks stop the chunk for the main session;
* a new round whose problem count did not shrink is a stall; the first stall
  after a real edit, or six rounds, marks the chunk exhausted and makes it
  eligible for the automatic placeholder fallback.

Once exhausted, the CLI runs the placeholder fallback itself and ``next``
stops dispatching the chunk to a subagent. The decision no longer depends on
the model remembering the rule.
"""
import hashlib
import json
from pathlib import Path

FILENAME = "check_state.json"
MAX_ROUNDS = 6
MAX_IDLE = 2


def path_for(cfg):
    return cfg.work_dir / FILENAME


def _key(name, ci):
    return "%s#%d" % (name, ci)


def read(cfg):
    path = path_for(cfg)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write(cfg, data):
    path = path_for(cfg)
    if not data:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _file_sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def get(cfg, name, ci):
    """Return one chunk's state, or an empty dict when it has none."""
    return read(cfg).get(_key(name, ci), {})


def clear(cfg, name, ci):
    data = read(cfg)
    if data.pop(_key(name, ci), None) is not None:
        write(cfg, data)


def mark_fallback(cfg, name, ci):
    data = read(cfg)
    state = data.setdefault(_key(name, ci), {})
    state["fallback"] = True
    state["exhausted"] = True
    write(cfg, data)


def mark_exhausted(cfg, name, ci):
    """Force a chunk to exhausted without waiting for the round budget.

    Used when a subagent was killed by its step limit before it could finish
    checking: the work is already abandoned, so the main session must not be
    asked to repeat six rounds.
    """
    data = read(cfg)
    state = data.setdefault(_key(name, ci), {})
    state["forced"] = True
    state["exhausted"] = True
    write(cfg, data)


def update(cfg, name, ci, chunk_path, problems):
    """Record one CLI check and return the chunk's state.

    ``problems`` is the list returned by ``check_file``. A passing chunk is
    removed from the state file: it needs no budget any more.
    """
    data = read(cfg)
    key = _key(name, ci)
    previous = data.get(key, {})
    sha = _file_sha(chunk_path)
    changed = sha is not None and sha != previous.get("last_sha")
    rounds = int(previous.get("rounds", 0)) + (1 if changed else 0)
    idle = 0 if changed else int(previous.get("idle", 0)) + 1
    count = len(problems)
    if count == 0:
        data.pop(key, None)
        write(cfg, data)
        return {"rounds": rounds, "problems": 0, "exhausted": False,
                "idle": 0, "changed": changed,
                "fallback": bool(previous.get("fallback")),
                "forced": bool(previous.get("forced")),
                "fallback_ready": False}
    last = previous.get("last_problems")
    forced = bool(previous.get("forced"))
    no_progress = changed and last is not None and count >= last
    failed_attempt = no_progress and rounds >= 2
    fallback_ready = forced or rounds >= MAX_ROUNDS or failed_attempt
    exhausted = (bool(previous.get("exhausted")) or forced
                 or rounds >= MAX_ROUNDS or failed_attempt
                 or idle >= MAX_IDLE)
    state = {
        "rounds": rounds,
        "problems": count,
        "last_problems": count,
        "last_sha": sha,
        "idle": idle,
        "changed": changed,
        "fallback": bool(previous.get("fallback")),
        "forced": forced,
        "fallback_ready": fallback_ready,
        "exhausted": exhausted,
    }
    data[key] = state
    write(cfg, data)
    return state


def is_placeholder_only(problems):
    """True when every remaining problem is a placeholder/formatting issue."""
    return bool(problems) and all(
        "placeholder" in problem or "stray" in problem
        for problem in problems)
