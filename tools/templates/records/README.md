# Coordination records

Made with `relay-kit init` (relay-kit {{VERSION}}). These are the owner's records: handoffs
between assistants, the decision and challenge ledgers, and the coordination index. They
quote the owner, so keep this repository **private**.

- `handoffs/`: one file per handoff, results in `handoffs/outputs/`. The owner is set in
  `handoffs/.ledger.json` ({{OWNER}}); only the owner's words authorize work.
- `governance/decisions.jsonl`, `governance/challenges.jsonl`: append-only. Add lines with
  `relay-kit ledger append`, never by hand.
- `threads.md`: which thread or session does what.

## Starting a session

    export RELAYKIT_ACTOR=<assistant>:<lane>#<session id>   # e.g. claude:research#01AB...
    relay-kit status --root .

It says whether this copy is behind, what waits on the owner, which handoffs are open and
with whom, and what other lanes decided since this session last committed.

## Writing

    relay-kit handoff --dir handoffs new --from claude:research --to chatgpt:review --title "..."
    relay-kit ledger append --dir governance decision '{"status": "proposed", "decision": "...", "scope": "..."}'
    relay-kit status --root . --touching <files or thread link>    # before changing shared things

## Checks

The same checks run in three places:
- by hand: `relay-kit handoff --dir handoffs check` and `relay-kit ledger check --dir governance --git`;
- before each push, through `.githooks/pre-push`. Turn it on once per clone with
  `git config core.hooksPath .githooks`. It's procedure, not enforcement: `--no-verify` skips it;
- in CI (`.github/workflows/records.yml`), pinned to relay-kit {{KIT_REF}}. A red run on the
  main branch opens an issue assigned to the owner, which closes on the next green run.
