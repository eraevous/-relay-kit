"""Tests for tools/cockpit.py (relay-kit/cockpit@1). Standard library only; synthetic data only.

Run alone with `python3 -m unittest discover -s tests`, or as part of tests/run_tests.py, which
reports each test by the first line of its docstring ("cockpit: ...")."""
import hashlib, importlib.util, json, os, pathlib, shlex, shutil, subprocess, sys, tempfile, unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("relaykit_cockpit_under_test", KIT / "tools" / "cockpit.py")
ck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ck)

HOSTILE = "</textarea></script><script>alert(1)</script><img src=x onerror=alert(2)> \"q\" 'a' & é"
BODY_DEC = "BODYDEC Adopt the synthetic plan."
BODY_CH = "BODYCH The sample may drift from the plan."
TITLE_HO = "BODYHO Draft the synthetic report"


def handoff(hid, status, frm, to, title, authorized, log):
    return f"""---
id: {hid}
title: {title}
from: {frm}
to: {to}
created: {log[0][0]}
status: {status}
contract: relay-kit/handoff@2
source: none
last_processed: none
reply_to:
---

## Task
Synthetic task body BODYTASK.

## Authorized
{authorized}

## Proposals
- none

## Context
- none

## Expected output
Write the result to outputs/{hid}.md.

## Limits
- none

## Log
""" + "".join(f"- {at} | {st} | {who} | {note}\n" for at, st, who, note in log)


HO_DONE = handoff("HO-RS-20261101-AB12", "completed", "claude:research", "chatgpt:review", "Review the synthetic draft",
                  '- Quote: "go ahead" (sam, synthetic, 2026-11-01)',
                  [("2026-11-01T12:00Z", "prepared", "claude:research#SESSA1", "drafted"),
                   ("2026-11-01T12:05Z", "sent", "claude:research#SESSA1", "pasted"),
                   ("2026-11-01T13:00Z", "completed", "claude:research#SESSA1 for chatgpt:review", "outputs/HO-RS-20261101-AB12.md")])
HO_WAIT = handoff("HO-RS-20261103-CD34", "prepared", "claude:research", "chatgpt:review", TITLE_HO,
                  "- TODO: the owner's words",
                  [("2026-11-03T09:00Z", "prepared", "claude:research#SESSA1", "drafted")])
HO_SENT = handoff("HO-RV-20261103-EF56", "sent", "claude:review", "claude:research", "Check the figures",
                  '- Quote: "yes" (sam, synthetic, 2026-11-03)',
                  [("2026-11-03T10:00Z", "prepared", "claude:review#SESSR9", "drafted"),
                   ("2026-11-03T10:05Z", "sent", "claude:review#SESSR9", "pasted")])

DECISIONS = [
    {"id": "DEC-001", "date": "2026-11-01", "status": "adopted", "decision": "The owner delegates merging research PRs to the research lane.",
     "scope": "research", "decider": "sam", "basis": "\"yes\"", "locator": "synthetic chat", "recorded_by": "claude:research#SESSA1"},
    {"id": "DEC-002", "date": "2026-11-02", "status": "proposed", "decision": BODY_DEC + " " + HOSTILE, "scope": "research",
     "proposed_by": "claude:research#SESSA1", "recorded_by": "claude:research#SESSA1"},
    {"id": "DEC-003", "date": "2026-11-02", "status": "proposed", "decision": "Settled already.", "scope": "research",
     "recorded_by": "claude:research#SESSA1"},
    {"id": "DEC-004", "date": "2026-11-03", "status": "adopted", "supersedes": "DEC-003", "decision": "Settled already.",
     "scope": "research", "decider": "sam", "basis": "\"ok\"", "locator": "synthetic chat", "recorded_by": "sam"},
]
RECORDED = {"id": "DEC-005", "date": "2026-11-03", "status": "adopted", "decision": "Review may close its own handoffs.",
            "scope": "review", "delegates_to": "review", "decider": "sam", "basis": "\"fine\"", "locator": "synthetic chat",
            "recorded_by": "sam"}
