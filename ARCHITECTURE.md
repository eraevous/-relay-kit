# Architecture

This kit grew out of real use: relays between ChatGPT and Claude, captures, handoffs to
fresh chats. This page fixes what that use showed to be load-bearing, and deliberately
leaves the rest open. Models, modes and chat interfaces change month to month, so the
architecture fixes **rules and interfaces**, not a topology of agents.

It has three layers.

## 1. Invariants (what never changes with the tools)

Full text, enforcement and tests: [governance/purpose.md](governance/purpose.md).

| | Invariant | Enforced by |
| --- | --- | --- |
| P-01 | Only the owner authorizes | software in the ledger (names, not identities) + procedure |
| P-02 | Proposals and reported decisions are not permission | procedure |
| P-03 | Sent is not done; completion needs a filed output | software |
| P-04 | Every message keeps its source; pastes and relays are marked | software |
| P-05 | Verification means fresh evidence, not agreement | software + procedure |
| P-06 | Say what wasn't checked | software where data can be missing + procedure |
| P-07 | The owner's words stay private | procedure (public kit / private records split) |

"Procedure" means an assistant follows it because a skill says so. Native chat tools
can't enforce permissions finely, and this kit prefers native tools over building an
orchestration layer, so each invariant says plainly which kind of guarantee it has.

## 2. Contracts (the interfaces every tool and assistant speaks)

Registry: [governance/contracts.json](governance/contracts.json).

| Contract | What it fixes | Spec |
| --- | --- | --- |
| `relay-kit/conversation@1` | conversation JSON and message ids (`T3a2`) | [docs/conversation-schema.md](docs/conversation-schema.md) |
| `relay-kit/handoff@1` | the handoff file, its states and log, the owner setting | [handoff-ledger](skills/handoff-ledger/SKILL.md) |
| `relay-kit/paste-markers@1` | `FROM:` … `END FROM` and `>"""` | [docs/paste-markers.md](docs/paste-markers.md) |
| `relay-kit/relay-result@1` | relay statuses: done / unconfirmed / pending | [chat-relay](skills/chat-relay/SKILL.md) |

**How a contract changes:** only by a new version. The spec, every implementer and the
tests change in the same commit, and the owner records the change in the decision ledger.
The tests fail if any implementer still names the old version, so a half-done change
can't pass.

## 3. Replaceable parts (chosen per task, free to change)

| Part | Current choices | Known from use |
| --- | --- | --- |
| Model and mode | Claude; ChatGPT Chat, Work or Temporary | Cold handoffs succeeded in all three ChatGPT modes |
| Delivery | browser relay (chat-relay), paste, shared folder | Paste with two files is the coldest and most portable route |
| Browser | Claude's built-in browser, Claude in Chrome | Site markup changes; the selector table records what was verified when |
| Contexts per task | one context, or inquiry + builder (+ fresh check) | Not yet compared; the first pilot does that |
| Record store | the owner's private repo | Chosen by the owner |

Nothing in this layer is mandated. Add a context, a role or a tool only when removing it
would make a result the owner cares about worse.

## Records

Kept proportionate; the owner shouldn't become a second-shift bookkeeper.

- **Handoff files**, in the owner's private folder: task, authorization, proposals, state.
- **Decision ledger and challenge ledger** (`decisions.jsonl`, `challenges.jsonl`), also
  private, append-only, checked by [tools/check_ledgers.py](tools/check_ledgers.py).
  Decisions record what the owner decided, in their words, with where they said it.
  Challenges record objections and known risks.
- Run notes go in the handoff's output. There is no separate run database.

Claims in records keep one of four labels where it matters: **stated preference**,
**belief to test**, **proposal**, **result** (with the artifact that shows it). A result
needs something inspectable: a test, a file, a captured conversation.

## How the architecture itself changes

1. A change starts as a **proposal** in the decision ledger (or a handoff).
2. If it's an empirical claim (say, "a separate verifier catches more"), it's tested on
   one real task against the simpler option, time-boxed, with the check written first.
3. The owner adopts, rejects or defers it; the ledger records that.

The first such test is set up: one context alone versus a handoff plus a fresh check, on
one Data-Guide task. It waits for the owner's authorization.

## What this deliberately doesn't do

No general orchestration layer, standing multi-agent team, fixed ChatGPT-versus-Claude
role map, or automatic sharing of the owner's history across models. Each would need a
measured reason first.

## Lineage

Draws on the owner's architecture proposal of 2026-10-28 (kept with the private records):
its four claim labels, "verify outcomes, not agreement", the A-versus-B selection policy,
and the rule that a tool-making role must show a second real use. Its 19 recommendations,
8 pilots and full templates were left out, to keep record-keeping proportionate.
