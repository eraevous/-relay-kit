# Records storage: what the kit needs, and what each store gives

The kit keeps its records (handoffs, the decision and challenge ledgers, the thread index)
as plain files. Version 0.1 stores them in **git** only. This page says what any other store
would have to provide, so a future adapter can be judged against it rather than against git's
habits. Nothing here is built yet beyond git and a plain folder.

## The operations

| Operation | What the kit uses it for |
| --- | --- |
| `read(key)` → value, revision | every check and summary |
| `list(prefix)` → keys | finding handoffs, outputs |
| `create_if_absent(key, value)` | claiming a new handoff id; an existing file is never replaced |
| `write_if_revision(key, value, expected)` | updating a handoff only if nobody changed it since it was read (compare-and-swap) |
| `history(key)` → revisions | the append-only rule (`check --base`), "changed since", the audit trail |

Two cautions (from the review of this plan):
- **History is lineage, not authority.** Git, or S3 versioning, records *that* something
  changed and under which account. Who authorized it still comes only from the owner's
  quoted words in the record.
- **Capabilities differ, and that must show.** A store that can't do one of these operations
  must switch off what depends on it and say so, never quietly weaken it.

## What each store gives

| Store | read / list | create-if-absent | compare-and-swap | history | Notes |
| --- | --- | --- | --- | --- | --- |
| **Git repository** (shared remote) | yes | yes (`new` skips ids seen in any clone; a clash is a merge conflict) | yes (the shared-copy check, then git refusing a non-fast-forward push) | yes | What 0.1 uses. Two writers race only between `set` and `push`. |
| **Local folder, one machine** | yes | yes (exclusive create) | yes (lock file plus content check) | no | Works today without git; `status` and `check --base` say what they couldn't check. |
| **Synced folder** (Drive, Dropbox, OneDrive desktop apps) | yes | **no** across machines | **no** across machines | the service's own, not the kit's | Safe only with **one writer at a time**: two machines can overwrite each other with no error. |
| **S3 or compatible** | yes | yes (`If-None-Match: *` on put) | yes (`If-Match: <ETag>`) | yes (bucket versioning) | Possible adapter. Needs request signing (doable in the standard library) and credentials. |
| **Google Drive API** | yes | partly (search then create can race) | yes (revision or ETag checks on update) | yes (revisions) | Possible adapter; needs OAuth, so it's heavy for a stdlib-only kit. |
| **A small records server on localhost or a LAN** | yes | yes | yes | yes, if it keeps one | Possible adapter; the server becomes the single writer. Only as safe as where it runs. |

## The rule for adding one

1. Build the adapter against the five operations, with a test for each: in particular,
   two writers claiming the same id must produce exactly one success.
2. Declare which operations it can't do. The commands that depend on them refuse with a
   message, rather than running with weaker guarantees.
3. Change the contract (`governance/contracts.json`) only if the file format changes. A new
   store for the same files isn't a format change.
