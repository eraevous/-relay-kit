"""relay-kit/budget@2: governance/budget.jsonl, read the same way by every reader (the cockpit,
budget_append, usage_ingest and the telemetry view). Pure: reads nothing itself. Standard library only.

A line is one reading of compute or time spent. @2 adds, all optional on old lines:

  id               a stable id; a line without one is known by "bud-" + the first 10 hex of its sha256
  method           measured | proxy | estimate (an older line without it is "unlabelled")
  interval_start / interval_end   what the reading covers (ISO, UTC); ts stays when it was recorded
  includes_children {"included": true|false, "ids": [...]}: whether sub-agent use is inside these
                   numbers; listed child lines are then not added again
  correction_of    the id of an earlier line (same window) this one replaces: never summed with it
  metric_version   which definition the numbers follow (e.g. "budget@2")
  scope            what the numbers cover, in words ("Kairos threads only")
  source           where the numbers came from (a log path pattern, a capture id, a status file)
  tokens_detail    {input, output, cache_write, cache_read, by_model}: the split behind tokens_in
                   (input + cache_write) and tokens_out (output); cache reads are kept apart

effective(rows) decides which lines count:
- a correction replaces the line it names; a chain resolves to its last line. Two corrections of the
  same line are a conflict: the later one counts, the earlier one doesn't, and both are reported.
- a correction must name an earlier line with the same window; anything else (itself, a later line,
  an unknown id, another window) is reported and the correction counts as a plain line.
- a counted line keeps the time of the line it replaces (`at`), so relabelling an old reading never
  moves its use into the current window.
- a parent that includes its children's use makes the children (through their correction chains) not
  count, but only where the parent itself counts; a correction that doesn't say otherwise inherits
  the parent's inclusion. Inclusion cycles are reported and ignored.
- two lines with the same id are both read; the later one is known by id~line, and it's reported.
"""
import datetime as dt
import hashlib
import json
import re

SCHEMA = "relay-kit/budget@2"
METHODS = ("measured", "proxy", "estimate")
ID = re.compile(r"^[\w.:-]{1,80}$")


def when(s):
    """An ISO time (with a zone) as an aware datetime, or None."""
    if not isinstance(s, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s):
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


def line_id(raw, obj):
    if isinstance(obj.get("id"), str) and ID.match(obj["id"]):
        return obj["id"]
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return "bud-" + hashlib.sha256(raw.strip()).hexdigest()[:10]


def problems(obj):
    """What is wrong with the @2 fields of one line (the @1 fields are checked by the reader)."""
    out = []
    if "id" in obj and not (isinstance(obj["id"], str) and ID.match(obj["id"])):
        out.append("id must be up to 80 letters, digits, '.', ':', '_' or '-'")
    if "method" in obj and obj["method"] not in METHODS:
        out.append(f"method must be one of {', '.join(METHODS)}")
    for f in ("interval_start", "interval_end"):
        if f in obj and not when(obj[f]):
            out.append(f"{f} must be an ISO time with a zone")
    if ("interval_start" in obj) != ("interval_end" in obj):
        out.append("interval_start and interval_end go together")
    a, b = when(obj.get("interval_start")), when(obj.get("interval_end"))
    if a and b and a > b:
        out.append("interval_start is after interval_end")
    ch = obj.get("includes_children")
    if ch is not None and not (isinstance(ch, dict) and isinstance(ch.get("included"), bool)
                               and isinstance(ch.get("ids", []), list)
                               and all(isinstance(i, str) and ID.match(i) for i in ch.get("ids") or [])):
        out.append('includes_children must be {"included": true|false, "ids": [line ids]}')
    elif isinstance(ch, dict) and ch.get("included") is False and ch.get("ids"):
        out.append("includes_children lists ids but says they aren't included")
    td = obj.get("tokens_detail")
    if td is not None and not (isinstance(td, dict) and all(isinstance(td.get(k), int) and not isinstance(td.get(k), bool) and td[k] >= 0
                                                          for k in ("input", "output", "cache_write", "cache_read"))):
        out.append("tokens_detail must hold input, output, cache_write and cache_read as integers >= 0")
    for f in ("correction_of", "metric_version", "scope", "source"):
        if f in obj and not (isinstance(obj[f], str) and obj[f].strip() and "\n" not in obj[f]):
            out.append(f"{f} must be one line of text")
    return out


