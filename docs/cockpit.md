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
| 05 | Topology | Two SVG diagrams. **Information**: one row per lane; actors, threads from threads.md, conversation@1 captures; handoff flows sender → receiver with counts. **Authority**: only *recorded* delegation, i.e. an adopted, in-force decision with a `delegates_to` field (a lane name or code, or a list); if none has one, the surface says "No recorded delegation" and draws nothing. A separate, visually distinct **hypothesised authority (inferred)** overlay, closed by default, holds the assumed owner → coordinator → lane lines and the text-matched delegations (decision text mentions delegating or authorizing; target read from its scope). Nothing executable is derived from either graph | handoffs/, threads.md, captures, decisions.jsonl, .ledger.json |
| 06 | Budget control | Burn over the last 7 days against the weekly allowance, by lane and by actor; an advisory resource policy; a pre-flight estimator ("Should we spend on this question?") that runs in the page and records nothing | governance/budget.jsonl, governance/cockpit.json |
| 07 | Project lanes | One card per lane (from .ledger.json and threads.md): code, handoffs open/total, decisions and proposals, open challenges, threads, last activity | all |
| 08 | Run commitments | **Authorized & in progress** (sent, acknowledged or in_progress, with a filled Authorized section; the only handoffs the gauge counts) apart from **pending authorization** (prepared, or no authorization quote); all handoffs by status; PR links found in threads.md; the Drift panel: proposals older than `--drift-days` (default 3) and challenges that mention drift | handoffs/, threads.md, ledgers |
| 09 | Review | The last 20 decisions, newest first; open challenges | ledgers |

### Choices compose; you apply (DEC-019)

The buttons write nothing. Accept and Reject ask "Settle this choice?" and then put a line into
the **To apply** box; Defer puts a comment there. **The owner supplies the justification**: each
line carries `<<REQUIRED: ...>>` placeholders for the decider, the basis (your words) and the
locator (where you said them), or for a challenge's resolution or a handoff note. The page never
fills in who decided or why, and never sets `recorded_by` (ledger_append takes it from
`RELAYKIT_ACTOR`; handoff.py takes the actor the same way). `check_ledgers.py` refuses any line
still holding `<<REQUIRED`, so a line pasted unedited is taken back out by ledger_append. Replace
the placeholders, copy, and run the lines in your records with `RELAYKIT_ACTOR=<owner>`:

| Item | Accept | Reject | Defer |
| --- | --- | --- | --- |
| proposed decision | `relay-kit ledger append --dir governance decision '{"status":"adopted","supersedes":"DEC-N",...}'` | same with `"status":"rejected"` | `# DEC-N: deferred ...` (nothing to append) |
| open challenge | `... challenge '{"status":"accepted-risk","resolves":"CH-N",...}'` | `... '{"status":"resolved","resolves":"CH-N",...}'` | comment |
| handoff | `relay-kit handoff --dir handoffs set HO-... <next>`, after you fill its Authorized section | `... set HO-... cancelled` | comment |

The handoff's `<next>` follows handoff.py's rules: prepared → sent, sent → acknowledged, acknowledged
→ in_progress, in_progress → completed (with a reminder to write `handoffs/outputs/HO-....md` first,
since handoff.py refuses completed without it), and blocked → the state it was blocked from, read from
its log.

**Your edits in To apply are kept.** Until you type in the box, each choice rebuilds it from the picks.
Once you have edited it, a new choice is added at the end (or replaces that item's earlier line if you
left that line untouched), and Clear asks before discarding your text.

`ledger_append.py` fills the id, date and recorded_by and runs the ledger check, which refuses an
adopted line without decider, basis and locator, and any line with a `<<REQUIRED` placeholder.
(handoff.py does not check notes for placeholders; the note is yours to write.)

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

Definitions:

- **Unit**: tokens (`tokens_in + tokens_out`). No money: there is no token-to-dollar rate.
- **Window**: the 7 days ending at the latest timestamp in the records (`as_of`), exclusive of the
  start; never the clock.
- **Provenance**: each line names its `actor`; the page reports what the lines say and nothing
  else. A line is an observation by whoever appended it, not a provider meter reading.
- **Aggregation**: the gauge, burn and by-lane / by-actor tables are the **sum of `session` lines**
  in the window. A `weekly` line is never added to that sum and never replaces it; the latest one
  in the window is shown separately as a labelled reference.
