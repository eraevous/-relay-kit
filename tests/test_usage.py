"""Tests for tools/usage_ingest.py: Claude Code session logs -> one measured relay-kit/budget@2 line.
Standard library only; synthetic logs only.

Run alone with `python3 -m unittest tests.test_usage`, or as part of tests/run_tests.py."""
import importlib.util, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("relaykit_test_cockpit_for_usage", KIT / "tests" / "test_cockpit.py")
TC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TC)
CK = TC.ck
SID = "11111111-2222-3333-4444-555555555555"


def msg(mid, ts, i, o, cw, cr, model="model-a"):
    return {"type": "assistant", "timestamp": ts, "sessionId": SID, "costUSD": 9.99,
            "message": {"id": mid, "model": model, "usage": {"input_tokens": i, "output_tokens": o,
                                                             "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-usage-"))
        self.projects = self.tmp / "projects"
        proj = self.projects / "-home-someone-secret-project"
        (proj / SID / "subagents").mkdir(parents=True)
        main = [msg("m1", "2026-11-03T10:00:00Z", 10, 100, 1000, 50000),
                msg("m1", "2026-11-03T10:00:01Z", 10, 100, 1000, 50000),     # the same response, split over two lines
                {"type": "user", "timestamp": "2026-11-03T10:05:00Z", "message": {"content": "hi"}},
                msg("m2", "2026-11-03T11:00:00Z", 5, 50, 0, 60000, model="model-b")]
        (proj / f"{SID}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in main) + "not json\n", encoding="utf-8")
        sub = [msg("s1", "2026-11-03T10:30:00Z", 1, 20, 300, 4000)]
        (proj / SID / "subagents" / "agent-a1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in sub), encoding="utf-8")
        self.root = TC.build_root(self.tmp)
        self._vis = CK.ex.repo_visibility
        CK.ex.repo_visibility = lambda repo: "unknown"

    def tearDown(self):
        CK.ex.repo_visibility = self._vis
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, *extra):
        return subprocess.run([sys.executable, "-I", str(KIT / "tools" / "usage_ingest.py"), "--session", SID, "--projects", str(self.projects),
                               "--dir", str(self.root / "governance"), "--actor", "claude:research#SESSA1", *extra],
                              capture_output=True, text=True)

    def test_one_line(self):
        """usage: one measured line per session; a message split over several log lines counts once; cache reads stay apart"""
        p = self.ingest()
        self.assertEqual(p.returncode, 0, p.stderr)
        row = json.loads(p.stdout)
        d = row["tokens_detail"]
        self.assertEqual((d["input"], d["output"], d["cache_write"], d["cache_read"]), (16, 170, 1300, 114000))
        self.assertEqual((row["tokens_in"], row["tokens_out"], row["method"], row["window"], row["agents"]), (1316, 170, "measured", "session", 1))
        self.assertEqual((row["interval_start"], row["interval_end"], row["id"]), ("2026-11-03T10:00:00Z", "2026-11-03T11:00:00Z", f"cc-{SID}"))
        self.assertEqual(sorted(d["by_model"]), ["model-a", "model-b"])
        self.assertNotIn("9.99", json.dumps(row))      # the logs' costUSD is never used
        b = CK.collect(self.root)["budget"]
        self.assertEqual((b["burn_7d"], b["session_tokens_7d_by_method"]), (1486, {"measured": 1486}))

    def test_again(self):
        """usage: ingesting a session again appends a correction, so the cockpit counts it once"""
        self.assertEqual(self.ingest().returncode, 0)
        p = self.ingest()
        self.assertEqual(p.returncode, 0, p.stderr)
        row = json.loads(p.stdout)
        self.assertEqual((row["id"], row["correction_of"]), (f"cc-{SID}-r1", f"cc-{SID}"))
        p = self.ingest()
        self.assertEqual(json.loads(p.stdout)["correction_of"], f"cc-{SID}-r1")
        b = CK.collect(self.root)["budget"]
        self.assertEqual((b["records"], b["session_lines_7d"], b["burn_7d"]), (3, 1, 1486))

    def test_nothing_else_leaves(self):
        """usage: the line carries counts and the session id only: no file paths or folder names"""
        row = json.loads(self.ingest("--dry-run").stdout)
        text = json.dumps(row)
        for leak in ("secret-project", "home-someone", str(self.projects), "subagents/"):
            self.assertNotIn(leak, text)
        self.assertFalse((self.root / "governance" / "budget.jsonl").exists())
        for bad in (["--session", "../x"], ["--session", "99999999-0000-0000-0000-000000000000"]):
            p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "usage_ingest.py"), *bad, "--projects", str(self.projects),
                                "--dir", str(self.root / "governance"), "--actor", "sam"], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0, bad)


if __name__ == "__main__":
    unittest.main()
