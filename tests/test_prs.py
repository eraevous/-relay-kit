"""Tests for tools/prs.py (relay-kit/prs@1) and the Pull requests surface, themes and landing of
tools/cockpit.py (relay-kit/cockpit@3). Standard library only; synthetic data only; no network: the
GitHub reader is given a fake `get`.

Run alone with `python3 -m unittest tests.test_prs`, or as part of tests/run_tests.py."""
import contextlib, hashlib, importlib.util, io, json, pathlib, shutil, tempfile, unittest

KIT = pathlib.Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


prs = load("relaykit_prs_under_test", KIT / "tools" / "prs.py")
tc = load("relaykit_test_cockpit_fixtures", KIT / "tests" / "test_cockpit.py")
ck = tc.ck

HOSTILE = tc.HOSTILE
BODY = f"""Author: claude:research#SESSA1

## What
Adds the synthetic report. {HOSTILE}

## Why
<!-- template hint only -->

## How
```
## What
not a heading: inside a fence
```
Built and checked.
"""


def gh_pr(n, state="open", merged=False, body=BODY, updated="2026-11-03T10:00:00Z", sha="a" * 40, title="Synthetic PR"):
    return {"number": n, "html_url": f"https://github.com/example/synthetic/pull/{n}", "title": title,
            "state": state, "draft": False, "user": {"login": "sam"}, "body": body,
            "created_at": "2026-11-02T10:00:00Z", "updated_at": updated,
            "merged_at": "2026-11-03T09:00:00Z" if merged else None, "closed_at": "2026-11-03T09:00:00Z" if merged else None,
            "base": {"ref": "main"}, "head": {"ref": f"b{n}", "sha": sha}}


class FakeGitHub:
    def __init__(self, prs_by_repo, reviews=None, runs=None, statuses=None, broken=()):
        self.prs, self.reviews, self.runs, self.statuses, self.broken = prs_by_repo, reviews or {}, runs or {}, statuses or {}, set(broken)
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        repo = "/".join(path.split("/")[1:3])
        if repo in self.broken:
            raise prs.Refused(f"GET {path}: HTTP 404")
        if "/pulls?" in path:
            return self.prs.get(repo, [])
        if "/reviews" in path:
            return self.reviews.get(int(path.split("/pulls/")[1].split("/")[0]), [])
        if path.endswith("/status"):
            return {"statuses": self.statuses.get(path.split("/commits/")[1].split("/")[0], [])}
        if "/check-runs" in path:
            return {"check_runs": self.runs.get(path.split("/commits/")[1].split("/")[0], [])}
        raise AssertionError(path)


def lines(root):
    return [json.loads(x) for x in (root / prs.OUT).read_text(encoding="utf-8").splitlines() if x.strip()]


