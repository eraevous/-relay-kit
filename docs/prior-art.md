# Prior art: coordination problems this kit inherits

Several assistants working on shared records is a distributed system. Its problems
(collisions, races, lost messages, stale reads, work nobody closes) were solved long ago
in databases, job queues, version control and network protocols. This table maps each
problem the kit has met, or will meet, to the established pattern, and says where the
kit stands. The point is to use what exists and not invent a protocol.

Status: **in kit** (code and tests), **procedure** (a skill says so), **proposed**
(decision ledger), **open**.

| Problem | Seen here | Established pattern | Kit status |
| --- | --- | --- | --- |
| Two writers pick the same id | HO-20261029-001, two clones, 2026-10-29 | **Partition the id space** by writer (namespaced ids, as in UUIDs with a node part, or Snowflake ids); or **claim with compare-and-swap** (a create that fails if the id exists) | Remote check before `new`: in kit, but there's still a gap between fetch and push. Lane-prefixed ids remove the race entirely: proposed (handoff@2) |
| Two writers update one file | Concurrent `set` on one handoff | **Optimistic concurrency**: read a version, write only if unchanged; retry on conflict. Locally a lock file; across clones `git push` refusing a non-fast-forward is the compare-and-swap | In kit (hash check and lock). Across clones it relies on git refusing the push; procedure: pull, retry |
| Acting on a stale copy | The colliding session hadn't fetched | **Read before write / read-your-writes**: refresh before deciding | `status.py` reports how far behind the copy is and says PULL FIRST: in kit |
| Delivered but never picked up | `sent` handoffs with no acknowledgement | **Acknowledgements with timeouts** (TCP retransmit, message-queue ack deadlines) | `status.py` flags NO ACK after `--stale-hours`: in kit. Who re-sends: open |
| Picked up, then abandoned | A claimed handoff with no progress | **Leases**: a claim expires unless renewed (a job queue's visibility timeout, a lock lease) | `status.py` flags STALE CLAIM: in kit. Automatic expiry: open (the owner reassigns) |
| Done but never closed | Three handoffs with outputs filed, still `sent` (CH-010) | **Completion is part of the transaction**: the step that writes the result also records the commit (write-ahead log, two-phase commit's commit record) | Flagged by `status.py`: in kit. Close-out rule (whoever files the output records `completed`): proposed |
| Started without permission | Codex ran an unauthorized pilot | **Two-person rule, guarded states**: a transition is refused unless its precondition holds | `sent` refuses an unfilled draft; pickup refuses NOT AUTHORIZED: in kit and procedure |
| Who said what, in what order | Relayed text mistaken for the owner's | **Message ids and causal links** (Message-ID / In-Reply-To in email), **append-only event log** as the source of truth (event sourcing) | conversation@1 ids, `reply_to`, append-only logs and ledgers: in kit |
| Two copies of a rule drift apart | Skills on claude.ai and ChatGPT behind the repo | **Versioned interfaces and cache invalidation**: stamp the version, check it at use | Contract markers checked by tests: in kit. Uploaded skill copies carry no version check: open |
| Too much work in flight | More threads than the owner can follow | **WIP limits** (Kanban; Little's law: more in flight means each item waits longer) and **backpressure** | Thread budget: proposed |
| Checking your own work | A model agreeing with itself | **Independent verification** (IV&V, N-version programming): the checker doesn't share the builder's reasoning | P-05; a fresh-context review per task: proposed |
| Who coordinates | One coordinator or none? | **Orchestration vs choreography**; **single writer per record** | Choreography over shared files, each state transition owned by sender, recipient or owner: in kit. No general coordinator by design |

## Deliberately not adopted

Consensus protocols (Paxos, Raft), CRDTs and distributed locks solve problems at a scale
this kit doesn't have: a few writers, one owner, and git as the shared store. The
owner arbitrates, and git's history is the log. Revisit if the number of writers or the
rate of writes grows enough that conflicts become routine.

Agent-to-agent protocols (MCP for tools, A2A-style task exchange) are worth reading for
their task-state and handoff vocabularies. They aren't adopted: the kit's assistants
mostly run in chat apps with no shared server, and the kit stays native-first.
