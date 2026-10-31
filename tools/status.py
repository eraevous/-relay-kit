#!/usr/bin/env python3
"""Where things stand: one read-only summary for starting back up after a restart.

    python3 tools/status.py --root path/to/records [--no-fetch]

Reads a records folder laid out like the kit's private repo (any part may be missing):
  handoffs/                 handoff files (relay-kit/handoff@1)
  governance/decisions.jsonl, governance/challenges.jsonl
  threads.md                the coordination index: which thread or session does what

and prints, in order of what needs attention:
  1. whether this copy is behind the shared copy (a stale copy is how two sessions
     created the same handoff id on 2026-10-29);
  2. what waits on the owner: proposed decisions nobody has superseded, and handoffs
     whose Authorized section is still unfilled;
  3. open handoffs by state, with who holds each one, flagging any whose output is
     already filed but whose state was never closed;
  4. open challenges;
  5. where the coordination index is and its last change-log line;
  6. what it couldn't check.

It changes nothing; `git fetch` only updates remote-tracking refs (skip it with
--no-fetch). Standard library only."""
import argparse, importlib.util, json, pathlib, re, subprocess, sys

KIT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = KIT / "skills" / "handoff-ledger" / "scripts" / "handoff.py"
HOLDER = {"prepared": "from", "sent": "to", "acknowledged": "to", "in_progress": "to", "blocked": "from"}


def load_ledger():
    spec = importlib.util.spec_from_file_location("handoff", LEDGER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def git(root, *args):
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or f"git {' '.join(args)} failed")
    return p.stdout.strip()


def freshness(root, fetch, gaps):
    try:
        git(root, "rev-parse", "--git-dir")
    except (RuntimeError, FileNotFoundError):
        gaps.append("not a git repository: couldn't tell whether this copy is current")
        return None
    if fetch:
        try:
            git(root, "fetch", "--quiet", "--all")
        except RuntimeError as e:
            gaps.append(f"git fetch failed, so 'behind' uses the last fetch ({e.splitlines()[0] if str(e) else e})")
    else:
        gaps.append("--no-fetch: 'behind' uses the last fetch")
    try:
        upstream = git(root, "rev-parse", "--abbrev-ref", "@{upstream}")
        behind, ahead = git(root, "rev-list", "--left-right", "--count", "@{upstream}...HEAD").split()
    except RuntimeError:
        gaps.append("current branch has no upstream: couldn't compare with the shared copy")
        return None
    dirty = bool(git(root, "status", "--porcelain"))
    return f"{git(root, 'rev-parse', '--abbrev-ref', 'HEAD')} vs {upstream}: {behind} behind, {ahead} ahead" + (
        ", uncommitted changes" if dirty else "")


def handoffs(root, gaps):
    d = root / "handoffs"
    if not d.is_dir():
        gaps.append("no handoffs/ folder")
        return [], []
    led = load_ledger()
    rows, unauthorized = [], []
    for p in sorted(d.glob("HO-*.md")):
        text = p.read_text(encoding="utf-8")
        try:
            meta, log, _, _ = led.parse_text(text, p.stem)
        except led.Refused as e:
            gaps.append(f"{p.name} couldn't be read ({e}); run handoff.py check")
            continue
        st = meta.get("status", "?")
        if st in led.FINAL:
            continue
        m = re.search(r"^## Authorized[ \t]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        auth = m.group(1) if m else ""
        if not auth.strip() or led.TODO_RE.search(auth) or "NOT AUTHORIZED" in auth:
            unauthorized.append(f"{meta.get('id')}  {meta.get('title', '')}")
        last = log[-1] if log else {}
        flag = "  OUTPUT FILED, state not closed" if (d / "outputs" / f"{meta.get('id')}.md").exists() else ""
        rows.append((st, meta.get("id"), meta.get(HOLDER.get(st, "to"), "?"), meta.get("title", ""),
                     f"{last.get('at', '')} {last.get('actor', '')}".strip() + flag))
    return rows, unauthorized


def jsonl(path, gaps):
    if not path.exists():
        gaps.append(f"no {path.parent.name}/{path.name}")
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                gaps.append(f"{path.name}:{n} isn't JSON; run tools/check_ledgers.py")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path("."))
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    root, gaps = a.root, []

    fresh = freshness(root, not a.no_fetch, gaps)
    rows, unauthorized = handoffs(root, gaps)
    decs = jsonl(root / "governance" / "decisions.jsonl", gaps)
    chs = jsonl(root / "governance" / "challenges.jsonl", gaps)
    superseded = {x.get("supersedes") for x in decs if x.get("supersedes")}
    waiting = [x for x in decs if x.get("status") == "proposed" and x.get("id") not in superseded]
    closed = {x.get("resolves") for x in chs if x.get("resolves")}
    open_ch = [x for x in chs if x.get("status") == "open" and x.get("id") not in closed]

    print("== Copy")
    print(fresh or "unknown (see Not checked)")
    if fresh and re.search(r": [1-9]\d* behind", fresh):
        print("PULL FIRST: this copy is behind; ids and states below may be stale.")

    print("\n== Waiting on the owner")
    for x in waiting:
        print(f"{x.get('id')}  proposed {x.get('date', '')}: {x.get('decision', '')}")
    for u in unauthorized:
        print(f"{u}  (Authorized not filled: nobody may start it)")
    if not waiting and not unauthorized:
        print("nothing")

    print("\n== Open handoffs")
    order = ["blocked", "prepared", "sent", "acknowledged", "in_progress"]
    for st, hid, holder, title, last in sorted(rows, key=lambda r: (order.index(r[0]) if r[0] in order else 9, r[1])):
        print(f"{hid}  {st:<12} with {holder}  {title}  [last: {last}]")
    if not rows:
        print("none")

    print("\n== Open challenges")
    for x in open_ch:
        print(f"{x.get('id')}  {x.get('concern', '')}")
    if not open_ch:
        print("none")

    print("\n== Coordination index")
    idx = root / "threads.md"
    if idx.exists():
        changes = [l for l in idx.read_text(encoding="utf-8").splitlines() if l.startswith("- 20")]
        print(f"{idx}" + (f"\nlast change: {changes[-1][:200]}" if changes else ""))
    else:
        gaps.append("no threads.md: which thread does what isn't recorded here")
        print("none")

    print("\n== Not checked")
    for g in gaps + ["whether chat threads still exist or match their records (only files are read)",
                     "work done outside the records (local Codex threads, unfiled chat replies)"]:
        print(f"- {g}")
    sys.exit(0)


if __name__ == "__main__":
    main()