def tree(root, skip=()):
    h = hashlib.sha256()
    for p in sorted(pathlib.Path(root).rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel in skip:
            continue
        h.update(rel.encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


class PrsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-prs-"))
        self.root = tc.build_root(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sections(self):
        """prs: body sections are kept verbatim; missing and comment-only sections are listed as missing"""
        b = prs.parse_body(BODY)
        self.assertEqual(b["sections"]["what"], f"Adds the synthetic report. {HOSTILE}")
        self.assertNotIn("why", b["sections"])                       # only an HTML comment: missing
        self.assertIn("not a heading: inside a fence", b["sections"]["how"])   # a fenced ## isn't a heading
        self.assertEqual(b["missing"], ["why", "authorized"])
        self.assertEqual(b["author"], "claude:research#SESSA1")
        self.assertEqual(prs.parse_body("## What this changes\nX\n")["sections"], {"what": "X"})
        self.assertEqual(prs.parse_body(None)["missing"], list(prs.SECTIONS))
        long = prs.parse_body("## What\n" + "x" * 5000)
        self.assertEqual((len(long["sections"]["what"]), long["truncated"]), (prs.MAX_SECTION, ["what"]))

    def test_append_only_changes(self):
        """prs: an unchanged PR appends no new line; a changed one appends one, and every run appends a fetch line"""
        gh = FakeGitHub({"example/synthetic": [gh_pr(7), gh_pr(8)]})
        prs.run(self.root, gh, "test", ["example/synthetic"], ts="2026-11-03T12:00:00Z")
        first = (self.root / prs.OUT).read_text(encoding="utf-8")
        self.assertEqual([x["kind"] for x in lines(self.root)], ["pr", "pr", "fetch"])
        prs.run(self.root, gh, "test", ["example/synthetic"], ts="2026-11-03T13:00:00Z")
        self.assertEqual([x["kind"] for x in lines(self.root)], ["pr", "pr", "fetch", "fetch"])
        gh.prs["example/synthetic"][0] = gh_pr(7, state="closed", merged=True)
        f = prs.run(self.root, gh, "test", ["example/synthetic"], ts="2026-11-03T14:00:00Z")
        after = (self.root / prs.OUT).read_text(encoding="utf-8")
        self.assertTrue(after.startswith(first))                   # nothing earlier rewritten
        got = lines(self.root)
        self.assertEqual([(x["kind"], x.get("number"), x.get("state")) for x in got[-2:]], [("pr", 7, "merged"), ("fetch", None, None)])
        self.assertEqual((f["prs_seen"], f["appended"]), (2, 1))

    def test_failed_repo(self):
        """prs: a failing repository is recorded in the fetch line and the others are still read"""
        gh = FakeGitHub({"example/synthetic": [gh_pr(7)]}, broken={"example/gone"})
        f = prs.run(self.root, gh, "test", ["example/gone", "example/synthetic"], ts="2026-11-03T12:00:00Z")
        self.assertEqual(f["ok"], ["example/synthetic"])
        self.assertIn("HTTP 404", f["failed"]["example/gone"])
        self.assertEqual(sum(1 for x in lines(self.root) if x["kind"] == "pr"), 1)

    def test_writes_only_its_file(self):
        """prs: it writes only governance/pr-state.jsonl and never rewrites a line"""
        before = tree(self.root)
        prs.run(self.root, FakeGitHub({"example/synthetic": [gh_pr(7)]}), "test", ["example/synthetic"], ts="2026-11-03T12:00:00Z")
        self.assertEqual(tree(self.root, skip={prs.OUT}), before)
        (self.root / prs.OUT).write_text((self.root / prs.OUT).read_text() + "not json\n", encoding="utf-8")
        with self.assertRaises(prs.Refused):                       # a damaged file is reported, not repaired
            prs.run(self.root, FakeGitHub({}), "test", ["example/synthetic"])
        out = self.tmp / "dry"
        dry_root = tc.build_root(out)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            prs.run(dry_root, FakeGitHub({"example/synthetic": [gh_pr(7)]}), "test", ["example/synthetic"], dry_run=True)
        self.assertEqual(len(printed.getvalue().splitlines()), 2)
        self.assertFalse((dry_root / prs.OUT).exists())

    def test_ci_and_reviews(self):
        """prs: CI state from check runs and statuses; reviews keep the verdict stated in their body"""
        gh = FakeGitHub({"example/synthetic": [gh_pr(7, sha="b" * 40), gh_pr(8, sha="c" * 40), gh_pr(9, sha="d" * 40)]},
                        reviews={7: [{"user": {"login": "sam"}, "state": "COMMENTED", "submitted_at": "2026-11-03T11:00:00Z",
                                      "body": "**APPROVED (round 2).**\nReviewer: model-b (other family)"},
                                     {"user": {"login": "x"}, "state": "PENDING", "body": ""}]},
                        runs={"b" * 40: [{"name": "tests", "status": "completed", "conclusion": "failure"}],
                              "c" * 40: [{"name": "tests", "status": "in_progress", "conclusion": None}]})
        prs.run(self.root, gh, "test", ["example/synthetic"], ts="2026-11-03T12:00:00Z")
        by = {x["number"]: x for x in lines(self.root) if x["kind"] == "pr"}
        self.assertEqual(by[7]["ci"], {"state": "failure", "failing": ["tests"]})
        self.assertEqual(by[8]["ci"]["state"], "pending")
        self.assertEqual(by[9]["ci"]["state"], "none")
        self.assertEqual(by[7]["reviews"], [{"by": "sam", "verdict": "COMMENTED", "at": "2026-11-03T11:00:00Z",
                                             "signed": "model-b (other family)", "stated": "APPROVED"}])

    def test_repos(self):
        """prs: repositories come from .ledger.json repos or --repo; anything not OWNER/NAME is refused"""
        with self.assertRaises(prs.Refused):
            prs.repos_of(self.root, None)
        cfg = json.loads((self.root / "handoffs" / ".ledger.json").read_text())
        cfg["repos"] = ["example/synthetic", "example/synthetic", "example/other"]
        (self.root / "handoffs" / ".ledger.json").write_text(json.dumps(cfg))
        self.assertEqual(prs.repos_of(self.root, None), ["example/synthetic", "example/other"])
        self.assertEqual(prs.repos_of(self.root, ["a/b"]), ["a/b"])
        for bad in (["a"], ["a/b/c"], ["../x"], ["a/b c"]):
            with self.assertRaises(prs.Refused):
                prs.repos_of(self.root, bad)


class CockpitPrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relaykit-ckpr-"))
        self.root = tc.build_root(self.tmp)
        self._vis = ck.ex.repo_visibility
        ck.ex.repo_visibility = lambda repo: "unknown"
        gh = FakeGitHub({"example/synthetic": [
            gh_pr(7),
            gh_pr(8, body="Author: sam\n\n## What\nOwner's own change.\n## Why\nBecause.\n## How\nBy hand.\n## Authorized\nn/a\n"),
            gh_pr(9, state="closed", merged=True, body="no sections here"),
        ]}, reviews={7: [{"user": {"login": "rev-bot"}, "state": "APPROVED", "submitted_at": "2026-11-03T11:00:00Z", "body": ""}]})
        prs.run(self.root, gh, "test", ["example/synthetic"], ts="2026-11-03T12:00:00Z")

    def tearDown(self):
        ck.ex.repo_visibility = self._vis
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, name="out", **kw):
        out = self.tmp / name
        data = ck.build(self.root, out, **kw)
        return data, (out / "index.html").read_text(encoding="utf-8")

    def test_pr_surface(self):
        """cockpit: Pull requests renders each PR's sections verbatim and escaped, with who wrote it and what is missing"""
        data, page = self.build()
        items = {x["number"]: x for x in data["pull_requests"]["items"]}
        self.assertEqual([x["number"] for x in data["pull_requests"]["items"]], [7, 8, 9])   # open first, then merged
        self.assertEqual(items[7]["sections"]["what"], f"Adds the synthetic report. {HOSTILE}")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;/textarea&gt;&lt;/script&gt;&lt;script&gt;alert(1)", page)
        self.assertIn("sections missing: why, authorized", page)
        self.assertEqual(items[7]["reviews"][0]["family"], None)
        self.assertEqual(data["counts"]["prs_open"], 2)
        self.assertEqual(page.count('class="card pr '), 3)

    def test_written_by(self):
        """cockpit: an agent-written PR is tagged as not written by the owner; one with no Author line says so"""
        data, page = self.build()
        items = {x["number"]: x for x in data["pull_requests"]["items"]}
        self.assertEqual((items[7]["written_by"], items[8]["written_by"], items[9]["written_by"]), ("agent", "owner", "unstated"))
        self.assertIn("written by claude:research#SESSA1, not the owner", page)
        self.assertIn("no Author: line · opened by sam", page)
        self.assertEqual(items[7]["author_family"], "claude")
        (self.root / "governance" / "cockpit.json").write_text(json.dumps({"review_families": {"rev-bot": "other"}}))
        data, _ = self.build("o2")
        self.assertIs({x["number"]: x for x in data["pull_requests"]["items"]}[7]["other_family"], True)

    def test_pr_metadata(self):
        """cockpit: --content metadata shows which PR sections exist but none of their text"""
        data, page = self.build(content="metadata")
        for word in ("Adds the synthetic report", "Owner's own change", "Synthetic PR", "By hand"):
            self.assertNotIn(word, page)
        self.assertIn("written by an agent, not the owner", page)   # actors stay in metadata; the Author line's text doesn't
        self.assertIn("Sections present: what, how", page)
        self.assertEqual({x["number"]: x["sections"] for x in data["pull_requests"]["items"]}[7], {})

    def test_overtaken_from_file(self):
        """cockpit: PR states for overtaken checks come from pr-state.jsonl without any network lookup"""
        with open(self.root / "governance" / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "DEC-010", "date": "2026-11-03", "status": "proposed", "scope": "research",
                                "decision": "Merge https://github.com/example/synthetic/pull/9 after review.",
                                "recorded_by": "claude:research#SESSA1"}) + "\n")
        def no_net(*a):
            raise AssertionError("network")
        data, _ = self.build(pr_lookup=no_net)                       # --pr-state off: the file alone
        c = next(x for x in data["choices"] if x["id"] == "DEC-010")
        self.assertIn("example/synthetic#9 is merged", c["possibly_overtaken"])
        self.assertIn("relay-kit prs", data["pr_state"]["via"])

    def test_theme(self):
        """cockpit: a theme can set only known variables to colours or font lists; anything else is refused"""
        good = self.tmp / "t.json"
        good.write_text(json.dumps({"name": "t", "dark": {"bg": "#000000", "mark": "var(--gold)", "heading": "Georgia, serif"}}))
        data, page = self.build(theme=good)
        self.assertIn("--bg:#000000", page)
        self.assertEqual(data["query"]["theme"], "t")
        for bad in ({"dark": {"bg": "red;}</style><script>"}}, {"dark": {"nope": "#000"}}, {"dark": {"heading": "x{}"}},
                    {"dark": {"bg": "url(http://x)"}}, {"light": "x"}, {"dark": {}, "extra": 1}, {"default": "light"}):
            p = self.tmp / "bad.json"
            p.write_text(json.dumps(bad))
            with self.assertRaises(ck.Refused, msg=str(bad)):
                self.build("b", theme=p)

    def test_light(self):
        """cockpit: a theme with a light block adds a light scheme and a toggle; without --theme the page is unchanged dark"""
        _, plain = self.build("plain")
        self.assertNotIn("prefers-color-scheme", plain)
        self.assertNotIn('id="theme-toggle"', plain)
        t = self.tmp / "t.json"
        t.write_text(json.dumps({"name": "t", "dark": {}, "light": {"bg": "#ffffff"}, "default": "light"}))
        _, page = self.build("lit", theme=t)
        self.assertIn("@media (prefers-color-scheme: light)", page)
        self.assertIn(":root[data-theme=light]{", page)
        self.assertIn("--bg:#ffffff", page)
        self.assertIn("--text:" + ck.LIGHT_DEFAULTS["text"], page)   # names it doesn't set come from the light defaults
        self.assertIn('<html lang="en" data-theme="light">', page)
        self.assertIn('id="theme-toggle"', page)

    def test_landing(self):
        """cockpit: --landing opens the page on the named surface; an unknown surface is refused"""
        data, page = self.build(landing="prs")
        self.assertIn('data-landing="prs"', page)
        with self.assertRaises(ck.Refused):
            self.build("x", landing="nowhere")
        (self.root / "governance" / "cockpit.json").write_text(json.dumps({"landing": "choices"}))
        data, _ = self.build("y")
        self.assertEqual(data["query"]["landing"], "choices")

    def test_no_file(self):
        """cockpit: without pr-state.jsonl the Pull requests surface says how to fill it and nothing is fetched"""
        (self.root / prs.OUT).unlink()
        data, page = self.build()
        self.assertFalse(data["pull_requests"]["present"])
        self.assertIn("relay-kit prs --root RECORDS", page)


if __name__ == "__main__":
    unittest.main()