def effective(rows):
    """rows: [(line_no, raw, obj)] in file order. Returns (kept, report).
    kept = [(line_no, id, obj, at)]: the lines whose numbers count, each with `at`, the time it is counted
    at (the ts of the first line in its correction chain). report = {"replaced": {id: by}, "children":
    {child id: parent id}, "conflicts", "invalid", "duplicates", "methods"}. Children are listed, not
    removed: a reader drops a child only where its parent is counted (exclude_children)."""
    ids, seen, dups = [], {}, []
    for n, raw, o in rows:
        i = line_id(raw, o)
        if i in seen:
            dups.append({"id": i, "lines": [seen[i], n]})
            i = f"{i}~{n}"
        seen.setdefault(i, n)
        ids.append((n, i, o))
    pos = {i: k for k, (_, i, _) in enumerate(ids)}
    obj = {i: o for _, i, o in ids}
    replaced, invalid, conflicts, corrects = {}, [], [], {}
    for k, (n, i, o) in enumerate(ids):
        tgt = o.get("correction_of")
        if not tgt:
            continue
        why = ("it corrects itself" if tgt == i else "no such line" if tgt not in pos else
               "the line it corrects comes after it" if pos[tgt] > k else
               "the line it corrects has another window" if obj[tgt].get("window") != o.get("window") else None)
        if why:
            invalid.append({"line": n, "id": i, "correction_of": tgt, "why": why})
            continue
        if tgt in corrects:
            conflicts.append({"id": tgt, "corrected_by": [corrects[tgt], i]})
            replaced[corrects[tgt]] = i          # the later correction counts; the earlier one doesn't
        corrects[tgt] = i
        replaced[tgt] = i
    def root(i):
        """The first line of i's correction chain (whose time the use is counted at)."""
        back = {v: t for t, v in corrects.items()}
        while i in back:
            i = back[i]
        return i
    def head(i):
        """The last line of i's chain: the one that counts."""
        hops = 0
        while i in replaced and hops < 1000:
            i, hops = replaced[i], hops + 1
        return i
    def inclusion(i):
        """includes_children of i, inherited from the line it corrects when i doesn't say."""
        hops = 0
        while i in obj and "includes_children" not in obj[i] and obj[i].get("correction_of") in obj and hops < 1000:
            i, hops = obj[i]["correction_of"], hops + 1
        ch = obj.get(i, {}).get("includes_children")
        return ch if isinstance(ch, dict) and ch.get("included") else None
    kept_ids = [i for _, i, _ in ids if i not in replaced]
    children = {}
    for p in kept_ids:
        ch = inclusion(p)
        for c in (ch or {}).get("ids") or []:
            c2 = head(c) if c in pos else None
            if c2 is None or c2 == p:
                invalid.append({"id": p, "includes": c, "why": "no such line" if c2 is None else "it includes itself"})
            else:
                children[c2] = p
    for c, p in list(children.items()):          # A includes B and B includes A: neither excludes the other
        if children.get(p) == c:
            invalid.append({"id": p, "includes": c, "why": "inclusion cycle"})
            children.pop(c, None)
            children.pop(p, None)
    kept = []
    for n, i, o in ids:
        if i in replaced:
            continue
        r = root(i)
        kept.append((n, i, o, obj[r].get("ts") if r in obj else o.get("ts")))
    methods = {}
    for _, i, o, _ in kept:
        if i in children:
            continue
        m = o.get("method") if o.get("method") in METHODS else "unlabelled"
        methods[m] = methods.get(m, 0) + 1
    return kept, {"replaced": replaced, "children": children, "conflicts": conflicts, "invalid": invalid,
                  "duplicates": dups, "methods": methods}


def exclude_children(kept, report, counted_ids=None):
    """kept minus the children whose parent is counted too (in counted_ids, default: all of kept)."""
    counted = {i for _, i, _, _ in kept} if counted_ids is None else set(counted_ids)
    return [k for k in kept if not (k[1] in report["children"] and report["children"][k[1]] in counted)]


def parse(text):
    """[(line_no, raw, obj)] and [(line_no, reason)] from the file's text (a leading BOM is ignored)."""
    rows, bad = [], []
    for n, raw in enumerate(text.lstrip("﻿").split("\n"), 1):
        if not raw.strip():
            continue
        try:
            o = json.loads(raw)
        except ValueError as x:
            bad.append((n, f"not JSON ({x})"))
            continue
        if not isinstance(o, dict):
            bad.append((n, "not an object"))
            continue
        rows.append((n, raw, o))
    return rows, bad
