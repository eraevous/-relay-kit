# Telemetry: `relay-kit/telemetry@1`

A **derived view** of how the work is going, rebuilt from the records on every run and never entered
by hand (DEC-123: budget.jsonl and pr-state.jsonl are the records; this isn't). It is built by
[tools/telemetry.py](../tools/telemetry.py) and shown on the Cockpit's Review surface.

```
relay-kit telemetry --root RECORDS [--out FILE]     # FILE outside the records; without --out it prints
```

**Inputs.** It reads `governance/budget.jsonl` ([budget@2](budget.md)), `governance/pr-state.jsonl`
([prs@2](prs.md)), the handoffs and the ledgers ([lifecycle@1](lifecycle.md)). It never calls GitHub
and writes nothing into the records.

**As of.** The newest timestamp it read, never the clock.

**What each figure carries.** Every metric carries its frozen **definition**, a **version**, and a
**label** taken from its inputs (`measured`, `proxy`, `estimate`, `unlabelled`, or `unknown`). Where
the records can't say, it gives `unknown` with a reason. **Missing is never zero.** Distributions
come as median, p90 and n, never a mean.

**No score, no targets.** DEC-100's "2-3 points" is an evaluation objective with no baseline yet, so
it appears nowhere. A merged pull request is not a production deployment, so the remediation figure
is not DORA's Change Failure Rate.

## Metrics

| Metric | Frozen definition | Label |
| --- | --- | --- |
| `compute_consumption` | Input and output tokens per uniquely identified execution interval (budget@2 session lines, with corrections and included child runs applied), by how each was measured; cache reads apart, from measured lines' `tokens_detail`. Unattributed use stays unattributed | the methods of its lines |
| `delivery_throughput` | Merged pull requests per ISO calendar week, by repository; abandoned (closed, not merged) counted apart | proxy (for delivered value) |
| `lead_time` | Wall-clock hours from pull request creation to merge. Never human work time | measured |
| `review_burden` | Change-request review rounds per merged pull request: GitHub's verdict, or a verdict stated at the start of a review posted as a comment | measured |
| `post_merge_remediation` | Share of merged pull requests with an explicitly linked later revert or fix (same repository; the PR referenced with fix, revert or hotfix wording) within 14 days (DEC-123) and 30 days (Kate's round 11), counted only over cohorts at least that old | proxy |
| `surviving_changes` | Merged pull requests not reverted or fixed by an explicitly linked change within 14 days (DEC-123's unit of output; ">50% rewritten" can't be told from pr-state and isn't applied) | proxy |
| `human_intervention` | Self-reported active owner minutes (`human_minutes` on budget lines); unknown otherwise, never inferred from session spans | proxy or unknown |
| `measurement_coverage` | The agent sessions named in the handoffs that have a measured budget line, budget lines by method, repositories read, and pull-request lines with unread parts | measured |
| `coordination_loss` | Stale handoffs (open, no step in the 24 h before as of), cancelled handoffs, handoffs in progress claiming the same path, and ledger transitions in conflict. Reconciliation time isn't recorded: unknown | measured |

## Not yet

- **Dollar figures.** No price table is recorded, so dollars per surviving change and per active day
  are left out rather than guessed.
- **Attributing sessions to pull requests by branch.** Budget lines don't carry branches yet.
- **ChatGPT and Codex spend.** Their estimates come from captures of Kairos threads (TXRM item 6).
- **Authorship-based comparisons.** They wait on CH-017 identities.
