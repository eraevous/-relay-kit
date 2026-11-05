#!/usr/bin/env python3
"""relay-kit/telemetry@1: a derived view of how the work is going, rebuilt from the records every time
and never entered by hand. Standard library only; reads the records, writes nothing to them, and
never calls GitHub (pull requests come from governance/pr-state.jsonl, written by relay-kit prs).

    relay-kit telemetry --root RECORDS [--out FILE]      # prints, or writes, the view as JSON

Every metric carries its frozen definition, a version, a label (measured / proxy / estimate, as its
inputs are), its denominator, and "unknown" with a reason where the records can't say: missing is
never zero. Distributions are reported (median, p90, n), never a composite score and never a target.

Metrics (docs/telemetry.md):
  compute_consumption     budget@2 lines, corrections and child runs applied, by method and category
  delivery_throughput     merged PRs per ISO calendar week, by repository; abandoned (closed unmerged) apart
  lead_time               PR created -> merged, hours: median, p90, n (wall clock, never work time)
  review_burden           change-request review rounds per merged PR
  post_merge_remediation  share of merged PRs with an explicitly linked revert or fix within 14 and 30
                          days, counted only for cohorts at least that old (not production CFR)
  human_intervention      self-reported active minutes; unknown otherwise
  surviving_changes       merged PRs not reverted within 14 days (cohort at least 14 days old)
  measurement_coverage    which actors, sessions, PRs and repositories have trustworthy telemetry
  coordination_loss       stale handoffs, cancelled handoffs, overlapping in-progress claims, ledger
                          transition conflicts
"""
import argparse, datetime as dt, importlib.util, json, pathlib, re, sys

SCHEMA = "relay-kit/telemetry@1"
HERE = pathlib.Path(__file__).resolve().parent
PRS_FILE = "governance/pr-state.jsonl"
PRS_READS = {"relay-kit/prs@1", "relay-kit/prs@2"}
FIX = re.compile(r"\b(?:revert(?:s|ed)?|hotfix|fix(?:es|ed)?|regression)\b", re.I)


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ex = _load("relaykit_export_for_telemetry", "export.py")
BG = _load("relaykit_budget_for_telemetry", "budget.py")
LC = _load("relaykit_lifecycle_for_telemetry", "lifecycle.py")


