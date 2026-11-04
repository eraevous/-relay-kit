"""Tests for tools/export.py (relay-kit/export@1). Standard library only; synthetic data only.

Run alone with `python3 -m unittest discover -s tests`, or as part of tests/run_tests.py, which
reports each test by the first line of its docstring ("export: ...")."""
import hashlib, importlib.util, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("relaykit_export_under_test", KIT / "tools" / "export.py")
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)

URL_A = "https://chatgpt.com/c/00000000-0000-0000-0000-00000000000a"
URL_B = "https://chatgpt.com/c/00000000-0000-0000-0000-00000000000b"
URL_UNCAPTURED = "https://chatgpt.com/c/00000000-0000-0000-0000-00000000000c"


def msg(mid, mid_id, ts, text, model=None):
    return {"mid": mid, "id": mid_id, "ts": ts, "model": model, "dictated": False, "text": text, "sources": []}


def capture(cid, actor, turns, hidden=None):
    return {"schema": "relay-kit/conversation@1",
            "meta": {"title": f"Synthetic {cid[-1]}", "platform": "chatgpt", "conversation_id": cid,
                     "source_url": f"https://chatgpt.com/c/{cid}", "checks": ["ok"], "actor": actor, "lane": "research",
                     "raw_sha256": "0" * 64},
            "hidden_counts": hidden or {}, "turns": turns}


CONV_A = capture("00000000-0000-0000-0000-00000000000a", "claude:research#SESSA1", [
    {"n": 1, "role": "user", "label": "You", "messages": [msg("T1u", "u-1", "2026-11-01T10:00:00Z", "Plan the survey.\n  Keep  spacing\texactly.")]},
    {"n": 1, "role": "assistant", "label": "ChatGPT", "messages": [
        msg("T1a", "a-1", "2026-11-01T10:00:30Z", "Here is a plan.", "gpt-x"),
        msg("T1a2", "a-2", "2026-11-01T10:00:40Z", "And a second part — with unicode é.", "gpt-x")]},
    {"n": 2, "role": "user", "label": "You", "messages": [msg("T2u", "u-2", "2026-11-01T11:00:00Z",
        "FROM: Ada (ChatGPT) T1a\nA relayed reply from another thread.\nEND FROM")]},
    {"n": 2, "role": "assistant", "label": "ChatGPT", "messages": [msg("T2a", "a-3", None, "Reply with no timestamp.", "gpt-x")]},
], hidden={"assistant:tool_use": 2})
CONV_B = capture("00000000-0000-0000-0000-00000000000b", "claude:research#SESSB2", [
    {"n": 1, "role": "user", "label": "You", "messages": [msg("T1u", "u-1", "2026-11-03T09:00:00Z", "Second session opener.")]},
    {"n": 1, "role": "assistant", "label": "ChatGPT", "messages": [msg("T1a", "a-1", "2026-11-03T09:01:00Z", "Second session reply.", "gpt-y")]},
])

HANDOFF_DONE = """---
id: HO-RS-20261101-AB12
title: Review the synthetic survey draft
from: claude:research
to: chatgpt:review
created: 2026-11-01T12:00Z
status: completed
contract: relay-kit/handoff@2
source: none
last_processed: none
reply_to:
---

## Task
Review the draft.

## Authorized
- Quote: "go ahead" (sam, synthetic, 2026-11-01)

## Proposals
- none

## Context
- none

## Expected output
Write the result to outputs/HO-RS-20261101-AB12.md.

## Limits
- none

## Log
- 2026-11-01T12:00Z | prepared | claude:research#SESSA1 | drafted
- 2026-11-01T12:05Z | sent | claude:research#SESSA1 | pasted
- 2026-11-01T13:00Z | completed | claude:research#SESSA1 for chatgpt:review | outputs/HO-RS-20261101-AB12.md
"""
HANDOFF_OPEN = HANDOFF_DONE.replace("HO-RS-20261101-AB12", "HO-RS-20261102-CD34").replace(
    "status: completed", "status: sent").replace("from: claude:research", "from: claude:review").split(
    "- 2026-11-01T13:00Z")[0].replace("claude:research#SESSA1", "claude:review#SESSR9")

