#!/usr/bin/env python3
"""Cockpit: one static, self-contained page over a records store: relay-kit/cockpit@1.

    python3 tools/cockpit.py --root RECORDS --out DIR [--lane L] [--content full|metadata]
                             [--weekly-tokens N] [--drift-days N] [--coordinator LANE]
                             [--allow-public-dest]

Writes DIR/index.html (inline CSS and JS, no external resources) and DIR/cockpit.json (the data
the page was built from). Four surfaces:
  Choices    what waits on the owner: proposed decisions, handoffs that need the owner, open
             challenges. Accept / Reject / Defer compose a paste-ready line; nothing is written.
  Topology   information topology (lanes, actors, threads, captures, handoff flows) and
             authority topology (owner -> coordinator -> lanes, plus delegations read from
             in-force decisions, marked inferred), each drawn in SVG.
  Budget     governance/budget.jsonl (if present) against a weekly allowance.
  Run/Drift  handoffs by status, PR links from threads.md, recent decisions, open challenges,
             drift flags.

It reads with the export module's readers (tools/export.py), changes nothing under --root, and
refuses a destination inside the records or inside a public (or unconfirmed) git repository,
using the export's guard. Output is deterministic: the only date on the page is the latest
timestamp found in the records. Spec: docs/cockpit.md. Standard library only."""
import argparse, datetime as dt, html, importlib.util, json, os, pathlib, re, shlex, sys, tempfile

SCHEMA = "relay-kit/cockpit@1"
OUT_FILES = {"index.html", "cockpit.json"}
BUDGET_FILE = "governance/budget.jsonl"
SETTINGS_FILE = "governance/cockpit.json"
BUDGET_FIELDS = {"ts": "ISO time (UTC) the line covers up to", "actor": "assistant:lane#session or the owner",
                 "window": "weekly | session", "tokens_in": "integer >= 0", "tokens_out": "integer >= 0",
                 "agents": "integer >= 0: agents running in that window", "note": "free text"}
POLICY_KEYS = ["default_effort", "spawn_route", "weekly_tokens_soft", "max_delegations_per_question",
               "chatgpt_standing_thread_cap", "claude_build_context_cap", "codex_repo_context_cap",
               "human_interruptions_per_day"]
STATE_ORDER = ["prepared", "sent", "acknowledged", "in_progress", "blocked", "completed", "cancelled"]
DELEGATION = re.compile(r"delegat|authori[sz]", re.I)
PR_LINK = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
LIMITS = [
    "Nothing on this page writes anywhere. Accept / Reject / Defer only compose text for the owner to paste "
    "(DEC-019: authority stays with the human paste).",
    "Open pull requests are not fetched (no network). PR links are the ones threads.md lists.",
    "The coordinator's status-file cost lines are not parsed; only governance/budget.jsonl counts toward the budget.",
    "The authority diagram shows only recorded delegation (an adopted decision with delegates_to). Text-matched "
    "delegations and the assumed owner -> coordinator -> lane lines are in a closed 'hypothesised authority (inferred)' "
    "overlay. Nothing executable is derived from either graph.",
    "Composed lines carry <<REQUIRED>> placeholders: the owner supplies decider, basis and locator; the ledger check "
    "refuses a line that still holds one.",
    "'Last 7 days' and 'older than N days' are measured back from the latest record timestamp, not from today.",
    "Topology layout is automatic: position carries no meaning beyond the band (lane) a node sits in.",
]

HERE = pathlib.Path(__file__).resolve().parent
try:
    from . import __version__
except ImportError:
    __version__ = re.search(r'__version__ = "([^"]+)"', (HERE / "__init__.py").read_text(encoding="utf-8")).group(1)


