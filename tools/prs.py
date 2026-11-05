#!/usr/bin/env python3
"""relay-kit/prs@1: a build-time, opt-in reader of GitHub pull-request state into the records.

    relay-kit prs --root RECORDS [--repo OWNER/NAME ...] [--since-days N] [--limit N]
                  [--via gh|token] [--token-env NAME] [--dry-run]

Reads the pull requests of the repositories listed in handoffs/.ledger.json ("repos": ["owner/name", ...])
or given with --repo, and appends to governance/pr-state.jsonl:

- one "pr" line per pull request whose state changed since its last line (title, state, author, head,
  reviews, CI, and the PR body's ## What / ## Why / ## How / ## Authorized sections verbatim, with an
  Author: line if the body has one, and which sections are missing);
- one "fetch" line per run: when it ran, which repositories it read and which failed.

It is the only part of relay-kit that talks to GitHub for pull requests: the Cockpit renders the file
and never fetches, and other readers (a telemetry collector, say) read the file rather than GitHub.
Nothing is summarised: section text is the body's own words. The file is append-only; this tool
never rewrites a line. It writes nothing else, under --root or anywhere.

Network: through the gh CLI (--via gh, the default when gh is installed) or the GitHub REST API
with a token read from an environment variable (--via token, default GITHUB_TOKEN then GH_TOKEN).
The token is never written or printed.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SCHEMA = "relay-kit/prs@1"
OUT = "governance/pr-state.jsonl"
SECTIONS = ("what", "why", "how", "authorized")
MAX_SECTION = 4000      # characters kept per section; longer ones are cut and listed under "truncated"
REPO = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
AUTHOR = re.compile(r"^[ \t]*\**Author\**[ \t]*:[ \t]*\**[ \t]*(\S.*?)[ \t]*$", re.I)
SIGNED = re.compile(r"^[ \t]*\**(?:Reviewer|Reviewed by|Review by)\**[ \t]*:[ \t]*\**[ \t]*(.+?)[ \t]*$", re.I)
COMMENT = re.compile(r"<!--.*?-->", re.S)
# a verdict written in a review's opening words, for reviews posted as comments ("**APPROVED (round 4)**")
STATED = re.compile(r"\b(APPROVED|APPROVE|REQUEST(?:ING)? CHANGES|CHANGES REQUESTED|LGTM|BLOCK(?:ING|ED)?)\b")
NEGATED = re.compile(r"\b(?:not|no|never|isn't|aren't|cannot|can't|won't|un)\W*$", re.I)
FENCE = re.compile(r"^[ \t]*(```|~~~)")
FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "error"}
COMPARE_SKIP = {"ts"}


class Refused(Exception):
    pass


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- the PR body ----------------
def section_key(title):
    """'What', 'What this changes', '1. Why:' -> 'what' / 'why'; anything else None."""
    w = re.sub(r"^[\d.)\s]+", "", title).strip().lower()
    w = re.split(r"[^a-z]", w, maxsplit=1)[0] if w else ""
    return w if w in SECTIONS else None


def strip_comments(text):
    """HTML comments removed outside code fences only, so a fenced example stays verbatim."""
    out, chunk, fenced = [], [], False
    for ln in text.split("\n"):
        if FENCE.match(ln):
            out.append(chunk if fenced else COMMENT.sub("", "\n".join(chunk)).split("\n"))
            out.append([ln])
            chunk, fenced = [], not fenced
            continue
        chunk.append(ln)
    out.append(chunk if fenced else COMMENT.sub("", "\n".join(chunk)).split("\n"))
    return "\n".join(ln for part in out for ln in part)


def stated_verdict(body):
    """A verdict word in a review's opening words, unless negated ("NOT APPROVED", "not LGTM yet")."""
    head = (body or "")[:160]
    for m in STATED.finditer(head):
        if not NEGATED.search(head[max(0, m.start() - 16):m.start()]):
            return m.group(1)
    return None


