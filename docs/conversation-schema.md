# Conversation schema: `relay-kit/conversation@1`

The one format the kit's tools share. chat-capture's renderer writes it; thread-seed,
selective-self-distillation and chat-telemetry read it. Each reader refuses any other
`schema` value, and accepts files with no `schema` field (captures made before it existed).

```json
{
  "schema": "relay-kit/conversation@1",
  "meta": { "title": "...", "platform": "chatgpt", "conversation_id": "...", "models_seen": [],
            "raw_sha256": "...", "timezone": "America/Chicago", "checks": ["ok"], "...": "..." },
  "hidden_counts": { "assistant:reasoning_recap": 1 },
  "turns": [
    { "n": 1, "role": "user", "label": "You",
      "messages": [ { "mid": "T1u", "id": "<platform message id>", "ts": "2026-10-28T19:17:25Z",
                      "model": null, "dictated": false, "text": "...", "sources": [] } ] },
    { "n": 1, "role": "assistant", "label": "ChatGPT",
      "messages": [ { "mid": "T1a", "...": "..." }, { "mid": "T1a2", "...": "..." } ] }
  ]
}
```

**Message ids (`mid`)** are the kit's shared reference, used in seeds, handoffs
(`last_processed`), quote checks and SSD records.
- `T<n>` is the exchange: a user turn and the reply after it share `n`.
- Then `u` for the user side or `a` for the reply, and `2`, `3`… for later messages in
  the same turn: `T3u`, `T3a`, `T3a2`.
- Replies before the first user turn are exchange 0: `T0a`.
- They're computed once, by the renderer. Readers use the stored value and fall back to
  the same rule only for older files.

**Markdown** carries the same ids: every message starts with a marker comment whose first
tag is its id, e.g. `<!-- T3a2 · 2026-10-28 14:17:30 CDT -->`. JSON stays the canonical
form; Markdown is for reading.

**What it doesn't hold:** tool calls and other hidden events are counted in
`hidden_counts`, not listed (use `--include-hidden` on the renderer to see them in the
Markdown). chat-telemetry therefore reads timing from it but can't count tool steps or
in-turn hangs; for those, give telemetry the raw capture.

A breaking change gets a new version (`@2`), and the readers are updated in the same change.
