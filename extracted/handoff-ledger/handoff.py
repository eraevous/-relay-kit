#!/usr/bin/env python3
"""Handoff ledger: create, update, list and check handoff files. Standard library only.

Enforced: handoff ids of the form HO-YYYYMMDD-NNN that stay inside --dir; the
whole record is validated before any update, with the same rules `check` uses;
legal state moves (blocked returns only to the state it left); notes on sent,
blocked and completed; an output file before completed; single-line fields; log
lines written inside the Log section; and --by matching the declared sender
(sent) or recipient (acknowledged, in_progress, completed), or the owner. The owner's
name comes from <dir>/.ledger.json ({"owner": "sam"}); without it the name is "owner".
Writes go to a temporary file that replaces the original, under a lock file,
and are refused if the file changed after it was read. --at records an event after
the fact: the log line takes the event time and the note gets "(recorded <now>)".

Not enforced: --by is a typed name, not an authenticated identity; an output
file existing says nothing about its quality; hand edits bypass all of this
(`check` finds the damage afterwards). The log is append-only by convention,
not tamper-evident: keep the folder in git for a history you can trust."""
import argparse, datetime as dt, hashlib, json, os, pathlib, re, sys

CONTRACT = "relay-kit/handoff@1"   # file format, states and log line; see governance/contracts.json
STATES = ["prepared", "sent", "acknowledged", "in_progress", "completed", "blocked", "cancelled"]
FINAL = {"completed", "cancelled"}
NEXT = {
    "prepared": {"sent"},
    "sent": {"acknowledged"},
    "acknowledged": {"in_progress", "completed"},
    "in_progress": {"completed"},
}
NOTE_REQUIRED = {"sent", "blocked", "completed"}
SENDER_STATES = {"sent"}
RECIPIENT_STATES = {"acknowledged", "in_progress", "completed"}
OWNER = "owner"   # replaced at start-up by <dir>/.ledger.json, if present


def load_owner(d):
    cfg = pathlib.Path(d) / ".ledger.json"
    if not cfg.exists():
        return "owner"
    try:
        name = str(json.loads(cfg.read_text(encoding="utf-8")).get("owner", "")).strip().lower()
    except (ValueError, AttributeError):
        raise Refused(f"{cfg} isn't valid JSON like {{\"owner\": \"sam\"}}")
    if not name or not re.fullmatch(r"[\w.:-]+", name):
        raise Refused(f"{cfg}: owner must be one word (letters, digits, . : - _)")
    return name
ID_RE = re.compile(r"^HO-\d{8}-\d{3}$")
REQUIRED = ["id", "title", "from", "to", "created", "status", "source", "last_processed"]
SECTIONS = ["Task", "Authorized", "Proposals", "Context", "Expected output", "Limits", "Log"]
LOG_RE = re.compile(r"^- (\S+) \| (\w+) \| ([^|]+?) \|(?: (.*))?$")
TODO_RE = re.compile(r"^\s*(?:- )?TODO\b", re.M)
LOCK = ".handoff.lock"

TEMPLATE = """---
id: {id}
title: {title}
from: {frm}
to: {to}
created: {now}
status: prepared
contract: relay-kit/handoff@1
source: {source}
last_processed: {last}
reply_to: {reply_to}
---

## Task
TODO

## Authorized
- TODO: quote the owner exactly, with a locator (thread URL or message id, date)
- Approving: TODO (the proposal or question their words answer, quoted or linked)
- Permits: TODO (the actions this approval covers)
- Doesn't cover: TODO (or "anything not listed above")

## Proposals
- none

## Context
TODO

## Expected output
Write the result to outputs/{id}.md.

## Limits
TODO

## Log
- {now} | prepared | {frm} | drafted
"""


class Refused(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def event_time(at, prev_at):
    """Normalize --at to YYYY-MM-DDTHH:MMZ; must be in the past and not before the previous log line."""
    try:
        d = dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        raise Refused(f"--at '{at}' isn't an ISO time like 2026-10-28T17:51Z")
    if d.tzinfo is None:
        raise Refused("--at needs a timezone (end it with Z for UTC)")
    d = d.astimezone(dt.timezone.utc)
    if d > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1):
        raise Refused("--at is in the future")
    s = d.strftime("%Y-%m-%dT%H:%MZ")
    if prev_at and s < prev_at:
        raise Refused(f"--at {s} is earlier than the previous log line ({prev_at})")
    return s


