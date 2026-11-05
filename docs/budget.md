# Budget records: `relay-kit/budget@2`

`governance/budget.jsonl` in the records: append-only JSON lines, one per reading of compute or time
spent. Read through [tools/budget.py](../tools/budget.py) by every reader (the [Cockpit](cockpit.md)
today, the telemetry view next), and written with `relay-kit budget-append`.

## Fields

| Field | Since | Meaning |
| --- | --- | --- |
| `ts` | @1 | ISO time (UTC) the line was recorded, or covers up to |
| `actor` | @1 | `assistant:lane#session`, or the owner |
| `window` | @1 | `session`: one session's or work block's use; `weekly`: a reading of the whole week so far |
| `tokens_in`, `tokens_out` | @1 | integers >= 0 |
| `agents`, `note`, `human_minutes`, `merges` | @1 | optional |
| `id` | @2 | stable id. A line without one is known by `bud-` + the first 10 hex digits of its sha256 (stable, since the file is append-only) |
| `method` | @2 | `measured` (a provider or tool meter: Claude Code's session logs, GitHub timestamps), `proxy` (a stand-in: a merge window, a harness-reported total), `estimate` (anything else). Required on new lines; an older line without it reads as `unlabelled`, never as measured |
| `interval_start`, `interval_end` | @2 | what the reading covers (ISO). Start is not after end |
| `includes_children` | @2 | `{"included": true or false, "ids": [...]}`: whether sub-agent use is inside these numbers. Listed child lines are then not counted again |
| `correction_of` | @2 | the id of an earlier line this one **replaces**: readers count this line instead, never both. Relabelling an old line (say, as an `estimate`) is a correction carrying the same numbers |
| `metric_version` | @2 | the definition the numbers follow (`budget@2`) |
| `scope` | @2 | what the numbers cover, in words ("Kairos threads only") |
| `source` | @2 | where the numbers came from (a log location, a capture id, a status file) |

**Corrections.** A chain resolves to its last line. Two lines correcting the same line are a
**conflict**: both are counted and the conflict is reported, rather than one being dropped silently.
A correction naming no known line is reported, and `budget-append` refuses to write one.

**Missing is not zero.** A reader that has no line for an actor or a period says so; it never fills
in a 0.

## Writing

```
relay-kit budget-append --window session --tokens-in N --tokens-out N --method measured \
    [--interval-start ISO --interval-end ISO] [--includes-children yes|no [--child-id ID ...]] \
    [--correction-of ID] [--scope TEXT] [--source TEXT] [--agents N] [--note TEXT]
```

The actor comes from `RELAYKIT_ACTOR` and the time is now, unless given. The line's id is computed
from its content, and the line is printed. Commit it with the records.

## Migration

Nothing is edited. Lines written before @2 stay as they are and read as `unlabelled`. To label them,
append one correction per line with the same numbers and `--method estimate` (or what they are).
Append those corrections only once the readers in use understand @2: a reader from before @2 would
add a correction to the line it replaces.
