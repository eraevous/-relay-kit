# Pull-request reader: `relay-kit/prs@1`

`relay-kit prs` reads pull-request state from GitHub, at build time and only when you run it, and
appends what changed to `governance/pr-state.jsonl` in the records. The [Cockpit](cockpit.md)
renders that file; it never fetches. Other readers that need pull-request data should read the
file too, so there is one GitHub reader, not one per tool.

    relay-kit prs --root RECORDS [--repo OWNER/NAME ...] [--since-days 14] [--limit 30]
                  [--via gh|token] [--token-env NAME] [--dry-run]

- **Which repositories:** `--repo` (repeatable), else `"repos": ["owner/name", ...]` in
  `handoffs/.ledger.json`. Anything that isn't `OWNER/NAME` is refused.
- **Which pull requests:** per repository, the `--limit` most recently updated; all open ones among
  them, and closed or merged ones updated within `--since-days`.
- **How it reaches GitHub:** the `gh` CLI (`--via gh`, the default when it is installed), or the REST
  API with a token from the environment (`--via token`; `--token-env NAME`, default `GITHUB_TOKEN`
  then `GH_TOKEN`). The token is never written or printed. Standard library only.
- **What it writes:** appends to `governance/pr-state.jsonl` and nothing else, anywhere. It never
  rewrites a line; a line it can't read stops it (fix the file by hand) rather than being repaired.
  `--dry-run` prints the lines instead.

## Lines

Every line has `"schema": "relay-kit/prs@1"` and a `kind`.

**`"kind": "pr"`**, written only when something about the pull request changed since its last line
(any field but `ts`):

| Field | Meaning |
| --- | --- |
| `ts` | when this run read it (UTC) |
| `repo`, `number`, `url`, `title` | as on GitHub |
| `state` | `open`, `closed` or `merged`; `draft` true or false |
| `author` | the GitHub login that opened it. Agents often push under the owner's login, so this doesn't say who wrote it |
| `author_line` | the text of the body's first `Author:` line, or null: who says they wrote it |
| `created_at`, `updated_at`, `merged_at`, `closed_at`, `base`, `head` (`ref`, `sha`) | as on GitHub |
| `reviews` | every submitted review: `by` (login), `verdict` (GitHub's state), `at`, `signed` (a `Reviewer:` line in the review body, or null), `stated` (a verdict word in the review's first 160 characters, e.g. `APPROVED` or `REQUEST CHANGES`, for reviews posted as comments; or null) |
| `ci` | `state`: `failure` if any check run or status failed, else `pending` if any is still running, else `success`, or `none` when there are none; `failing`: their names |
| `body_sections` | `what`, `why`, `how`, `authorized`: the text under each heading, verbatim (HTML comments removed), up to 4000 characters each. A heading counts when its first word is the section's name ("What this changes" is `what`); headings in code fences don't |
| `missing`, `truncated` | sections absent or empty; sections cut at 4000 characters |

**`"kind": "fetch"`**, one per run: `ts`, `via`, `repos`, `ok`, `failed` (repo → error),
`since_days`, `prs_seen`, `appended`. A repository that fails doesn't stop the others.

Readers take the last `pr` line per `(repo, number)`, and a repository's freshness from its last
`fetch` line that lists it under `ok`. Lines of another schema version are unreadable to an @1
reader, not guessed at.

## The PR body template

[tools/templates/PULL_REQUEST_TEMPLATE.md](../tools/templates/PULL_REQUEST_TEMPLATE.md) has an
`Author:` line and the four sections. Copy it to `.github/pull_request_template.md` in a repository
to make it GitHub's default. An agent writing a PR names itself on the `Author:` line with its lane
and session; the Cockpit then shows "written by <agent>, not the owner".

## What it doesn't do

It summarises nothing, infers no authorship from commits, and decides nothing: whether a pull request
had enough reviews is for whoever reads it (the Cockpit states families; the rule is yours). It reads
no issue comments and no review threads, only submitted reviews. Running it is a network action;
the records hold only what GitHub returned to the account it ran as.