def single_line(name, value, allow_pipe=True):
    if value is None:
        return
    if "\n" in value or "\r" in value:
        raise Refused(f"{name} must be one line")
    if not allow_pipe and "|" in value:
        raise Refused(f"{name} can't contain '|'")


def owner_error(meta, state, by):
    """Name matching only: compares a typed name with the file's from/to fields."""
    by = by.strip().lower()
    if by == OWNER:
        return None
    if state == "cancelled":
        return f"only the owner can cancel (--by {OWNER})"
    if state in SENDER_STATES and by != meta.get("from", "").lower():
        return f"'{state}' is set by the sender ({meta.get('from')}) or the owner ({OWNER})"
    if state in RECIPIENT_STATES and by != meta.get("to", "").lower():
        return f"'{state}' is set by the recipient ({meta.get('to')}) or the owner ({OWNER})"
    if state == "blocked" and by not in (meta.get("from", "").lower(), meta.get("to", "").lower()):
        return f"'blocked' is set by the sender, the recipient or the owner ({OWNER})"
    if state == "prepared" and by not in (meta.get("from", "").lower(),):
        return f"'prepared' is set by the sender or the owner ({OWNER})"
    return None


def legal(prev_states, new):
    """prev_states: states logged so far. Returns an error string or None."""
    if new not in STATES:
        return f"unknown state '{new}'"
    cur = prev_states[-1]
    if cur in FINAL:
        return f"handoff is {cur}; open a new one with --reply-to"
    if new == "cancelled":
        return None
    if new == "blocked":
        return None if cur != "blocked" else "already blocked"
    if cur == "blocked":
        before = next((s for s in reversed(prev_states) if s != "blocked"), "prepared")
        return None if new == before else f"blocked from '{before}': it can only return to '{before}' (or be cancelled)"
    return None if new in NEXT.get(cur, set()) else f"can't go {cur} -> {new}"


