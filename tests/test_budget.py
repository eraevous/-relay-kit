"""Tests for relay-kit/budget@2: tools/budget.py, budget_append and the cockpit's reading of
governance/budget.jsonl. Standard library only; synthetic data only.

Run alone with `python3 -m unittest tests.test_budget`, or as part of tests/run_tests.py."""
import importlib.util, json, pathlib, shutil, subprocess, sys, tempfile, unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("relaykit_test_cockpit_for_budget", KIT / "tests" / "test_cockpit.py")
TC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TC)
CK = TC.ck
BG = CK.BG


def line(**k):
    base = {"ts": "2026-11-03T09:00:00Z", "actor": "claude:research#SESSA1", "window": "session", "tokens_in": 100, "tokens_out": 10}
    base.update(k)
    return base


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-budget-"))
        self.root = TC.build_root(self.tmp)
        self._vis = CK.ex.repo_visibility
        CK.ex.repo_visibility = lambda repo: "unknown"

    def tearDown(self):
        CK.ex.repo_visibility = self._vis
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, *rows):
        (self.root / "governance" / "budget.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def append(self, *args):
        return subprocess.run([sys.executable, "-I", str(KIT / "tools" / "budget_append.py"), "--dir", str(self.root / "governance"),
                               "--window", "session", "--actor", "sam", "--ts", "2026-11-03T10:00:00Z", *args],
                              capture_output=True, text=True)

    def test_correction(self):
        """budget: a correction replaces the line it names; the two are never summed"""
        old = line(note="estimate, unlabelled")
        raw = json.dumps(old)
        oid = BG.line_id(raw, old)
        self.write(old)
        p = self.append("--tokens-in", "100", "--tokens-out", "10", "--method", "estimate", "--correction-of", oid)
        self.assertEqual(p.returncode, 0, p.stderr)
        b = CK.collect(self.root)["budget"]
        self.assertEqual((b["records"], b["burn_7d"], b["session_lines_7d"]), (2, 110, 1))
        self.assertEqual(b["session_tokens_7d_by_method"], {"estimate": 110})
        self.assertEqual(list(b["corrections"]["replaced"]), [oid])

    def test_children(self):
        """budget: a parent that includes its children's use isn't counted with them"""
        self.write(line(id="child-1", tokens_in=40, tokens_out=0, method="measured"),
                   line(id="parent", tokens_in=100, tokens_out=0, method="measured", includes_children={"included": True, "ids": ["child-1"]}),
                   line(id="other", tokens_in=5, tokens_out=0, method="proxy"))
        b = CK.collect(self.root)["budget"]
        self.assertEqual((b["burn_7d"], b["session_tokens_7d_by_method"]), (105, {"measured": 100, "proxy": 5}))

    def test_method_required(self):
        """budget: new lines must say how they were measured; malformed @2 fields are refused"""
        p = self.append("--tokens-in", "1", "--tokens-out", "1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--method", p.stderr)
        for bad in (["--interval-start", "yesterday"], ["--interval-start", "2026-11-03T10:00:00Z", "--interval-end", "2026-11-03T09:00:00Z"],
                    ["--child-id", "x"], ["--correction-of", "bud-0000000000"]):
            p = self.append("--tokens-in", "1", "--tokens-out", "1", "--method", "measured", *bad)
            self.assertNotEqual(p.returncode, 0, bad)
        self.assertFalse((self.root / "governance" / "budget.jsonl").exists())
        p = self.append("--tokens-in", "1", "--tokens-out", "1", "--method", "measured", "--interval-start", "2026-11-03T08:00:00Z",
                        "--interval-end", "2026-11-03T09:30:00Z", "--includes-children", "no", "--scope", "this lane", "--source", "session log")
        self.assertEqual(p.returncode, 0, p.stderr)
        row = json.loads(p.stdout)
        self.assertEqual((row["method"], row["metric_version"], row["includes_children"], row["id"][:4]),
                         ("measured", "budget@2", {"included": False, "ids": []}, "bud-"))
        self.write(line(method="guess"))
        b = CK.collect(self.root)["budget"]
        self.assertEqual(b["invalid_lines"][0]["reason"], "method must be one of measured, proxy, estimate")

    def test_conflict(self):
        """budget: two corrections of one line are a conflict, reported; only the later one counts"""
        self.write(line(id="a", tokens_in=100, tokens_out=0), line(id="b", tokens_in=50, tokens_out=0, correction_of="a"),
                   line(id="c", tokens_in=70, tokens_out=0, correction_of="a"), line(id="d", tokens_in=1, tokens_out=0, correction_of="zz"))
        b = CK.collect(self.root)["budget"]
        self.assertEqual(b["corrections"]["conflicts"], [{"id": "a", "corrected_by": ["b", "c"]}])
        self.assertEqual(b["corrections"]["unknown_targets"][0]["correction_of"], "zz")
        self.assertEqual(b["burn_7d"], 50 + 70 + 1)    # b and c are both kept: the conflict is shown, not resolved by dropping one

    def test_unlabelled(self):
        """budget: older lines without a method count as 'unlabelled', never as measured"""
        self.write(line(tokens_in=3, tokens_out=0), line(tokens_in=4, tokens_out=0, method="measured"))
        b = CK.collect(self.root)["budget"]
        self.assertEqual(b["session_tokens_7d_by_method"], {"measured": 4, "unlabelled": 3})
        out = self.tmp / "o"
        CK.build(self.root, out)
        self.assertIn("measured: 4; unlabelled: 3", (out / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
