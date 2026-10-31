#!/usr/bin/env python3
"""Check an AEC-style decision ledger and challenge ledger (JSON lines). Standard library only.

    python3 tools/check_ledgers.py --dir path/to/governance [--git]

decisions.jsonl, one object per line:
  id        DEC-NNN, numbered in order
  date      YYYY-MM-DD
  status    proposed | adopted | rejected | superseded
  decision  what was decided, in one or two sentences
  scope     what it applies to
  decider   who decided ("owner" or a name); required unless proposed
  basis     the owner's words or the option they chose, quoted; required unless proposed
  locator   where those words are (thread, date, time)
  supersedes / evidence  optional

challenges.jsonl, one object per line:
  id        CH-NNN, numbered in order
  date      YYYY-MM-DD
  status    open | resolved | accepted-risk
  concern   the objection
  raised_by who raised it
  resolution required unless open
  resolves  optional: an earlier CH id this line closes (the earlier line stays as written)

With --git, also checks that both files only grew since the last commit, or since
--base REF (append-only):
earlier lines may not be edited or removed. A status change is a new line with the same
subject, not an edit."""
import argparse, json, pathlib, re, subprocess, sys

DEC_STATUS = {"proposed", "adopted", "rejected", "superseded"}
CH_STATUS = {"open", "resolved", "accepted-risk"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load(path):
    rows, problems = [], []
    if not path.exists():
        return rows, problems
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as e:
            problems.append(f"{path.name}:{n}: not JSON ({e})")
            continue
        if not isinstance(obj, dict):
            problems.append(f"{path.name}:{n}: not an object")
            continue
        rows.append((n, obj))
    return rows, problems


def check_ids(name, rows, prefix):
    out = []
    for i, (n, r) in enumerate(rows, 1):
        want = f"{prefix}-{i:03d}"
        if r.get("id") != want:
            out.append(f"{name}:{n}: id {r.get('id')!r}, expected {want} (ids are numbered in order)")
        if not DATE.match(str(r.get("date", ""))):
            out.append(f"{name}:{n}: date must be YYYY-MM-DD")
    return out


def check_decisions(rows):
    out = check_ids("decisions.jsonl", rows, "DEC")
    ids = {r.get("id") for _, r in rows}
    for n, r in rows:
        where = f"decisions.jsonl:{n}"
        if r.get("status") not in DEC_STATUS:
            out.append(f"{where}: status must be one of {sorted(DEC_STATUS)}")
        for f in ("decision", "scope"):
            if not str(r.get(f, "")).strip():
                out.append(f"{where}: '{f}' is required")
        if r.get("status") != "proposed":
            for f in ("decider", "basis", "locator"):
                if not str(r.get(f, "")).strip():
                    out.append(f"{where}: '{f}' is required once a decision isn't just proposed")
        sup = r.get("supersedes")
        if sup and sup not in ids:
            out.append(f"{where}: supersedes unknown {sup}")
    return out


def check_challenges(rows):
    out = check_ids("challenges.jsonl", rows, "CH")
    seen = set()
    for n, r in rows:
        res = r.get("resolves")
        if res and res not in seen:
            out.append(f"challenges.jsonl:{n}: resolves {res!r}, which isn't an earlier challenge")
        seen.add(r.get("id"))
        where = f"challenges.jsonl:{n}"
        if r.get("status") not in CH_STATUS:
            out.append(f"{where}: status must be one of {sorted(CH_STATUS)}")
        for f in ("concern", "raised_by"):
            if not str(r.get(f, "")).strip():
                out.append(f"{where}: '{f}' is required")
        if r.get("status") != "open" and not str(r.get("resolution", "")).strip():
            out.append(f"{where}: 'resolution' is required unless the challenge is open")
    return out


def check_append_only(path, base):
    try:
        rel = subprocess.run(["git", "-C", str(path.parent), "ls-files", "--full-name", path.name],
                             capture_output=True, text=True, check=True).stdout.strip()
        if not rel:
            return []   # not committed yet
        old = subprocess.run(["git", "-C", str(path.parent), "show", f"{base}:{rel}"],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    new = path.read_text(encoding="utf-8")
    if not new.startswith(old.rstrip("\n")):
        return [f"{path.name}: earlier lines changed since {base}; ledgers are append-only"]
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=pathlib.Path, default=pathlib.Path("governance"))
    ap.add_argument("--git", action="store_true", help="also check append-only against --base")
    ap.add_argument("--base", default="HEAD", help="git revision to compare with (default HEAD)")
    a = ap.parse_args()
    problems = []
    dec_rows, p = load(a.dir / "decisions.jsonl"); problems += p
    ch_rows, p = load(a.dir / "challenges.jsonl"); problems += p
    problems += check_decisions(dec_rows) + check_challenges(ch_rows)
    if a.git:
        for f in ("decisions.jsonl", "challenges.jsonl"):
            if (a.dir / f).exists():
                problems += check_append_only(a.dir / f, a.base)
    for x in problems:
        print("PROBLEM", x)
    print(f"{len(dec_rows)} decision(s), {len(ch_rows)} challenge(s), {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
