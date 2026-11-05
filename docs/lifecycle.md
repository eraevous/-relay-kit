# Ledger lifecycle: `relay-kit/lifecycle@1`

What the decision and challenge ledgers say is **open**, **settled**, **in force** or **contested**,
decided once, in [tools/lifecycle.py](../tools/lifecycle.py), and used by every reader:
`check_ledgers`, `status`, [export](export.md), the [MCP server](mcp.md) and the
[Cockpit](cockpit.md). Before this, each reader made its own set of "superseded" and "resolved" ids,
and some counted any line that named one, a proposal included. A proposal could then hide the item it
proposed to replace.

The module is pure: it reads no files and touches no network. `resolve(decisions, challenges, as_of)`
takes the ledger lines in file order and returns, per id, its state, what closed it, and the
problems and conflicts it found.

## Transitions

| Line | Field | Takes effect when | Effect |
| --- | --- | --- | --- |
| decision, `adopted` | `supersedes: DEC-N` | always | closes DEC-N: a proposal is **settled**, an adopted decision **superseded** |
| decision, `rejected` or `superseded` | `supersedes: DEC-N` | DEC-N is a proposal | settles the proposal (rejected, or closed as overtaken without adopting anything) |
| decision, `rejected` or `superseded` | `supersedes: DEC-N` | DEC-N is adopted | **no effect, reported.** Retiring an adopted decision takes an adopted line that supersedes or revokes it |
| decision, `proposed` | `supersedes: DEC-N` | never | a **proposed replacement**: DEC-N stays as it was, shown with it |
| decision, `adopted` | `revokes: DEC-N` (or a list) | DEC-N is adopted | DEC-N is **revoked** from the line's `effective_from`, else its `date` |
| decision, `adopted` | `amends: DEC-N` or `{"id": "DEC-N", "clauses": ["2"]}` | always | a partial amendment: DEC-N stays standing and lists it |
| challenge, `resolved` or `accepted-risk` | `resolves: CH-N` | always | closes CH-N |
| challenge, `open` | `resolves: CH-N` | never | **no effect, reported** |

Every transition must point at an **earlier, different, known** record: a self, forward or unknown
reference is reported and takes no effect. Since every target is earlier, there are no cycles.

**The fifth case of the review** (a `superseded` line targeting an adopted decision): rejected as
invalid, not read as retiring it. `superseded` is what the Cockpit's "Close as overtaken" writes to
close a proposal without adopting anything; letting it also retire an adopted decision would let a
non-adopting line end an adoption. An adopted line that supersedes or revokes it is the way.

**Conflicts.** When more than one effective line closes the same record, the record is closed, every
closer is kept in `closed_by`, and the conflict is reported. Nothing chooses one silently.

## Applicability of an adopted decision

| Value | When |
| --- | --- |
| `adopted, no structured supersession found` | adopted, and nothing below applies. This is what `in_force` means: no recorded line ends it, not that it is known to still apply |
| `superseded` | an adopted later line supersedes it |
| `revoked` | an adopted later line revokes it, from that line's `effective_from` (else its date) |
| `expired` | its own `effective_until` is before `as_of` |
| `not yet effective` | its own `effective_from` is after `as_of` |

`effective_from` and `effective_until` are `YYYY-MM-DD` (a start is the beginning of that day UTC, an
end its last second) or an ISO time in UTC; anything else is reported. Without `as_of`, dates are not
evaluated. Export evaluates them against the ledger's own latest date, never the clock.

A proposal is `open` or `settled`; a `rejected` or `superseded` line is "not a standing decision".
A challenge is open when its status is `open` and no effective line resolves it.

**No migration.** Older records carry none of `revokes`, `amends` with clauses, or effective dates,
so they read as before. Nothing infers a revocation or an expiry from prose. A rule tightened and
later relaxed is represented by the owner appending an adopted line that `amends` (clause-scoped) or
`revokes` it; a delegation limited to a day is represented by `effective_until` on it.

## Where it shows

- `check_ledgers.py` refuses any transition that would take no effect, and malformed `revokes`,
  `amends` and effective dates.
- `status` lists only items still open, notes a proposed replacement beside its target, and prints
  the transitions that take no effect and the conflicts.
- Export records gain `applicability`, `closed_by`, `proposed_replacements`, `amended_by`, `conflict`
  and `transition_problems` (decisions), and `open`, `closed_by` and `conflict` (challenges).
  `in_force` follows the table above.
- The MCP server's `status` tool uses the export's resolution.
- The Cockpit's Choices, Questions and Review use it, and every line it composes carries
  `--expect` (see [cockpit.md](cockpit.md)).

The fixture table [tests/fixtures/lifecycle_cases.json](../tests/fixtures/lifecycle_cases.json)
holds the six reproductions from the coordination review and the other cases; `tests/test_lifecycle.py`
holds every reader to it.
