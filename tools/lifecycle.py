"""relay-kit/lifecycle@1: what the decision and challenge ledgers say is open, settled, in force or
contested. One pure module, used by check_ledgers, status, export, mcp and cockpit, so every reader
draws the same conclusions from the same lines. Standard library only; reads nothing itself.

    resolve(decisions, challenges, as_of=None) -> {"decisions": {id: D}, "challenges": {id: C},
                                                   "problems": [P], "conflicts": [X]}

Rules (docs/lifecycle.md):

- A `supersedes` takes effect only from a settled line. `adopted` closes any earlier decision
  (a proposal is settled by it; an adopted decision is replaced). `rejected` and `superseded`
  settle a *proposal* only: they cannot retire an adopted decision; that takes an adopted line
  that supersedes or `revokes` it. A `proposed` line that supersedes is a proposed replacement:
  the target stays as it was and is shown with it.
- A challenge `resolves` takes effect only from a settled line (`resolved` or `accepted-risk`);
  an open challenge carrying `resolves` is a problem and closes nothing.
- A transition must point at an earlier, different, known record (no self, forward or unknown
  reference, so no cycles). An invalid transition is reported and has no effect.
- More than one effective transition on the same record is a conflict: the record is closed, and
  every closer is kept, with the conflict reported, never collapsed to one.
- Applicability (finding 2): an adopted decision is `standing` ("adopted, no structured supersession
  found") unless an effective later line `supersedes` it (`superseded`) or `revokes` it (`revoked`),
  or its own `effective_until` is before `as_of` (`expired`) or its `effective_from` after it
  (`not yet effective`). `amends` (an id, or {"id", "clauses"}) records a partial amendment: the
  target stays standing and lists what amends it. Without `as_of`, dates are not evaluated.
"""
import re

SCHEMA = "relay-kit/lifecycle@1"
SETTLES_PROPOSAL = {"adopted", "rejected", "superseded"}
RETIRES_ADOPTED = {"adopted"}
CH_SETTLED = {"resolved", "accepted-risk"}
STANDING = "adopted, no structured supersession found"
WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?Z)?$")


def num(rid):
    m = re.search(r"(\d+)$", str(rid or ""))
    return int(m.group(1)) if m else None


def refs(v):
    """A field that names one id or several: 'DEC-1', ['DEC-1', 'DEC-2'] or {"id": ..., "clauses": [...]}."""
    if isinstance(v, str):
        return [(v, None)]
    if isinstance(v, dict) and isinstance(v.get("id"), str):
        cl = v.get("clauses")
        return [(v["id"], [str(c) for c in cl] if isinstance(cl, list) else None)]
    if isinstance(v, list):
        return [r for x in v for r in refs(x)]
    return []


def _as_time(when, end=False):
    """'2026-11-04' -> '2026-11-04T00:00:00Z' (or T23:59:59Z as an end); a time is kept, seconds added."""
    if not isinstance(when, str) or not WHEN.match(when):
        return None
    if len(when) == 10:
        return when + ("T23:59:59Z" if end else "T00:00:00Z")
    return when if when.count(":") == 2 else when[:-1] + ":00Z"


