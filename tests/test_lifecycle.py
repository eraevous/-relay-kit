"""relay-kit/lifecycle@1: one fixture table (tests/fixtures/lifecycle_cases.json), every reader held to it:
the resolver itself, check_ledgers, status, export, the MCP server and the cockpit. Standard library
only; synthetic data only; no network.

Run alone with `python3 -m unittest tests.test_lifecycle`, or as part of tests/run_tests.py."""
import importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile, unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
CASES = json.loads((KIT / "tests" / "fixtures" / "lifecycle_cases.json").read_text(encoding="utf-8"))["cases"]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LC = load("relaykit_lifecycle_under_test", KIT / "tools" / "lifecycle.py")
CK = load("relaykit_cockpit_for_lifecycle", KIT / "tools" / "cockpit.py")
MCP = load("relaykit_mcp_for_lifecycle", KIT / "tools" / "mcp_server.py")
EX = CK.ex


def records(base, case):
    r = pathlib.Path(base) / "records"
    (r / "handoffs").mkdir(parents=True)
    (r / "governance").mkdir()
    (r / "handoffs" / ".ledger.json").write_text(json.dumps({"owner": "sam"}), encoding="utf-8")
    for name, rows in (("decisions.jsonl", case["decisions"]), ("challenges.jsonl", case["challenges"])):
        (r / "governance" / name).write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    return r


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-lifecycle-"))
        self._vis = EX.repo_visibility
        EX.repo_visibility = lambda repo: "unknown"

    def tearDown(self):
        EX.repo_visibility = self._vis
        shutil.rmtree(self.tmp, ignore_errors=True)

    def each(self):
        return [(case, records(self.tmp / f"{self._testMethodName}-{i}", case)) for i, case in enumerate(CASES)]

    def test_resolver(self):
        """lifecycle: the six review reproductions and the other fixture cases resolve as specified"""
        for case, _ in self.each():
            r = LC.resolve(case["decisions"], case["challenges"], LC.ledger_as_of(case["decisions"]))
            e = case["expect"]
            got = {"open_decisions": sorted(k for k, v in r["decisions"].items() if v["open"]),
                   "open_challenges": sorted(k for k, v in r["challenges"].items() if v["open"]),
                   "in_force": sorted(k for k, v in r["decisions"].items() if v["in_force"]),
                   "problems": sorted([p["id"], p["rule"]] for p in r["problems"]),
                   "conflicts": sorted(x["id"] for x in r["conflicts"])}
            for k in got:
                if k in e:
                    self.assertEqual(got[k], sorted(e[k]), k)
            for k, v in (e.get("pending") or {}).items():
                self.assertEqual([x["id"] for x in r["decisions"][k]["revocations_pending"]], v)
            for k, v in (e.get("applicability") or {}).items():
                self.assertEqual(r["decisions"][k]["applicability"], v, k)
            for k, v in (e.get("proposed_replacements") or {}).items():
                self.assertEqual(r["decisions"][k]["proposed_replacements"], v)
            for k, v in (e.get("amended_by") or {}).items():
                self.assertEqual([a["id"] for a in r["decisions"][k]["amended_by"]], v)

    def test_check_ledgers(self):
        """lifecycle: check_ledgers refuses exactly the cases with a transition that takes no effect"""
        for case, root in self.each():
            p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "check_ledgers.py"), "--dir", str(root / "governance")],
                               capture_output=True, text=True)
            want = case["expect"].get("problems") or []
            self.assertEqual(p.returncode != 0, bool(want), p.stdout)
            self.assertGreaterEqual(p.stdout.count("PROBLEM"), len(want), p.stdout)

    def test_readers_agree(self):
        """lifecycle: status, export, the MCP server and the cockpit show the same open items and in-force decisions"""
        for case, root in self.each():
            e = case["expect"]
            want_dec = sorted(e["open_decisions"]) if "open_decisions" in e else None
            want_ch = sorted(e["open_challenges"]) if "open_challenges" in e else None
            # status
            env = {**os.environ, "RELAYKIT_ACTOR": "sam", "GIT_CEILING_DIRECTORIES": str(self.tmp)}
            p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "status.py"), "--root", str(root), "--no-fetch", "--no-ci"],
                               capture_output=True, text=True, env=env, cwd=root)
            out = p.stdout
            sec = lambda title: re.split(r"\n== ", out.split(f"== {title}", 1)[1])[0] if f"== {title}" in out else ""
            if want_dec is not None:
                self.assertEqual(sorted(re.findall(r"^(DEC-\d+)\s+proposed", sec("Waiting on the owner"), re.M)), want_dec, "status")
            if want_ch is not None:
                self.assertEqual(sorted(re.findall(r"^(CH-\d+)\s", sec("Open challenges"), re.M)), want_ch, "status")
            # export
            rd = EX.Reader(root)
            rd.config()
            _, decs = EX.ledger_records(rd, "decisions.jsonl", "decision")
            _, chs = EX.ledger_records(rd, "challenges.jsonl", "challenge")
            if "in_force" in e:
                self.assertEqual(sorted(r["ledger_id"] for r in decs if r["in_force"]), sorted(e["in_force"]), "export")
            want_probs = sorted({p[0] for p in e.get("problems") or []})
            self.assertEqual(sorted({r["ledger_id"] for r in decs + chs if r.get("transition_problems")}), want_probs, "export problems")
            self.assertEqual(sorted(r["ledger_id"] for r in decs + chs if r.get("conflict")), sorted(e.get("conflicts") or []), "export conflicts")
            self.assertEqual(sorted(re.findall(r"^((?:DEC|CH)-\d+)\s", sec("Ledger transitions that take no effect, and conflicts"), re.M)),
                             sorted([p[0] for p in e.get("problems") or []] + list(e.get("conflicts") or [])), "status problems")
            # MCP
            st = MCP.Server(root, "full").t_status({})["summary"]
            if want_dec is not None:
                self.assertEqual(sorted(st["waiting_on_owner"]["proposed_decisions"]), want_dec, "mcp")
            if want_ch is not None:
                self.assertEqual(sorted(st["open_challenges"]), want_ch, "mcp")
            # cockpit
            data = CK.collect(root)
            if want_dec is not None:
                self.assertEqual(sorted(c["id"] for c in data["choices"] if c["kind"] == "decision"), want_dec, "cockpit")
            if want_ch is not None:
                self.assertEqual(sorted(c["id"] for c in data["choices"] if c["kind"] == "challenge"), want_ch, "cockpit")
            self.assertEqual(sorted({x["id"] for x in data["transitions"]["problems"]}), want_probs, "cockpit problems")
            self.assertEqual(sorted(x["id"] for x in data["transitions"]["conflicts"]), sorted(e.get("conflicts") or []), "cockpit conflicts")

    def git(self, root, *args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True)

    def apply(self, root, command, fill):
        """Run a cockpit-composed ledger line (its own argv, placeholders filled) through ledger_append."""
        import shlex
        argv = shlex.split(command)
        obj = json.loads(argv[-1])
        obj.update(fill)
        return subprocess.run([sys.executable, "-I", str(KIT / "tools" / "ledger_append.py"), "--offline"] + argv[3:-1] + [json.dumps(obj)],
                              cwd=root, capture_output=True, text=True, env={**os.environ, "RELAYKIT_ACTOR": "sam"})

    def test_expect(self):
        """lifecycle: a composed line with --expect is refused, with what changed, once its item has been settled"""
        case = next(c for c in CASES if c["name"].startswith("review 1"))
        root = records(self.tmp / "x", case)
        # a Windows clone: CRLF line ends. The page's hash and ledger_append's must still agree.
        for f in ("decisions.jsonl", "challenges.jsonl"):
            p = root / "governance" / f
            p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        self.git(root, "init", "-q", "-b", "main")
        self.git(root, "add", ".")
        self.git(root, "commit", "-qm", "r")
        data = CK.collect(root)
        fill = {"decider": "sam", "basis": '"yes"', "locator": "here"}
        first = next(c for c in data["choices"] if c["id"] == "DEC-001")
        second = next(c for c in data["choices"] if c["id"] == "DEC-002")
        self.assertIn("--expect DEC-001=open@", second["compose"]["accept"]["command"])   # the target it replaces, too
        p = self.apply(root, first["compose"]["accept"]["command"], fill)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.apply(root, first["compose"]["accept"]["command"], fill)    # the same click again
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("DEC-001 is now settled, not open", p.stdout + p.stderr)
        self.assertIn("DEC-003 (adopted", p.stdout + p.stderr)
        p = self.apply(root, second["compose"]["accept"]["command"], fill)   # adopting the replacement too: refused
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("DEC-001 is now settled, not open", p.stdout + p.stderr)
        self.assertEqual(len([l for l in (root / "governance" / "decisions.jsonl").read_bytes().split(b"\n") if l.strip()]), 3)

    def test_legacy(self):
        """lifecycle: a transition that takes no effect on an earlier line is a warning, so later appends still work; a new one is refused"""
        case = next(c for c in CASES if c["name"].startswith("review 5"))
        root = records(self.tmp / "l", case)
        self.git(root, "init", "-q", "-b", "main")
        self.git(root, "add", ".")
        self.git(root, "commit", "-qm", "r")
        env = {**os.environ, "RELAYKIT_ACTOR": "sam"}
        ok = {"status": "open", "concern": "A new concern.", "raised_by": "sam"}
        p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "ledger_append.py"), "--offline", "--dir", "governance",
                            "challenge", json.dumps(ok)], cwd=root, capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.git(root, "commit", "-qam", "c")
        p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "check_ledgers.py"), "--dir", str(root / "governance"), "--git"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("WARNING decisions.jsonl:2", p.stdout)
        bad = {"status": "superseded", "supersedes": "DEC-001", "decision": "Close it.", "scope": "s", "decider": "sam",
               "basis": '"yes"', "locator": "here"}
        p = subprocess.run([sys.executable, "-I", str(KIT / "tools" / "ledger_append.py"), "--offline", "--dir", "governance",
                            "decision", json.dumps(bad)], cwd=root, capture_output=True, text=True, env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("settles a proposal only", p.stdout + p.stderr)

    def test_real_shape(self):
        """lifecycle: the cockpit composes Accept, Reject, Amend and Close-as-overtaken with --expect, and shows a proposed replacement on the card"""
        root = records(self.tmp / "y", CASES[0])
        out = self.tmp / "out"
        data = CK.build(root, out)
        c = next(x for x in data["choices"] if x["id"] == "DEC-001")
        for a in ("accept", "reject", "amend"):
            self.assertRegex(c["compose"][a]["command"], r" --expect DEC-001=open@[0-9a-f]{12} decision ")
        self.assertEqual(c["proposed_replacements"], ["DEC-002"])
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("DEC-002 (proposed, so this is still open)", page)
        self.assertIn('data-expect="DEC-001=open@', page)


if __name__ == "__main__":
    unittest.main()