def parse_body(body):
    """{sections: {key: text}, author: str|None, missing: [...], truncated: [...]}. Section text is the
    body's own words between the heading and the next heading of the same or a higher level, with HTML
    comments (template hints) removed; a section with nothing left counts as missing. Headings inside
    code fences don't count."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    heads, fenced = [], False
    for i, ln in enumerate(lines):
        if FENCE.match(ln):
            fenced = not fenced
            continue
        m = None if fenced else HEADING.match(ln)
        if m:
            heads.append((i, len(m.group(1)), section_key(m.group(2))))
    sections, truncated = {}, []
    for j, (i, level, key) in enumerate(heads):
        if not key or key in sections:
            continue
        end = next((k for k, lv, _ in heads[j + 1:] if lv <= level), len(lines))
        text = strip_comments("\n".join(lines[i + 1:end])).strip()
        if not text:
            continue
        if len(text) > MAX_SECTION:
            text, _ = text[:MAX_SECTION], truncated.append(key)
        sections[key] = text
    author, fenced = None, False
    for ln in strip_comments("\n".join(lines)).split("\n"):
        if FENCE.match(ln):
            fenced = not fenced
        elif not fenced and (m := AUTHOR.match(ln)):
            author = m.group(1)[:200]
            break
    return {"sections": sections, "author": author,
            "missing": [k for k in SECTIONS if k not in sections], "truncated": truncated}


# ---------------- GitHub ----------------
def gh_getter():
    def get(path):
        try:
            p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as x:
            raise Refused(f"gh api {path}: {x}")
        if p.returncode != 0:
            raise Refused(f"gh api {path}: {(p.stderr or p.stdout).strip()[:200]}")
        return json.loads(p.stdout or "null")
    return get


def token_getter(token):
    def get(path):
        req = urllib.request.Request("https://api.github.com/" + path,
                                     headers={"Authorization": f"Bearer {token}",
                                              "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28",
                                              "User-Agent": "relay-kit-prs"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as x:
            raise Refused(f"GET {path}: HTTP {x.code}")
        except (urllib.error.URLError, OSError) as x:
            raise Refused(f"GET {path}: {x}")
    return get


def ci_state(get, repo, sha):
    """{state: success|failure|pending|none, failing: [names]} from check runs and commit statuses."""
    runs = (get(f"repos/{repo}/commits/{sha}/check-runs?per_page=100") or {}).get("check_runs") or []
    stat = get(f"repos/{repo}/commits/{sha}/status") or {}
    failing, pending, seen = [], False, 0
    for r in runs:
        seen += 1
        if r.get("status") != "completed":
            pending = True
        elif (r.get("conclusion") or "") in FAILED:
            failing.append(r.get("name") or "?")
    for s in stat.get("statuses") or []:
        seen += 1
        if s.get("state") == "pending":
            pending = True
        elif s.get("state") in ("failure", "error"):
            failing.append(s.get("context") or "?")
    state = "failure" if failing else "pending" if pending else "success" if seen else "none"
    return {"state": state, "failing": sorted(set(failing))}


def pr_record(get, repo, pr, ts):
    n = pr["number"]
    body = parse_body(pr.get("body"))
    reviews = []
    for r in get(f"repos/{repo}/pulls/{n}/reviews?per_page=100") or []:
        if r.get("state") in (None, "PENDING"):
            continue
        sig = next((m.group(1)[:120] for ln in (r.get("body") or "").split("\n") if (m := SIGNED.match(ln))), None)
        reviews.append({"by": (r.get("user") or {}).get("login"), "verdict": r.get("state"),
                        "at": r.get("submitted_at"), "signed": sig, "stated": stated_verdict(r.get("body"))})
    sha = (pr.get("head") or {}).get("sha")
    state = "merged" if pr.get("merged_at") else pr.get("state")
    return {"schema": SCHEMA, "kind": "pr", "ts": ts, "repo": repo, "number": n,
            "url": pr.get("html_url"), "title": pr.get("title"), "state": state, "draft": bool(pr.get("draft")),
            "author": (pr.get("user") or {}).get("login"), "author_line": body["author"],
            "created_at": pr.get("created_at"), "updated_at": pr.get("updated_at"),
            "merged_at": pr.get("merged_at"), "closed_at": pr.get("closed_at"),
            "base": (pr.get("base") or {}).get("ref"), "head": {"ref": (pr.get("head") or {}).get("ref"), "sha": sha},
            "reviews": reviews, "ci": ci_state(get, repo, sha) if sha else {"state": "none", "failing": []},
            "body_sections": body["sections"], "missing": body["missing"], "truncated": body["truncated"]}


# ---------------- the file ----------------
def read_lines(path):
    out = []
    if path.exists():
        for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    raise Refused(f"{OUT} line {i} isn't JSON; fix it by hand before appending")
    return out


def latest_by_pr(lines):
    last = {}
    for x in lines:
        if x.get("kind") == "pr":
            last[(x.get("repo"), x.get("number"))] = x
    return last


def same(a, b):
    return {k: v for k, v in a.items() if k not in COMPARE_SKIP} == {k: v for k, v in b.items() if k not in COMPARE_SKIP}


def repos_of(root, given):
    if given:
        repos = list(given)
    else:
        try:
            cfg = json.loads((root / "handoffs" / ".ledger.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        repos = list(cfg.get("repos") or [])
        if not repos:
            raise Refused('no repositories: pass --repo OWNER/NAME or add "repos": ["owner/name"] '
                          "to handoffs/.ledger.json")
    bad = [r for r in repos if not isinstance(r, str) or not REPO.fullmatch(r)]
    if bad:
        raise Refused(f"not OWNER/NAME: {bad}")
    return list(dict.fromkeys(repos))


def run(root, get, via, repos=None, since_days=14, limit=30, ts=None, dry_run=False):
    """Read, compare with the last line per PR, append what changed and a fetch line. Returns the
    fetch line. `get(path)` returns parsed JSON for a GitHub REST path (injected so tests needn't fetch)."""
    root = pathlib.Path(root)
    if not root.is_dir():
        raise Refused(f"--root {root} isn't a folder")
    if not (root / "governance").is_dir():
        raise Refused(f"{root} has no governance/ folder; is it a records store?")
    path = root / OUT
    if path.is_symlink() or (root / "governance").is_symlink() or \
            not path.parent.resolve().is_relative_to(root.resolve()):
        raise Refused(f"{OUT} or governance/ is a symlink; refusing to append through it")
    repos = repos_of(root, repos)
    ts = ts or now_iso()
    cutoff = (dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ") - dt.timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last = latest_by_pr(read_lines(path))
    new, ok, failed, seen = [], [], {}, 0
    for repo in repos:
        found = []
        try:
            prs = get(f"repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page={max(1, min(limit, 100))}")
            if not isinstance(prs, list):
                raise Refused(f"repos/{repo}/pulls: unexpected response")
            listed = {pr.get("number") for pr in prs if isinstance(pr, dict)}
            # a PR last recorded open is re-read whatever its age, so a closed one never stays "open"
            for (r_, n_), prev in sorted(last.items(), key=lambda kv: kv[0][1]):
                if r_ == repo and prev.get("state") == "open" and n_ not in listed:
                    one = get(f"repos/{repo}/pulls/{n_}")
                    if isinstance(one, dict):
                        prs.append(one)
            for pr in prs:
                if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
                    raise Refused(f"repos/{repo}/pulls: unexpected response")
                was_open = (last.get((repo, pr["number"])) or {}).get("state") == "open"
                if pr.get("state") != "open" and not was_open and (pr.get("updated_at") or "") < cutoff:
                    continue
                seen += 1
                rec = pr_record(get, repo, pr, ts)
                prev = last.get((repo, rec["number"]))
                if prev is None or not same(prev, rec):
                    found.append(rec)
            new += found          # a repository that fails part-way contributes nothing
            ok.append(repo)
        except Refused as x:
            failed[repo] = str(x)[:300]
    fetch = {"schema": SCHEMA, "kind": "fetch", "ts": ts, "via": via, "repos": repos, "ok": ok, "failed": failed,
             "since_days": since_days, "prs_seen": seen, "appended": len(new)}
    lines = new + [fetch]
    if dry_run:
        for x in lines:
            print(json.dumps(x, ensure_ascii=False, sort_keys=True))
        return fetch
    with open(path, "a+b") as f:     # a file not ending in a newline gets one first, so no line is joined
        f.seek(0, os.SEEK_END)
        if f.tell():
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for x in lines:
            f.write(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n")
    return fetch


def main(argv=None):
    ap = argparse.ArgumentParser(prog="relay-kit prs", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=pathlib.Path, help="the records store")
    ap.add_argument("--repo", action="append", help="OWNER/NAME (repeatable); default: .ledger.json 'repos'")
    ap.add_argument("--since-days", type=int, default=14, help="closed or merged PRs updated within N days (default 14); open ones among the --limit read, and any last recorded open")
    ap.add_argument("--limit", type=int, default=30, help="most recently updated PRs read per repository (max 100)")
    ap.add_argument("--via", choices=["gh", "token"], help="gh CLI (default when installed) or a token from the environment")
    ap.add_argument("--token-env", help="environment variable holding the token (default GITHUB_TOKEN, then GH_TOKEN)")
    ap.add_argument("--dry-run", action="store_true", help="print the lines instead of appending them")
    a = ap.parse_args(argv)
    via = a.via or ("gh" if shutil.which("gh") else "token")
    try:
        if via == "gh":
            if not shutil.which("gh"):
                raise Refused("gh isn't installed; use --via token")
            get = gh_getter()
        else:
            names = [a.token_env] if a.token_env else ["GITHUB_TOKEN", "GH_TOKEN"]
            token = next((os.environ[n] for n in names if os.environ.get(n)), None)
            if not token:
                raise Refused(f"no token in {' or '.join(names)}")
            get = token_getter(token)
        f = run(a.root, get, via, a.repo, a.since_days, a.limit, dry_run=a.dry_run)
    except Refused as x:
        sys.exit(f"refused: {x}")
    print(f"{f['prs_seen']} PR(s) read from {len(f['ok'])} repo(s), {f['appended']} changed line(s) "
          f"{'printed' if a.dry_run else 'appended to ' + OUT}"
          + (f"; failed: {', '.join(f['failed'])}" if f["failed"] else ""), file=sys.stderr)
    if f["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