def load_export():
    spec = importlib.util.spec_from_file_location("relaykit_export_for_cockpit", HERE / "export.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ex = load_export()
Refused = ex.Refused


def dumps(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%MZ")


# ---------------- reading ----------------
def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def base_of(actor, owner):
    """'claude:aec#01N4' -> 'claude:aec'; the owner's bare name -> 'owner'; anything else -> None."""
    s = str(actor or "").strip()
    if s and s.lower() in (owner, "owner"):
        return "owner"
    sp = ex.split_actor(s)
    return sp[0][0] if sp else None


def read_budget(root, rd):
    p = root / "governance" / "budget.jsonl"
    if not p.is_file() or p.is_symlink():
        return None, []
    raw = rd.read(p, "budget")
    rows, bad = [], []
    for n, line in enumerate(raw.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if not isinstance(r, dict):
                raise ValueError("not an object")
            sp = ex.span(r.get("ts"))
            if not sp:
                raise ValueError("ts isn't an ISO time")
            if r.get("window") not in ("weekly", "session"):
                raise ValueError("window must be weekly or session")
            for f in ("tokens_in", "tokens_out"):
                if not isinstance(r.get(f), int) or isinstance(r.get(f), bool) or r[f] < 0:
                    raise ValueError(f"{f} must be an integer >= 0")
        except ValueError as e:
            bad.append({"line": n, "reason": str(e)})
            continue
        rows.append((n, sp[0], r))
    return {"rows": rows, "bad": bad}, bad


def captures(rd, threads):
    """conversation@1 captures, read with the export's conv_records (topology nodes only)."""
    out = []
    for p in rd.walk_json():
        if rd.rel(p) == "handoffs/.ledger.json" or rd.rel(p).startswith("governance/"):
            continue
        try:
            raw = p.read_bytes()
            d = json.loads(raw)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        legacy = d.get("schema") is None and isinstance(d.get("turns"), list) and isinstance(d.get("meta"), dict)
        if d.get("schema") != ex.CONVERSATION and not legacy:
            continue
        try:
            conv, _ = ex.conv_records(rd, p, raw, d, threads)
        except (ValueError, AttributeError, TypeError):
            continue
        out.append(conv)
    return sorted(out, key=lambda c: c["rid"])


# ---------------- composing paste lines ----------------
REQ = {"decider": "<<REQUIRED: your name>>", "basis": "<<REQUIRED: your words, quoted>>",
       "locator": "<<REQUIRED: where you said it (thread, date, time)>>"}


def ledger_cmd(kind, obj):
    return f"relay-kit ledger append --dir governance {kind} {shlex.quote(dumps(obj))}"


def compose_decision(rec, owner, as_of, full):
    f = rec["fields"]
    did = f.get("id")
    text = f.get("decision") if full else f"As proposed in {did} (text left out of this page)."
    scope = f.get("scope") if full else f"As in {did}."
    out = {}
    for action, status in (("accept", "adopted"), ("reject", "rejected")):
        obj = {"status": status, "supersedes": did, "decision": text or f"As proposed in {did}.",
               "scope": scope or f"As in {did}.", "decider": REQ["decider"],
               "basis": REQ["basis"], "locator": REQ["locator"]}   # recorded_by: ledger_append fills it from $RELAYKIT_ACTOR
        out[action] = {"command": ledger_cmd("decision", obj), "json": obj}
    out["defer"] = {"command": f"# {did}: deferred. Nothing to append; it stays proposed and stays on this page.",
                    "json": None}
    return out


def compose_challenge(rec, owner, full):
    f = rec["fields"]
    cid = f.get("id")
    concern = f.get("concern") if full else f"As raised in {cid} (text left out of this page)."
    out = {}
    for action, status, why in (("accept", "accepted-risk", "<<REQUIRED: why you accept the risk, in your words>>"),
                                ("reject", "resolved", "<<REQUIRED: why it is dismissed, or how it was fixed, in your words>>")):
        obj = {"status": status, "resolves": cid, "concern": concern or f"As raised in {cid}.",
               "raised_by": f.get("raised_by") or owner, "resolution": why}
        out[action] = {"command": ledger_cmd("challenge", obj), "json": obj}
    out["defer"] = {"command": f"# {cid}: deferred. Nothing to append; it stays open and stays on this page.",
                    "json": None}
    return out


def compose_handoff(h, owner, unauthorized):
    hid, st = h["handoff_id"], h["status"]
    nxt = {"prepared": "sent", "sent": "acknowledged", "blocked": "in_progress"}.get(st, "acknowledged")
    pre = f"# first write your words under '## Authorized' in handoffs/{hid}.md\n" if unauthorized else ""
    # no --by: handoff.py takes the actor from $RELAYKIT_ACTOR, i.e. whoever runs the line
    acc = pre + (f"relay-kit handoff --dir handoffs set {hid} {nxt} "
                 f"--note {shlex.quote('<<REQUIRED: your words>>')}")
    rej = (f"relay-kit handoff --dir handoffs set {hid} cancelled "
           f"--note {shlex.quote('<<REQUIRED: why it is cancelled, in your words>>')}")
    return {"accept": {"command": acc, "json": None, "state": nxt},
            "reject": {"command": rej, "json": None, "state": "cancelled"},
            "defer": {"command": f"# {hid}: deferred. Nothing to run; it stays {st}.", "json": None}}


# ---------------- collecting ----------------
def collect(root, lane=None, content="full", weekly_tokens=None, drift_days=None, coordinator=None, brand=None):
    root = pathlib.Path(root)
    if not root.is_dir():
        raise Refused(f"--root {root} isn't a folder")
    if content not in ("full", "metadata"):
        raise Refused("--content must be full or metadata")
    full = content == "full"
    rd = ex.Reader(root)
    led = ex.load_ledger()
    rd.config()
    cfg = read_json(rd.root / "handoffs" / ".ledger.json") or {}
    owner = str(cfg.get("owner") or "owner").strip().lower() if isinstance(cfg, dict) else "owner"
    settings = read_json(rd.root / SETTINGS_FILE)
    settings = settings if isinstance(settings, dict) else {}
    if weekly_tokens is None and isinstance(settings.get("weekly_tokens"), int):
        weekly_tokens = settings["weekly_tokens"]
    if drift_days is None:
        drift_days = settings["drift_days"] if isinstance(settings.get("drift_days"), int) else 3
    if brand is None:
        brand = settings["brand"] if isinstance(settings.get("brand"), str) else "relay-kit"
    tpath, threads = ex.parse_threads(rd)
    _, decisions = ex.ledger_records(rd, "decisions.jsonl", "decision")
    _, challenges = ex.ledger_records(rd, "challenges.jsonl", "challenge")
    _, handoffs, _ = ex.handoff_records(rd, led)
    convs = captures(rd, threads)
    budget, _ = read_budget(rd.root, rd)
    threads_text = (rd.root / "threads.md").read_text(encoding="utf-8", errors="replace") if tpath else ""

    def lane_ok(r):
        ls = [l for l in (r.get("lanes") or [r.get("lane")]) if l]
        return lane is None or any(rd.lanes.matches(lane, l) for l in ls)

    decisions = [d for d in decisions if lane_ok(d)]
    challenges = [c for c in challenges if lane_ok(c)]
    handoffs = [h for h in handoffs if lane_ok(h)]
    convs = [c for c in convs if lane_ok(c)]

    # as of: the latest timestamp in what was read (never the clock)
    stamps = [ex.span(r.get("ts")) for r in decisions + challenges + handoffs + convs]
    stamps += [ex.span(e["at"]) for h in handoffs for e in h["log"]]
    if budget:
        stamps += [(t, t) for _, t, _ in budget["rows"]]
    stamps = sorted(s[0] for s in stamps if s)
    as_of_dt = stamps[-1] if stamps else None
    as_of = iso(as_of_dt) if as_of_dt else None

    all_dec = [d["fields"] for d in decisions]
    superseded = {x.get("supersedes") for x in all_dec if x.get("supersedes")}
    closed = {c["fields"].get("resolves") for c in challenges if c["fields"].get("resolves")}

    # ---- Choices ----
    choices = []
    for d in decisions:
        f = d["fields"]
        if d["status"] != "proposed" or f.get("id") in superseded:
            continue
        item = {"kind": "decision", "id": f.get("id"), "status": "proposed", "date": f.get("date"),
                "proposed_by": f.get("proposed_by") or f.get("recorded_by"), "decider": f.get("decider"),
                "source": f"{d['source_path']}:{d['line']}", "compose": compose_decision(d, owner, as_of, full)}
        if full:
            item.update(text=f.get("decision"), scope=f.get("scope"), basis=f.get("basis"), locator=f.get("locator"))
        choices.append(item)
    def authorized(h):
        """The handoff's Authorized section holds something other than a TODO or NOT AUTHORIZED."""
        m = re.search(r"^## Authorized[ \t]*\n(.*?)(?=^## |\Z)", h["text"], re.S | re.M)
        auth = m.group(1) if m else ""
        return bool(auth.strip()) and not led.TODO_RE.search(auth) and "NOT AUTHORIZED" not in auth

    def run_class(h):
        """'authorized' (sent, acknowledged or in progress, with an Authorized section), 'pending'
        (prepared, or no authorization), 'blocked', or None for completed and cancelled."""
        st = h["status"]
        if st in led.FINAL:
            return None
        if st == "prepared" or not authorized(h):
            return "pending"
        return "authorized" if st in ("sent", "acknowledged", "in_progress") else "blocked"

    waiting_handoffs = []
    for h in handoffs:
        st = h["status"]
        if st in led.FINAL:
            continue
        unauth = not authorized(h)
        why = []
        if st == "prepared":
            why.append("prepared, not sent")
        if unauth:
            why.append("Authorized not filled: nobody may start it")
        if base_of(h["to"], owner) == "owner":
            why.append("addressed to the owner")
        if st == "blocked":
            why.append("blocked")
        if not why:
            continue
        item = {"kind": "handoff", "id": h["handoff_id"], "status": st, "date": h["created"], "from": h["from"],
                "to": h["to"], "why": why, "source": h["source_path"],
                "compose": compose_handoff(h, owner, unauth)}
        if full:
            item["title"] = h["title"]
        waiting_handoffs.append(item["id"])
        choices.append(item)
    for c in challenges:
        f = c["fields"]
        if c["status"] != "open" or f.get("id") in closed:
            continue
        item = {"kind": "challenge", "id": f.get("id"), "status": "open", "date": f.get("date"),
                "raised_by": f.get("raised_by"), "source": f"{c['source_path']}:{c['line']}",
                "compose": compose_challenge(c, owner, full)}
        if full:
            item.update(text=f.get("concern"), affects=f.get("affects"), proposed_test=f.get("proposed_test"))
        choices.append(item)
    choices.sort(key=lambda i: (["decision", "handoff", "challenge"].index(i["kind"]), str(i["id"])))

    # handoff titles in metadata mode: ids only (the title can carry the substance)
    def htitle(h):
        return h["title"] if full else None

    # ---- Topology: information ----
    nodes, edges = {}, []

    def node(nid, typ, label, ln):
        nodes.setdefault(nid, {"id": nid, "type": typ, "label": label, "lane": ln})

    node("owner", "owner", owner, None)
    lanes_all = set(rd.lanes.codes) | {t["lane"] for t in threads.values() if t.get("lane")}
    for h in handoffs:
        for a in (h["from"], h["to"]):
            b = base_of(a, owner)
            if b and b != "owner":
                ln = rd.lanes.lane(b)
                node(b, "actor", b, ln)
                if ln:
                    lanes_all.add(ln)
    for c in convs:
        if c.get("actor"):
            b = base_of(c["actor"], owner)
            if b and b != "owner":
                node(b, "actor", b, rd.lanes.lane(b))
    if lane:
        lanes_all = {l for l in lanes_all if rd.lanes.matches(lane, l)}
    for l in sorted(lanes_all):
        node(f"lane:{l}", "lane", l, l)
    thread_ids = {}
    for cid, t in sorted(threads.items()):
        if lane and not (t.get("lane") and rd.lanes.matches(lane, t["lane"])):
            continue
        nid = "thread:" + ex.sha(cid)[:10]
        thread_ids[cid] = nid
        node(nid, "thread", f"{t['platform']} …{cid[-4:]}", t.get("lane"))
        nodes[nid]["status"] = t.get("status")
    for c in convs:
        nid = "capture:" + ex.sha(c["rid"])[:10]
        lbl = (c.get("title") if full and c.get("title") else f"{c.get('platform') or 'capture'} …{str(c.get('conversation_id') or c['rid'])[-4:]}")
        node(nid, "capture", lbl, c.get("lane"))
        if c.get("conversation_id") in thread_ids:
            edges.append({"from": nid, "to": thread_ids[c["conversation_id"]], "type": "capture-of", "count": 1, "refs": []})
        if c.get("actor") and base_of(c["actor"], owner) in nodes:
            edges.append({"from": base_of(c["actor"], owner), "to": nid, "type": "captured", "count": 1, "refs": []})
    flows = {}
    for h in handoffs:
        a, b = base_of(h["from"], owner), base_of(h["to"], owner)
        if a and b:
            flows.setdefault((a, b), []).append(h["handoff_id"])
    for (a, b), ids in sorted(flows.items()):
        edges.append({"from": a, "to": b, "type": "handoff", "count": len(ids), "refs": sorted(ids)})
    info = {"nodes": sorted(nodes.values(), key=lambda n: (n["type"], n["id"])),
            "edges": sorted(edges, key=lambda e: (e["type"], e["from"], e["to"]))}

    # ---- Topology: authority ----
    lane_names = sorted(l for l in lanes_all)
    coords = [coordinator.lower()] if coordinator else [l for l in lane_names if "coord" in l]
    coords = [c for c in coords if c in lane_names] or []
    lane_nodes = [{"id": f"lane:{l}", "type": "coordinator" if l in coords else "lane", "label": l} for l in lane_names]

    def lane_targets(text):
        return sorted({l for l in lane_names if re.search(rf"(?<![\w-]){re.escape(l)}(?![\w-])", text, re.I)}
                      | {l for l in lane_names if rd.lanes.codes.get(l) and re.search(rf"\b{re.escape(rd.lanes.codes[l])}\b", text)})

    def source_of(decider):
        if base_of(decider, owner) == "owner" or str(decider or "").strip().lower() in (owner, "owner"):
            return "owner"
        if coords and re.search(r"coordinat", str(decider or ""), re.I):
            return f"lane:{coords[0]}"
        return "decider:" + str(decider or "(none)")

    # recorded: an adopted, in-force decision that names its delegate in a delegates_to field
    rec_edges = []
    for d in decisions:
        f = d["fields"]
        dt_ = f.get("delegates_to")
        if not d.get("in_force") or not dt_:
            continue
        for target in ([dt_] if isinstance(dt_, str) else [x for x in dt_ if isinstance(x, str)]):
            tos = lane_targets(target) or [None]
            for l in tos:
                rec_edges.append({"from": source_of(f.get("decider")), "to": f"lane:{l}" if l else "target:" + target,
                                  "kind": "recorded", "decision": f.get("id"), "date": f.get("date"), "inferred": False,
                                  "delegates_to": target, "scope": f.get("scope") if full else None})
    rec_ids = {e["from"] for e in rec_edges} | {e["to"] for e in rec_edges}
    rec_nodes = [{"id": "owner", "type": "owner", "label": owner}] + [n for n in lane_nodes if n["id"] in rec_ids] + \
        [{"id": x, "type": "lane", "label": x.split(":", 1)[1]} for x in sorted(rec_ids) if x.startswith(("target:", "decider:"))]

    # hypothesised (an overlay, off by default): assumed reporting lines and text-matched delegations
    hyp_edges = []
    for c in coords:
        hyp_edges.append({"from": "owner", "to": f"lane:{c}", "kind": "structure", "decision": None, "inferred": True,
                          "basis": "assumed reporting line: the owner directs the coordinator"})
    for l in lane_names:
        if l in coords:
            continue
        hyp_edges.append({"from": f"lane:{coords[0]}" if coords else "owner", "to": f"lane:{l}", "kind": "structure",
                          "decision": None, "inferred": True,
                          "basis": "assumed reporting line" + (" via the coordinator" if coords else "")})
    delegations = []
    for d in decisions:
        f = d["fields"]
        if not d.get("in_force") or not DELEGATION.search(str(f.get("decision", ""))):
            continue
        frm = source_of(f.get("decider"))
        frm = "owner" if frm.startswith("decider:") else frm
        tos = lane_targets(str(f.get("scope") or "")) or sorted(d.get("lanes") or [])
        for l in tos or [None]:
            to = f"lane:{l}" if l else "unspecified"
            if to == frm:
                continue
            e = {"from": frm, "to": to, "kind": "delegation", "decision": f.get("id"), "date": f.get("date"),
                 "inferred": True, "basis": "decision text mentions delegating or authorizing; target read from its scope"}
            hyp_edges.append(e)
            delegations.append(e)
    hyp_nodes = [{"id": "owner", "type": "owner", "label": owner}] + lane_nodes + \
        ([{"id": "unspecified", "type": "lane", "label": "(scope names no lane)"}] if any(e["to"] == "unspecified" for e in hyp_edges) else [])
    srt = lambda es: sorted(es, key=lambda e: (e["kind"], e["from"], e["to"], str(e["decision"])))
    authority = {"recorded": {"nodes": rec_nodes if rec_edges else [], "edges": srt(rec_edges)},
                 "hypothesised": {"nodes": hyp_nodes, "edges": srt(hyp_edges)},
                 "coordinator": coords, "executable": False,
                 "note": "Nothing executable is derived from either graph; the decisions are the authority."}

    # ---- Budget ----
    b = {"present": budget is not None, "file": BUDGET_FILE, "schema": BUDGET_FIELDS,
         "weekly_allowance": weekly_tokens, "status_file_costs_parsed": False}
    if budget is not None:
        start = (as_of_dt - dt.timedelta(days=7)) if as_of_dt else None
        recent = [(n, t, r) for n, t, r in budget["rows"] if start is None or t > start]
        sess = [r for _, _, r in recent if r["window"] == "session"]
        weekly = sorted([(t, n, r) for n, t, r in recent if r["window"] == "weekly"], key=lambda x: (x[0], x[1]))
        by_actor, by_lane = {}, {}
        for r in sess:
            tot = r["tokens_in"] + r["tokens_out"]
            a = str(r.get("actor") or "(none)")
            by_actor[a] = by_actor.get(a, 0) + tot
            bb = base_of(a, owner)
            ln = rd.lanes.lane(bb) if bb and bb != "owner" else None
            by_lane[ln or "(no lane)"] = by_lane.get(ln or "(no lane)", 0) + tot
        sess_total = sum(r["tokens_in"] + r["tokens_out"] for r in sess)
        latest_weekly = weekly[-1][2] if weekly else None
        burn = sess_total if sess else None   # no session lines: no observation, never a manufactured 0
        b.update({
            "records": len(budget["rows"]), "invalid_lines": budget["bad"],
            "window": {"from": iso(start) if start else None, "to": as_of},
            "session_tokens_7d": sess_total,
            "session_in_7d": sum(r["tokens_in"] for r in sess), "session_out_7d": sum(r["tokens_out"] for r in sess),
            "session_lines_7d": len(sess),
            "weekly_reference": ({"ts": latest_weekly["ts"], "tokens": latest_weekly["tokens_in"] + latest_weekly["tokens_out"],
                                  "actor": latest_weekly.get("actor"),
                                  "note": "a separately reported weekly reading; reference only, never added to or substituted for the session sum"}
                                 if latest_weekly else None),
            "burn_7d": burn, "burn_source": f"sum of {len(sess)} session line(s) in the window" if sess else "no session lines in the window",
            "by_actor": dict(sorted(by_actor.items())), "by_lane": dict(sorted(by_lane.items())),
            "max_agents_7d": max((r.get("agents") for _, _, r in recent if isinstance(r.get("agents"), int)), default=None),
            "fraction": (burn / weekly_tokens) if (weekly_tokens and burn is not None) else None,
        })
        if full:
            b["recent_notes"] = [{"ts": r["ts"], "actor": r.get("actor"), "note": r.get("note")}
                                 for _, _, r in sorted(recent, key=lambda x: (x[1], x[0]))[-10:] if r.get("note")]

    # ---- Run / Drift ----
    by_status, commitments = {}, {"authorized": [], "pending": [], "blocked": []}
    for h in sorted(handoffs, key=lambda h: (h["created"] or "", h["handoff_id"])):
        last = h["log"][-1] if h["log"] else {}
        row = {"id": h["handoff_id"], "title": htitle(h), "from": h["from"], "to": h["to"], "created": h["created"],
               "status": h["status"], "last": {"at": last.get("at"), "state": last.get("state"), "actor": last.get("actor")}}
        by_status.setdefault(h["status"], []).append(row)
        if run_class(h):
            commitments[run_class(h)].append(row)
    by_status = {s: by_status[s] for s in sorted(by_status, key=lambda s: (STATE_ORDER.index(s) if s in STATE_ORDER else 99, s))}

    def dec_row(d):
        f = d["fields"]
        r = {"id": f.get("id"), "date": f.get("date"), "status": d["status"], "supersedes": f.get("supersedes"),
             "decider": f.get("decider"), "in_force": d.get("in_force")}
        if full:
            r["text"] = f.get("decision")
        return r

    def ch_row(c):
        f = c["fields"]
        r = {"id": f.get("id"), "date": f.get("date"), "status": c["status"], "raised_by": f.get("raised_by"),
             "resolves": f.get("resolves"), "open": c["status"] == "open" and f.get("id") not in closed}
        if full:
            r["text"] = f.get("concern")
        return r

    recent_dec = [dec_row(d) for d in sorted(decisions, key=lambda d: d["line"])[-20:]][::-1]
    open_ch = [ch_row(c) for c in challenges if c["status"] == "open" and c["fields"].get("id") not in closed]
    drift_ch = [ch_row(c) for c in challenges if "drift" in dumps(c["fields"]).lower()]
    stale = []
    for d in decisions:
        f = d["fields"]
        sp = ex.span(f.get("date"))
        if d["status"] == "proposed" and f.get("id") not in superseded and sp and as_of_dt and \
                (as_of_dt - sp[0]).days > drift_days:
            stale.append({**dec_row(d), "age_days": (as_of_dt - sp[0]).days})
    run = {"handoffs_by_status": by_status,
           "handoff_counts": {s: len(v) for s, v in by_status.items()},
           "commitments": commitments,
           "pr_links": sorted(set(PR_LINK.findall(threads_text))),
           "decisions_recent": recent_dec, "challenges_open": open_ch,
           "drift": {"challenges_mentioning_drift": drift_ch, "stale_proposals": stale, "drift_days": drift_days}}

    # ---- Project lanes ----
    lane_rows = []
    for l in lane_names:
        def mine(r):
            return any(x == l for x in (r.get("lanes") or [r.get("lane")]))
        hs = [h for h in handoffs if mine(h)]
        ds = [d for d in decisions if mine(d)]
        cs = [c for c in challenges if mine(c) and c["status"] == "open" and c["fields"].get("id") not in closed]
        ts = sorted(s[0] for s in [ex.span(r.get("ts")) for r in hs + ds + cs] if s)
        lane_rows.append({"lane": l, "code": rd.lanes.codes.get(l), "coordinator": l in coords,
                          "handoffs": len(hs), "open_handoffs": sum(1 for h in hs if h["status"] not in led.FINAL),
                          "decisions": len(ds), "proposed": sum(1 for d in ds if d["status"] == "proposed"
                                                                and d["fields"].get("id") not in superseded),
                          "open_challenges": len(cs),
                          "threads": sum(1 for t in threads.values() if t.get("lane") == l),
                          "last_activity": iso(ts[-1]) if ts else None})
    intake = sorted({u.rstrip(".,;:") for line in threads_text.splitlines() if re.search(r"intake", line, re.I)
                     for u in ex.URL.findall(line)})
    policy = settings.get("policy") if isinstance(settings.get("policy"), dict) else {}
    b["policy"] = {"advisory": True, "source": SETTINGS_FILE if policy else None,
                   "caps": {k: policy.get(k) for k in POLICY_KEYS}}

    counts = {"choices": len(choices),"choices_by_kind": {k: sum(1 for c in choices if c["kind"] == k)
                                                           for k in ("decision", "handoff", "challenge")},
              "decisions": len(decisions), "challenges": len(challenges), "handoffs": len(handoffs),
              "captures": len(convs), "threads": len(thread_ids),
              "open_handoffs": sum(1 for h in handoffs if h["status"] not in led.FINAL),
              "authorized_in_progress": sum(1 for h in handoffs if run_class(h) == "authorized"),
              "pending_authorization": sum(1 for h in handoffs if run_class(h) == "pending"),
              "drift_flags": len([c for c in drift_ch if c["open"]]) + len(stale),
              "delegations_inferred": len(delegations), "delegations_recorded": len(rec_edges)}
    return {
        "schema": SCHEMA,
        "generator": {"tool": "relay-kit cockpit (tools/cockpit.py)", "kit_version": __version__,
                      "reads_with": "tools/export.py (relay-kit/export@1 readers)"},
        "as_of": as_of, "owner": owner,
        "query": {"lane": lane, "content": content, "weekly_tokens": weekly_tokens, "drift_days": drift_days,
                  "coordinator": coords, "brand": brand},
        "counts": counts,
        "choices": choices,
        "topology": {"information": info, "authority": authority},
        "budget": b,
        "run": run,
        "lanes": lane_rows,
        "intake_links": intake,
        "unreadable": sorted(rd.unreadable, key=lambda x: str(x["path"])),
        "limits": LIMITS,
    }


# ---------------- rendering ----------------
def e(x):
    return html.escape("" if x is None else str(x), quote=True)


def trunc(s, n):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def fmt_n(n):
    return "—" if n is None else f"{n:,}"


def gauge(value, maximum, label, readout, sub="", tone="ok"):
    """A semicircular dial: needle at value/maximum (clamped); ticks every tenth."""
    frac = None if not maximum else max(0.0, min(1.0, value / maximum))
    cx, cy, r = 70, 70, 54
    import math
    ticks = []
    for i in range(11):
        a = math.pi * (1 - i / 10)
        r1 = r - (8 if i % 5 == 0 else 4)
        ticks.append(f'<line x1="{cx + r * math.cos(a):.1f}" y1="{cy - r * math.sin(a):.1f}" '
                     f'x2="{cx + r1 * math.cos(a):.1f}" y2="{cy - r1 * math.sin(a):.1f}"/>')
    arc = ""
    if frac:
        a = math.pi * (1 - frac)
        arc = (f'<path class="g-fill {tone}" d="M {cx - r} {cy} A {r} {r} 0 0 1 '
               f'{cx + r * math.cos(a):.1f} {cy - r * math.sin(a):.1f}"/>')
    a = math.pi * (1 - (frac or 0))
    needle = (f'<line class="g-needle" x1="{cx}" y1="{cy}" x2="{cx + (r - 12) * math.cos(a):.1f}" '
              f'y2="{cy - (r - 12) * math.sin(a):.1f}"/>') if frac is not None else ""
    return (f'<figure class="gauge"><svg viewBox="0 0 140 84" role="img" aria-label="{e(label)}: {e(readout)}">'
            f'<path class="g-track" d="M {cx - r} {cy} A {r} {r} 0 0 1 {cx + r} {cy}"/>{arc}'
            f'<g class="g-ticks">{"".join(ticks)}</g>{needle}<circle class="g-hub" cx="{cx}" cy="{cy}" r="4"/></svg>'
            f'<figcaption><b class="{tone}">{e(readout)}</b><span>{e(label)}</span>'
            f'{f"<small>{e(sub)}</small>" if sub else ""}</figcaption></figure>')


def info_svg(info):
    nodes = info["nodes"]
    bands = ["(owner)"] + sorted({n["lane"] for n in nodes if n["type"] != "owner" and n["lane"]}) + ["(no lane)"]
    rows = {b: [] for b in bands}
    for n in nodes:
        if n["type"] == "owner":
            rows["(owner)"].append(n)
        elif n["type"] == "lane":
            continue
        else:
            rows[n["lane"] if n["lane"] in rows else "(no lane)"].append(n)
    order = {"actor": 0, "thread": 1, "capture": 2}
    bands = [b for b in bands if rows[b] or b == "(owner)"]
    pos, BH, X0, DX = {}, 84, 170, 150
    width = X0 + DX * max([len(rows[b]) for b in bands] + [1]) + DX / 2 + 20
    height = BH * len(bands) + 10
    parts = []
    for i, b in enumerate(bands):
        y = 10 + i * BH
        parts.append(f'<rect class="band{" alt" if i % 2 else ""}" x="0" y="{y}" width="{width}" height="{BH}"/>'
                     f'<text class="band-label" x="10" y="{y + 26}">{e(trunc(b, 20))}</text>')
        for j, n in enumerate(sorted(rows[b], key=lambda n: (order.get(n["type"], 9), n["id"]))):
            x, cy = X0 + j * DX + (i % 2) * DX / 2 + 30, y + 32   # alternate rows are staggered
            pos[n["id"]] = (x, cy)
            t = n["type"]
            if t in ("actor", "owner"):
                shape = f'<circle class="n-{t}" cx="{x}" cy="{cy}" r="13"/>'
            elif t == "thread":
                shape = f'<rect class="n-thread" x="{x - 9}" y="{cy - 9}" width="18" height="18"/>'
            else:
                shape = f'<path class="n-capture" d="M {x} {cy - 12} L {x + 12} {cy} L {x} {cy + 12} L {x - 12} {cy} Z"/>'
            parts.append(f'<g><title>{e(n["type"])}: {e(n["label"])}{" (" + e(n["status"]) + ")" if n.get("status") else ""}</title>{shape}'
                         f'<text class="n-label" x="{x}" y="{cy + 30}">{e(trunc(n["label"], 20))}</text></g>')
    lines = []
    for k, ed in enumerate(info["edges"]):
        if ed["from"] not in pos or ed["to"] not in pos:
            continue
        (x1, y1), (x2, y2) = pos[ed["from"]], pos[ed["to"]]
        cls = "e-" + ed["type"]
        w = 1.2 + min(ed["count"], 8) * 0.5 if ed["type"] == "handoff" else 1
        tip = f'{ed["type"]}: {ed["from"]} → {ed["to"]}' + (f' ({ed["count"]}: {", ".join(ed["refs"][:8])}{"…" if len(ed["refs"]) > 8 else ""})' if ed["refs"] else "")
        if (x1, y1) == (x2, y2):
            d = f"M {x1 - 8} {y1 - 11} C {x1 - 30} {y1 - 48}, {x1 + 30} {y1 - 48}, {x1 + 8} {y1 - 11}"
            mx, my = x1, y1 - 42
        else:
            bend = (26 + (k % 3) * 12) * (1 if k % 2 else -1)
            cx_, cy_ = (x1 + x2) / 2 + (bend if y1 != y2 else 0), (y1 + y2) / 2 - (abs(bend) * 2 if y1 == y2 else 0)
            d = f"M {x1:.1f} {y1:.1f} Q {cx_:.1f} {cy_:.1f} {x2:.1f} {y2:.1f}"
            mx, my = (x1 + 2 * cx_ + x2) / 4, (y1 + 2 * cy_ + y2) / 4
        lines.append(f'<g><title>{e(tip)}</title><path class="{cls}" d="{d}" stroke-width="{w:.1f}" marker-end="url(#arrow-{ed["type"]})"/>'
                     + (f'<text class="e-count" x="{mx:.1f}" y="{my:.1f}">{ed["count"]}</text>' if ed["type"] == "handoff" else "")
                     + "</g>")
    defs = "".join(f'<marker id="arrow-{t}" viewBox="0 0 10 10" refX="23" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">'
                   f'<path class="m-{t}" d="M 0 0 L 10 5 L 0 10 z"/></marker>' for t in ("handoff", "captured", "capture-of"))
    return (f'<svg class="topo" viewBox="0 0 {width:.0f} {height}" width="{width:.0f}" height="{height}" role="img" '
            f'aria-label="Information topology"><defs>{defs}</defs>{"".join(parts)}{"".join(lines)}</svg>')


def authority_svg(auth, pid="a"):
    nodes = auth["nodes"]
    owner = [n for n in nodes if n["type"] == "owner"]
    mid = [n for n in nodes if n["type"] == "coordinator"]
    low = [n for n in nodes if n["type"] == "lane"]
    DX = 128
    width = max(len(low), len(mid), 1) * DX + 40
    tiers = [(owner, 40), (mid, 140), (low, 250)] if mid else [(owner, 40), (low, 200)]
    height = tiers[-1][1] + 60
    pos, parts = {}, []
    for row, y in tiers:
        for i, n in enumerate(row):
            x = 20 + (width - 40) * (i + 0.5) / len(row)
            pos[n["id"]] = (x, y)
            cls = {"owner": "n-owner", "coordinator": "n-coord", "lane": "n-lane"}[n["type"]]
            parts.append(f'<g><title>{e(n["type"])}: {e(n["label"])}</title><rect class="{cls}" x="{x - 52}" y="{y - 15}" '
                         f'width="104" height="30" rx="4"/><text class="a-label" x="{x}" y="{y + 5}">{e(trunc(n["label"], 15))}</text></g>')
    groups = {}
    for ed in auth["edges"]:
        groups.setdefault((ed["from"], ed["to"], ed["kind"]), []).append(ed)
    lines = []
    for k, ((a, b, kind), eds) in enumerate(sorted(groups.items())):
        if a not in pos or b not in pos:
            continue
        (x1, y1), (x2, y2) = pos[a], pos[b]
        y1 += 15
        y2 -= 15
        if kind == "structure":
            d = f"M {x1:.1f} {y1} L {x2:.1f} {y2}"
            label = ""
        else:
            off = 36 + (k % 3) * 14
            d = f"M {x1:.1f} {y1} Q {(x1 + x2) / 2 + off:.1f} {(y1 + y2) / 2:.1f} {x2:.1f} {y2}"
            ids = [x["decision"] for x in eds]
            label = f'<text class="e-dec" x="{(x1 + 2 * ((x1 + x2) / 2 + off) + x2) / 4:.1f}" y="{(y1 + y2) / 2:.1f}">{e(trunc(", ".join(ids), 22))}</text>'
        ids_ = ", ".join(x["decision"] for x in eds if x["decision"])
        tip = f"{kind}: {a} → {b}" + {"delegation": f" ({ids_}; inferred from text)", "recorded": f" ({ids_}; delegates_to)"}.get(kind, " (assumed)")
        lines.append(f'<g><title>{e(tip)}</title><path class="a-{kind}" d="{d}" marker-end="url(#{pid}-arrow-{kind})"/>{label}</g>')
    defs = "".join(f'<marker id="{pid}-arrow-{t}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">'
                   f'<path class="m-{t}" d="M 0 0 L 10 5 L 0 10 z"/></marker>' for t in ("structure", "delegation", "recorded"))
    return (f'<svg class="topo" viewBox="0 0 {width:.0f} {height}" width="{width:.0f}" height="{height}" role="img" '
            f'aria-label="Authority and delegation topology"><defs>{defs}</defs>{"".join(lines)}{"".join(parts)}</svg>')


def dl(pairs):
    return "<dl>" + "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in pairs if v not in (None, "", [])) + "</dl>"


def choice_card(c):
    key = f'{c["kind"]}:{c["id"]}'
    head = (f'<header><span class="badge {c["kind"]}">{e(c["kind"])}</span><b>{e(c["id"])}</b>'
            f'<span class="st">{e(c["status"])}</span><span class="date">{e(c.get("date"))}</span></header>')
    if c["kind"] == "decision":
        body = (f'<p class="txt">{e(c.get("text"))}</p>' if c.get("text") else "") + dl(
            [("scope", c.get("scope")), ("proposed by", c.get("proposed_by")), ("decider", c.get("decider")),
             ("basis", c.get("basis")), ("locator", c.get("locator")), ("source", c["source"])])
        hint = "Accept appends an adopted line that supersedes it; Reject a rejected one. You fill in decider, basis and locator."
    elif c["kind"] == "handoff":
        body = (f'<p class="txt">{e(c.get("title"))}</p>' if c.get("title") else "") + dl(
            [("from", c.get("from")), ("to", c.get("to")), ("needs the owner", "; ".join(c["why"])), ("source", c["source"])])
        hint = f'Accept moves it to {c["compose"]["accept"]["state"]}; Reject cancels it (owner only). These are handoff.py commands; your words go in the note.'
    else:
        body = (f'<p class="txt">{e(c.get("text"))}</p>' if c.get("text") else "") + dl(
            [("raised by", c.get("raised_by")), ("affects", c.get("affects")), ("proposed test", c.get("proposed_test")),
             ("source", c["source"])])
        hint = "Accept records accepted-risk; Reject records resolved (dismissed or fixed). You write the resolution."
    btns = "".join(f'<button type="button" class="act {a}" data-key="{e(key)}" data-act="{a}" '
                   f'data-line="{e(c["compose"][a]["command"])}">{a.title()}</button>' for a in ("accept", "reject", "defer"))
    return f'<article class="card choice" data-key="{e(key)}">{head}{body}<p class="hint">{e(hint)}</p><div class="btns">{btns}</div></article>'


def table(head, rows, cls=""):
    if not rows:
        return '<p class="empty">none</p>'
    th = "".join(f"<th>{e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f'<td data-h="{e(h)}">{e(v)}</td>' for h, v in zip(head, r)) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


SURFACES = [("overview", "◈", "Overview"), ("intake", "✳", "Idea intake"), ("questions", "❖", "Questions"),
            ("choices", "⌁", "Choices"), ("topology", "◎", "Topology"), ("budget", "◉", "Budget control"),
            ("lanes", "▦", "Project lanes"), ("run", "◇", "Run commitments"), ("review", "◷", "Review")]
CREDIT = "design after Kate's Cockpit v0.4 prototype (2026-11-04)"


def head(n, eyebrow, title, lede, tag):
    return (f'<div class="sec-head"><div><p class="eyebrow">{n:02d} / {e(eyebrow)}</p><h1>{e(title)}<em>.</em></h1>'
            f'<p class="lede">{e(lede)}</p></div><span class="tag hot">{e(tag)}</span></div>')


def stat(label, value, small, extra=""):
    return f'<div class="stat">{extra}<p class="stat-label">{e(label)}</p><strong>{e(value)}</strong><small>{e(small)}</small></div>'


def not_wired(what, links, also):
    li = "".join(f'<li><a class="subtle-link" href="{e(u)}" rel="noopener noreferrer">{e(u)}</a></li>' for u in links)
    return (f'<div class="panel pad notwired"><span class="tag">NOT WIRED TO RECORDS YET</span>'
            f'<h2>{e(what)} isn\'t read from the records yet.</h2><p>So nothing is shown here rather than placeholder '
            f'content. {e(also)}</p><p class="mini-label">INTAKE THREAD, FROM THREADS.MD</p>'
            + (f'<ul class="links">{li}</ul>' if li else '<p class="empty">threads.md lists no line mentioning intake.</p>')
            + "</div>")


def render(data):
    c, b, run, topo = data["counts"], data["budget"], data["run"], data["topology"]
    full = data["query"]["content"] == "full"
    brand = data["query"].get("brand") or "relay-kit"
    allowance, burn = b.get("weekly_allowance"), b.get("burn_7d")
    if not b["present"]:
        bg = gauge(0, None, "resource budget / 7 d", "NO RECORDS", "no budget records yet", "muted")
        budget_read = "NO RECORDS"
    elif burn is None:
        bg = gauge(0, None, "resource budget / 7 d", "NO DATA", "no session lines in the window", "muted")
        budget_read = "NO DATA"
    elif allowance:
        frac = burn / allowance
        bg = gauge(burn, allowance, "resource budget / 7 d", f"{frac:.0%}", f"{fmt_n(burn)} of {fmt_n(allowance)} tokens",
                   "bad" if frac >= 0.9 else ("warn" if frac >= 0.7 else "ok"))
        budget_read = f"{frac:.0%}"
    else:
        bg = gauge(0, None, "resource budget / 7 d", fmt_n(burn), "tokens · allowance unset", "muted")
        budget_read = "UNSET"
    kinds = c["choices_by_kind"]
    dials = (gauge(c["choices"], max(c["choices"], 10), "choice matrix / open", str(c["choices"]),
                   f'{kinds["decision"]} decisions · {kinds["handoff"]} handoff{"" if kinds["handoff"] == 1 else "s"} · {kinds["challenge"]} challenges',
                   "warn" if c["choices"] else "ok")
             + bg
             + gauge(c["authorized_in_progress"], max(c["open_handoffs"], 1), "run commitments / authorized",
                     str(c["authorized_in_progress"]), f'authorized & in progress · {c["pending_authorization"]} pending authorization', "ok")
             + gauge(c["drift_flags"], max(c["drift_flags"], 5), "drift flags", str(c["drift_flags"]),
                     f'stale proposals > {run["drift"]["drift_days"]} d · drift challenges', "bad" if c["drift_flags"] else "ok"))
    waiting = "".join(f'<a class="direction" href="#choices" data-goto="choices" data-card="{e(x["kind"])}:{e(x["id"])}">'
                      f'<span class="direction-num">{i:02d}</span><span class="direction-body"><strong>{e(x["id"])}</strong>'
                      f'<p>{e(trunc(x.get("text") or x.get("title") or x["kind"] + " · " + x["status"], 120))}</p></span>'
                      f'<span class="tag">{e(x["kind"])}</span></a>' for i, x in enumerate(data["choices"][:6], 1)) \
        or '<p class="empty">Nothing waits on you.</p>'
    s_overview = f"""
<section class="section surface" id="overview">
{head(1, "MISSION OVERVIEW", "Overview", "Generated from your records: what waits on you, what is running, what it costs, and where it drifts.", "ADVISORY ONLY")}
<div class="dials">{dials}</div>
<div class="overview-grid">
 <div class="panel pad"><div class="panel-head"><p class="eyebrow dark">WAITING ON YOU</p>
 <a class="subtle-link" href="#choices" data-goto="choices">Open Choices ↗</a></div><div class="direction-list">{waiting}</div></div>
 <div class="panel pad principle-panel"><p class="eyebrow dark">THE GOVERNING DISTINCTION</p>
 <div class="split-concept"><div class="concept"><span>01 / DRIFT</span><strong>Explore without obligation.</strong>
 <p>Questions, hypotheses, alternatives and uncertainty belong here.</p></div><div class="concept-divider">≠</div>
 <div class="concept run-concept"><span>02 / RUN</span><strong>Act with explicit intent.</strong>
 <p>A commitment needs your authorization and a concrete next action.</p></div></div>
 <p class="principle-footer">✳ No automatic promotions. Ever. This page writes nothing.</p></div>
</div></section>"""

    s_intake = f"""
<section class="section surface" id="intake">
{head(2, "OPEN INQUIRY", "Idea intake", "Somewhere to throw things before they need to make sense.", "DRIFT BY DEFAULT")}
{not_wired("Idea intake", data["intake_links"], "Capture in the intake thread; nothing captured there becomes a task on its own.")}
</section>"""
    s_questions = f"""
<section class="section surface" id="questions">
{head(3, "QUESTIONS & REALIZATIONS", "Question stewards", "A persistent home for questions worth answering, challenging, or asking you.", "NO AGENT EXECUTION")}
{not_wired("Question stewards", data["intake_links"], "Questions that are already recorded live as open challenges and proposed decisions: see Choices.")}
</section>"""

    cards = "".join(choice_card(x) for x in data["choices"]) or '<p class="empty">Nothing waits on you.</p>'
    s_choices = f"""
<section class="section surface" id="choices">
{head(4, "HUMAN DECISION CONSOLE", "Choices", "Proposed decisions, handoffs that need you, and open challenges, read from the records.", "CHOICE ≠ COMMITMENT / ONLY YOU CAN SETTLE")}
<div class="stats-row">{stat("DECISIONS PROPOSED", kinds["decision"], "not superseded")}{stat("HANDOFFS WAITING", kinds["handoff"], "prepared, unauthorized or to you")}
{stat("OPEN CHALLENGES", kinds["challenge"], "not resolved")}{stat("LEDGER SYNC", "READ-ONLY", "this page appends nothing")}</div>
<div class="notice"><b>Authority stays with your paste (DEC-019).</b> Accept, Reject and Defer write nothing. Each composes a line
into <i>To apply</i>: a <code>relay-kit ledger append</code> command for decisions and challenges, a <code>relay-kit handoff set</code>
command for handoffs. <b>You supply the justification:</b> every line carries <code>&lt;&lt;REQUIRED: …&gt;&gt;</code>
placeholders for the decider, your words (basis) and where you said them (locator), and the ledger check refuses a line that
still holds one. The page never fills in who decided or why. Replace them, copy, and run the line in your records with
<code>RELAYKIT_ACTOR={e(data["owner"])}</code>, which records you as the writer. Until you do, nothing has changed.</div>
<div class="choices-grid"><div class="cards">{cards}</div>
<aside class="apply panel"><p class="eyebrow dark">TO APPLY</p><textarea id="apply" spellcheck="false" aria-label="Lines to apply"
placeholder="Choose Accept, Reject or Defer on an item."></textarea>
<div class="btns"><button type="button" class="primary small" id="copy">Copy</button><button type="button" class="secondary small" id="clear">Clear</button>
<span id="copied" role="status"></span></div></aside></div>
</section>"""

    a = topo["authority"]
    rec, hyp = a["recorded"], a["hypothesised"]
    rec_rows = [(x["decision"], x.get("date"), x["from"], x["to"], x["delegates_to"]) for x in rec["edges"]]
    deleg_rows = [(x["decision"], x.get("date"), x["from"], x["to"], "inferred from text") for x in hyp["edges"] if x["kind"] == "delegation"]
    rec_block = (f'<div class="scroll">{authority_svg(rec, "rec")}</div><p class="mini-label">RECORDED DELEGATIONS</p>'
                 + table(["decision", "date", "from", "to", "delegates_to"], rec_rows)) if rec["edges"] else \
        ('<p class="empty norec">No recorded delegation. A delegation is recorded when an adopted decision names its delegate '
         'in a <code>delegates_to</code> field; none does yet.</p>')
    s_topo = f"""
<section class="section surface" id="topology">
{head(5, "MISSION MAPS", "Topology", "Two systems drawn from the records: where information flows, and who may act.", "DRAWN FROM RECORDS / NO LIVE AGENT STATES")}
<div class="selector" role="group" aria-label="Network selector"><span class="mini-label">NETWORK SELECTOR / 01–02</span>
<button type="button" class="secondary small sel" data-net="info" aria-pressed="true">◈ Information</button>
<button type="button" class="secondary small sel" data-net="auth" aria-pressed="false">⌘ Authority / agents</button></div>
<div class="panel pad net" data-net="info"><p class="eyebrow dark">01 / INFORMATION</p>
<p class="legend"><span class="k k-actor"></span>actor (assistant:lane) <span class="k k-owner"></span>owner
<span class="k k-thread"></span>thread in threads.md <span class="k k-capture"></span>captured conversation
<span class="k k-handoff"></span>handoff, sender → receiver (number = handoffs; thicker = more)
<span class="k k-captured"></span>actor → capture → thread. Rows are lanes; placement inside a row is automatic and means nothing.
Hover a node or edge for detail.</p>
<div class="scroll">{info_svg(topo["information"])}</div></div>
<div class="panel pad net" data-net="auth"><p class="eyebrow dark">02 / AUTHORITY / AGENTS · RECORDED ONLY</p>
<p class="legend"><span class="k k-recorded"></span>recorded delegation: an adopted, in-force decision with a <code>delegates_to</code>
field (label = decision id). Nothing executable is derived from this or the overlay below: the decisions are the authority.</p>
{rec_block}
<details class="hyp" id="hyp"><summary>Show hypothesised authority (inferred)</summary>
<p class="eyebrow dark">HYPOTHESISED AUTHORITY (INFERRED) · NOT A RECORD</p>
<p class="legend"><span class="k k-structure"></span>assumed reporting line, owner → coordinator → lanes (no decision says so)
<span class="k k-delegation"></span>text match: an in-force decision whose text mentions delegating or authorizing, drawn to the lanes
its scope names. These are readings, not delegations. Layout is automatic.{"" if a["coordinator"] else " No coordinator lane found (none is named *coord*; pass --coordinator)."}</p>
<div class="scroll">{authority_svg(hyp, "hyp")}</div>
<p class="mini-label">TEXT-MATCHED EDGES</p>{table(["decision", "date", "from", "to", "status"], deleg_rows)}</details></div>
</section>"""

    pol = b["policy"]
    pol_rows = [(k.replace("_", " "), "UNSET (unknown, not unlimited)" if v is None else v) for k, v in pol["caps"].items()]
    remaining = (allowance - burn) if (allowance and b["present"] and burn is not None) else None
    wref = b.get("weekly_reference")
    if b["present"]:
        bud = f"""
<div class="panel pad">{dl([("window", f'{b["window"]["from"]} → {b["window"]["to"]}'), ("burn", f'{fmt_n(burn)} tokens ({b["burn_source"]})'),
  ("session lines, 7 d", f'{fmt_n(b["session_tokens_7d"])} ({fmt_n(b["session_in_7d"])} in, {fmt_n(b["session_out_7d"])} out)'),
  ("weekly reading (reference only)", f'{fmt_n(wref["tokens"])} tokens at {wref["ts"]}, reported by {wref["actor"]}; not added to the gauge' if wref else None),
  ("most agents at once", fmt_n(b["max_agents_7d"])), ("records", b["records"]),
  ("invalid lines", ", ".join(f'{x["line"]}: {x["reason"]}' for x in b["invalid_lines"]) or "none")])}
<div class="cols"><div><p class="mini-label">LAST 7 DAYS BY LANE</p>{table(["lane", "tokens"], [(k, fmt_n(v)) for k, v in b["by_lane"].items()])}</div>
<div><p class="mini-label">LAST 7 DAYS BY ACTOR</p>{table(["actor", "tokens"], [(k, fmt_n(v)) for k, v in b["by_actor"].items()])}</div></div>
{('<p class="mini-label">RECENT NOTES</p>' + table(["ts", "actor", "note"], [(x["ts"], x["actor"], x["note"]) for x in b.get("recent_notes", [])])) if full else ""}</div>"""
    else:
        bud = '<div class="panel pad"><p class="empty">No budget records yet: <code>governance/budget.jsonl</code> is absent. Its schema is below.</p></div>'
    s_budget = f"""
<section class="section surface" id="budget">
{head(6, "RESOURCE & ATTENTION GOVERNOR", "Budget control", "Recorded token use against a weekly allowance, with an advisory policy before any automated enforcement.", "RECORDED DATA · NO LIVE PROVIDER TELEMETRY")}
<div class="stats-row">{stat("CONTROL MODE", pol["caps"].get("default_effort") or "UNSET", "advisory setting")}
{stat("WEEKLY ALLOWANCE", fmt_n(allowance) if allowance else "UNSET", "tokens · --weekly-tokens or governance/cockpit.json")}
{stat("BURN / LAST 7 DAYS", fmt_n(burn) if burn is not None else "—", b.get("burn_source") or "no budget records yet")}
{stat("LIVE METERING", "OFFLINE", "no external services connected")}</div>
<div class="budget-top"><div class="gauge-big">{bg}</div>{bud}</div>
<div class="overview-grid">
<div class="panel pad"><div class="panel-head"><h2>Resource policy</h2><span class="tag hot">ADVISORY ONLY</span></div>
<p class="hint">These caps constrain the policy model shown here, not Claude, ChatGPT, Codex, AWS or any account. Set them under
<code>policy</code> in <code>governance/cockpit.json</code>{f' (read from {e(pol["source"])})' if pol["source"] else " (none set)"}.</p>
{table(["cap", "value"], pol_rows)}</div>
<div class="panel pad" id="preflight" data-remaining="{'' if remaining is None else remaining}"><p class="eyebrow dark">PRE-FLIGHT / DECISION SUPPORT</p>
<h2>Should we spend on this question?</h2><p class="hint">Estimate before delegating. This launches nothing and reserves nothing.</p>
<label class="mini-label" for="pf-tokens">ESTIMATED TOKENS</label><input id="pf-tokens" type="number" min="0" inputmode="numeric" placeholder="unknown">
<label class="mini-label" for="pf-imp">QUESTION IMPORTANCE</label><select id="pf-imp"><option value="routine">Routine / reversible</option>
<option value="meaningful">Meaningful decision</option><option value="high">High consequence / evidence-sensitive</option></select>
<button type="button" class="primary small" id="pf-go">Evaluate route ↗</button><p id="pf-out" class="pf-out" role="status">NOT ASSESSED</p></div>
</div>
<div class="principles">
<div class="concept"><span>01 / COST</span><p>Money, tokens and provider allowances are different resources. No token-to-dollar estimate without a model and rate.</p></div>
<div class="concept"><span>02 / CAPACITY</span><p>Concurrency slots are ceilings, not occupancy measurements.</p></div>
<div class="concept"><span>03 / ATTENTION</span><p>Your review and interruptions consume a budget too. A question can wait instead of interrupting.</p></div>
<div class="concept"><span>04 / AUTHORITY</span><p>A budget may constrain routing; it cannot grant agents rights to decide, spawn or execute.</p></div></div>
<p class="mini-label">BUDGET.JSONL SCHEMA</p>{table(["field", "meaning"], list(b["schema"].items()))}
<p class="hint">Append with <code>relay-kit budget-append --window session --tokens-in N --tokens-out N</code>. The gauge sums the
<i>session</i> lines inside the 7-day window; a <i>weekly</i> line is shown only as a separately labelled reference. With no session
lines the gauge says so; it never shows a zero it didn't observe. The coordinator's status-file cost lines are
<b>not</b> parsed.</p>
</section>"""

    lane_cards = "".join(
        f'<div class="lane-card panel pad"><div class="panel-head"><p class="eyebrow dark">{e(x["code"] or "—")}'
        f'{" · COORDINATOR" if x["coordinator"] else ""}</p><span class="tag">{e(x["last_activity"] or "no activity")}</span></div>'
        f'<h2>{e(x["lane"])}</h2>{dl([("handoffs", f"{x["open_handoffs"]} open of {x["handoffs"]}"), ("decisions", f"{x["decisions"]} ({x["proposed"]} proposed)"), ("open challenges", x["open_challenges"]), ("threads", x["threads"])])}</div>'
        for x in data["lanes"]) or '<p class="empty">No lanes declared in handoffs/.ledger.json or threads.md.</p>'
    s_lanes = f"""
<section class="section surface" id="lanes">
{head(7, "THE CONSTELLATION", "Project lanes", "Separate places to build, investigate, and return: lanes declared in .ledger.json and threads.md.", "FROM RECORDS · NO LIVE SYNC")}
<div class="lane-grid">{lane_cards}</div></section>"""

    hs = run["handoffs_by_status"]
    status_blocks = "".join(
        f'<details{" open" if s not in ("completed", "cancelled") else ""}><summary>{e(s)} <b>{len(v)}</b></summary>'
        + table(["id", "title", "from → to", "last"],
                [(x["id"], x["title"] if x["title"] is not None else "(title left out)", f'{x["from"]} → {x["to"]}',
                  f'{x["last"]["at"] or ""} {x["last"]["state"] or ""}') for x in (v if s not in ("completed", "cancelled") else v[-10:])])
        + "</details>" for s, v in hs.items()) or '<p class="empty">no handoffs</p>'
    prs = "".join(f'<li><a class="subtle-link" href="{e(u)}" rel="noopener noreferrer">{e(u)}</a></li>' for u in run["pr_links"])
    dr = run["drift"]
    stale_items = "".join(
        f'<div class="direction"><span class="direction-num">{e(x["age_days"])} d</span><span class="direction-body"><strong>{e(x["id"])}</strong>'
        f'<p>{e(trunc(x.get("text") or "proposed " + str(x["date"]), 140))}</p></span>'
        f'<button type="button" class="secondary small torun" data-card="decision:{e(x["id"])}">Move to Run?</button></div>'
        for x in dr["stale_proposals"]) or '<p class="empty">none</p>'
    s_run = f"""
<section class="section surface" id="run">
{head(8, "RUN COMMITMENTS", "Run commitments", "Handoffs by state, and what has drifted: proposals left waiting and challenges that name drift.", "EXPLICITLY AUTHORIZED ONLY")}
<div class="stats-row">{stat("AUTHORIZED & IN PROGRESS", c["authorized_in_progress"], "sent, acknowledged or in progress, with an Authorized section")}
{stat("PENDING AUTHORIZATION", c["pending_authorization"], "prepared, or no authorization quote: not a commitment yet")}</div>
<div class="cols"><div class="panel pad"><p class="eyebrow dark">AUTHORIZED &amp; IN PROGRESS</p>
{table(["id", "status", "from → to"], [(x["id"], x["status"], f'{x["from"]} → {x["to"]}') for x in run["commitments"]["authorized"]])}
<p class="eyebrow dark">PENDING AUTHORIZATION · NOT RUN</p>
{table(["id", "status", "from → to"], [(x["id"], x["status"], f'{x["from"]} → {x["to"]}') for x in run["commitments"]["pending"]])}
<p class="eyebrow dark">ALL HANDOFFS BY STATUS</p>{status_blocks}
<p class="mini-label">PULL REQUESTS</p><p class="hint">Open pull requests aren't fetched (no network): see GitHub. Links listed in threads.md:</p>
{f"<ul class='links'>{prs}</ul>" if prs else '<p class="empty">none in threads.md</p>'}</div>
<div class="panel pad"><p class="eyebrow dark">DRIFT</p><p class="mini-label">PROPOSALS OLDER THAN {dr["drift_days"]} DAYS</p>
<div class="direction-list">{stale_items}</div>
<p class="mini-label">CHALLENGES MENTIONING DRIFT</p>
{table(["id", "status", "raised by"] + (["concern"] if full else []), [[x["id"], ("open" if x["open"] else x["status"]), x["raised_by"]] + ([trunc(x.get("text"), 160)] if full else []) for x in dr["challenges_mentioning_drift"]])}</div></div>
</section>"""

    s_review = f"""
<section class="section surface" id="review">
{head(9, "REVIEW", "Review", "The recent decision timeline and the challenges still open.", "LEDGER IS THE SOURCE OF TRUTH")}
<div class="panel pad"><p class="eyebrow dark">RECENT DECISIONS / LAST 20</p>
{table(["id", "date", "status", "supersedes"] + (["decision"] if full else []), [[x["id"], x["date"], x["status"], x["supersedes"] or ""] + ([trunc(x.get("text"), 200)] if full else []) for x in run["decisions_recent"]], "timeline")}</div>
<div class="panel pad"><p class="eyebrow dark">OPEN CHALLENGES</p>
{table(["id", "date", "raised by"] + (["concern"] if full else []), [[x["id"], x["date"], x["raised_by"]] + ([trunc(x.get("text"), 160)] if full else []) for x in run["challenges_open"]])}</div>
</section>"""

    nav = "".join(f'<a class="nav-link" href="#{k}" data-goto="{k}" data-name="{e(t)}"><span class="nav-icon">{i}</span>{e(t)}'
                  f'<span class="nav-kbd">{n:02d}</span></a>' for n, (k, i, t) in enumerate(SURFACES, 1))
    limits = "".join(f"<li>{e(x)}</li>" for x in data["limits"])
    unread = (f'<p class="hint">Unreadable: {e(", ".join(str(u["path"]) for u in data["unreadable"]))}</p>' if data["unreadable"] else "")
    q = data["query"]
    modal = """
<dialog id="settle"><form method="dialog"><p class="eyebrow dark" id="settle-eyebrow">SETTLE THIS CHOICE?</p><h2 id="settle-title"></h2>
<p class="hint" id="settle-body"></p><pre id="settle-line"></pre><div class="btns"><button value="ok" class="primary small" id="settle-ok">Add to To apply</button>
<button value="cancel" class="secondary small">Cancel</button></div></form></dialog>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<title>{e(brand.title())} · Cockpit</title><style>{CSS}</style></head>
<body><div class="shell">
<aside class="sidebar"><div class="brand"><span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 44 44"><circle cx="22" cy="22" r="19" fill="none" stroke="currentColor"/>
<circle cx="22" cy="22" r="11" fill="none" stroke="currentColor" opacity=".5"/><circle cx="22" cy="22" r="3" fill="currentColor"/></svg></span>
<span class="brand-word"><b>{e(brand)}</b><small>COCKPIT / {e(SCHEMA.split("/")[1])}</small></span></div>
<p class="nav-title">WORKSPACE</p><nav class="nav-links" aria-label="Surfaces">{nav}</nav>
<div class="sidebar-section"><p class="nav-title">SYSTEM</p><div class="system-badges">
<div><span class="light light-good"></span>Generated from records</div><div><span class="light light-warn"></span>Integrations not connected</div>
<div><span class="light light-good"></span>Writes nothing</div></div></div>
<div class="side-quote"><p class="small-caps">OPERATING PRINCIPLE</p><p>Inquiry is not authority.<br>Capture is not commitment.</p></div>
<div class="version"><span>{e(SCHEMA)}</span><span>v{e(data["generator"]["kit_version"])}</span></div></aside>
<div class="content-shell"><header class="topbar"><div class="breadcrumb"><span>{e(brand.upper())} COCKPIT</span><span class="slash">/</span>
<span id="crumb">OVERVIEW</span></div><div class="topbar-right"><span class="offline-tag"><span class="light light-warn"></span>LOCAL-FIRST PREVIEW</span>
<span class="date-label">generated from records as of {e(data["as_of"] or "(no timestamps)")} · lane {e(q["lane"] or "all")} · content {e(q["content"])}</span>
<a class="primary small" href="#intake" data-goto="intake">+ Capture thought</a></div></header>
<main>{s_overview}{s_intake}{s_questions}{s_choices}{s_topo}{s_budget}{s_lanes}{s_run}{s_review}</main>
<footer class="section"><p class="mini-label">WHAT THIS PAGE CAN'T TELL YOU</p><ul>{limits}</ul>{unread}
<p class="hint">{e(SCHEMA)} · relay-kit {e(data["generator"]["kit_version"])} · owner {e(data["owner"])} · data: cockpit.json beside this page ·
{e(CREDIT)}.</p>
<p class="hint"><b>Privacy:</b> this page and cockpit.json hold your records{"' text" if full else " (metadata: no bodies, but thread links, session ids, actor names and file paths remain)"}.
<code>--content metadata</code> is redaction, not publication safety. Serving it from a domain needs authentication on both
index.html and cockpit.json; <code>noindex</code> is not access control.</p></footer></div></div>
{modal}<script>{JS}</script></body></html>
"""


CSS = """
:root{--bg:#0b1015;--panel:#121b22;--panel-2:#162128;--border:#26343d;--line:#203039;--text:#f0f0e9;--muted:#96a5a8;--soft:#71818b;
--teal:#9bd5c8;--teal-deep:#254b46;--gold:#dcc28c;--purple:#ac9dd7;--coral:#dfa696;--blue:#90b4d7;
--display:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;--mono:ui-monospace,"SFMono-Regular",Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 var(--display);-webkit-font-smoothing:antialiased}
button,input,textarea,select{font:inherit}a{color:inherit;text-decoration:none}button{cursor:pointer}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--teal);outline-offset:3px}
code{font-family:var(--mono);font-size:.9em;color:var(--teal)}h1,h2,h3,p{margin-top:0}
.shell{min-height:100vh;display:grid;grid-template-columns:248px minmax(0,1fr)}
.sidebar{height:100vh;position:sticky;top:0;display:flex;flex-direction:column;border-right:1px solid #1d2a32;padding:24px 16px 18px;background:#0c1318;overflow-y:auto}
.brand{display:flex;align-items:center;gap:9px;padding:0 13px;margin-bottom:36px}.brand-mark{width:40px;height:40px;color:var(--teal);display:flex}
.brand-word b{font-weight:660;font-size:22px;letter-spacing:-1px;line-height:1}.brand-word small{display:block;color:#6f8487;font:9px var(--mono);letter-spacing:1.45px;margin-top:6px}
.nav-title{font:10px var(--mono);letter-spacing:1.75px;color:#647880;padding-left:13px;margin:0 0 10px}
.nav-links{display:flex;flex-direction:column;gap:3px}
.nav-link{display:flex;align-items:center;gap:13px;font-size:13px;color:#a3b2b6;padding:11px 13px;border-radius:7px;border:1px solid transparent}
.nav-link:hover{color:#fff;background:#17232a}.nav-link.active{color:#d7f3ea;background:#152923;border-color:#25423a}
.nav-icon{width:17px;text-align:center;font-size:16px;color:#9bb4b5}.nav-link.active .nav-icon{color:var(--teal)}
.nav-kbd{margin-left:auto;font:10px var(--mono);color:#6f8988}
.sidebar-section{margin-top:32px}.system-badges{display:grid;gap:10px;padding:5px 13px;color:#9fb0af;font:11px var(--mono)}
.system-badges>div{display:flex;align-items:center;gap:9px}
.light{display:inline-block;width:6px;height:6px;border-radius:50%;flex-shrink:0}.light-good{background:#8fccad;box-shadow:0 0 9px #85cba34a}.light-warn{background:#d5b675}
.side-quote{margin:26px 12px 0;border-top:1px solid var(--border);padding-top:18px}.small-caps,.mini-label{font:10px var(--mono);letter-spacing:1.5px;color:#9ab0ad;text-transform:uppercase}
.side-quote p:last-child{font-size:13px;line-height:1.6;color:#b4c4bd;margin:8px 0 0}
.version{display:flex;justify-content:space-between;gap:8px;color:#6a7f88;font:9px var(--mono);padding:24px 3px 0;margin-top:auto}
.content-shell{min-width:0}
.topbar{position:relative;z-index:15;min-height:64px;padding:10px 40px;display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;justify-content:space-between;
border-bottom:1px solid #1e2c34;background:#0b1015ee;backdrop-filter:blur(15px)}
.breadcrumb{display:flex;gap:12px;align-items:center;letter-spacing:1.1px;font:11px var(--mono);color:#d3e0dc}.slash{color:#63777d}
.topbar-right{display:flex;flex-wrap:wrap;align-items:center;gap:8px 20px}
.offline-tag{color:#cbb88a;font:10px var(--mono);letter-spacing:.8px;display:flex;align-items:center;gap:8px}.date-label{color:#a0b1b4;font:11px var(--mono)}
.primary,.secondary{border:1px solid transparent;display:inline-flex;align-items:center;justify-content:center;gap:6px;border-radius:5px;min-height:40px;padding:9px 16px;font-size:12px;font-weight:650;white-space:nowrap}
.primary{color:#122820;background:var(--teal)}.primary:hover{background:#c3ede0}.small{min-height:36px;padding:7px 13px;font-size:12px}
.secondary{border-color:#39474b;color:#d9e2db;background:#152026}.secondary:hover{background:#1d2e34;border-color:#59766c}
.section{padding:32px 40px 8px}.js .surface[hidden]{display:none}
.sec-head{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:20px}
.eyebrow{font:10px var(--mono);letter-spacing:1.6px;color:#a7d1c0;text-transform:uppercase;margin-bottom:8px}.eyebrow.dark{color:#9ab8b3}
.sec-head h1{font-size:clamp(30px,3.6vw,46px);line-height:1.1;letter-spacing:-1.8px;font-weight:580;margin:4px 0 8px}.sec-head h1 em{font-style:normal;color:var(--teal)}
.lede{color:#b2c5bd;font-size:14px;max-width:620px;margin:0}
.tag{font:10px var(--mono);letter-spacing:.3px;color:#b1c0be;border:1px solid #3a4a4d;border-radius:3px;padding:5px 8px;white-space:nowrap}
.tag.hot{border-color:#5c6b4b;color:#e6c891;background:#3a352620}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:8px;min-width:0}.pad{padding:18px 20px;margin-bottom:16px}
.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.panel h2{font-size:18px;font-weight:560;letter-spacing:-.3px;margin:6px 0 12px}
.subtle-link{font-size:12px;color:#a9d2c2;overflow-wrap:anywhere}.subtle-link:hover{color:#effff5}
.dials{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.gauge{margin:0;background:#121b21;border:1px solid var(--border);border-radius:7px;padding:12px 12px 14px;text-align:center}
.gauge svg{width:100%;max-width:170px;height:auto;display:block;margin:0 auto}
.g-track{fill:none;stroke:#1f2d33;stroke-width:9}.g-fill{fill:none;stroke-width:9}
.g-fill.ok{stroke:var(--teal)}.g-fill.warn{stroke:var(--gold)}.g-fill.bad{stroke:var(--coral)}.g-fill.muted{stroke:var(--soft)}
.g-ticks line{stroke:#5d7279;stroke-width:1.2}.g-needle{stroke:var(--text);stroke-width:2.5;stroke-linecap:round}.g-hub{fill:var(--text)}
.gauge figcaption{display:flex;flex-direction:column}.gauge figcaption b{font:560 26px/1.3 var(--display);letter-spacing:-1px}
.gauge figcaption span{font:10px var(--mono);letter-spacing:1px;text-transform:uppercase;color:#9fb3af}.gauge small{font-size:12px;color:#8ea4a3}
b.ok{color:var(--teal)}b.warn{color:var(--gold)}b.bad{color:var(--coral)}b.muted{color:var(--muted)}
.stats-row{margin:0 0 18px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.stat{background:#121b21;border:1px solid var(--border);border-radius:7px;padding:16px 18px}
.stat-label{font:10px var(--mono);letter-spacing:1px;color:#9fb3af;margin:0}.stat strong{display:block;font-size:24px;font-weight:560;letter-spacing:-.6px;line-height:1.5;color:#e5f1e8;overflow-wrap:anywhere}
.stat small{font-size:12px;color:#8ea4a3}
.overview-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(280px,1fr);gap:16px}
.direction-list{display:flex;flex-direction:column}.direction{padding:12px 0;border-top:1px solid #25343b;display:flex;align-items:flex-start;gap:13px}
.direction-num{font:11px var(--mono);color:#86aaa5;margin-top:2px;min-width:26px}.direction-body{flex:1;min-width:0}
.direction-body strong{font:600 13px var(--mono)}.direction-body p{font-size:13px;color:#9db0ae;margin:3px 0 0;line-height:1.5;overflow-wrap:anywhere}
a.direction:hover strong{color:var(--teal)}
.principle-panel{background:linear-gradient(155deg,#14241f,#121d22 70%)}.split-concept{display:flex;flex-direction:column;gap:12px;margin-top:10px}
.concept{border-left:2px solid #68988d;padding:1px 0 1px 15px}.concept span{font:10px var(--mono);letter-spacing:1.2px;color:var(--teal)}
.concept strong{font-size:15px;font-weight:600;display:block;margin:6px 0}.concept p{font-size:13px;color:#a8bab4;margin:4px 0 0}
.run-concept{border-left-color:#c9b17d}.run-concept span{color:#d9be8f}.concept-divider{color:#536a65;margin-left:14px}
.principle-footer{border-top:1px solid #293f39;margin:18px 0 0;padding-top:14px;font-size:13px;color:#cbb88a}
.notwired h2{margin-top:12px}.links{padding-left:18px}
.notice{border-left:2px solid var(--gold);background:#1b1a14;padding:12px 14px;border-radius:4px;margin-bottom:16px;font-size:14px;color:#d8d4c4}
.choices-grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}.cards{display:grid;gap:10px}
.card{background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:12px 14px;min-width:0}
.card.flash{border-color:var(--teal);box-shadow:0 0 0 2px #9bd5c855}
.card.picked-accept{border-color:var(--teal)}.card.picked-reject{border-color:var(--coral)}.card.picked-defer{border-color:var(--soft)}
.card header{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.card header b{font-family:var(--mono)}
.badge{font:10px var(--mono);text-transform:uppercase;letter-spacing:1px;padding:3px 7px;border-radius:3px;border:1px solid #3a4a4d}
.badge.decision{color:var(--gold)}.badge.handoff{color:var(--blue)}.badge.challenge{color:var(--purple)}
.st,.date{font:12px var(--mono);color:#92a7a6}.date{margin-left:auto}
.txt{margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}
dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:3px 12px;margin:6px 0;font-size:13px}dt{color:#92a7a6}dd{margin:0;overflow-wrap:anywhere}
.hint{color:#9fb1b1;font-size:13px;margin:6px 0}.empty{color:#8ea4a3;font-style:italic}
.btns{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
.act{min-height:36px;padding:7px 14px;border-radius:5px;border:1px solid #39474b;background:#152026;font:600 12px var(--mono)}
.act.accept{color:var(--teal)}.act.reject{color:var(--coral)}.act.defer{color:var(--muted)}.act:hover{border-color:#59766c}
.apply{position:sticky;top:80px;padding:14px;border-color:#5c6b4b}
textarea,input,select{width:100%;background:#0a1013;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px}
textarea{min-height:260px;color:var(--teal);font:12px/1.45 var(--mono);resize:vertical}input,select{margin:4px 0 12px}
#copied{font-size:12px;color:var(--teal)}
.selector{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:12px}.sel[aria-pressed=true]{border-color:var(--teal);color:var(--teal)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:#0c1418;margin-bottom:12px}svg.topo{display:block}
.band{fill:#0e161b}.band.alt{fill:#111b21}.band-label{fill:#a0b4b1;font:600 12px var(--mono);letter-spacing:.06em}
.n-actor{fill:#183a33;stroke:var(--teal);stroke-width:2}.n-owner{fill:#3a3220;stroke:var(--gold);stroke-width:2}
.n-thread{fill:#1a2c3d;stroke:var(--blue);stroke-width:1.5}.n-capture{fill:#2a2540;stroke:var(--purple);stroke-width:1.5}
.n-label,.a-label{fill:var(--text);font:11px var(--mono);text-anchor:middle}
.e-handoff{fill:none;stroke:var(--gold);opacity:.85}.e-captured,.e-capture-of{fill:none;stroke:var(--purple);stroke-dasharray:3 3}
.m-handoff{fill:var(--gold)}.m-captured,.m-capture-of{fill:var(--purple)}
.e-count{fill:var(--gold);font:700 11px var(--mono);text-anchor:middle;paint-order:stroke;stroke:#0c1418;stroke-width:3}
.n-coord{fill:#183a33;stroke:var(--teal);stroke-width:2}.n-lane{fill:#1a2c3d;stroke:var(--blue);stroke-width:1.5}
.a-structure{fill:none;stroke:#6f858c;stroke-dasharray:5 4;stroke-width:1.5}.m-structure{fill:#6f858c}
.a-delegation{fill:none;stroke:var(--gold);stroke-width:1.8}.m-delegation{fill:var(--gold)}
.e-dec{fill:var(--gold);font:600 10px var(--mono);text-anchor:middle;paint-order:stroke;stroke:#0c1418;stroke-width:3}
.legend{font-size:13px;color:#a7b8b6}
.a-recorded{fill:none;stroke:var(--teal);stroke-width:2.2}.m-recorded{fill:var(--teal)}
.k-recorded{height:3px;width:22px;border-radius:0;background:var(--teal);vertical-align:3px}
.hyp{border:1px dashed #6b5f45;border-radius:8px;padding:12px;margin-top:10px;background:repeating-linear-gradient(135deg,#14181b,#14181b 8px,#161b1d 8px,#161b1d 16px)}
.hyp summary{color:var(--gold);font:600 12px var(--mono);letter-spacing:.06em}.hyp[open] summary{margin-bottom:10px}
.k{display:inline-block;width:14px;height:14px;vertical-align:-2px;margin:0 4px 0 10px;border-radius:50%}.k:first-child{margin-left:0}
.k-actor{background:#183a33;border:2px solid var(--teal)}.k-owner{background:#3a3220;border:2px solid var(--gold)}
.k-thread{background:#1a2c3d;border:2px solid var(--blue);border-radius:2px}.k-capture{background:#2a2540;border:2px solid var(--purple);transform:rotate(45deg);border-radius:1px;width:11px;height:11px}
.k-handoff,.k-delegation{height:3px;width:22px;border-radius:0;background:var(--gold);vertical-align:3px}
.k-captured{height:0;width:22px;border-radius:0;border-top:2px dashed var(--purple);vertical-align:3px}
.k-structure{height:0;width:22px;border-radius:0;border-top:2px dashed #6f858c;vertical-align:3px}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font:10px var(--mono);text-transform:uppercase;letter-spacing:1px;color:#92a7a6;border-bottom:1px solid var(--border);padding:7px 8px}
td{border-bottom:1px solid #1c2a31;padding:7px 8px;vertical-align:top;overflow-wrap:anywhere}td:first-child{font-family:var(--mono);white-space:nowrap}
details{margin:6px 0}summary{cursor:pointer;font:600 13px var(--mono)}summary b{color:var(--gold)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.budget-top{display:grid;grid-template-columns:240px minmax(0,1fr);gap:16px;align-items:start}
.principles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:6px 0 18px}
.pf-out{font:12px var(--mono);color:var(--gold);margin-top:12px}
.lane-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
dialog{background:var(--panel);color:var(--text);border:1px solid #5c6b4b;border-radius:9px;max-width:560px;width:calc(100% - 32px);padding:20px}
dialog::backdrop{background:#000a}dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px var(--mono);color:var(--teal);background:#0a1013;padding:10px;border-radius:6px;max-height:40vh;overflow:auto}
footer{color:#9fb1b1;font-size:13px;border-top:1px solid var(--border);margin:24px 0 0;padding-bottom:30px}footer ul{padding-left:18px}
@media (max-width:1000px){.choices-grid,.cols,.overview-grid,.budget-top{grid-template-columns:1fr}.apply{position:static;order:-1}
 .apply textarea{min-height:120px}
 .dials,.stats-row,.principles{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.shell{display:block}.sidebar{position:static;height:auto;padding:12px 16px;border-right:none;border-bottom:1px solid #1d2a32}
 .brand{margin-bottom:10px;padding:0}.nav-title,.sidebar-section,.side-quote,.version{display:none}
 .nav-links{flex-direction:row;overflow-x:auto;gap:4px;padding-bottom:4px}.nav-link{padding:8px 10px;white-space:nowrap}.nav-kbd{display:none}
 .topbar{position:static;padding:10px 16px}.section{padding:20px 16px 4px}.date-label{font-size:10px}
 table,thead,tbody,tr,td{display:block}thead{display:none}tr{border-bottom:1px solid var(--border);padding:4px 0}
 td{border:none;padding:2px 4px}td::before{content:attr(data-h) ": ";color:#92a7a6;font:10px var(--mono)}td:first-child{white-space:normal}
 dl{grid-template-columns:1fr}dt{margin-top:4px}}
@media (max-width:420px){.dials,.stats-row,.principles{grid-template-columns:1fr 1fr;gap:8px}.gauge figcaption b{font-size:20px}.stat strong{font-size:19px}}
"""

JS = """
(function(){
 var d=document,$=function(s){return [].slice.call(d.querySelectorAll(s))};d.documentElement.classList.add('js');
 var names={};$('.nav-link').forEach(function(a){names[a.getAttribute('data-goto')]=a.getAttribute('data-name')});
 function show(n){if(!names[n])n='overview';$('.surface').forEach(function(s){s.hidden=s.id!==n});
  $('.nav-link').forEach(function(a){a.classList.toggle('active',a.getAttribute('data-goto')===n)});
  d.getElementById('crumb').textContent=names[n].toUpperCase();window.scrollTo(0,0)}
 function go(n){show(n);try{history.replaceState(null,'','#'+n)}catch(e){}}
 function flash(key){var c=d.querySelector('.card[data-key="'+key.replace(/"/g,'')+'"]');if(c){c.scrollIntoView({block:'center'});c.classList.add('flash');setTimeout(function(){c.classList.remove('flash')},1600)}}
 $('[data-goto]').forEach(function(a){a.addEventListener('click',function(ev){ev.preventDefault();go(a.getAttribute('data-goto'));
  var k=a.getAttribute('data-card');if(k)flash(k)})});
 show((location.hash||'').slice(1));
 $('.sel').forEach(function(b){b.addEventListener('click',function(){var n=b.getAttribute('data-net');
  $('.sel').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});$('.net').forEach(function(p){p.hidden=p.getAttribute('data-net')!==n})})});
 $('.net').forEach(function(p){p.hidden=p.getAttribute('data-net')!=='info'});
 var picks={},box=d.getElementById('apply'),note=d.getElementById('copied'),dlg=d.getElementById('settle'),pending=null;
 function draw(){var k=Object.keys(picks).sort();box.value=k.map(function(x){return picks[x]}).join('\\n\\n')+(k.length?'\\n':'')}
 function pick(b){var k=b.getAttribute('data-key'),a=b.getAttribute('data-act');picks[k]=b.getAttribute('data-line');draw();
  var c=b.closest('.card');c.classList.remove('picked-accept','picked-reject','picked-defer');c.classList.add('picked-'+a);note.textContent=''}
 function ask(eyebrow,title,body,line,ok,cb){if(!dlg||!dlg.showModal){cb();return}
  d.getElementById('settle-eyebrow').textContent=eyebrow;d.getElementById('settle-title').textContent=title;
  d.getElementById('settle-body').textContent=body;d.getElementById('settle-line').textContent=line||'';
  d.getElementById('settle-line').hidden=!line;d.getElementById('settle-ok').textContent=ok;pending=cb;dlg.returnValue='';dlg.showModal()}
 if(dlg)dlg.addEventListener('close',function(){if(dlg.returnValue==='ok'&&pending)pending();pending=null});
 $('button[data-line]').forEach(function(b){b.addEventListener('click',function(){var a=b.getAttribute('data-act');
  if(a==='defer'){pick(b);return}
  ask('SETTLE THIS CHOICE?',(a==='accept'?'Accept ':'Reject ')+b.getAttribute('data-key').split(':')[1]+'?',
   'This adds a line to To apply. Nothing is written until you edit the placeholders, copy it, and run it yourself.',
   b.getAttribute('data-line'),'Add to To apply',function(){pick(b)})})});
 $('.torun').forEach(function(b){b.addEventListener('click',function(){var k=b.getAttribute('data-card');
  ask('MOVE FROM DRIFT TO RUN?',k.split(':')[1]+' is still a proposal.','A commitment needs your explicit authorization and a concrete next action. Nothing moves from here: open its card in Choices and compose the ledger line.',
   '','Open in Choices',function(){go('choices');flash(k)})})});
 d.getElementById('clear').addEventListener('click',function(){picks={};draw();note.textContent='';
  $('.card').forEach(function(c){c.classList.remove('picked-accept','picked-reject','picked-defer')})});
 d.getElementById('copy').addEventListener('click',function(){var t=box.value;function done(){note.textContent='copied'}
  function fb(){box.focus();box.select();try{d.execCommand('copy');done()}catch(e){note.textContent='select and copy by hand'}}
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,fb)}else{fb()}});
 var pf=d.getElementById('pf-go');if(pf)pf.addEventListener('click',function(){
  var rem=d.getElementById('preflight').getAttribute('data-remaining'),v=d.getElementById('pf-tokens').value,imp=d.getElementById('pf-imp').value,o=d.getElementById('pf-out');
  var est=v===''?null:Number(v),r=rem===''?null:Number(rem),route;
  if(est===null){route='UNKNOWN COST: '+(imp==='routine'?'reuse an existing session or evidence first.':'ask the coordinator for an estimate before delegating.')}
  else if(r===null){route='NO ALLOWANCE SET: the estimate cannot be compared. '+(imp==='high'?'Worth asking the owner.':'Prefer an existing session.')}
  else if(est>r){route='OVER: the estimate ('+est.toLocaleString()+') exceeds what is left this week ('+r.toLocaleString()+'). Ask the owner before spending.'}
  else if(est>r*0.5){route=(imp==='routine'?'HOLD: a routine question would take over half of what is left.':'PROCEED WITH CARE: over half of what is left; say so in the handoff.')}
  else{route='WITHIN: '+(imp==='routine'?'economy route, reuse existing context.':'proceed; record the actual use in budget.jsonl.')}
  o.textContent='ADVISORY · '+route});
})();
"""


# ---------------- writing ----------------
def build(root, out, lane=None, content="full", weekly_tokens=None, drift_days=None, coordinator=None,
          allow_public_dest=False, brand=None):
    root, out = pathlib.Path(root), pathlib.Path(out)
    if not root.is_dir():
        raise Refused(f"--root {root} isn't a folder")
    ex.check_destination(out, root, allow_public_dest)   # the export's guard: not in the records, not in a public repo
    o = out.resolve()
    if o.exists():
        if not o.is_dir():
            raise Refused(f"--out {o} exists and isn't a folder")
        foreign = sorted(x.name for x in o.iterdir() if x.name not in OUT_FILES)
        if foreign:
            raise Refused(f"--out {o} holds other files ({', '.join(foreign[:5])}); give a new or empty folder, "
                          "or one holding only a previous cockpit")
    data = collect(root, lane=lane, content=content, weekly_tokens=weekly_tokens, drift_days=drift_days,
                   coordinator=coordinator, brand=brand)
    page = render(data)
    o.mkdir(parents=True, exist_ok=True)
    for name, text in (("cockpit.json", json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n"),
                       ("index.html", page)):
        fd, tmp = tempfile.mkstemp(dir=o, prefix=".cockpit-")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, o / name)
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(prog="relay-kit cockpit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, type=pathlib.Path, help="the private records folder to read")
    ap.add_argument("--out", required=True, type=pathlib.Path, help="destination folder (new, or holding a previous cockpit)")
    ap.add_argument("--lane", help="lane name or code; default all lanes")
    ap.add_argument("--content", choices=["full", "metadata"], default="full",
                    help="metadata leaves out decision, challenge and handoff text (ids, states and actors only)")
    ap.add_argument("--weekly-tokens", type=int, help="weekly token allowance for the budget gauge")
    ap.add_argument("--drift-days", type=int, help="flag proposed decisions older than this (default 3, or governance/cockpit.json)")
    ap.add_argument("--coordinator", help="the coordinator lane (default: the lane whose name contains 'coord')")
    ap.add_argument("--brand", help="name shown in the header (default relay-kit, or 'brand' in governance/cockpit.json)")
    ap.add_argument("--allow-public-dest", action="store_true",
                    help="write even inside a git repository that is public or whose visibility can't be confirmed")
    a = ap.parse_args(argv)
    try:
        d = build(a.root, a.out, lane=a.lane, content=a.content, weekly_tokens=a.weekly_tokens,
                  drift_days=a.drift_days, coordinator=a.coordinator, allow_public_dest=a.allow_public_dest,
                  brand=a.brand)
    except Refused as err:
        sys.exit(f"refused: {err}")
    c = d["counts"]
    print(f"cockpit written to {pathlib.Path(a.out).resolve()}/index.html ({SCHEMA}), records as of {d['as_of']}: "
          f"{c['choices']} waiting on the owner, {c['authorized_in_progress']} authorized handoffs in progress "
          f"({c['pending_authorization']} pending authorization), {c['drift_flags']} drift flags, "
          f"budget {'present' if d['budget']['present'] else 'absent'}")


if __name__ == "__main__":
    main()