def parse_text(text, name="handoff"):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise Refused(f"{name}: no front matter")
    meta = {}
    for line in m.group(1).splitlines():
        line = re.sub(r"\s+#.*$", "", line)
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    body = m.group(2)
    log, log_span = [], None
    sec = re.search(r"^## Log[ \t]*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    if sec:
        start = m.start(2) + sec.start(1)
        log_span = (start, m.start(2) + sec.end(1))
        for line in sec.group(1).splitlines():
            lm = LOG_RE.match(line.strip())
            if lm:
                log.append(dict(at=lm.group(1), state=lm.group(2), actor=lm.group(3).strip(), note=(lm.group(4) or "").strip()))
    sections = re.findall(r"^## (.+?)\s*$", body, re.M)
    return meta, log, sections, log_span


def problems_in(text, stem, outputs_dir):
    """Every rule `check` applies to one file. `set` runs this before writing."""
    out = []
    try:
        meta, log, sections, _ = parse_text(text, stem)
    except Refused as e:
        return [str(e)], None, []
    if not ID_RE.match(stem):
        out.append(f"file name {stem} isn't a handoff id (HO-YYYYMMDD-NNN)")
    for k in REQUIRED:
        if not meta.get(k):
            out.append(f"missing '{k}'")
    if meta.get("contract", CONTRACT) != CONTRACT:
        out.append(f"contract {meta.get('contract')!r}; this script reads {CONTRACT}")
    if meta.get("id") and meta["id"] != stem:
        out.append(f"id {meta['id']} doesn't match file name")
    for s in SECTIONS:
        if s not in sections:
            out.append(f"missing section '## {s}'")
    if sections and sections[-1] != "Log":
        out.append("'## Log' must be the last section")
    if meta.get("status") not in ("prepared", "cancelled") and TODO_RE.search(text):
        out.append("still has TODO placeholders after leaving 'prepared'")
    if not log:
        out.append("empty log")
        return out, meta, log
    if log[0]["state"] != "prepared":
        out.append("log must start with 'prepared'")
    for i in range(1, len(log)):
        if log[i]["at"] < log[i - 1]["at"]:
            out.append(f"log line {i + 1}: time {log[i]['at']} is earlier than the line before ({log[i - 1]['at']})")
    for i in range(1, len(log)):
        err = legal([e["state"] for e in log[:i]], log[i]["state"])
        if err:
            out.append(f"log line {i + 1}: {err}")
    for i, e in enumerate(log, 1):
        if i > 1:
            err = owner_error(meta, e["state"], e["actor"])
            if err:
                out.append(f"log line {i}: {err}")
        if e["state"] in NOTE_REQUIRED and not e["note"]:
            out.append(f"log line {i}: '{e['state']}' has no note")
    if log[-1]["state"] != meta.get("status"):
        out.append(f"status '{meta.get('status')}' but last log line says '{log[-1]['state']}'")
    if meta.get("status") == "completed" and not (outputs_dir / f"{stem}.md").exists():
        out.append(f"completed but outputs/{stem}.md is missing")
    return out, meta, log


def resolve(d, hid):
    if not ID_RE.match(hid):
        raise Refused(f"'{hid}' isn't a handoff id (HO-YYYYMMDD-NNN)")
    base = d.resolve()
    p = (base / f"{hid}.md").resolve()
    if p.parent != base:
        raise Refused(f"'{hid}' resolves outside {d}")
    if not p.exists():
        raise Refused(f"no handoff {hid} in {d}")
    return p


class Lock:
    def __init__(self, d):
        self.path = d / LOCK

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise Refused(f"another update holds {self.path}; retry, or delete it if no update is running")
        os.write(self.fd, f"{os.getpid()} {now()}\n".encode())
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def atomic_write(p, text):
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def cmd_new(a):
    for name in ("from", "to", "title", "source", "last_processed", "reply_to"):
        single_line(f"--{name.replace('_', '-')}", getattr(a, "frm" if name == "from" else name))
    single_line("--from", a.frm, allow_pipe=False)
    if a.reply_to and not ID_RE.match(a.reply_to):
        raise Refused(f"--reply-to '{a.reply_to}' isn't a handoff id")
    a.dir.mkdir(parents=True, exist_ok=True)
    (a.dir / "outputs").mkdir(exist_ok=True)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    text_for = lambda hid: TEMPLATE.format(id=hid, title=a.title, frm=a.frm, to=a.to, now=now(),
                                           source=a.source or "none", last=a.last_processed or "none",
                                           reply_to=a.reply_to or "")
    for n in range(1, 1000):
        hid = f"HO-{day}-{n:03d}"
        try:
            with open(a.dir / f"{hid}.md", "x", encoding="utf-8", newline="\n") as f:
                f.write(text_for(hid))
        except FileExistsError:
            continue
        print(hid)
        return
    raise Refused(f"no free id left for {day}")


def cmd_set(a):
    single_line("--by", a.by, allow_pipe=False)
    single_line("--note", a.note)
    single_line("--last-processed", a.last_processed)
    single_line("--at", a.at)
    p = resolve(a.dir, a.id)
    with Lock(a.dir.resolve()):
        text = p.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode()).hexdigest()
        problems, meta, log = problems_in(text, p.stem, a.dir / "outputs")
        if problems:
            raise Refused("the file has problems; fix them first (or record 'blocked' by hand):\n  " + "\n  ".join(problems))
        states = [e["state"] for e in log]
        for err in (legal(states, a.state), owner_error(meta, a.state, a.by)):
            if err:
                raise Refused(err)
        if a.state in NOTE_REQUIRED and not a.note:
            raise Refused(f"'{a.state}' needs --note (how it was sent / why blocked / where the output is)")
        if a.state != "prepared" and TODO_RE.search(text) and a.state not in ("blocked", "cancelled"):
            raise Refused("TODO placeholders are still in the file; fill them before it leaves 'prepared'")
        if a.state == "completed" and not (a.dir / "outputs" / f"{a.id}.md").exists():
            raise Refused(f"completed needs outputs/{a.id}.md to exist")
        when, note = now(), a.note
        if not a.at and log and when < log[-1]["at"]:
            raise Refused(f"the clock says {when}, earlier than the last log line ({log[-1]['at']}); check the times or use --at")
        if a.at:   # recording after the fact: the line carries the event time, the note the recording time
            when = event_time(a.at, log[-1]["at"])
            note = f"{note or ''} (recorded {now()})".strip()
        line = f"- {when} | {a.state} | {a.by} |" + (f" {note}" if note else "")
        _, _, _, span = parse_text(text, p.stem)
        log_text = text[span[0]:span[1]].rstrip("\n")
        new_text = text[:span[0]] + (log_text + "\n" if log_text else "") + line + "\n" + text[span[1]:].lstrip("\n")
        new_text = re.sub(r"^status:.*$", f"status: {a.state}", new_text, count=1, flags=re.M)
        if a.last_processed:
            new_text = re.sub(r"^last_processed:.*$", f"last_processed: {a.last_processed}", new_text, count=1, flags=re.M)
        after, _, _ = problems_in(new_text, p.stem, a.dir / "outputs")
        if after:
            raise Refused("the update would leave the file invalid:\n  " + "\n  ".join(after))
        if hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest() != digest:
            raise Refused("the file changed while updating; re-read it and retry")
        atomic_write(p, new_text)
    print(f"{a.id}: {states[-1]} -> {a.state}")


