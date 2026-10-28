# Marking pasted material

Marker the kit's parsers already recognize (the first line `FROM: <name>`):

```
FROM: Ada (ChatGPT)
<pasted text, any formatting>
END FROM
```

- Put your own instruction *before* the paste. If END FROM gets lost, nothing
  of yours is swallowed into the quote.
- Labels: `FROM: Ada`, `FROM: Claude (AEC)`, `FROM: article`, `FROM: my notes`.
  The name is what the tools report as the speaker.
- Several pastes in one message: one FROM … END FROM block each.
- Code, or anything where exact characters matter: put it in a fence inside
  the block (```` ```` ```` if the text itself contains ``` ).
- Phone: set up text replacement (iPhone: Settings → General → Keyboard →
  Text Replacement; Android Gboard: personal dictionary shortcuts), e.g.
  `;fa` → `FROM: Ada (ChatGPT)` and `;ef` → `END FROM`.

Avoid as delimiters: straight quotes (`"""`), `--` and `...`. Phone keyboards
with smart punctuation turn them into “ ” — and …, so the marker no longer
matches. Letters, colons and line breaks survive every keyboard and renderer.
Per-line `>` quoting is impractical on a phone, and backticks are buried there.

How the kit reads it:
- A message that *opens* with `FROM:` is treated as that source's message, carried
  by you (thread-seed shows the speaker as e.g. "Ada (relayed)";
  selective-self-distillation flags it `relayed_from_model`; chat-telemetry counts it
  as a relayed turn).
- A `FROM:` … `END FROM` block *after* your own words is a paste inside your message:
  selective-self-distillation strips it from your own text, and thread-seed flags
  "contains paste". The speaker stays you.
- `>"""` pastes still work, including the smart-quoted `>“””` a phone produces.
