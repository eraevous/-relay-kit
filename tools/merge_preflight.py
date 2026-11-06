#!/usr/bin/env python3
"""PR collision preflight: Git merge simulation plus optional Telosmith static edges.

    python3 tools/merge_preflight.py --base origin/main --head HEAD \
      --peer PR-12=origin/pr-12 --run-edges --csv risks.csv --json

Exit: 0 clean, 2 conflicts found, 1 unable to simulate. Does not merge,
check out, advance refs or modify working-tree files.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=repo, capture_output=True, text=True,
                          check=False, timeout=90)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(repo, "git", *args)


def resolve(repo: Path, ref: str) -> str:
    p = git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    if p.returncode:
        raise ValueError(f"ref {ref!r} unavailable: {p.stderr.strip()}")
    return p.stdout.strip()


def files_changed(repo: Path, base: str, head: str) -> set[str]:
    p = git(repo, "diff", "--name-only", "-z", f"{base}...{head}")
    if p.returncode:
        raise ValueError(f"could not diff {base[:12]}...{head[:12]}: {p.stderr.strip()}")
    return set(filter(None, p.stdout.split("\0")))


def edge_diff(repo: Path, base: str, head: str) -> dict:
    p = run(repo, sys.executable, "-m", "aec", "diff", "--base", base,
            "--head", head, "--json")
    if p.returncode:
        raise ValueError(f"Telosmith edge diff unavailable: {(p.stderr or p.stdout).strip()[:300]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Telosmith edge diff returned non-JSON output") from exc


def cross_nodes(diff: dict) -> set[str]:
    changes = diff.get("cross_module_changes") or {}
    return {str(edge[key]) for direction in ("added", "removed")
            for edge in (changes.get(direction) or []) if isinstance(edge, dict)
            for key in ("src", "dst") if edge.get(key) is not None}


def edge_signal(left: dict | None, right: dict | None) -> dict:
    if left is None or right is None:
        return {"coverage": "not_available", "shared_cross_module_nodes": None, "examples": []}
    if left.get("base_revision") != right.get("base_revision") or not left.get("base_revision"):
        return {"coverage": "incompatible_bases", "shared_cross_module_nodes": None, "examples": []}
    if (left.get("head_method_sha") != right.get("head_method_sha")
            or left.get("method_comparison") or right.get("method_comparison")):
        return {"coverage": "incompatible_extraction_methods",
                "shared_cross_module_nodes": None, "examples": []}
    overlap = sorted(cross_nodes(left) & cross_nodes(right))
    return {"coverage": "comparable_static", "shared_cross_module_nodes": len(overlap),
            "examples": overlap[:10]}


def merge_sim(repo: Path, left: str, right: str) -> tuple[str, str]:
    # Git's actual three-way merge machinery. No changes to tracked files or refs.
    p = git(repo, "merge-tree", "--write-tree", "--name-only", left, right)
    if p.returncode == 0:
        return "clean", ""
    detail = (p.stdout + "\n" + p.stderr).strip()[:4000]
    return ("conflict" if p.returncode == 1 else "error"), detail


def inspect(repo: Path, base_ref: str, head_ref: str,
            peers: dict[str, str], use_edges: bool) -> list[dict]:
    base, head = resolve(repo, base_ref), resolve(repo, head_ref)
    head_files = files_changed(repo, base, head)
    left_edges = None
    edge_error = None
    if use_edges:
        try:
            left_edges = edge_diff(repo, base, head)
        except (ValueError, subprocess.TimeoutExpired) as exc:
            edge_error = str(exc)
    rows: list[dict] = []
    for name, ref in [("target", base_ref), *peers.items()]:
        other = base if name == "target" else resolve(repo, ref)
        state, diagnostic = merge_sim(repo, head, other)
        other_files = set() if name == "target" else files_changed(repo, base, other)
        shared = sorted(head_files & other_files)
        if name == "target":
            signal = {"coverage": "not_applicable", "shared_cross_module_nodes": None,
                      "examples": []}
        else:
            other_edges = None
            if use_edges and left_edges is not None:
                try:
                    other_edges = edge_diff(repo, base, other)
                except (ValueError, subprocess.TimeoutExpired) as exc:
                    edge_error = (edge_error + "; " if edge_error else "") + str(exc)
            signal = edge_signal(left_edges, other_edges)
        rows.append({"pair": name, "ref": ref, "head_sha": head, "other_sha": other,
                     "merge": state, "shared_paths_count": len(shared),
                     "shared_paths": shared[:30], "shared_paths_truncated": max(0, len(shared) - 30),
                     "edges": signal, "edge_error": edge_error, "diagnostic": diagnostic})
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--base", default="origin/main", help="target branch/ref")
    p.add_argument("--head", default="HEAD", help="candidate branch/ref")
    p.add_argument("--peer", action="append", default=[], metavar="NAME=REF",
                   help="another open PR head")
    p.add_argument("--run-edges", action="store_true",
                   help="run Telosmith python -m aec diff when installed")
    p.add_argument("--csv", type=Path, help="observation table for Fathom")
    p.add_argument("--json", action="store_true", help="machine-readable stdout")
    a = p.parse_args(argv)
    repo = a.repo.resolve()
    peers = {}
    try:
        for item in a.peer:
            name, sep, ref = item.partition("=")
            if not sep or not name or not ref or name == "target" or name in peers:
                raise ValueError("--peer must be a unique NAME=REF (NAME cannot be target)")
            peers[name] = ref
        if git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            raise ValueError("--repo must be a Git working tree")
        rows = inspect(repo, a.base, a.head, peers, a.run_edges)
    except (ValueError, subprocess.TimeoutExpired) as exc:
        print(f"preflight error: {exc}", file=sys.stderr)
        return 1
    result = {"schema": "merge-preflight/probe@1", "base": a.base, "head": a.head,
              "pairs": rows, "warning": "A clean Git merge does not guarantee compatible "
              "behavior. Static edges are incomplete and advisory."}
    if a.csv:
        a.csv.parent.mkdir(parents=True, exist_ok=True)
        with a.csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "pair", "head_sha", "other_sha", "git_merge", "shared_paths",
                "edge_coverage", "shared_cross_module_nodes"])
            w.writeheader()
            for r in rows:
                w.writerow({"pair": r["pair"], "head_sha": r["head_sha"],
                            "other_sha": r["other_sha"], "git_merge": r["merge"],
                            "shared_paths": r["shared_paths_count"],
                            "edge_coverage": r["edges"]["coverage"],
                            "shared_cross_module_nodes":
                                r["edges"]["shared_cross_module_nodes"]})
    if a.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        for r in rows:
            print(f"{r['pair']}: {r['merge']} | shared files={r['shared_paths_count']} | "
                  f"edge coverage={r['edges']['coverage']} | "
                  f"shared cross-module nodes={r['edges']['shared_cross_module_nodes']}")
            for path in r["shared_paths"][:5]:
                print(f"    file: {path}")
            if r["edge_error"]:
                print(f"    edge warning: {r['edge_error']}")
            if r["diagnostic"]:
                print(f"    Git: {r['diagnostic'][:500]}")
        print(result["warning"])
    if any(r["merge"] == "error" for r in rows):
        return 1
    return 2 if any(r["merge"] == "conflict" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
