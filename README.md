# Relay kit

Skills for passing work between AI assistants (Claude and ChatGPT, or two Claude
threads) without losing track of who said what, who authorized what, and where the
work stands.

They're written as [Claude skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview):
each is a `SKILL.md` that an assistant reads and follows, with any code it needs
embedded in it. The procedures also work as plain instructions for other assistants.

## The skills

| Skill | What it does | Code |
| --- | --- | --- |
| **chat-relay** | Fills a chat box in the browser, sends, waits for an explicit "finished" signal, returns the reply | `fill.js`, `send_wait.js` |
| **relay-governor** | Policy for multi-round relays: a run card, budgets, permission tiers, stop conditions, provenance tags | none |
| **chat-capture** | Saves a full ChatGPT or Claude conversation as timestamped Markdown and JSON | `capture.js`, DOM fallback, `render.py` |
| **chat-telemetry** | Reply latency, turn duration, hangs and message sizes, with a dashboard | `chat_telemetry.py` |
| **thread-map** | Splits a long conversation into a navigable map of segments and open loops | none |
| **thread-seed** | Writes the opening message for a successor thread, with checked quotes | `seed_index.py` |
| **selective-self-distillation** | Pulls the user's own words and positions out of mixed human/AI transcripts | `extract_turns.py` |
| **meta-distill** | Turns an exchange into a reusable method, with who contributed what | none |
| **handoff-ledger** | One file per handoff: task, the owner's authorization, proposals kept separate, and a status log | `handoff.py` |

How they fit: relay-governor → chat-relay → chat-capture → chat-telemetry / thread-map →
thread-seed. handoff-ledger works beside the chain: a handoff can travel by relay, a
shared folder or a paste.

## Principles

- **Only the owner authorizes.** The owner is the person the assistants work for. A
  model's reply, a relayed message or a reported decision is a proposal or history,
  never permission.
- **Sent is not done.** Delivery, acknowledgement and completion are separate states.
- **Say what you can't verify.** Every script reports what it checked and what it couldn't.

## Install

- **claude.ai:** run `python3 tools_extract.py --zips`, then upload the zips from `upload/`
  as skills (one per skill).
- **Claude Code:** copy the folders under `skills/` into `~/.claude/skills/`.
- **Other assistants:** attach or paste the `SKILL.md` you need.

Browser steps (chat-relay, chat-capture's live path) need a browser tool that can run
JavaScript in a signed-in page, such as Claude's built-in browser or Claude in Chrome.

## Setup

- **Handoff folder:** keep it private, since it holds the owner's words. Put a
  `.ledger.json` in it naming the owner: `{"owner": "sam"}`.
- **Names:** selective-self-distillation takes `--user-names` and `--assistant-names`, and
  thread-seed takes `--names ChatGPT=Ada`, so transcripts that use names instead of
  "You"/"ChatGPT" are read correctly.
- **Time zone:** defaults to America/Chicago; pass `--tz`. Without IANA time zone data
  (e.g. Windows without `tzdata`), the scripts fall back to UTC and say so.

## Docs

- [Conversation schema](docs/conversation-schema.md): the shared format and message ids (`T3a2`).
- [Marking pasted material](docs/paste-markers.md): phone-friendly `FROM:` … `END FROM` blocks.
- [Cold catch-up test](docs/cold-test.md): checking whether a fresh assistant can pick up a handoff.

## Tests

```
python3 -I -B tests/run_tests.py     # ledger, capture, seed, SSD, telemetry, schema, a live capture
node tests/browser_tests.cjs         # relay and capture scripts against mocked pages
```

Standard library only; no network or accounts. The browser tests check the scripts'
logic, not the current markup of chatgpt.com or claude.ai.

`extracted/` holds the code from each `SKILL.md` as standalone files and is generated:
run `python3 tools_extract.py` after editing a skill. The `SKILL.md` is the source of truth.

## Status and limits

- **Live-checked on chatgpt.com (signed in), 2026-10-28:** fill, send, the completion
  signal and the conversation endpoint. Not yet checked live: claude.ai, a long reply
  with tool pauses, and the DOM fallback.
- **Expect breakage:** site markup changes, and the capture endpoints are undocumented.
  The selector table in chat-relay records what was verified when.
- **The ledger is a prototype:** actor names aren't authenticated, the log is append-only
  by convention rather than tamper-evident, and the lock is local to one machine. Keep the
  handoff folder in git if you need history you can trust.
- **Cold catch-up tests:** three runs across ChatGPT's Work mode, a Temporary chat and a
  regular chat all caught up from two files and stayed in scope. Their findings shaped the
  current Authorized and logging rules.

## License

Licensed under the [MIT License](LICENSE). Contributions are welcome under the
[Contributor License Agreement](CLA.md), which keeps them open under the same license
while letting the maintainer also offer commercial versions. See
[CONTRIBUTING.md](CONTRIBUTING.md).
