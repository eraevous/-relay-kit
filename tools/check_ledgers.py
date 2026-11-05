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
  amends    optional, informational: on a line that supersedes a proposal with the owner's edited
            text (the cockpit's Amend), the proposal it amends
  ask       optional, on a proposal: what it asks of the owner, in the proposer's words (checked
            when it's an object; question, at least one option and the default are required):
            {"question": "...?", "options": [{"label": "Accept", "effect": "what happens"}, ...],
             "default_if_unanswered": "what happens if nobody answers"}

challenges.jsonl, one object per line:
  id        CH-NNN, numbered in order
  date      YYYY-MM-DD
  status    open | resolved | accepted-risk
  concern   the objection
  raised_by who raised it
  resolution required unless open
  resolves  optional: an earlier CH id this line closes (the earlier line stays as written)
  ask       optional, as on decisions
  kind      optional: "question" for a question asked on the record (the cockpit's Clarify); it then
            needs about (the DEC, CH or HO id it asks about) and addressed_to (who it's for). Other
            values aren't checked.
            Whoever answers appends a line that resolves it, with the answer as the resolution.

No field of any line may still hold a "<<REQUIRED" placeholder: the cockpit composes lines with
them where only the owner can supply the words (decider, basis, locator, resolution).

With --git, also checks that both files only grew since the last commit, or since
--base REF (append-only):
earlier lines may not be edited or removed. A status change is a new line with the same
subject, not an edit. It also checks that the ledgers aren't set to git's `merge=union`
(which would join two branches' lines silently, duplicate ids included), and that every
line added since --base has `recorded_by`: the owner's name, or an agent with its session
(`"claude:aec#01N4"`: a lane is not an actor)."""
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


PLACEHOLDER = "<<REQUIRED"   # the cockpit's composed lines carry these until the owner fills them in


def check_placeholders(name, rows):
    """A line still holding a <<REQUIRED: ...>> placeholder was pasted without the owner's own words."""
    out = []

    def walk(v):
        if isinstance(v, str):
            return PLACEHOLDER in v
        if isinstance(v, dict):
            return any(walk(x) for x in v.values())
        if isinstance(v, list):
            return any(walk(x) for x in v)
        return False
    for n, r in rows:
        bad = sorted(k for k, v in r.items() if walk(v))
        if bad:
            out.append(f"{name}:{n}: {', '.join(bad)} still hold a {PLACEHOLDER}...>> placeholder; replace it with your own words")
    return out


ASK_KEYS = {"question", "options", "default_if_unanswered"}
REF_RE = re.compile(r"^(?:DEC-\d{3,}|CH-\d{3,}|HO-[A-Z0-9]{1,12}-\d{8}-[0-9A-HJKMNP-TV-Z]{4}|HO-\d{8}-\d{3})$")


def check_ask(where, a):
    """The optional `ask` object: what a proposal asks of the owner, in the proposer's words.
    Checked only in its object form; another value in an 'ask' field (allowed before) is left alone,
    and the cockpit ignores it."""
    if not isinstance(a, dict):
        return []
    out = [f"{where}: 'ask' has unknown key {k!r}" for k in sorted(set(a) - ASK_KEYS)]
    q = a.get("question")
    if not isinstance(q, str) or not q.strip().endswith("?") or len(q.strip()) < 3:
        out.append(f"{where}: ask.question must be one sentence ending in '?'")
    opts = a.get("options")
    if not isinstance(opts, list) or not opts or not all(isinstance(o, dict) and isinstance(o.get("label"), str) and o["label"].strip()
                                             and isinstance(o.get("effect"), str) and o["effect"].strip() for o in opts):
        out.append(f"{where}: ask.options must be a non-empty list of {{label, effect}}, both non-empty text")
    d = a.get("default_if_unanswered")
    if not (isinstance(d, str) and d.strip()):
        out.append(f"{where}: ask.default_if_unanswered must say what happens if nobody answers")
    return out


def check_decisions(rows):
    out = check_ids("decisions.jsonl", rows, "DEC") + check_placeholders("decisions.jsonl", rows)
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
        out += check_ask(where, r.get("ask"))
    return out


def check_challenges(rows):
    out = check_ids("challenges.jsonl", rows, "CH") + check_placeholders("challenges.jsonl", rows)
    seen = set()
    for n, r in rows:
        res = r.get("resolves")
        if res and res not in seen:
            out.append(f"challenges.jsonl:{n}: resolves {res!r}, which isn't an earlier challenge")
        seen.add(r.get("id"))
        where = f"challenges.jsonl:{n}"
        out += check_ask(where, r.get("ask"))
        if r.get("kind") == "question":   # other kinds were allowed before and still are
            if not isinstance(r.get("about"), str) or not REF_RE.match(r["about"]):
                out.append(f"{where}: a question needs 'about': the DEC, CH or HO id it asks about")
            if not isinstance(r.get("addressed_to"), str) or not r["addressed_to"].strip():
                out.append(f"{where}: a question needs 'addressed_to': who it's addressed to")
        if r.get("status") not in CH_STATUS:
            out.append(f"{where}: status must be one of {sorted(CH_STATUS)}")
        for f in ("concern", "raised_by"):
            if not str(r.get(f, "")).strip():
                out.append(f"{where}: '{f}' is required")
        if r.get("status") != "open" and not str(r.get("resolution", "")).strip():
            out.append(f"{where}: 'resolution' is required unless the challenge is open")
    return out


def check_merge_attr(path):
    p = subprocess.run(["git", "-C", str(path.parent), "check-attr", "merge", "--", path.name],
                       capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip().endswith(": union"):
        return [f"{path.name}: .gitattributes sets merge=union; concurrent appends must conflict, not be joined"]
    return []


def check_new_recorders(path, base):
    """Lines added since base: an agent recorder ('x:y') must carry a session ('x:y#id')."""
    try:
        rel = subprocess.run(["git", "-C", str(path.parent), "ls-files", "--full-name", path.name],
                             capture_output=True, text=True, check=True).stdout.strip()
        old = subprocess.run(["git", "-C", str(path.parent), "show", f"{base}:{rel}"],
                             capture_output=True, text=True, check=True).stdout if rel else ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        old = ""
    out = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines[len(old.splitlines()):], len(old.splitlines()) + 1):
        try:
            who = str(json.loads(line).get("recorded_by", "")).strip()
        except (ValueError, AttributeError):
            continue
        first = who.split()[0] if who else ""
        if not who:
            out.append(f"{path.name}:{n}: new lines need 'recorded_by' (the owner's name, or lane#session)")
        elif ":" in first and not re.fullmatch(r"[\w.-]+:[\w.-]+#[\w-]+", first):
            out.append(f"{path.name}:{n}: recorded_by {who!r} names a lane, not a session (e.g. {first.split('#')[0]}#<session>)")
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
                problems += check_merge_attr(a.dir / f) + check_new_recorders(a.dir / f, a.base)
    for x in problems:
        print("PROBLEM", x)
    print(f"{len(dec_rows)} decision(s), {len(ch_rows)} challenge(s), {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
