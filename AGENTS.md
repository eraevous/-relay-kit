# AGENTS.md: working rules for coding agents in this repo

These rules apply to Claude Code, Codex or any other coding agent. Human contributors follow
[CONTRIBUTING.md](CONTRIBUTING.md); everything there applies to agents too.

- Purpose and invariants: [governance/purpose.md](governance/purpose.md). Contracts:
  [governance/contracts.json](governance/contracts.json) and [ARCHITECTURE.md](ARCHITECTURE.md).
  A contract changes only by a new version, with the spec, every implementer and the tests in the same change.
- A change to a contract, a check or guard, or anything that decides who may do what needs the outside
  review described in CONTRIBUTING.md before it merges.
- Standard library only. Before you finish: `python3 -I -B tests/run_tests.py`,
  `node tests/browser_tests.cjs` and `python3 -I tools/prepublish_scan.py`.

## Records and ledgers

Two places hold reasons, and they do different jobs:

- **Records** (`.aec/records.jsonl`, [Telosmith](https://github.com/eraevous/Telosmith)) are the design truth
  inside this repo: its purpose (`P0`), the requirements behind each contract and the code that implements
  them (`implemented_by code:...`). A record is provisional until Zach commits it (`"epistemic": "committed"`);
  agents may append provisional records and proposed decisions, never accepted ones.
- **Ledgers** (the owner's private records repository, `governance/decisions.jsonl` and `challenges.jsonl`)
  hold cross-repo coordination and the owner's decisions. This public repo cannot read them; records cite
  their ids as text only (for example `RK-D-ledger` lists the DEC ids that govern the kit). The in-review
  cockpit@1 and mcp@1 get requirement records only once their code is on main.
- **Append only.** Never edit or delete an earlier line in `.aec/records.jsonl`. To change a record, append
  the same `id` with the next `version` and a `supersedes` relation.

After editing code, the Claude Code hook in `.claude/settings.json` runs `aec check` and `aec code-impact`
(it needs `pip install "git+https://github.com/eraevous/Telosmith.git@<the commit pinned in
.github/workflows/telosmith.yml>"`; without it the hook reports that and does not block). Read what it reports:
`[stale]` / `[needs_review]` means your change touched code a record relies on; say in your summary whether
the record still holds. The `telosmith` CI job runs the same check on every pull request.
