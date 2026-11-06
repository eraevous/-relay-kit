# Merge collision preflight — experimental

**Status:** Proposed read-only probe, not an adopted merge gate. The immediate purpose is to reduce time lost to competing pull requests, while testing whether Telosmith edges add anything beyond Git.

The standard-library script tools/merge_preflight.py produces two explicitly different signals:

1. **Actual merge conflicts:** Git's merge-tree --write-tree simulates merging a candidate branch into the locally fetched target, and against each supplied peer PR branch. Git exit 1 = conflict; exit 0 = clean simulation. It doesn't checkout or advance refs; Git may create unreachable tree objects in the local object store.
2. **Structural review overlap (optional):** --run-edges invokes Telosmith python -m aec diff --base SHA --head SHA --json for both branches. With the same merge base and compatible extractor methods, it counts common endpoints in changed cross-module calls/imports. This is advisory, not evidence of a Git conflict, behavioral incompatibility or complete runtime reachability.

## Use

Fetch the target and candidate/peer PR heads before testing. This tool deliberately does not query GitHub or fetch refs; relay-kit prs remains the one GitHub PR reader.

    git fetch origin main
    git fetch origin pull/123/head:refs/remotes/origin/pr-123
    python3 tools/merge_preflight.py --base origin/main --head HEAD \
        --peer PR-123=refs/remotes/origin/pr-123 --csv /tmp/merge-risk.csv --json

For Python code with a compatible Telosmith installation:

    python3 tools/merge_preflight.py --base origin/main --head HEAD \
        --peer PR-123=refs/remotes/origin/pr-123 --run-edges

Exit codes: **0** clean simulations, **2** at least one true simulated conflict, **1** missing refs or failed simulation. A shared-file warning does not imply an actual conflict; two branches can edit different lines of one file and merge cleanly. An absent edge reading is unknown, not zero.

The peer checks are pairwise. They do not simulate every possible sequence of three or more PR merges. A clean Git merge never proves that the software will work.

## Fathom

--csv PATH emits one aggregate row per pair: commit identities, git_merge, shared_paths, edge_coverage, and shared_cross_module_nodes. Fathom can treat this as ordinary tabular data for profiling, missingness analysis and later trend comparisons. It does not receive private source text or infer a causal merge-risk score. A single run cannot support predictive claims. Any history collector should pin source revisions, extraction methods, timestamps, and sampling coverage.

## Follow-up experiments (not implemented)

- Consume the existing relay-kit/prs@2 record store and fetch only required peer heads through a separately reviewed network wrapper; no redundant GitHub polling.
- Run a read-only report when PRs are opened, when the base moves, and just before merging.
- Compare three approaches across actual historical collisions: file overlap alone; Git merge simulation; Git plus Telosmith edges, measuring true conflicts found, false alerts, review time saved and semantic regressions missed.
- Evaluate whether Fathom finds actionable patterns in the accumulated observation table, and whether using those analyses improves merge planning.
- Keep structural analysis optional for non-Python repos; any mandatory enforcement requires an explicit owner decision and additional review.

## Tests

    python3 -I -B -m unittest discover -s tests -p test_merge_preflight.py -v

Tests cover clean changes, clean same-file edits, real conflicts, preservation of dirty working trees and branch refs, unavailable refs, CSV output and edge-method/base mismatches. The code introduces no versioned relay-kit contract, new authority, automatic merge or new persistent record schema. As a check-related change, it needs independent outside review before merging, per CONTRIBUTING.md.
