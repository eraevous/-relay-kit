#!/usr/bin/env python3
"""Append one line to the decision or challenge ledger, safely. Standard library only.

    python3 tools/ledger_append.py --dir governance decision '{"status": "adopted", "decision": "...", ...}'
    python3 tools/ledger_append.py --dir governance challenge '{"status": "open", "concern": "...", ...}'

Decision and challenge ids are one shared count (DEC-NNN, CH-NNN; DEC-018), so two sessions
that each take "the next number" from their own copy collide (DEC-035, 2026-11-02). This
command takes the id instead of you:
  1. fetches, and takes the next id after the highest one in *either* this copy or the shared
     copy (origin's default branch);
  2. fills `id`, `date` (today, UTC) and `recorded_by` ($RELAYKIT_ACTOR) when they're missing;
  3. appends the line, then runs check_ledgers.py on the result (with --git);
  4. on any problem, takes the line back out and exits non-zero. Its exit code is the check's:
     don't pipe it into `tail`, which hides a failure.
It prints the new id. What it can't close: another session taking the same id between this
append and your push. Push (or open the pull request) right away; the records check on main
catches what slips through.
"""
import argparse, datetime as dt, json, os, pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
KINDS = {"decision": ("decisions.jsonl", "DEC"), "challenge": ("challenges.jsonl", "CH")}


def git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, timeout=60)


def max_id(text, prefix):
    nums = [int(m) for m in re.findall(rf'"id":\s*"{prefix}-(\d+)"', text)]
    return max(nums, default=0)


def shared_text(d, name):
    """The ledger as the shared copy has it, after a fetch. (text, note)."""
    if git(d, "rev-parse", "--git-dir").returncode != 0:
        return "", "not a git repository: only this copy was read"
    note = None if git(d, "fetch", "--quiet", "origin").returncode == 0 else "fetch failed: used the last fetch"
    ref = git(d, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").stdout.strip()
    if not ref:
        for cand in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
            if git(d, "rev-parse", "--verify", "--quiet", cand).returncode == 0:
                ref = cand
                break
    if not ref:
        return "", "no shared branch found: only this copy was read"
    prefix = git(d, "rev-parse", "--show-prefix").stdout.strip()
    shown = git(d, "show", f"{ref}:{prefix}{name}")
    return (shown.stdout if shown.returncode == 0 else ""), note


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=pathlib.Path, default=pathlib.Path("governance"))
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("json", help="the line's fields as a JSON object; id, date and recorded_by are filled if missing")
    a = ap.parse_args()
    name, prefix = KINDS[a.kind]
    path = a.dir / name
    try:
        row = json.loads(a.json)
    except ValueError as e:
        sys.exit(f"refused: not JSON ({e})")
    if not isinstance(row, dict):
        sys.exit("refused: give one JSON object")
    if "id" in row:
        sys.exit("refused: leave 'id' out; this command assigns it")
    mine = path.read_text(encoding="utf-8") if path.exists() else ""
    theirs, note = shared_text(a.dir, name)
    if note:
        print(f"note: {note}", file=sys.stderr)
    new_id = f"{prefix}-{max(max_id(mine, prefix), max_id(theirs, prefix)) + 1:03d}"
    if max_id(theirs, prefix) > max_id(mine, prefix):
        sys.exit(f"refused: the shared copy has {prefix}-{max_id(theirs, prefix):03d}, which this copy doesn't; "
                 "pull first, then append (otherwise the numbering will have a gap here)")
    row = {"id": new_id, **row}
    row.setdefault("date", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    actor = os.environ.get("RELAYKIT_ACTOR", "").strip()
    if "recorded_by" not in row:
        if not actor:
            sys.exit("refused: set RELAYKIT_ACTOR (lane#session, or the owner's name) or give recorded_by")
        row["recorded_by"] = actor
    line = json.dumps(row, ensure_ascii=False) + "\n"
    body = mine if (not mine or mine.endswith("\n")) else mine + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + line, encoding="utf-8")
    base = "HEAD" if git(a.dir, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0 else None
    cmd = [sys.executable, "-I", str(HERE / "check_ledgers.py"), "--dir", str(a.dir)] + (["--git", "--base", base] if base else [])
    chk = subprocess.run(cmd, capture_output=True, text=True)
    if chk.returncode != 0:
        if mine:
            path.write_text(mine, encoding="utf-8")   # take it back out
        else:
            path.unlink()
        sys.stderr.write(chk.stdout + chk.stderr)
        sys.exit(f"refused: the ledger check failed with {new_id} added; nothing was appended")
    print(new_id)


if __name__ == "__main__":
    main()