def t(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def dist(values):
    """median, p90 (nearest rank), n: never a mean."""
    v = sorted(values)
    if not v:
        return {"n": 0, "median": None, "p90": None}
    rank = lambda q: v[max(0, min(len(v) - 1, -(-int(q * len(v) * 100) // 100) - 1))]
    mid = (v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2
    return {"n": len(v), "median": round(mid, 2), "p90": round(rank(0.9), 2), "min": round(v[0], 2), "max": round(v[-1], 2)}


def metric(name, definition, label, version=1, **body):
    return {"metric": name, "definition": definition, "label": label, "version": f"{name}@{version}", **body}


def read_prs(root):
    p = pathlib.Path(root) / PRS_FILE
    latest, fetches, bad = {}, {}, 0
    if not p.is_file():
        return None
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            x = json.loads(ln) if ln.strip() else None
        except ValueError:
            bad += 1
            continue
        if not isinstance(x, dict) or x.get("schema") not in PRS_READS:
            bad += 0 if x is None else 1
            continue
        if x.get("kind") == "pr" and isinstance(x.get("number"), int):
            latest[(x.get("repo"), x["number"])] = x
        elif x.get("kind") == "fetch":
            for r in x.get("ok") or []:
                fetches[r] = x.get("ts")
    return {"latest": latest, "fetches": fetches, "unreadable": bad}


def derive(root):
    root = pathlib.Path(root)
    rd = ex.Reader(root)
    rd.config()
    led = ex.load_ledger()
    _, decisions = ex.ledger_records(rd, "decisions.jsonl", "decision")
    _, challenges = ex.ledger_records(rd, "challenges.jsonl", "challenge")
    _, handoffs, _ = ex.handoff_records(rd, led)
    prs = read_prs(root)
    bpath = root / "governance" / "budget.jsonl"
    brows, bbad = BG.parse(bpath.read_text(encoding="utf-8")) if bpath.is_file() and not bpath.is_symlink() else ([], [])
    kept4, corr = BG.effective(brows) if brows else ([], {"replaced": {}, "children": {}, "conflicts": [], "invalid": [], "duplicates": [], "methods": {}})
    kept4 = BG.exclude_children(kept4, corr)            # all-time view: a child inside a counted parent isn't added again
    kept = [(n, i, dict(o, ts=at or o.get("ts"))) for n, i, o, at in kept4]   # counted at the replaced line's time

    # as of: the newest time anywhere in what was read, never the clock
    stamps = [t(x.get("ts")) for x in (prs or {"latest": {}})["latest"].values()]
    stamps += [t(v) for v in (prs or {"fetches": {}})["fetches"].values()]
    stamps += [t(o.get("ts")) for _, _, o in kept]
    stamps += [t(e["at"]) for h in handoffs for e in h["log"]]
    stamps = [s for s in stamps if s]
    if not stamps:   # only dated ledger lines: the end of the latest date
        stamps = [t(f"{r['fields'].get('date')}T23:59:59Z") for r in decisions + challenges if r["fields"].get("date")]
        stamps = [s for s in stamps if s]
    as_of = max(stamps) if stamps else None
    out = {"schema": SCHEMA, "as_of": iso(as_of), "derived_from": {
        "budget": "governance/budget.jsonl (relay-kit/budget@2)" if brows else None,
        "pull_requests": PRS_FILE + " (relay-kit/prs@2)" if prs else None,
        "handoffs": "handoffs/ (relay-kit/handoff@2)", "ledgers": "governance/*.jsonl (relay-kit/lifecycle@1)"},
        "notes": ["A derived view: rebuild it, don't edit it.",
                  "No composite score and no targets: DEC-100's '2-3 points' needs a baseline that doesn't exist yet.",
                  "A merged pull request is not a production deployment; remediation here is not DORA's Change Failure Rate."],
        "metrics": []}
    M = out["metrics"]

    # ---- compute consumption ----
    cats = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    by_method, by_actor, days, unknown_split = {}, {}, set(), 0
    for _, _, o in kept:
        if o.get("window") != "session":
            continue
        m = o.get("method") if o.get("method") in BG.METHODS else "unlabelled"
        tot = int(o.get("tokens_in") or 0) + int(o.get("tokens_out") or 0)
        by_method[m] = by_method.get(m, 0) + tot
        by_actor[str(o.get("actor"))] = by_actor.get(str(o.get("actor")), 0) + tot
        d = t(o.get("interval_end") or o.get("ts"))
        if d:
            days.add(d.date().isoformat())
        td = o.get("tokens_detail")
        if isinstance(td, dict):
            for k in cats:
                cats[k] += int(td.get(k) or 0)
        else:
            unknown_split += 1
    labels = set(by_method)
    M.append(metric("compute_consumption",
                    "Input and output tokens per uniquely identified execution interval (budget@2 session lines, corrections "
                    "and included child runs applied), by how each was measured; cache reads apart. Unattributed use stays "
                    "unattributed.", "+".join(sorted(labels)) if labels else "unknown",
                    tokens_by_method=dict(sorted(by_method.items())), tokens_by_actor=dict(sorted(by_actor.items())),
                    categories_from_measured_detail=cats, lines_without_category_split=unknown_split,
                    active_days=len(days), lines=len([1 for _, _, o in kept if o.get("window") == "session"]),
                    corrections_applied=len(corr["replaced"]), correction_conflicts=corr["conflicts"],
                    budget_lines_taking_no_effect=corr["invalid"],
                    unknown=None if by_method else "no budget lines"))

    # ---- pull-request metrics ----
    items = list((prs or {"latest": {}})["latest"].values())
    merged = [x for x in items if x.get("state") == "merged" and t(x.get("merged_at"))]
    abandoned = [x for x in items if x.get("state") == "closed" and not x.get("merged_at")]
    weeks = {}
    for x in merged:
        y, w, _ = t(x["merged_at"]).isocalendar()
        k = f"{y}-W{w:02d}"
        weeks.setdefault(k, {}).setdefault(x["repo"], 0)
        weeks[k][x["repo"]] += 1
    M.append(metric("delivery_throughput", "Merged pull requests per ISO calendar week, by repository; abandoned (closed, "
                    "not merged) counted apart. A proxy for delivered value.", "proxy",
                    merged_per_week=dict(sorted(weeks.items())), merged=len(merged), abandoned=len(abandoned),
                    unknown=None if prs else "no governance/pr-state.jsonl: run relay-kit prs"))
    lt = [(t(x["merged_at"]) - t(x.get("created_at"))).total_seconds() / 3600 for x in merged if t(x.get("created_at"))]
    M.append(metric("lead_time", "Wall-clock hours from pull request creation to merge (GitHub timestamps). Never human work time.",
                    "measured", hours=dist(lt), unknown=None if lt else "no merged pull requests recorded"))
    rounds = []
    for x in merged:
        n = sum(1 for r in x.get("reviews") or [] if r.get("verdict") == "CHANGES_REQUESTED"
                or str(r.get("stated") or "").upper() in ("REQUEST CHANGES", "REQUESTING CHANGES", "CHANGES REQUESTED"))
        if "reviews" not in (x.get("unread") or []):
            rounds.append(n)
    M.append(metric("review_burden", "Change-request review rounds per merged pull request (GitHub's verdict, or a verdict "
                    "stated at the start of a review posted as a comment); each implies a revision and a re-review.",
                    "measured", rounds=dist(rounds), prs_with_unread_reviews=len(merged) - len(rounds),
                    unknown=None if rounds else "no merged pull requests with readable reviews"))

    def remediated(x, days_):
        """A later merged PR in the same repository that explicitly links this one with fix/revert wording."""
        mt = t(x["merged_at"])
        pat = re.compile(rf"(?:#{x['number']}\b|/pull/{x['number']}\b)")
        for y in merged:
            if y is x or y.get("repo") != x.get("repo"):
                continue
            yt = t(y["merged_at"])
            if not (mt < yt <= mt + dt.timedelta(days=days_)):
                continue
            text = " ".join([str(y.get("title") or "")] + [str(v) for v in (y.get("body_sections") or {}).values()])
            if pat.search(text) and FIX.search(text):
                return True
        return False
    rem = {}
    for days_ in (14, 30):
        cohort = [x for x in merged if as_of and t(x["merged_at"]) <= as_of - dt.timedelta(days=days_)]
        hit = sum(1 for x in cohort if remediated(x, days_))
        rem[f"{days_}d"] = {"cohort": len(cohort), "remediated": hit,
                            "share": round(hit / len(cohort), 3) if cohort else None,
                            "unknown": None if cohort else f"no merged pull request is {days_} days old yet"}
    M.append(metric("post_merge_remediation", "Share of merged pull requests with an explicitly linked later revert or fix "
                    "(same repository, the PR referenced with fix/revert/hotfix wording) within 14 and 30 days, counted "
                    "only over cohorts at least that old. An internal quality proxy, not production Change Failure Rate.",
                    "proxy", windows=rem))
    surv = rem["14d"]
    M.append(metric("surviving_changes", "Merged pull requests not reverted or fixed by an explicitly linked change within 14 "
                    "days (DEC-123's unit of output; '>50% rewritten' isn't detectable from pr-state and isn't applied).",
                    "proxy", count=(surv["cohort"] - surv["remediated"]) if surv["cohort"] else None,
                    cohort=surv["cohort"], unknown=surv["unknown"]))
    hm = [o.get("human_minutes") for _, _, o in kept if isinstance(o.get("human_minutes"), int)]
    M.append(metric("human_intervention", "Self-reported active owner minutes spent reviewing, deciding, repairing or "
                    "coordinating (budget lines with human_minutes). Unknown otherwise; never inferred from session spans.",
                    "proxy" if hm else "unknown", minutes_reported=sum(hm) if hm else None, lines=len(hm),
                    unknown=None if hm else "no line reports human_minutes"))

    # ---- measurement coverage ----
    actors = set()
    for h in handoffs:
        for e in h["log"]:
            if e.get("actor"):
                actors.add(str(e["actor"]).split()[0])
    measured_actors = {str(o.get("actor")).split()[0] for _, _, o in kept if o.get("method") == "measured"}
    any_actors = {str(o.get("actor")).split()[0] for _, _, o in kept}
    agents = sorted(a for a in actors if ":" in a)
    M.append(metric("measurement_coverage", "Which known sessions, pull requests and repositories have trustworthy telemetry. "
                    "Missing means unmeasured, never zero.", "measured",
                    agent_sessions_in_handoffs=len(agents),
                    with_measured_budget_line=len([a for a in agents if a in measured_actors]),
                    with_any_budget_line=len([a for a in agents if a in any_actors]),
                    unmeasured_sessions=[a for a in agents if a not in measured_actors],
                    budget_lines_by_method=corr["methods"],
                    repositories_read=sorted((prs or {"fetches": {}})["fetches"]),
                    pr_lines=len(items), pr_lines_with_unread=len([x for x in items if x.get("unread")]),
                    not_meterable=["ChatGPT and Codex cloud sessions (no exposed metering; estimates from captures, "
                                   "Kairos threads only, when recorded)"]))

    # ---- coordination loss ----
    stale, open_ = [], []
    for h in handoffs:
        if h["status"] in led.FINAL:
            continue
        last = max((t(e["at"]) for e in h["log"] if t(e["at"])), default=None)
        open_.append(h)
        if as_of and last and as_of - last > dt.timedelta(hours=24):
            stale.append({"id": h["handoff_id"], "status": h["status"], "last_step": iso(last)})
    cancelled = [h["handoff_id"] for h in handoffs if h["status"] == "cancelled"]
    claims = {}
    for h in open_:
        if h["status"] not in ("acknowledged", "in_progress"):
            continue
        m = re.search(r"^touches:[ \t]*(.+)$", h.get("text") or "", re.M)
        for part in (m.group(1).split(",") if m else []):
            claims.setdefault(part.strip(), []).append(h["handoff_id"])
    overlaps = {k: v for k, v in claims.items() if k and len(v) > 1}
    res = LC.resolve([r["fields"] for r in decisions], [r["fields"] for r in challenges])
    M.append(metric("coordination_loss", "Stale handoffs (open, no step in 24 h before as_of), cancelled handoffs, handoffs in "
                    "progress that claim the same path, and ledger transitions in conflict. Time spent reconciling isn't "
                    "recorded yet: unknown.", "measured", stale_handoffs=stale, cancelled_handoffs=cancelled,
                    overlapping_claims=overlaps, ledger_conflicts=[c["id"] for c in res["conflicts"]],
                    reconciliation_minutes=None, unknown="reconciliation time isn't recorded"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="relay-kit telemetry", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, help="write the view here (outside the records) instead of printing it")
    a = ap.parse_args(argv)
    try:
        view = derive(a.root)
    except ex.Refused as x:
        sys.exit(f"refused: {x}")
    text = json.dumps(view, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if a.out:
        if a.out.resolve().is_relative_to(a.root.resolve()):
            sys.exit("refused: --out is inside the records; the view is derived, not a record")
        a.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