def resolve(decisions, challenges, as_of=None):
    """decisions, challenges: ledger lines (dicts) in file order. as_of: ISO time or None."""
    probs, conflicts = [], []
    D, order = {}, {}
    for i, r in enumerate(decisions):
        rid = r.get("id")
        if not isinstance(rid, str) or rid in D:
            continue
        order[rid] = i
        D[rid] = {"id": rid, "status": r.get("status"), "closed_by": [], "closure": None,
                  "proposed_replacements": [], "amended_by": [], "revoked_by": [],
                  "effective_from": r.get("effective_from"), "effective_until": r.get("effective_until")}

    def bad(rid, field, target, rule, msg):
        probs.append({"id": rid, "field": field, "target": target, "rule": rule, "message": msg})

    def check_ref(rid, field, target, table, earlier_than):
        if target == rid:
            bad(rid, field, target, "self", f"{rid} {field} itself")
            return False
        if target not in table:
            bad(rid, field, target, "unknown", f"{rid} {field} {target}, which isn't in the ledger")
            return False
        if earlier_than(target, rid):
            return True
        bad(rid, field, target, "forward", f"{rid} {field} {target}, which isn't earlier")
        return False

    dec_earlier = lambda t, rid: order[t] < order[rid] and (num(t) or 0) < (num(rid) or 0)
    for r in decisions:
        rid, st = r.get("id"), r.get("status")
        if rid not in D or order.get(rid) is None:
            continue
        for target, _ in refs(r.get("supersedes")):
            if not check_ref(rid, "supersedes", target, D, dec_earlier):
                continue
            t = D[target]
            if st == "proposed":
                t["proposed_replacements"].append(rid)
            elif st in RETIRES_ADOPTED or (st in SETTLES_PROPOSAL and t["status"] == "proposed"):
                t["closed_by"].append({"id": rid, "status": st})
            else:
                bad(rid, "supersedes", target, "ineffective",
                    f"{rid} is {st} and supersedes {target}, which is {t['status']}: a {st} line settles a proposal only; "
                    f"retiring an adopted decision takes an adopted line that supersedes or revokes it")
        for target, _ in refs(r.get("revokes")):
            if not check_ref(rid, "revokes", target, D, dec_earlier):
                continue
            if st != "adopted":
                bad(rid, "revokes", target, "ineffective", f"{rid} is {st}: only an adopted line revokes")
            elif D[target]["status"] != "adopted":
                bad(rid, "revokes", target, "ineffective", f"{rid} revokes {target}, which isn't adopted")
            else:
                D[target]["revoked_by"].append({"id": rid, "from": r.get("effective_from") or r.get("date")})
        for target, clauses in refs(r.get("amends")):
            if not check_ref(rid, "amends", target, D, dec_earlier):
                continue
            if st == "adopted":
                D[target]["amended_by"].append({"id": rid, "clauses": clauses})
        for f in ("effective_from", "effective_until"):
            if f in r and not _as_time(r.get(f)):
                bad(rid, f, None, "format", f"{rid} {f} must be YYYY-MM-DD or an ISO time in UTC")

    now = _as_time(as_of) if as_of else None
    for rid, d in D.items():
        closers = d["closed_by"]
        statuses = sorted({c["status"] for c in closers})
        if len(closers) > 1:
            conflicts.append({"id": rid, "kind": "decision", "closed_by": [c["id"] for c in closers], "statuses": statuses})
        d["closure"] = None if not closers else statuses[0] if len(statuses) == 1 else "conflict"
        d["conflict"] = len(closers) > 1
        st = d["status"]
        if st == "proposed":
            d["applicability"] = "settled" if closers else "open"
        elif st == "adopted":
            frm, until = _as_time(d["effective_from"]), _as_time(d["effective_until"], end=True)
            revoked = [x for x in d["revoked_by"] if not now or not _as_time(x["from"]) or _as_time(x["from"]) <= now]
            if any(c["status"] == "adopted" for c in closers):
                d["applicability"] = "superseded"
            elif revoked:
                d["applicability"] = "revoked"
            elif now and until and until < now:
                d["applicability"] = "expired"
            elif now and frm and frm > now:
                d["applicability"] = "not yet effective"
            else:
                d["applicability"] = STANDING
            d["dates_evaluated"] = bool(now)
        else:
            d["applicability"] = f"not a standing decision ({st})"
        d["in_force"] = d["applicability"] == STANDING
        d["open"] = d["applicability"] == "open"

    C, corder = {}, {}
    for i, r in enumerate(challenges):
        rid = r.get("id")
        if not isinstance(rid, str) or rid in C:
            continue
        corder[rid] = i
        C[rid] = {"id": rid, "status": r.get("status"), "closed_by": [], "closure": None}
    ch_earlier = lambda t, rid: corder[t] < corder[rid] and (num(t) or 0) < (num(rid) or 0)
    for r in challenges:
        rid, st = r.get("id"), r.get("status")
        if rid not in C:
            continue
        for target, _ in refs(r.get("resolves")):
            if not check_ref(rid, "resolves", target, C, ch_earlier):
                continue
            if st in CH_SETTLED:
                C[target]["closed_by"].append({"id": rid, "status": st})
            else:
                bad(rid, "resolves", target, "ineffective",
                    f"{rid} is {st} and resolves {target}: only a resolved or accepted-risk line closes a challenge")
    for rid, c in C.items():
        closers = c["closed_by"]
        statuses = sorted({x["status"] for x in closers})
        if len(closers) > 1:
            conflicts.append({"id": rid, "kind": "challenge", "closed_by": [x["id"] for x in closers], "statuses": statuses})
        c["closure"] = None if not closers else statuses[0] if len(statuses) == 1 else "conflict"
        c["conflict"] = len(closers) > 1
        c["open"] = c["status"] == "open" and not closers
    return {"schema": SCHEMA, "as_of": as_of, "decisions": D, "challenges": C, "problems": probs, "conflicts": conflicts}
