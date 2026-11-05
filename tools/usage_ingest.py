#!/usr/bin/env python3
"""Turn a Claude Code session's own usage log into one `measured` budget line (relay-kit/budget@2).

    relay-kit usage-ingest --session ID [--session ID ...] [--projects DIR] [--dir governance]
                           [--actor A] [--dry-run]

Claude Code keeps each session's transcript on the machine that ran it, under
~/.claude/projects/<folder>/<session>.jsonl, with the session's subagents in
<session>/subagents/agent-*.jsonl. Each assistant message there carries the API's `usage`:
input_tokens, output_tokens, cache_creation_input_tokens and cache_read_input_tokens, and the model.
This reads those files for the sessions named and appends, per session, one budget line:

- tokens_in  = input + cache_creation (input written fresh), tokens_out = output;
  tokens_detail = {input, output, cache_write, cache_read, by_model: {...}}: cache reads are kept
  apart, never folded into tokens_in;
- method "measured", window "session", interval_start / interval_end from the first and last
  timestamps in the files, includes_children {"included": true} with the number of subagent logs;
- id "cc-<session>"; running it again for the same session appends a correction of the last line
  for it, never a second line to be summed.

A message split over several log lines (one API response) is counted once, by its message id, across
the main log and the subagent logs. `costUSD` is not used. What's on disk is all it can see: a
transcript that was compacted, or a subagent log that was deleted, is missing from the count, and
the line's scope says which files it read. Nothing leaves the machine but the line it appends; no
file paths or folder names go into it. Standard library only.
"""
import argparse, datetime as dt, importlib.util, json, os, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("relaykit_budget_for_ingest", HERE / "budget.py")
BG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BG)
SESSION = re.compile(r"^[0-9a-fA-F-]{8,64}$")


class Refused(Exception):
    pass


def find_session(projects, sid):
    """The main log for a session id under the projects folder (any project subfolder)."""
    hits = sorted(p for p in projects.glob(f"*/{sid}.jsonl") if p.is_file() and not p.is_symlink())
    if not hits:
        raise Refused(f"no log for session {sid} under {projects}")
    if len(hits) > 1:
        raise Refused(f"session {sid} has logs in more than one project folder; give --projects narrower")
    return hits[0]


def read_usage(path, seen, totals, by_model, stamps):
    """Add one log file's usage (once per message id) to the running totals."""
    n = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            ts = o.get("timestamp")
            if isinstance(ts, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", ts):
                stamps.append(ts)
            m = o.get("message")
            if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
                continue
            mid = m.get("id") or o.get("requestId") or o.get("uuid")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            u = m["usage"]
            vals = {"input": u.get("input_tokens"), "output": u.get("output_tokens"),
                    "cache_write": u.get("cache_creation_input_tokens"), "cache_read": u.get("cache_read_input_tokens")}
            vals = {k: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0 for k, v in vals.items()}
            for k, v in vals.items():
                totals[k] += v
            mod = str(m.get("model") or "unknown")[:60]
            bm = by_model.setdefault(mod, {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "messages": 0})
            for k, v in vals.items():
                bm[k] += v
            bm["messages"] += 1
            n += 1
    return n


def utc(ts):
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def session_line(main, sid, actor):
    seen, stamps, by_model = set(), [], {}
    totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    msgs = read_usage(main, seen, totals, by_model, stamps)
    subdir = main.with_suffix("") / "subagents"
    subs = sorted(p for p in subdir.glob("agent-*.jsonl") if p.is_file() and not p.is_symlink()) if subdir.is_dir() else []
    sub_msgs = sum(read_usage(p, seen, totals, by_model, stamps) for p in subs)
    times = sorted(t for t in (utc(s) for s in stamps) if t)
    if not msgs and not sub_msgs:
        raise Refused(f"session {sid}: no usage in its logs")
    return {"ts": times[-1], "actor": actor, "window": "session", "method": "measured",
            "metric_version": BG.SCHEMA.split("/")[1],
            "tokens_in": totals["input"] + totals["cache_write"], "tokens_out": totals["output"],
            "tokens_detail": {**totals, "by_model": by_model},
            "interval_start": times[0], "interval_end": times[-1],
            "includes_children": {"included": True, "ids": []},
            "scope": (f"Claude Code session {sid} on the machine that ran it: main log ({msgs} messages) and "
                      f"{len(subs)} subagent log(s) ({sub_msgs} messages), as on disk; compacted or deleted parts aren't counted"),
            "source": "Claude Code local session logs (usage per assistant message; costUSD not used)",
            "agents": len(subs), "note": ""}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="relay-kit usage-ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="append", required=True, help="a Claude Code session id (repeatable)")
    ap.add_argument("--projects", type=pathlib.Path, default=pathlib.Path.home() / ".claude" / "projects",
                    help="Claude Code's projects folder (default ~/.claude/projects)")
    ap.add_argument("--dir", type=pathlib.Path, default=pathlib.Path("governance"), help="the records' governance folder")
    ap.add_argument("--actor", default=os.environ.get("RELAYKIT_ACTOR", ""), help="who ran the session (default $RELAYKIT_ACTOR)")
    ap.add_argument("--dry-run", action="store_true", help="print the lines instead of appending them")
    a = ap.parse_args(argv)
    try:
        if not a.actor.strip() or "\n" in a.actor:
            raise Refused("--actor (or RELAYKIT_ACTOR) is required: whose session this was")
        if a.dir.is_symlink() or (a.dir / "budget.jsonl").is_symlink():
            raise Refused(f"{a.dir} or its budget.jsonl is a symlink")
        p = a.dir / "budget.jsonl"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        rows = BG.parse(text)[0]
        known = [(BG.line_id(raw, o), o) for _, raw, o in rows]
        out = []
        for sid in a.session:
            if not SESSION.match(sid):
                raise Refused(f"{sid!r} isn't a session id")
            row = session_line(find_session(a.projects, sid), sid, a.actor.strip())
            base = f"cc-{sid}"
            chain = [i for i, _ in known if i == base or i.startswith(base + "-r")]
            if chain:      # again for a session already ingested: replace its latest line, never add a second
                replaced = {o.get("correction_of") for _, o in known if o.get("correction_of")}
                latest = next((i for i in reversed(chain) if i not in replaced), chain[-1])
                row["correction_of"], row["id"] = latest, f"{base}-r{len(chain)}"
            else:
                row["id"] = base
            bad = BG.problems(row)
            if bad:
                raise Refused("; ".join(bad))
            out.append(row)
            known.append((row["id"], row))
    except Refused as x:
        sys.exit(f"refused: {x}")
    lines = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out)
    if a.dry_run:
        sys.stdout.write(lines)
        return
    a.dir.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if p.exists() and p.stat().st_size:
        with open(p, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(prefix + lines)
    sys.stdout.write(lines)


if __name__ == "__main__":
    main()
