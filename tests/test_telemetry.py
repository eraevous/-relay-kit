"""Tests for tools/telemetry.py (relay-kit/telemetry@1): a derived view rebuilt from the records.
Standard library only; synthetic data only; no network.

Run alone with `python3 -m unittest tests.test_telemetry`, or as part of tests/run_tests.py."""
import importlib.util, json, pathlib, shutil, subprocess, sys, tempfile, unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("relaykit_test_cockpit_for_telemetry", KIT / "tests" / "test_cockpit.py")
TC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TC)
CK = TC.ck
TEL = CK.TEL


def pr(n, created, merged=None, state=None, reviews=(), title="t", sections=None, repo="o/r"):
    return {"schema": "relay-kit/prs@2", "kind": "pr", "ts": "2026-12-01T00:00:00Z", "repo": repo, "number": n, "title": title,
            "state": state or ("merged" if merged else "open"), "created_at": created, "merged_at": merged,
            "reviews": list(reviews), "body_sections": sections or {}, "unread": []}


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-tel-"))
        self.root = TC.build_root(self.tmp)
        self._vis = CK.ex.repo_visibility
        CK.ex.repo_visibility = lambda repo: "unknown"

    def tearDown(self):
        CK.ex.repo_visibility = self._vis
        shutil.rmtree(self.tmp, ignore_errors=True)

    def m(self, view, name):
        return next(x for x in view["metrics"] if x["metric"] == name)

    def write_prs(self, *rows):
        fetch = {"schema": "relay-kit/prs@2", "kind": "fetch", "ts": "2026-12-01T00:00:00Z", "repos": ["o/r"], "ok": ["o/r"], "failed": {}}
        (self.root / "governance" / "pr-state.jsonl").write_text("".join(json.dumps(r) + "\n" for r in list(rows) + [fetch]), encoding="utf-8")

    def test_unknown(self):
        """telemetry: every metric carries its definition, version and label; with no records each says unknown, never zero"""
        v = TEL.derive(self.root)
        self.assertEqual(v["schema"], "relay-kit/telemetry@1")
        for x in v["metrics"]:
            self.assertTrue(x["definition"] and x["label"] and x["version"].startswith(x["metric"] + "@"), x["metric"])
        self.assertEqual(self.m(v, "delivery_throughput")["unknown"], "no governance/pr-state.jsonl: run relay-kit prs")
        self.assertIsNone(self.m(v, "lead_time")["hours"]["median"])
        self.assertIsNone(self.m(v, "human_intervention")["minutes_reported"])
        self.assertEqual(self.m(v, "compute_consumption")["label"], "unknown")

    def test_prs(self):
        """telemetry: lead time and review burden are distributions from pr-state; abandoned PRs are counted apart"""
        self.write_prs(pr(1, "2026-11-02T00:00:00Z", "2026-11-02T02:00:00Z", reviews=[{"verdict": "CHANGES_REQUESTED"}, {"verdict": "APPROVED"}]),
                       pr(2, "2026-11-03T00:00:00Z", "2026-11-03T10:00:00Z", reviews=[{"verdict": "COMMENTED", "stated": "REQUEST CHANGES"}]),
                       pr(3, "2026-11-04T00:00:00Z", "2026-11-04T01:00:00Z"),
                       pr(4, "2026-11-04T00:00:00Z", state="closed"))
        v = TEL.derive(self.root)
        self.assertEqual(self.m(v, "lead_time")["hours"], {"n": 3, "median": 2.0, "p90": 10.0, "min": 1.0, "max": 10.0})
        self.assertEqual(self.m(v, "review_burden")["rounds"]["n"], 3)
        self.assertEqual(self.m(v, "review_burden")["rounds"]["max"], 1)
        d = self.m(v, "delivery_throughput")
        self.assertEqual((d["merged"], d["abandoned"], d["merged_per_week"]), (3, 1, {"2026-W45": {"o/r": 3}}))

    def test_remediation(self):
        """telemetry: remediation is counted only over cohorts old enough; a linked fix within the window counts"""
        self.write_prs(pr(1, "2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z"),
                       pr(2, "2026-11-01T00:00:00Z", "2026-11-01T02:00:00Z"),
                       pr(3, "2026-11-05T00:00:00Z", "2026-11-05T01:00:00Z", title="Fix regression from #1"),
                       pr(4, "2026-11-28T00:00:00Z", "2026-11-28T01:00:00Z"))
        v = TEL.derive(self.root)          # as of 2026-12-01: PRs 1-3 are 14 days old, PR 4 isn't
        w = self.m(v, "post_merge_remediation")["windows"]
        self.assertEqual((w["14d"]["cohort"], w["14d"]["remediated"]), (3, 1))
        self.assertEqual((w["30d"]["cohort"], w["30d"]["share"]), (0, None))
        self.assertEqual(self.m(v, "surviving_changes")["count"], 2)

    def test_compute_and_coverage(self):
        """telemetry: corrections and child runs are applied before compute is summed; coverage lists unmeasured agent sessions"""
        rows = [{"id": "a", "ts": "2026-11-03T09:00:00Z", "actor": "claude:research#SESSA1", "window": "session", "tokens_in": 100, "tokens_out": 0},
                {"id": "b", "ts": "2026-11-03T09:00:00Z", "actor": "claude:research#SESSA1", "window": "session", "tokens_in": 100, "tokens_out": 0,
                 "method": "estimate", "correction_of": "a"},
                {"id": "c", "ts": "2026-11-03T10:00:00Z", "actor": "claude:review#SESSR9", "window": "session", "tokens_in": 40, "tokens_out": 2,
                 "method": "measured", "tokens_detail": {"input": 4, "output": 2, "cache_write": 36, "cache_read": 900}}]
        (self.root / "governance" / "budget.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        v = TEL.derive(self.root)
        c = self.m(v, "compute_consumption")
        self.assertEqual((c["tokens_by_method"], c["label"], c["corrections_applied"]), ({"estimate": 100, "measured": 42}, "estimate+measured", 1))
        self.assertEqual(c["categories_from_measured_detail"]["cache_read"], 900)
        cov = self.m(v, "measurement_coverage")
        self.assertIn("claude:research#SESSA1", cov["unmeasured_sessions"])
        self.assertNotIn("claude:review#SESSR9", cov["unmeasured_sessions"])

    def test_panel(self):
        """telemetry: the cockpit's Review panel shows each metric with its label and definition, and no score"""
        self.write_prs(pr(1, "2026-11-02T00:00:00Z", "2026-11-02T02:00:00Z"))
        out = self.tmp / "o"
        data = CK.build(self.root, out)
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("TELEMETRY (relay-kit/telemetry@1, DERIVED", page)
        for x in data["telemetry"]["metrics"]:
            self.assertIn(f'>{x["metric"]}<', page)
        self.assertNotIn("score:", page.lower())
        self.assertNotIn("2-3 points", page.split("DEC-100")[0])   # only mentioned to say there is no target

    def test_not_a_record(self):
        """telemetry: the view is derived and is never written into the records"""
        p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "telemetry.py"), "--root", str(self.root),
                            "--out", str(self.root / "governance" / "telemetry.json")], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.root / "governance" / "telemetry.json").exists())
        p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "telemetry.py"), "--root", str(self.root),
                            "--out", str(self.tmp / "view.json")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads((self.tmp / "view.json").read_text())["schema"], "relay-kit/telemetry@1")


if __name__ == "__main__":
    unittest.main()