- **No manufactured zeros**: with no session lines in the window, burn is "no data", not 0; with
  no file, "no budget records yet".

The allowance comes from `--weekly-tokens N`, else `weekly_tokens` in `governance/cockpit.json`.
Without the file the page shows this schema. Lines that don't parse are listed by line number.
**The coordinator's status-file cost lines are not parsed**; only this file counts.

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
- Decide: the authority diagram shows only recorded delegation (`delegates_to`); text-matched
  edges and the owner → coordinator → lane lines live in a closed overlay labelled hypothesised.
  Nothing executable (permissions, routing, commands) is derived from either graph.
- Supply a justification: decider, basis and locator are always left for the owner.
- Show idea intake or question stewards it can't read: those surfaces say "not wired to records
  yet" instead of showing placeholder content.

## `--content metadata`

Leaves out decision text, scope, basis and locator, challenge concerns, handoff titles and bodies,
capture titles and budget notes, in the page and in `cockpit.json`. Ids, statuses, dates, actors,
lanes and counts remain. Composed lines then say "As proposed in DEC-N" instead of copying the text.
It is redaction of content, not anonymisation and not publication safety. What remains in
metadata mode: thread URLs (intake links, PR links), session ids inside actor names, actor and
owner names, lane names and codes, handoff and decision ids, dates, source file paths
(`governance/decisions.jsonl:12`, `handoffs/HO-...md`) and counts.

## Privacy and deploying

**The page holds your records' text by default.** It refuses to write inside the records or inside
a git repository that is public or whose visibility it can't confirm (the export's guard;
`--allow-public-dest` overrides). It sets `noindex` and `no-referrer`, but **`noindex` is not
access control**. Serving the folder from a domain requires authentication on **both**
`index.html` and `cockpit.json` (the JSON holds everything the page shows). `--content metadata`
reduces what is exposed (see above); it does not make the folder safe to publish.

In both recipes access control is on, and checked, **before** any records go up.

AWS S3 (private bucket; serve it through CloudFront with access control, or open it with a
presigned link):

    # all four settings must be true before anything is synced
    aws s3api get-public-access-block --bucket YOUR-PRIVATE-BUCKET
    relay-kit cockpit --root ~/records --out ~/cockpit --brand Kairos
    aws s3 sync ~/cockpit s3://YOUR-PRIVATE-BUCKET/cockpit/ --delete
    aws s3 presign s3://YOUR-PRIVATE-BUCKET/cockpit/index.html --expires-in 3600

AWS Amplify Hosting (password protection is set per branch, so the branch has to exist first;
give it a placeholder that holds no records):

    # 1. a placeholder, no records in it
    mkdir -p ~/cockpit-placeholder && printf '<!doctype html><title>placeholder</title>\n' > ~/cockpit-placeholder/index.html
    (cd ~/cockpit-placeholder && zip -r ../placeholder.zip .)
    # Amplify console > Deploy without Git > drag placeholder.zip
    # 2. App settings > Access control > Manage access: password-protect the branch
    # 3. verify: both paths must answer 401 without the password
    curl -s -o /dev/null -w '%{http_code}\n' https://BRANCH.APP-ID.amplifyapp.com/index.html
    curl -s -o /dev/null -w '%{http_code}\n' https://BRANCH.APP-ID.amplifyapp.com/cockpit.json
    # 4. only then build and upload the records
    relay-kit cockpit --root ~/records --out ~/cockpit && (cd ~/cockpit && zip -r ../cockpit.zip .)
    # Amplify console > the same branch > Deploy updates > drag cockpit.zip

Rebuild and redeploy whenever the records change; the page never updates itself.

## Restoration candidates from v0.4

Parts of Kate's prototype that were dropped because the records can't feed them yet. Each needs a
record format (and a decision) before it can come back:

- **Choices lifecycle**: Open / Leaning / Settled / Reconsider / Deferred, with alternatives,
  evidence, objections, uncertainty and consequences per choice (the ledger has proposed /
  adopted / rejected / superseded only).
- **Provenance chain**: Source → Evidence → Interpretation → Assertion → Decision, so a decision
  resolves to what it rests on ("Why did we choose this?").
- **Budget profiles**: soft and hard limits per resource, effort profiles, and attention budgeting
  (human interruptions per day), beyond the advisory caps shown now.
- **Browser-local capture and JSON import/export**: Idea intake and Question stewards as local
  notebooks with export to the records, instead of the "not wired to records yet" tiles.
