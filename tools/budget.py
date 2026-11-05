"""relay-kit/budget@2: governance/budget.jsonl, read the same way by every reader (the cockpit,
budget_append, and the telemetry view to come). Pure: reads nothing itself. Standard library only.

A line is one reading of compute or time spent. @2 adds, all optional on old lines:

  id               a stable id; a line without one is known by "bud-" + the first 10 hex of its sha256
  method           measured | proxy | estimate (an older line without it is "unlabelled")
  interval_start / interval_end   what the reading covers (ISO, UTC); ts stays when it was recorded
  includes_children {"included": true|false, "ids": [...]}: whether sub-agent use is inside these
                   numbers; listed child lines are then not added again
  correction_of    the id of the line this one replaces: the replaced line is never summed with it
  metric_version   which definition the numbers follow (e.g. "budget@2")
  scope            what the numbers cover, in words ("Kairos threads only")
  source           where the numbers came from (a log path pattern, a capture id, a status file)

effective(rows) applies corrections (a chain resolves to its last line; two corrections of one line
are a conflict: the later one is used and both are reported) and child inclusion, so an aggregate
never counts the same use twice.
"""
import hashlib
import json
import re

SCHEMA = "relay-kit/budget@2"
METHODS = ("measured", "proxy", "estimate")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$")
ID = re.compile(r"^[\w.:-]{1,80}$")


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
        if f in obj and not (isinstance(obj[f], str) and ISO.match(obj[f])):
            out.append(f"{f} must be an ISO time")
    if isinstance(obj.get("interval_start"), str) and isinstance(obj.get("interval_end"), str) \
            and ISO.match(obj["interval_start"]) and ISO.match(obj["interval_end"]) and obj["interval_start"] > obj["interval_end"]:
        out.append("interval_start is after interval_end")
    ch = obj.get("includes_children")
    if ch is not None and not (isinstance(ch, dict) and isinstance(ch.get("included"), bool)
                               and all(isinstance(i, str) for i in ch.get("ids") or [])):
        out.append('includes_children must be {"included": true|false, "ids": [line ids]}')
    for f in ("correction_of", "metric_version", "scope", "source"):
        if f in obj and not (isinstance(obj[f], str) and obj[f].strip() and "\n" not in obj[f]):
            out.append(f"{f} must be one line of text")
    return out


def effective(rows):
    """rows: [(line_no, raw, obj)] in file order. Returns (kept, report): kept = [(line_no, id, obj)] whose
    numbers count; report = {"replaced": {id: by}, "children_included": {id: parent}, "conflicts": [...],
    "unknown_targets": [...], "methods": {method: lines}}."""
    ids = [(n, line_id(raw, o), o) for n, raw, o in rows]
    known = {i for _, i, _ in ids}
    replaced, conflicts, unknown = {}, [], []
    for n, i, o in ids:
        t = o.get("correction_of")
        if not t:
            continue
        if t not in known:
            unknown.append({"line": n, "id": i, "correction_of": t})
            continue
        if t in replaced:
            conflicts.append({"id": t, "corrected_by": [replaced[t], i]})
        replaced[t] = i
    children = {}
    for n, i, o in ids:
        ch = o.get("includes_children")
        if i not in replaced and isinstance(ch, dict) and ch.get("included"):
            for c in ch.get("ids") or []:
                if c in known:
                    children[c] = i
    kept = [(n, i, o) for n, i, o in ids if i not in replaced and i not in children]
    methods = {}
    for _, _, o in kept:
        m = o.get("method") if o.get("method") in METHODS else "unlabelled"
        methods[m] = methods.get(m, 0) + 1
    return kept, {"replaced": replaced, "children_included": children, "conflicts": conflicts,
                  "unknown_targets": unknown, "methods": methods}


def parse(text):
    """[(line_no, raw, obj)] and [(line_no, reason)] from the file's text."""
    rows, bad = [], []
    for n, raw in enumerate(text.split("\n"), 1):
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
