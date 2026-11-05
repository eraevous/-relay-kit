#!/usr/bin/env python3
"""Append one line to governance/budget.jsonl, the cockpit's budget record. Standard library only.

    python3 tools/budget_append.py [--dir governance] --window session|weekly --tokens-in N --tokens-out N
                                   --method measured|proxy|estimate [--interval-start ISO --interval-end ISO]
                                   [--includes-children yes|no [--child-id ID ...]] [--correction-of ID]
                                   [--scope TEXT] [--source TEXT] [--agents N] [--note TEXT] [--actor A] [--ts ISO]

Each line (relay-kit/budget@2, docs/budget.md): {"id", "ts", "actor", "window", "tokens_in",
"tokens_out", "method", "metric_version", ...the optional fields given..., "agents", "note"}.
--method is required: every new line says whether its numbers were measured, are a proxy, or are an
estimate. To replace an earlier line (relabel it, or fix its numbers), append one with
--correction-of <its id>: readers then count the new line instead, never both. It prints the line
with its id.
`window: session` is one session's or block's use; `window: weekly` is a reading of the whole week
so far (for example from the plan's usage page). The file is append-only: a correction is a new
line with a note, never an edit. --actor defaults to $RELAYKIT_ACTOR, --ts to now (UTC). It
appends and prints the line; it doesn't commit. Commit and push it with the records."""
import argparse, datetime as dt, hashlib, importlib.util, json, os, pathlib, re, sys

_spec = importlib.util.spec_from_file_location("relaykit_budget", pathlib.Path(__file__).resolve().parent / "budget.py")
BG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BG)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="relay-kit budget-append", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="governance", type=pathlib.Path)
    ap.add_argument("--window", required=True, choices=["session", "weekly"])
    ap.add_argument("--tokens-in", required=True, type=int)
    ap.add_argument("--tokens-out", required=True, type=int)
    ap.add_argument("--method", required=True, choices=list(BG.METHODS),
                    help="measured (a provider or tool meter), proxy (a stand-in such as a merge window), estimate")
    ap.add_argument("--interval-start", help="start of what the reading covers (ISO, UTC)")
    ap.add_argument("--interval-end", help="end of what the reading covers (ISO, UTC)")
    ap.add_argument("--includes-children", choices=["yes", "no"], help="whether sub-agent use is inside these numbers")
    ap.add_argument("--child-id", action="append", default=[], help="a child line's id this one includes (repeatable)")
    ap.add_argument("--correction-of", help="the id of the earlier line this one replaces")
    ap.add_argument("--scope", help="what the numbers cover, in words")
    ap.add_argument("--source", help="where the numbers came from")
    ap.add_argument("--agents", type=int)
    ap.add_argument("--note", default="")
    ap.add_argument("--actor", default=os.environ.get("RELAYKIT_ACTOR", ""))
    ap.add_argument("--ts")
    a = ap.parse_args(argv)
    if a.tokens_in < 0 or a.tokens_out < 0 or (a.agents is not None and a.agents < 0):
        sys.exit("refused: token and agent counts must be >= 0")
    if not a.actor.strip():
        sys.exit("refused: --actor (or RELAYKIT_ACTOR) is required: who used these tokens")
    if "\n" in a.note or "\n" in a.actor:
        sys.exit("refused: --note and --actor must be one line")
    ts = a.ts or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        sys.exit(f"refused: --ts {ts!r} isn't an ISO time (2026-11-05T14:00:00Z)")
    if a.dir.is_symlink():
        sys.exit(f"refused: {a.dir} is a symlink")
    a.dir.mkdir(parents=True, exist_ok=True)
    p = a.dir / "budget.jsonl"
    if p.is_symlink():
        sys.exit(f"refused: {p} is a symlink")
    row = {"ts": ts, "actor": a.actor.strip(), "window": a.window, "tokens_in": a.tokens_in,
           "tokens_out": a.tokens_out, "method": a.method, "metric_version": BG.SCHEMA.split("/")[1],
           "agents": a.agents, "note": a.note}
    for f, v in (("interval_start", a.interval_start), ("interval_end", a.interval_end),
                 ("correction_of", a.correction_of), ("scope", a.scope), ("source", a.source)):
        if v is not None:
            row[f] = v
    if a.includes_children:
        row["includes_children"] = {"included": a.includes_children == "yes", "ids": a.child_id}
    elif a.child_id:
        sys.exit("refused: --child-id needs --includes-children yes")
    if a.correction_of:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        known = {BG.line_id(raw, o) for _, raw, o in BG.parse(text)[0]}
        if a.correction_of not in known:
            sys.exit(f"refused: --correction-of {a.correction_of} isn't the id of a line in {p}")
    bad = BG.problems(row)
    if bad:
        sys.exit("refused: " + "; ".join(bad))
    row["id"] = "bud-" + hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:10]
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    prefix = ""
    if p.exists() and p.stat().st_size:
        with open(p, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"   # never glue onto a half line
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as f:
        f.write(prefix + line)
    print(line, end="")


if __name__ == "__main__":
    main()