CHALLENGES = [
    {"id": "CH-001", "date": "2026-11-02", "status": "open", "concern": BODY_CH, "raised_by": "claude:review#SESSR9"},
    {"id": "CH-002", "date": "2026-11-02", "status": "open", "concern": "Closed later.", "raised_by": "claude:review#SESSR9"},
    {"id": "CH-003", "date": "2026-11-03", "status": "resolved", "resolves": "CH-002", "concern": "Closed later.",
     "raised_by": "claude:review#SESSR9", "resolution": "fixed", "recorded_by": "sam"},
]
THREADS = """# Coordination index

## Lanes

### Research
| | Where | Status |
|---|---|---|
| Ada (ChatGPT) | [RS] coord: https://chatgpt.com/c/00000000-0000-0000-0000-00000000000a | **active** |

### Review
| | Where | Status |
|---|---|---|
| Claude | review session: https://claude.ai/code/session_SESSR9 | active |

## Intake
- idea intake thread: https://chatgpt.com/c/00000000-0000-0000-0000-0000000000ff

## Change log
- 2026-11-03 | claude:research#SESSA1 | PR https://github.com/example/synthetic/pull/7 opened
"""
BUDGET = [
    {"ts": "2026-11-03T08:00:00Z", "actor": "claude:research#SESSA1", "window": "session", "tokens_in": 1000, "tokens_out": 500, "agents": 2, "note": "n1"},
    {"ts": "2026-10-20T08:00:00Z", "actor": "claude:research#SESSA1", "window": "session", "tokens_in": 9999, "tokens_out": 0, "agents": 1, "note": "old"},
]


def build_root(base, budget=False, recorded=False):
    r = pathlib.Path(base) / "records"
    (r / "handoffs" / "outputs").mkdir(parents=True)
    (r / "governance").mkdir()
    (r / "handoffs" / ".ledger.json").write_text(json.dumps({"owner": "sam", "lanes": {"research": "RS", "review": "RV"}}), encoding="utf-8")
    for text, hid in ((HO_DONE, "HO-RS-20261101-AB12"), (HO_WAIT, "HO-RS-20261103-CD34"), (HO_SENT, "HO-RV-20261103-EF56")):
        (r / "handoffs" / f"{hid}.md").write_text(text, encoding="utf-8")
    (r / "handoffs" / "outputs" / "HO-RS-20261101-AB12.md").write_text("HO-RS-20261101-AB12 · synthetic\n\nok\n", encoding="utf-8")
    (r / "governance" / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in DECISIONS + ([RECORDED] if recorded else [])), encoding="utf-8")
    (r / "governance" / "challenges.jsonl").write_text("".join(json.dumps(c) + "\n" for c in CHALLENGES), encoding="utf-8")
    (r / "threads.md").write_text(THREADS, encoding="utf-8")
    if budget:
        (r / "governance" / "budget.jsonl").write_text("".join(json.dumps(b) + "\n" for b in BUDGET) + "not json\n", encoding="utf-8")
    return r


def tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(pathlib.Path(root).rglob("*")):
        h.update(p.relative_to(root).as_posix().encode())
        if p.is_file():
            h.update(p.read_bytes())
            h.update(str(p.stat().st_mtime_ns).encode())
    return h.hexdigest()


class CockpitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-cockpit-"))
        self.root = build_root(self.tmp)
        self._vis = ck.ex.repo_visibility
        ck.ex.repo_visibility = lambda repo: "unknown"   # no network in tests

    def tearDown(self):
        ck.ex.repo_visibility = self._vis
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, name="out", root=None, **kw):
        out = self.tmp / name
        data = ck.build(root or self.root, out, **kw)
        return data, (out / "index.html").read_text(encoding="utf-8"), (out / "cockpit.json").read_text(encoding="utf-8")

    def test_page_builds(self):
        """cockpit: the page builds self-contained, with every surface, and its data matches cockpit.json"""
        data, page, js = self.build()
        self.assertEqual(json.loads(js)["schema"], "relay-kit/cockpit@1")
        for sid in ("overview", "intake", "questions", "choices", "topology", "budget", "lanes", "run", "review"):
            self.assertIn(f'id="{sid}"', page)
        for bad in ("<link", "src=\"http", "src='http", "@import", "url(http"):
            self.assertNotIn(bad, page)
        self.assertEqual(page.count("<script"), 1)
        self.assertIn("generated from records as of 2026-11-03T10:05Z", page)   # the latest record, not the clock
        self.assertIn("not wired to records yet".upper(), page)
        self.assertIn("https://chatgpt.com/c/00000000-0000-0000-0000-0000000000ff", page)   # intake link from threads.md
        self.assertEqual(data["run"]["pr_links"], ["https://github.com/example/synthetic/pull/7"])

    def test_choices_exact(self):
        """cockpit: Choices lists exactly the proposed decision, the owner-waiting handoff and the open challenge"""
        data, page, _ = self.build()
        got = sorted((c["kind"], c["id"]) for c in data["choices"])
        self.assertEqual(got, [("challenge", "CH-001"), ("decision", "DEC-002"), ("handoff", "HO-RS-20261103-CD34")])
        self.assertEqual(page.count('class="card choice"'), 3)

    def test_composed_line(self):
        """cockpit: a composed ledger line parses as JSON with the referenced id and the chosen status, and passes the ledger check"""
        data, _, _ = self.build()
        dec = next(c for c in data["choices"] if c["id"] == "DEC-002")
        ch = next(c for c in data["choices"] if c["id"] == "CH-001")
        for item, action, key, status in ((dec, "accept", "supersedes", "adopted"), (dec, "reject", "supersedes", "rejected"),
                                          (ch, "accept", "resolves", "accepted-risk"), (ch, "reject", "resolves", "resolved")):
            argv = shlex.split(item["compose"][action]["command"])
            self.assertEqual(argv[:5], ["relay-kit", "ledger", "append", "--dir", "governance"])
            obj = json.loads(argv[-1])
            self.assertEqual((obj[key], obj["status"]), (item["id"], status))
            self.assertEqual(obj, item["compose"][action]["json"])
        self.assertTrue(dec["compose"]["defer"]["command"].startswith("# DEC-002"))
        ho = next(c for c in data["choices"] if c["kind"] == "handoff")
        self.assertIn("set HO-RS-20261103-CD34 sent --note", ho["compose"]["accept"]["command"])
        self.assertIn("set HO-RS-20261103-CD34 cancelled --note", ho["compose"]["reject"]["command"])
        self.assertNotIn("--by", ho["compose"]["accept"]["command"])   # the actor comes from whoever runs it
        acc = json.loads(shlex.split(dec["compose"]["accept"]["command"])[-1])
        self.assertNotIn("recorded_by", acc)
        for f in ("decider", "basis", "locator"):
            self.assertIn("<<REQUIRED", acc[f])
        # once the owner replaces the placeholders, the line (with the id and date ledger_append fills) is valid
        g = self.tmp / "gov"
        g.mkdir()
        filled = dict(acc, id="DEC-005", date="2026-11-04", decider="sam", basis='"adopt it"', locator="synthetic chat", recorded_by="sam")
        (g / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in DECISIONS + [filled]), encoding="utf-8")
        (g / "challenges.jsonl").write_text("".join(json.dumps(c) + "\n" for c in CHALLENGES), encoding="utf-8")
        p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "check_ledgers.py"), "--dir", str(g)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_unedited_paste_refused(self):
        """cockpit: a composed line pasted unedited is refused by ledger_append (its <<REQUIRED placeholders fail the ledger check)"""
        data, _, _ = self.build()
        rec = self.tmp / "rec"
        (rec / "governance").mkdir(parents=True)
        shutil.copy(self.root / "governance" / "decisions.jsonl", rec / "governance")
        shutil.copy(self.root / "governance" / "challenges.jsonl", rec / "governance")
        env = dict(os.environ, RELAYKIT_ACTOR="sam", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "."], ["git", "commit", "-q", "-m", "synthetic"]):
            subprocess.run(cmd, cwd=rec, check=True, capture_output=True, env=env)
        before = (rec / "governance" / "decisions.jsonl").read_bytes(), (rec / "governance" / "challenges.jsonl").read_bytes()
        for item in data["choices"]:
            if item["kind"] == "handoff":
                continue
            for action in ("accept", "reject"):
                argv = shlex.split(item["compose"][action]["command"])
                p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "ledger_append.py"), "--offline"] + argv[3:],
                                   cwd=rec, capture_output=True, text=True, env=env)
                self.assertNotEqual(p.returncode, 0, f"{item['id']} {action}: {p.stdout}")
                self.assertIn("<<REQUIRED", p.stdout + p.stderr)
        self.assertEqual(before, ((rec / "governance" / "decisions.jsonl").read_bytes(), (rec / "governance" / "challenges.jsonl").read_bytes()))

    def test_run_commitments(self):
        """cockpit: Run commitments count only authorized handoffs in progress; prepared or unauthorized ones are pending"""
        data, page, _ = self.build()
        c, com = data["counts"], data["run"]["commitments"]
        self.assertEqual((c["authorized_in_progress"], c["pending_authorization"]), (1, 1))
        self.assertEqual([x["id"] for x in com["authorized"]], ["HO-RV-20261103-EF56"])
        self.assertEqual([x["id"] for x in com["pending"]], ["HO-RS-20261103-CD34"])
        self.assertIn("run commitments / authorized", page)

    def test_topology_edges(self):
        """cockpit: information topology handoff edges match the planted handoffs; delegations are inferred from decisions"""
        data, page, _ = self.build()
        edges = {(x["from"], x["to"], tuple(x["refs"])) for x in data["topology"]["information"]["edges"] if x["type"] == "handoff"}
        self.assertEqual(edges, {("claude:research", "chatgpt:review", ("HO-RS-20261101-AB12", "HO-RS-20261103-CD34")),
                                 ("claude:review", "claude:research", ("HO-RV-20261103-EF56",))})
        auth = data["topology"]["authority"]
        self.assertEqual(auth["recorded"]["edges"], [])   # no decision carries delegates_to
        self.assertIn("No recorded delegation", page)
        dele = [x for x in auth["hypothesised"]["edges"] if x["kind"] == "delegation"]
        self.assertEqual([(x["decision"], x["to"], x["inferred"]) for x in dele], [("DEC-001", "lane:research", True)])
        self.assertIn('<details class="hyp" id="hyp">', page)   # inferred overlay: closed unless opened
        self.assertNotIn('<details class="hyp" id="hyp" open', page)
        self.assertFalse(auth["executable"])

    def test_recorded_delegation(self):
        """cockpit: only an adopted decision with delegates_to draws a recorded delegation edge"""
        root = build_root(self.tmp / "r", recorded=True)
        data, page, _ = self.build(root=root)
        rec = data["topology"]["authority"]["recorded"]["edges"]
        self.assertEqual([(x["decision"], x["from"], x["to"], x["inferred"]) for x in rec], [("DEC-005", "owner", "lane:review", False)])
        self.assertNotIn("No recorded delegation", page)

    def test_budget_absent(self):
        """cockpit: without budget.jsonl the gauge says there are no budget records yet and shows the schema"""
        data, page, _ = self.build()
        self.assertFalse(data["budget"]["present"])
        self.assertIn("no budget records yet", page)
        self.assertIn("tokens_out", page)

    def test_budget_present(self):
        """cockpit: with budget.jsonl the gauge shows 7-day burn against the weekly allowance, by lane and actor"""
        root = build_root(self.tmp / "b", budget=True)
        data, page, _ = self.build(root=root, weekly_tokens=10000)
        b = data["budget"]
        self.assertEqual((b["burn_7d"], b["fraction"], b["records"]), (1500, 0.15, 2))
        self.assertIsNone(b["weekly_reference"])
        self.assertEqual(b["by_lane"], {"research": 1500})
        self.assertEqual([x["line"] for x in b["invalid_lines"]], [3])
        self.assertFalse(b["status_file_costs_parsed"])
        self.assertIn("15%", page)

    def test_budget_weekly_reference(self):
        """cockpit: a weekly reading is a labelled reference and never replaces the session sum; no session lines means no reading, not zero"""
        root = build_root(self.tmp / "w")
        (root / "governance" / "budget.jsonl").write_text(json.dumps(
            {"ts": "2026-11-03T09:00:00Z", "actor": "sam", "window": "weekly", "tokens_in": 7000, "tokens_out": 0, "agents": None, "note": "plan page"}) + "\n", encoding="utf-8")
        data, page, _ = self.build(root=root, weekly_tokens=10000)
        b = data["budget"]
        self.assertIsNone(b["burn_7d"])
        self.assertIsNone(b["fraction"])
        self.assertEqual(b["weekly_reference"]["tokens"], 7000)
        self.assertIn("no session lines in the window", page)
        self.assertIn("reference only", page)

    def test_budget_append(self):
        """cockpit: budget-append adds one valid line the cockpit reads"""
        g = self.root / "governance"
        p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "budget_append.py"), "--dir", str(g), "--window", "session",
                            "--tokens-in", "10", "--tokens-out", "5", "--actor", "claude:research#SESSA1",
                            "--ts", "2026-11-03T11:00:00Z"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        data, _, _ = self.build()
        self.assertEqual((data["budget"]["records"], data["budget"]["burn_7d"]), (1, 15))

    def test_metadata_mode(self):
        """cockpit: --content metadata leaves out decision, challenge and handoff bodies (ids and states only)"""
        data, page, js = self.build(content="metadata")
        for text in (page, js):
            for body in ("BODYDEC", "BODYCH", "BODYHO", "BODYTASK", "alert(1)"):
                self.assertNotIn(body, text)
        self.assertIn("DEC-002", page)
        self.assertIn("HO-RS-20261103-CD34", page)

    def test_hostile_strings(self):
        """cockpit: hostile strings in the records are escaped in the page"""
        _, page, _ = self.build()
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("</textarea></script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertEqual(page.count("<script"), 1)

    def test_refuse_public(self):
        """cockpit: a destination inside a public GitHub repository or inside the records is refused"""
        repo = self.tmp / "pub"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        with open(repo / ".git" / "config", "a", encoding="utf-8") as f:
            f.write('[remote "origin"]\n\turl = https://github.com/example/public-repo.git\n')
        ck.ex.repo_visibility = lambda r: "public"
        with self.assertRaises(ck.Refused):
            ck.build(self.root, repo / "site")
        self.assertFalse((repo / "site").exists())
        with self.assertRaises(ck.Refused):
            ck.build(self.root, self.root / "site")

    def test_deterministic(self):
        """cockpit: two runs give byte-identical output and leave the records untouched"""
        before = tree_hash(self.root)
        _, p1, j1 = self.build("one")
        _, p2, j2 = self.build("two")
        self.assertEqual((p1, j1), (p2, j2))
        self.assertEqual(before, tree_hash(self.root))


if __name__ == "__main__":
    unittest.main()
