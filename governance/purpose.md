# Purpose and invariants

**Purpose:** let AI assistants carry real work between each other and across time
(relay, capture, handoff, successor threads) without losing who said what, who authorized
what, or where the work stands. It should save the owner attention, not create a second
job of bookkeeping.

The invariants below hold whatever model, mode, browser or delivery route is used. Each
one names how it's enforced:
- **software**, checked by the kit's code and tests where the kit's scripts run;
- **procedure**, followed by assistants because a skill says so, with nothing to stop a
  model that ignores it.

Native chat tools can't enforce most of these, so "procedure" isn't a defect to hide.
It's the honest boundary of what this kit can guarantee. A change to an invariant is a
decision recorded in `decisions.jsonl`.

## P-01 Only the owner authorizes
Permission to act comes from the owner's own words: in the current chat, or quoted with
a locator in a handoff's Authorized section, with what the approval permits and doesn't
cover.
- Enforced by: software (handoff ledger: owner-only cancel, sender/recipient name checks,
  a draft with placeholders can't be sent) + procedure (every skill).
- Limit: actor names are typed, not authenticated (README, Status and limits).
- Tests: `ledger: only the owner cancels`, `ledger: wrong sender refused`,
  `ledger: a TODO-filled draft can't be sent`.

## P-02 Proposals and reports are not permission
A model's suggestion, a relayed message, or a decision a model reports the owner made is
a proposal or history until the owner confirms it.
- Enforced by: procedure (handoff-ledger "Which text binds", relay-governor, thread-seed
  status ledger).

## P-03 Sent is not done
Delivery, acknowledgement and completion are separate states. A handoff is completed
only when its output is filed.
- Enforced by: software (ledger state machine).
- Tests: `ledger: can't skip delivery`, `ledger: completed needs the output file`,
  `ledger: completed can't reopen`.

## P-04 Every message keeps its source
Messages carry stable ids; relayed and pasted material is marked and kept apart from the
owner's own words.
- Enforced by: software (conversation schema ids; paste and relay detection).
- Tests: `schema: renderer tags the version and gives every message a unique mid`,
  `ssd: a FROM ... END FROM block after your words is stripped from own text`,
  `seed: a FROM block inside your message is flagged as a paste, speaker stays you`.

## P-05 Verification means fresh evidence, not agreement
A reply is done only on an explicit completion signal; a quote counts only if it is in
exactly the message it cites; a second model agreeing is not a check.
- Enforced by: software (relay completion, quote verification) + procedure (reviews).
- Tests: `relay: text that stops changing without a signal is never "done"`,
  `seed: quote cited to the wrong message fails`.

## P-06 Say what wasn't checked
Every tool reports what it verified and what it couldn't, rather than returning a clean
result over missing data.
- Enforced by: software where data can be missing (render, SSD, telemetry) + procedure.
- Tests: `capture: ambiguous active branch is an error, not an empty 'ok'`,
  `ssd: unsupported JSON fails loudly`,
  `schema: chat-telemetry reads a normalized capture and says what it can't measure`.

## P-07 The owner's words stay private
Real conversations, handoffs and evidence live in a private repository. The public kit
holds code, docs and neutral test data only.
- Enforced by: procedure (repository split) + a pre-publish scan (see CONTRIBUTING).