DECISIONS = [
    {"id": "DEC-001", "date": "2026-11-01", "status": "adopted", "decision": "Use the synthetic survey.", "scope": "research",
     "decider": "owner", "basis": "\"yes\"", "locator": "synthetic chat", "recorded_by": "claude:research#SESSA1"},
    {"id": "DEC-002", "date": "2026-11-02", "status": "proposed", "decision": "Add a second survey.", "scope": "research",
     "proposed_by": "chatgpt:review"},
]
CHALLENGES = [{"id": "CH-001", "date": "2026-11-02", "status": "open", "concern": "Sample is small.", "raised_by": "claude:research#SESSB2"}]
THREADS = f"""# Coordination index

## Lanes

### Research
| | Where | Status |
|---|---|---|
| Ada (ChatGPT) | [RS] coord: {URL_A} | **active** |
| Ada (ChatGPT) | [RS] second: {URL_B} | active |
| Ada (ChatGPT) | [RS] archive: {URL_UNCAPTURED} | archive |
| Claude | review session: https://claude.ai/code/session_SESSR9 | active |

## Change log
- 2026-11-01 | claude:research#SESSA1 | created
"""


def build_root(base):
    r = pathlib.Path(base) / "records"
    (r / "handoffs" / "outputs").mkdir(parents=True)
    (r / "governance").mkdir()
    (r / "captures" / "nested").mkdir(parents=True)
    (r / "handoffs" / ".ledger.json").write_text(json.dumps({"owner": "sam", "lanes": {"research": "RS", "review": "RV"}}), encoding="utf-8")
    (r / "handoffs" / "HO-RS-20261101-AB12.md").write_text(HANDOFF_DONE, encoding="utf-8")
    (r / "handoffs" / "HO-RS-20261102-CD34.md").write_text(HANDOFF_OPEN, encoding="utf-8")
    (r / "handoffs" / "outputs" / "HO-RS-20261101-AB12.md").write_text("HO-RS-20261101-AB12 · chatgpt:review · synthetic\n\nLooks fine.\n", encoding="utf-8")
    (r / "governance" / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in DECISIONS), encoding="utf-8")
    (r / "governance" / "challenges.jsonl").write_text("".join(json.dumps(c) + "\n" for c in CHALLENGES), encoding="utf-8")
    (r / "threads.md").write_text(THREADS, encoding="utf-8")
    (r / "captures" / "a.json").write_text(json.dumps(CONV_A, ensure_ascii=False, indent=1), encoding="utf-8")
    (r / "captures" / "nested" / "b.json").write_text(json.dumps(CONV_B), encoding="utf-8")
    (r / "captures" / "other.json").write_text(json.dumps({"not": "a conversation"}), encoding="utf-8")
    return r


def tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(pathlib.Path(root).rglob("*")):
        h.update(p.relative_to(root).as_posix().encode())
        if p.is_file():
            h.update(p.read_bytes())
            h.update(str(p.stat().st_mtime_ns).encode())
    return h.hexdigest()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-export-"))
        self.root = build_root(self.tmp)
        self._vis = ex.repo_visibility
        ex.repo_visibility = lambda repo: "unknown"   # no network in tests; each test sets what it needs

    def tearDown(self):
        ex.repo_visibility = self._vis
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_export(self, name="out", **kw):
        out = self.tmp / name
        m = ex.export(self.root, out, **kw)
        f = out / ("records.json" if kw.get("fmt") == "json" else "records.jsonl")
        text = f.read_text(encoding="utf-8")
        recs = json.loads(text) if kw.get("fmt") == "json" else [json.loads(l) for l in text.splitlines()]
        return m, recs, out

    def test_two_actors(self):
        """export: two actors in one lane export separately and together without id collisions"""
        _, a, _ = self.run_export("a", actors=["claude:research#SESSA1"], kinds=["conversations"])
        _, b, _ = self.run_export("b", actors=["claude:research#SESSB2"], kinds=["conversations"])
        _, both, _ = self.run_export("both", actors=["claude:research"], kinds=["conversations"])
        ma = {r["rid"] for r in a if r["kind"] == "message"}
        mb = {r["rid"] for r in b if r["kind"] == "message"}
        self.assertEqual(len(ma), 5)
        self.assertEqual(len(mb), 2)
        self.assertFalse(ma & mb)   # both have T1u and T1a, yet no rid collides
        rids = [r["rid"] for r in both]
        self.assertEqual(len(rids), len(set(rids)))
        self.assertEqual({r["rid"] for r in both if r["kind"] == "message"}, ma | mb)
        self.assertEqual({r["actor"] for r in a}, {"claude:research#SESSA1"})

    def test_round_trip(self):
        """export: message round trip from a conversation@1 capture preserves text, ids and order"""
        _, recs, _ = self.run_export(kinds=["conversations"], lane="research")
        got = [r for r in recs if r["kind"] == "message" and r["source_path"] == "captures/a.json"]
        want = [(t["role"], m) for t in CONV_A["turns"] for m in t["messages"]]
        self.assertEqual(len(got), len(want))
        for r, (role, m) in zip(got, want):
            for k in ("mid", "id", "ts", "model", "text", "dictated", "sources"):
                self.assertEqual(r[k], m[k], k)
            self.assertEqual(r["role"], role)
        self.assertEqual([r["seq"] for r in got], list(range(1, len(want) + 1)))
        src = (self.root / "captures" / "a.json").read_bytes()
        self.assertEqual(got[0]["source_hash"], hashlib.sha256(src).hexdigest())

    def test_relay_distinguishable(self):
        """export: a relayed message is marked relay with its origin, distinct from original speech"""
        _, recs, _ = self.run_export(kinds=["conversations"])
        by = {r["mid"]: r for r in recs if r["kind"] == "message" and r["source_path"] == "captures/a.json"}
        self.assertEqual(by["T2u"]["evidence_class"], "relay")
        self.assertEqual(by["T2u"]["relay"]["origin_label"], "Ada (ChatGPT) T1a")
        self.assertEqual(by["T2u"]["relay"]["origin_ref"], "T1a")
        self.assertEqual(by["T1u"]["evidence_class"], "source-grounded")
        self.assertIsNone(by["T1u"]["relay"])
        conv = next(r for r in recs if r["kind"] == "conversation" and r["source_path"] == "captures/a.json")
        self.assertEqual(conv["relayed_messages"], 1)
        self.assertEqual(conv["evidence_class"], "derived")

    def test_gaps_reported(self):
        """export: missing captures, hidden steps, absent timestamps and missing outputs are reported in the manifest"""
        m, recs, _ = self.run_export()
        g = m["coverage_gaps"]
        self.assertIn("00000000-0000-0000-0000-00000000000c", [t["conversation_id"] for t in g["threads_without_capture"]])
        self.assertNotIn("00000000-0000-0000-0000-00000000000a", [t["conversation_id"] for t in g["threads_without_capture"]])
        self.assertIn("SESSR9", [t["conversation_id"] for t in g["threads_without_capture"]])
        gap_actors = {a["actor"]: a for a in g["actors_without_capture"]}
        self.assertIn("chatgpt:review", gap_actors)
        self.assertIn("claude:review", gap_actors)
        self.assertNotIn("claude:research", gap_actors)
        self.assertIn("review", g["lanes_without_capture"])
        self.assertEqual([c["hidden_counts"] for c in g["conversations_with_hidden_steps"]], [{"assistant:tool_use": 2}])
        self.assertIn("message", g["records_missing_timestamps"])
        self.assertIn("output", g["records_missing_timestamps"])
        self.assertEqual([h["handoff"] for h in g["handoffs_missing_output"]], ["HO-RS-20261102-CD34"])
        self.assertEqual(m["exclusions"]["non_conversation_json_files"], 1)
        no_ts = next(r for r in recs if r.get("mid") == "T2a")
        self.assertIsNone(no_ts["ts"])
        self.assertTrue(no_ts["ts_missing"])
        self.assertTrue(all(r["ts_missing"] is False for r in recs if r["kind"] == "decision"))
        self.assertEqual(m["counts"]["conversation"], 2)   # captured arms exported; uncaptured ones never appear as empty conversations
        self.assertTrue(m["limits"])

    def test_decisions_vs_proposals(self):
        """export: decisions, proposals and derived views keep distinct evidence classes"""
        _, recs, _ = self.run_export(kinds=["decisions", "challenges"])
        by = {r["rid"]: r for r in recs}
        self.assertEqual(by["decision:DEC-001"]["evidence_class"], "decision")
        self.assertEqual(by["decision:DEC-002"]["evidence_class"], "proposal")
        self.assertEqual(by["decision:DEC-002"]["status"], "proposed")
        self.assertEqual(by["challenge:CH-001"]["evidence_class"], "source-grounded")
        self.assertTrue(all(r["evidence_class"] in ex.EVIDENCE for r in self.run_export("all")[1]))

    def test_deterministic(self):
        """export: repeated export over unchanged inputs gives matching content hashes"""
        kw = dict(since="2026-11-01", until="2026-11-02T23:00Z")
        m1, _, out = self.run_export("same", **kw)
        b1 = (out / "records.jsonl").read_bytes()
        m2, _, _ = self.run_export("same", **kw)
        b2 = (out / "records.jsonl").read_bytes()
        self.assertEqual(b1, b2)
        m1.pop("nondeterministic"); m2.pop("nondeterministic")
        self.assertEqual(m1, m2)
        self.assertEqual(m1["outputs"]["records.jsonl"]["sha256"], hashlib.sha256(b1).hexdigest())
        # the time window is applied: session B (2026-11-03) is out, undated records are counted, not silently kept
        _, recs, _ = self.run_export("win", **kw)
        self.assertFalse([r for r in recs if r["source_path"] == "captures/nested/b.json"])
        self.assertGreater(m1["exclusions"]["no_timestamp_under_time_filter"], 0)

    def test_no_mutation(self):
        """export: no original record is modified, whatever the options"""
        before = tree_hash(self.root)
        for i, kw in enumerate([{}, dict(granularity="inventory", fmt="json"), dict(content="metadata", lane="RS"),
                                dict(content="none", actors=["chatgpt:review"])]):
            self.run_export(f"o{i}", **kw)
        self.assertEqual(before, tree_hash(self.root))

    def test_public_destination_refused(self):
        """export: a destination inside a public or unconfirmed GitHub repository is refused"""
        repo = self.tmp / "somerepo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text('[core]\n\tbare = false\n[remote "origin"]\n\turl = https://github.com/example/public-thing.git\n', encoding="utf-8")
        for vis, refused in (("public", True), ("unknown", True), ("private", False)):
            ex.repo_visibility = lambda r, v=vis: v
            dest = repo / f"exp-{vis}"
            if refused:
                with self.assertRaises(ex.Refused):
                    ex.export(self.root, dest)
                self.assertFalse(dest.exists())
            else:
                ex.export(self.root, dest)
                self.assertTrue((dest / "manifest.json").exists())
        ex.repo_visibility = lambda r: "public"
        m = ex.export(self.root, repo / "exp-allowed", allow_public_dest=True)
        self.assertTrue(m["destination"]["allow_public_dest"])
        with self.assertRaises(ex.Refused):   # never into the records themselves
            ex.export(self.root, self.root / "export")

    def test_metadata_only(self):
        """export: metadata content leaves out free text and records the redaction policy"""
        m, recs, out = self.run_export(content="metadata")
        body = (out / "records.jsonl").read_text(encoding="utf-8")
        for secret in ("Plan the survey", "Here is a plan", "Use the synthetic survey", "Sample is small", "Looks fine", "Review the draft"):
            self.assertNotIn(secret, body)
        self.assertEqual(m["redaction"]["content"], "metadata")
        self.assertTrue(m["redaction"]["suppressed_fields"])
        t1u = next(r for r in recs if r.get("mid") == "T1u" and r["source_path"] == "captures/a.json")
        self.assertEqual(t1u["text_hash"]["sha256"], hashlib.sha256(CONV_A["turns"][0]["messages"][0]["text"].encode()).hexdigest())
        _, recs_none, out_none = self.run_export("none", content="none")
        self.assertNotIn("_hash", (out_none / "records.jsonl").read_text(encoding="utf-8").replace("source_hash", "").replace("source_file_hash", ""))

    def test_cli(self):
        """export: python -m tools export runs read-only from the command line and refuses bad input"""
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        ok = subprocess.run([sys.executable, "-B", "-m", "tools", "export", "--root", str(self.root), "--out", str(self.tmp / "cli"),
                             "--lane", "RS", "--granularity", "inventory"], cwd=KIT, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue((self.tmp / "cli" / "manifest.json").exists())
        bad = subprocess.run([sys.executable, "-B", "-m", "tools", "export", "--root", str(self.root), "--out", str(self.tmp / "cli2"),
                              "--kinds", "everything"], cwd=KIT, capture_output=True, text=True, env=env, timeout=60)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("refused", bad.stderr)


if __name__ == "__main__":
    unittest.main()
