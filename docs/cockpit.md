# Cockpit (`relay-kit/cockpit@1`)

One static page over your records: what waits on you, how the work is wired, what it costs, and
what has drifted. It is generated, not hand-kept: every number and line on it comes from the
records store, read with the same readers as [export](export.md).

    relay-kit cockpit --root path/to/records --out ~/cockpit [--lane L] [--content full|metadata]
                      [--weekly-tokens N] [--drift-days N] [--coordinator LANE] [--brand NAME]

It writes two files into `--out`:

- `index.html`: one self-contained page (inline CSS and JS; no fonts, scripts or images from
  anywhere else; a Content-Security-Policy that forbids loading any). It works offline, from a
  file, at phone width.
- `cockpit.json`: the data the page was built from (`"schema": "relay-kit/cockpit@1"`).

The page is deterministic: two runs over the same records give byte-identical files. The only
date on it is "generated from records as of" the latest timestamp found in the records, and
"last 7 days" and "older than N days" are measured back from that timestamp, never from today.

The layout follows Kate's Cockpit v0.4 prototype (2026-11-04): a left rail of numbered surfaces,
the dark instrument-panel palette, and its vocabulary ("Inquiry is not authority. Capture is not
commitment."; "Settle this choice?"; "Move from Drift to Run?"). `--brand` sets the name in the
header (default `relay-kit`; or `brand` in `governance/cockpit.json`).

## Surfaces

| # | Surface | What it shows | Read from |
| --- | --- | --- | --- |
| 01 | Overview | Four dials (choices waiting, 7-day budget, open handoffs, drift flags), the first items waiting on you, the Drift ≠ Run distinction | everything below |
| 02 | Idea intake | **Not wired to records yet**: a tile saying so and the intake thread link(s), i.e. threads.md lines that mention "intake" | threads.md |
| 03 | Questions | **Not wired to records yet**, same tile; recorded questions are open challenges and proposed decisions, shown in Choices | threads.md |
| 04 | Choices | Proposed decisions nobody has superseded (id, text, scope, proposer, decider, basis, locator, source line); handoffs that need you (prepared, Authorized not filled, addressed to you, or blocked); open challenges nobody has resolved. Accept / Reject / Defer on each | decisions.jsonl, challenges.jsonl, handoffs/ |
| 05 | Topology | Two SVG diagrams. **Information**: one row per lane; actors, threads from threads.md, conversation@1 captures; handoff flows sender → receiver with counts. **Authority**: owner → coordinator → lanes (assumed reporting line), plus standing delegations: in-force decisions whose text mentions delegating or authorizing, drawn to the lanes their scope names, labelled with the decision id and marked inferred | handoffs/, threads.md, captures, decisions.jsonl, .ledger.json |
| 06 | Budget control | Burn over the last 7 days against the weekly allowance, by lane and by actor; an advisory resource policy; a pre-flight estimator ("Should we spend on this question?") that runs in the page and records nothing | governance/budget.jsonl, governance/cockpit.json |
| 07 | Project lanes | One card per lane (from .ledger.json and threads.md): code, handoffs open/total, decisions and proposals, open challenges, threads, last activity | all |
| 08 | Run commitments | Handoffs by status; PR links found in threads.md; the Drift panel: proposals older than `--drift-days` (default 3) and challenges that mention drift | handoffs/, threads.md, ledgers |
| 09 | Review | The last 20 decisions, newest first; open challenges | ledgers |

### Choices compose; you apply (DEC-019)

The buttons write nothing. Accept and Reject ask "Settle this choice?" and then put a line into
the **To apply** box; Defer puts a comment there. You edit the placeholders in angle brackets,
copy, and run the lines in your records with `RELAYKIT_ACTOR=<owner>`:

| Item | Accept | Reject | Defer |
| --- | --- | --- | --- |
| proposed decision | `relay-kit ledger append --dir governance decision '{"status":"adopted","supersedes":"DEC-N",...}'` | same with `"status":"rejected"` | `# DEC-N: deferred ...` (nothing to append) |
| open challenge | `... challenge '{"status":"accepted-risk","resolves":"CH-N",...}'` | `... '{"status":"resolved","resolves":"CH-N",...}'` | comment |
| handoff | `relay-kit handoff --dir handoffs set HO-... sent` (or the next state), after you fill its Authorized section | `... set HO-... cancelled` | comment |

`ledger_append.py` fills the id and date and runs the ledger check, which refuses an adopted line
without decider, basis and locator. The basis placeholder is yours to replace with your words.

## Budget records: `governance/budget.jsonl`

Append-only JSON lines, one per reading:

| field | meaning |
| --- | --- |
| `ts` | ISO time (UTC) the line covers up to |
| `actor` | `assistant:lane#session`, or the owner |
| `window` | `session`: one session's or work block's use; `weekly`: a reading of the whole week so far (for example the plan's usage page) |
| `tokens_in`, `tokens_out` | integers ≥ 0 |
| `agents` | integer ≥ 0: agents running in that window (optional) |
| `note` | free text |

Add a line with `relay-kit budget-append --window session --tokens-in N --tokens-out N [--agents N]
[--note TEXT]` (actor from `RELAYKIT_ACTOR`, time now); commit it with the records. A correction is
a new line with a note, never an edit.

The gauge uses the latest `weekly` reading inside the 7 days if there is one, otherwise the sum of
`session` lines. The allowance comes from `--weekly-tokens N`, else `weekly_tokens` in
`governance/cockpit.json`. Without the file the gauge says "no budget records yet" and the page
shows this schema. Lines that don't parse are listed by line number. **The coordinator's
status-file cost lines are not parsed**; only this file counts.

`governance/cockpit.json` (optional, read-only to the cockpit):

```json
{"weekly_tokens": 5000000, "drift_days": 3, "brand": "Kairos",
 "policy": {"default_effort": "economy-first", "spawn_route": "coordinator only",
            "max_delegations_per_question": 2, "claude_build_context_cap": 4}}
```

`policy` is shown as the **advisory** resource policy (keys: `default_effort`, `spawn_route`,
`weekly_tokens_soft`, `max_delegations_per_question`, `chatgpt_standing_thread_cap`,
`claude_build_context_cap`, `codex_repo_context_cap`, `human_interruptions_per_day`). Unset means
unknown, not unlimited. Nothing enforces it.

## What it never does

- Write to the records, the ledgers, the handoffs or any capture. It reads them and writes only
  the two files in `--out`.
- Fetch anything: no GitHub calls for pull requests, no provider usage, no fonts. (The destination
  guard below may ask GitHub whether a repository is public, as export does.)
- Decide: delegation edges are readings of decisions, marked inferred; the owner → coordinator →
  lane lines are an assumed structure, not a decision.
- Show idea intake or question stewards it can't read: those surfaces say "not wired to records
  yet" instead of showing placeholder content.

## `--content metadata`

Leaves out decision text, scope, basis and locator, challenge concerns, handoff titles and bodies,
capture titles and budget notes, in the page and in `cockpit.json`. Ids, statuses, dates, actors,
lanes and counts remain. Composed lines then say "As proposed in DEC-N" instead of copying the text.
It is redaction of content, not anonymisation: thread links and session ids still appear.

## Privacy and deploying

**The page holds your records' text by default.** It refuses to write inside the records or inside
a git repository that is public or whose visibility it can't confirm (the export's guard;
`--allow-public-dest` overrides). It sets `noindex` and `no-referrer`. Before you put it on the
web, put it behind access control, or build it with `--content metadata`.

AWS S3 (private bucket; serve it through CloudFront with access control, or open it with a
presigned link):

    relay-kit cockpit --root ~/records --out ~/cockpit --brand Kairos
    aws s3 sync ~/cockpit s3://YOUR-PRIVATE-BUCKET/cockpit/ --delete
    aws s3 presign s3://YOUR-PRIVATE-BUCKET/cockpit/index.html --expires-in 3600

AWS Amplify Hosting (manual deploy, then turn on password protection under Access control):

    relay-kit cockpit --root ~/records --out ~/cockpit && (cd ~/cockpit && zip -r ../cockpit.zip .)
    # Amplify console > Deploy without Git > drag cockpit.zip; then App settings > Access control

Rebuild and redeploy whenever the records change; the page never updates itself.
