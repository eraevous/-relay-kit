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
    ap.add_argument("--human-minutes", type=int, help="self-reported active owner minutes")
    ap.add_argument("--merges", type=int, help="pull requests merged in the window")
    ap.add_argument("--note", default="")
    ap.add_argument("--actor", default=os.environ.get("RELAYKIT_ACTOR", ""))
    ap.add_argument("--ts")
    a = ap.parse_args(argv)
    if a.tokens_in < 0 or a.tokens_out < 0 or any(v is not None and v < 0 for v in (a.agents, a.human_minutes, a.merges)):
        sys.exit("refused: token, agent, minute and merge counts must be >= 0")
    if not a.actor.strip():
        sys.exit("refused: --actor (or RELAYKIT_ACTOR) is required: who used these tokens")
    if "\n" in a.note or "\n" in a.actor:
        sys.exit("refused: --note and --actor must be one line")
    if a.dir.is_symlink():
        sys.exit(f"refused: {a.dir} is a symlink")
    p = a.dir / "budget.jsonl"
    if p.is_symlink():
        sys.exit(f"refused: {p} is a symlink")
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    rows = BG.parse(text)[0]
    lines = {BG.line_id(raw, o): o for _, raw, o in rows if not BG.problems(o)}   # what readers count
    kept, rep = BG.effective(rows)
    target = None
    if a.correction_of:
        if a.correction_of not in lines:
            sys.exit(f"refused: --correction-of {a.correction_of} isn't the id of a valid line in {p}")
        if a.correction_of in rep["replaced"]:
            last = a.correction_of
            while last in rep["replaced"]:
                last = rep["replaced"][last]
            sys.exit(f"refused: {a.correction_of} is already corrected; correct the latest line in its chain, {last}")
        target = lines[a.correction_of]
        if target.get("window") != a.window:
            sys.exit(f"refused: {a.correction_of} has window {target.get('window')!r}; a correction keeps the window")
    for c in a.child_id:
        if c not in lines:
            sys.exit(f"refused: --child-id {c} isn't the id of a valid line in {p}")
    if a.child_id and a.includes_children != "yes":
        sys.exit("refused: --child-id needs --includes-children yes")
    # a correction is recorded at the time of the line it replaces unless --ts says otherwise, so
    # relabelling an old reading never moves it into today's window
    ts = a.ts or (target.get("ts") if target else dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if not BG.when(ts):
        sys.exit(f"refused: --ts {ts!r} isn't an ISO time with a zone (2026-11-05T14:00:00Z)")
    a.dir.mkdir(parents=True, exist_ok=True)
    row = {"ts": ts, "actor": a.actor.strip(), "window": a.window, "tokens_in": a.tokens_in,
           "tokens_out": a.tokens_out, "method": a.method, "metric_version": BG.SCHEMA.split("/")[1],
           "agents": a.agents, "note": a.note}
    for f, v in (("interval_start", a.interval_start), ("interval_end", a.interval_end), ("correction_of", a.correction_of),
                 ("scope", a.scope), ("source", a.source), ("human_minutes", a.human_minutes), ("merges", a.merges)):
        if v is not None:
            row[f] = v
    if a.includes_children:
        row["includes_children"] = {"included": a.includes_children == "yes", "ids": a.child_id}
    elif target and isinstance(target.get("includes_children"), dict):
        row["includes_children"] = target["includes_children"]      # a correction keeps the parent's inclusion
    bad = BG.problems(row)
    if bad:
        sys.exit("refused: " + "; ".join(bad))
    base = "bud-" + hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:10]
    taken = {BG.line_id(raw, o) for _, raw, o in rows}
    row["id"], k = base, 2
    while row["id"] in taken:        # the same reading twice still gets two ids
        row["id"], k = f"{base}-{k}", k + 1
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