def cmd_list(a):
    rows = []
    for p in sorted(a.dir.glob("HO-*.md")):
        try:
            meta, log, _, _ = parse_text(p.read_text(encoding="utf-8"), p.stem)
        except Refused as e:
            rows.append((p.stem, "?", "", "", str(e)))
            continue
        if a.status and meta.get("status") != a.status:
            continue
        last = log[-1] if log else {}
        rows.append((meta.get("id", p.stem), meta.get("status", "?"), meta.get("from", ""), meta.get("to", ""),
                     f"{meta.get('title', '')}  [{last.get('at', '')} {last.get('actor', '')}]"))
    if not rows:
        print("no handoffs")
    for r in rows:
        print(f"{r[0]}  {r[1]:<12} {r[2]} -> {r[3]}  {r[4]}")


def cmd_check(a):
    total, ids = [], {}
    files = sorted(a.dir.glob("HO-*.md"))
    for p in files:
        problems, meta, _ = problems_in(p.read_text(encoding="utf-8"), p.stem, a.dir / "outputs")
        if meta and meta.get("id") in ids:
            problems.append(f"duplicate id with {ids[meta['id']]}")
        if meta:
            ids[meta.get("id")] = p.name
        total += [f"{p.name}: {x}" for x in problems]
    if (a.dir / LOCK).exists():
        total.append(f"{LOCK} exists: an update is running or was interrupted")
    for t in total:
        print("PROBLEM", t)
    print(f"{len(files)} handoff(s), {len(total)} problem(s)")
    sys.exit(1 if total else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=pathlib.Path, default=pathlib.Path("handoffs"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--from", dest="frm", required=True)
    n.add_argument("--to", required=True)
    n.add_argument("--title", required=True)
    n.add_argument("--source")
    n.add_argument("--last-processed")
    n.add_argument("--reply-to")
    s = sub.add_parser("set")
    s.add_argument("id")
    s.add_argument("state", choices=STATES)
    s.add_argument("--by", required=True)
    s.add_argument("--note")
    s.add_argument("--last-processed")
    s.add_argument("--at", help="when the event happened, if recording it later (ISO, e.g. 2026-10-28T17:51Z)")
    l = sub.add_parser("list")
    l.add_argument("--status", choices=STATES)
    sub.add_parser("check")
    a = ap.parse_args()
    global OWNER
    try:
        OWNER = load_owner(a.dir)
        {"new": cmd_new, "set": cmd_set, "list": cmd_list, "check": cmd_check}[a.cmd](a)
    except Refused as e:
        sys.exit(f"refused: {e}")


if __name__ == "__main__":
    main()
