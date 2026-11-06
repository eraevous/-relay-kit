"""Deterministic fixtures for merge-preflight Git and static-edge evidence."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "merge_preflight.py"
spec = importlib.util.spec_from_file_location("merge_preflight", TOOL)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class GitMergeFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture")
        self.write("shared.py", "value = 1\n")
        self.write("a.py", "one = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, text=True,
                              capture_output=True, check=True)

    def write(self, name, content):
        (self.repo / name).write_text(content, encoding="utf-8")

    def commit_branch(self, name, changes):
        self.git("checkout", "-qb", name, "main")
        for path, contents in changes.items():
            self.write(path, contents)
        self.git("add", ".")
        self.git("commit", "-qm", name)

    def call(self, *args):
        return subprocess.run([sys.executable, str(TOOL), "--repo", str(self.repo),
                               "--base", "main", *args, "--json"],
                              text=True, capture_output=True)

    def test_clean_independent_changes(self):
        self.commit_branch("candidate", {"a.py": "one = 2\n"})
        self.commit_branch("peer", {"b.py": "two = 2\n"})
        result = self.call("--head", "candidate", "--peer", "peer=peer")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        pairs = json.loads(result.stdout)["pairs"]
        self.assertEqual(["clean", "clean"], [x["merge"] for x in pairs])
        self.assertEqual(0, pairs[1]["shared_paths_count"])
        self.assertEqual("not_available", pairs[1]["edges"]["coverage"])

    def test_clean_overlapping_file(self):
        self.git("checkout", "-q", "main")
        self.write("shared.py", "value = 1\nkeep1\nkeep2\nkeep3\nkeep4\nother = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "two-sections")
        self.commit_branch("candidate", {"shared.py": "value = 2\nkeep1\nkeep2\nkeep3\nkeep4\nother = 1\n"})
        self.commit_branch("peer", {"shared.py": "value = 1\nkeep1\nkeep2\nkeep3\nkeep4\nother = 2\n"})
        result = self.call("--head", "candidate", "--peer", "peer=peer")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        peer = json.loads(result.stdout)["pairs"][1]
        self.assertEqual("clean", peer["merge"])
        self.assertEqual(1, peer["shared_paths_count"])

    def test_detects_conflicting_peer_and_preserves_worktree(self):
        self.commit_branch("candidate", {"shared.py": "value = 2\n"})
        self.commit_branch("peer", {"shared.py": "value = 3\n"})
        self.write("scratch.txt", "untouched\n")
        before = self.git("status", "--porcelain").stdout
        result = self.call("--head", "candidate", "--peer", "peer=peer")
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        pairs = json.loads(result.stdout)["pairs"]
        self.assertEqual("clean", pairs[0]["merge"])
        self.assertEqual("conflict", pairs[1]["merge"])
        self.assertEqual(["shared.py"], pairs[1]["shared_paths"])
        self.assertEqual(before, self.git("status", "--porcelain").stdout)
        self.assertEqual("peer", self.git("branch", "--show-current").stdout.strip())

    def test_target_only_and_dirty_tree(self):
        self.commit_branch("candidate", {"shared.py": "value = 2\n"})
        self.write("scratch.txt", "do not touch\n")
        before = self.git("status", "--porcelain").stdout
        result = self.call("--head", "candidate")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(before, self.git("status", "--porcelain").stdout)

    def test_unknown_peer_fails_closed(self):
        result = self.call("--peer", "missing=not_a_real_ref")
        self.assertEqual(1, result.returncode)
        self.assertIn("unavailable", result.stderr)

    def test_emits_fathom_observation_csv(self):
        self.commit_branch("candidate", {"shared.py": "value = 2\n"})
        self.commit_branch("peer", {"shared.py": "value = 3\n"})
        output = self.repo / "risk.csv"
        result = self.call("--head", "candidate", "--peer", "peer=peer", "--csv", str(output))
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        table = output.read_text()
        self.assertIn("shared_cross_module_nodes", table)
        self.assertIn("not_available", table)


class EdgeEvidence(unittest.TestCase):
    def fixture(self, pairs, base="abc", method="one"):
        return {"base_revision": base, "head_method_sha": method,
                "cross_module_changes": {
                    "added": [{"src": a, "dst": b} for a, b in pairs], "removed": []}}

    def test_shared_nodes_requires_matching_methods_and_bases(self):
        a = self.fixture([("pkg.a", "pkg.b"), ("pkg.a", "pkg.c")])
        b = self.fixture([("pkg.a", "pkg.z")])
        result = preflight.edge_signal(a, b)
        self.assertEqual("comparable_static", result["coverage"])
        self.assertEqual(1, result["shared_cross_module_nodes"])
        self.assertEqual("incompatible_bases",
                         preflight.edge_signal(a, self.fixture([], base="def"))["coverage"])
        self.assertEqual("incompatible_extraction_methods",
                         preflight.edge_signal(a, self.fixture([], method="two"))["coverage"])

    def test_missing_is_not_zero(self):
        self.assertIsNone(
            preflight.edge_signal(None, self.fixture([]))["shared_cross_module_nodes"])


if __name__ == "__main__":
    unittest.main()
