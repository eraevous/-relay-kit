---
name: "selective-self-distillation"
description: "Extract the user's own contributions from mixed human-AI transcripts and rebuild their thinking with evidence, separating their words from AI proposals. Use when the user asks for SSD or self-distillation."
---

# Selective Self-Distillation

Reconstruct the trajectory of the user's own thinking from mixed human-AI transcripts, rather than summarizing the whole exchange. The output must never let a polished AI description replace the author's record.

Use it when the user says "Selective Self-Distillation", "SSD", "self-distill", "what did I actually say", or asks to pull their ideas, positions or voice out of conversations, chat exports or a notes vault.

## Inputs

Any of these, mixed:
- Markdown or text transcripts with "You said:" / "<Name> said:" headers (chat-capture's Markdown included). If the user's own turns are headed with their name ("Sam said:"), pass `--user-names Sam` so those count as theirs
- chat-capture normalized JSON (schema `relay-kit/conversation@1`)
- ChatGPT `conversations.json` exports
- Claude `conversations.json` exports
- A pasted single conversation (treat its turns the same way)

If no files are given, apply the method to the current conversation.

## Known traps in mixed transcripts

- **Paste delimiters.** Pasted material marked `>"""` ... `"""` (or a `FROM:` … `END FROM` block, see the kit's docs/paste-markers.md) is not the user's.
- **Undelimited pastes.** Long turns with heavy markdown, tables or an AI-style opening ("Here's...", "Great prompt...") usually contain a pasted model reply. Use only the lines before the paste, or flag the turn.
- **Inverted roles.** In some experiments an AI plays the user (a "Human Prompter" role-play, say). There the "You said" turns are model text. Check the first turn of any thread that sets up role-play, and exclude it if so.
- **Relays.** Users pass replies between sibling threads ("your sister's response", "another thread said"). The relayed text is not theirs; their framing sentence is.
- **Coinage drift.** A model often names the user's idea in its reply (Selective Self-Distillation itself was named that way). Credit the idea to the user and the label to whoever coined it.
- **Silence is not adoption.** Continuing the conversation does not mean the user accepted a proposal. Adoption needs an explicit statement ("I like", "let's go with", "yes, that").

## Procedure

1. **Extract.** Write the script at the end of this file to a working directory outside the inputs, then run `python3 -I extract_turns.py INPUTS -o turns.json [--user-names NAME]`. It keeps one copy of duplicate turns, strips delimited pastes and quoted lines into `own`, and flags likely pastes and relays. Report the counts.
2. **Screen flags.** Read every flagged turn. Keep the user's framing sentences, drop the pasted body, and mark role inversions and relays. Never quote a flagged region as theirs.
3. **Resolve references minimally.** When a turn says "that third option" or "I like it", add only the smallest fragment of the model reply needed to understand it, labeled as model text.
4. **Label every claim.** Use: Direct (their words) · Adopted (a model proposal they explicitly accepted, with the acceptance quoted) · Revised (their change to a proposal) · AI proposal (no acceptance) · Unresolved.
5. **Track changes of mind.** List positions they changed, quoting before and after with locations. A later statement outranks an earlier one; keep both.
6. **Reconstruct.** Write a concise account of their positions in a voice close to theirs, keeping their metaphors and register. Every strong sentence must trace to a Direct or Adopted row.
7. **Verify quotes.** Check every quotation against the source text programmatically (normalize whitespace and curly quotes). Fix or drop any that fail. Report how many were checked.

## Output

Deliver these, in a doc when the result is long:
1. **Evidence table**: claim · their words (verbatim) · source and line · label.
2. **Reconstruction**: positions in their voice, with sources.
3. **Changes of mind**: before and after, with locations.
4. **How they work**: recurring conversational moves, with approximate counts and one example each, labeled as keyword counts.
5. **Unresolved attribution**: items where authorship can't be settled, and the check that would settle each.
6. **Corpus file**: the extracted `turns.json` (and a readable Markdown version) so the evidence travels with the synthesis.

## Checks before delivering

- Is any sentence attributing a belief to the user supported only by a model reply? Remove it or relabel it AI proposal.
- Did a flagged or relayed region get quoted as theirs? Re-run step 2.
- Are labels on names (coinages) separated from labels on ideas?
- Did every quote pass verification?

## extract_turns.py

```python
#!/usr/bin/env python3
'''Extract the user's own turns from chat exports, with provenance and flags.
Inputs (any mix, files or folders): Markdown/text transcripts with "You said:" /
"<Name> said:" headers (chat-capture .md included); chat-capture normalized .json;
ChatGPT conversations.json; Claude conversations.json. Unsupported JSON is reported, not
skipped silently, and the run fails if nothing was extracted. Output is UTF-8.
Usage: python3 -I extract_turns.py INPUT [INPUT ...] -o turns.json [--delim '>"""']
'''
import argparse, hashlib, json, os, re, sys

ASSISTANTS = ['Claude', 'ChatGPT', 'Gemini', 'Copilot']   # extend with --assistant-names

def header_patterns(user_names):
    '''User headers: "You said:" etc., plus "<name> said:" for each --user-names entry.'''
    names = '|'.join(re.escape(n) for n in user_names)
    U = re.compile(r'^#{0,6}\s*(You said|User|Human|Me' + (f'|(?:{names}) said' if names else '') + r')\s*:?\s*$', re.I)
    A = re.compile(r'^#{0,6}\s*(?!You\b|When\b' + (f'|(?:{names})\b' if names else '') + r')[\w .-]{1,30}\s+said\s*:?\s*$'
                   r'|^#{0,6}\s*(Assistant|' + '|'.join(map(re.escape, ASSISTANTS)) + r')\s*:?\s*$', re.I)
    return U, A

U, A = header_patterns([])

def relay_pattern(names):
    return re.compile(r'^\W*(FROM:\s*\**\s*\w|(' + '|'.join(map(re.escape, names)) + r')(\s*\([^)]*\))?\s+here\b)', re.I)

RELAY = relay_pattern(ASSISTANTS)
CAPTURE_MARK = re.compile(r'^\s*(<!--.*?-->|---)\s*$')

# relay-kit/paste-markers@1: FROM: ... END FROM blocks and >""" pastes
FROM_BLOCK = re.compile(r'^\W*FROM:[^\n]*\n[\s\S]*?(?:^\s*END FROM\s*$|\Z)', re.M | re.I)

def strip_pastes(t, delim):
    if not RELAY.match(t):   # a FROM: block after your own words is a paste; a message that opens with one is a relay
        t = FROM_BLOCK.sub(' [PASTE] ', t)
    if delim:
        d = re.escape(delim).replace('\\"', '"').replace('"', '["“”]')   # phones turn """ into “””
        t = re.sub(d + r'[\s\S]*?(["“”]{3}|$)', ' [PASTE] ', t)
    lines = ['[QUOTE]' if l.lstrip().startswith('>') else l for l in t.split('\n')]
    return re.sub(r'(\[QUOTE\]\n?)+', '[QUOTE]\n', '\n'.join(lines)).strip()

def likely_paste(t):
    w = len(t.split())
    if w < 250: return False
    return (t.count('**') / w > 0.02 or t.count('|') / w > 0.05
            or len(re.findall(r'^#+ ', t, re.M)) >= 3
            or bool(re.match(r"\s*(Here's|Here’s|Great (prompt|question)|Absolutely|Certainly|This (plan|is a))", t)))

def from_markdown(path, text):
    out, cur, buf, start = [], None, [], 0
    def flush():
        if cur == 'user' and '\n'.join(buf).strip():
            out.append((start, '\n'.join(buf).strip()))
    for i, l in enumerate(text.split('\n'), 1):
        if U.match(l): flush(); cur, buf, start = 'user', [], i + 1
        elif A.match(l): flush(); cur, buf = 'ai', []
        elif cur == 'user' and not CAPTURE_MARK.match(l): buf.append(l)
    flush()
    return out

def from_chatgpt_json(data):
    for conv in data:
        title = conv.get('title') or 'untitled'
        nodes = conv.get('mapping', {}).values()
        msgs = [n['message'] for n in nodes if n.get('message')]
        msgs.sort(key=lambda m: m.get('create_time') or 0)
        for k, m in enumerate(msgs):
            if (m.get('author') or {}).get('role') == 'user':
                parts = (m.get('content') or {}).get('parts') or []
                t = '\n'.join(p for p in parts if isinstance(p, str)).strip()
                if t: yield title, k, t

SCHEMA = 'relay-kit/conversation@1'

def from_capture_json(data):
    '''chat-capture's normalized JSON (schema relay-kit/conversation@1, or older with no schema field).'''
    if data.get('schema', SCHEMA) != SCHEMA:
        raise ValueError(f"schema {data.get('schema')!r}; this script reads {SCHEMA}")
    title = (data.get('meta') or {}).get('title') or 'untitled'
    for t in data['turns']:
        if t.get('role') == 'user':
            for k, m in enumerate(t.get('messages') or [], 1):
                if (m.get('text') or '').strip():
                    yield title, m.get('mid') or (f"T{t['n']}u" + (str(k) if k > 1 else '')), m['text'].strip()

def from_claude_json(data):
    for conv in data:
        title = conv.get('name') or 'untitled'
        for k, m in enumerate(conv.get('chat_messages', [])):
            if m.get('sender') == 'human':
                t = (m.get('text') or '').strip()
                if t: yield title, k, t

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+'); ap.add_argument('-o', default='turns.json')
    ap.add_argument('--delim', default='>"""', help="the user's paste delimiter; '' to disable")
    ap.add_argument('--user-names', default='', help='names that head the user\'s own turns, e.g. "Sam" for "Sam said:"')
    ap.add_argument('--assistant-names', default='', help='extra assistant names for relay detection, e.g. "Ada"')
    a = ap.parse_args()
    global U, A, RELAY
    U, A = header_patterns([n.strip() for n in a.user_names.split(',') if n.strip()])
    RELAY = relay_pattern(ASSISTANTS + [n.strip() for n in a.assistant_names.split(',') if n.strip()])
    seen, recs = {}, []
    def add(src, loc, t):
        h = hashlib.sha1(re.sub(r'\s+', ' ', t).encode()).hexdigest()[:12]
        if h in seen: seen[h]['dupes'].append(f'{src}:{loc}'); return
        own = strip_pastes(t, a.delim)
        r = {'id': h, 'source': src, 'loc': loc, 'words': len(t.split()),
             'own_words': len(own.split()), 'own': own, 'text': t, 'dupes': [],
             'flags': [f for f, c in [('likely_paste', likely_paste(own)),
                                     ('contains_paste_or_quote', '[PASTE]' in own or '[QUOTE]' in own),
                                     ('relayed_from_model', bool(RELAY.match(t)))] if c]}
        seen[h] = r; recs.append(r)
    files = []
    for p in a.inputs:
        if os.path.isdir(p):
            for dp, _, fn in os.walk(p):
                files += [os.path.join(dp, f) for f in fn if f.endswith(('.md', '.txt', '.json'))]
        else: files.append(p)
    skipped = []
    for f in sorted(files):
        raw = open(f, encoding='utf-8', errors='replace').read()
        if f.endswith('.json'):
            try: data = json.loads(raw)
            except Exception as e: skipped.append(f'{f}: not valid JSON ({e})'); continue
            if isinstance(data, dict) and isinstance(data.get('turns'), list):
                try:
                    for title, k, t in from_capture_json(data): add(f'{f}#{title}', k, t)
                except ValueError as e:
                    skipped.append(f'{f}: {e}')
            elif isinstance(data, list) and data and 'mapping' in data[0]:
                for title, k, t in from_chatgpt_json(data): add(f'{f}#{title}', f'msg{k}', t)
            elif isinstance(data, list) and data and 'chat_messages' in data[0]:
                for title, k, t in from_claude_json(data): add(f'{f}#{title}', f'msg{k}', t)
            else:
                skipped.append(f'{f}: unsupported JSON shape (raw capture? render it with chat-capture first)')
        else:
            for line, t in from_markdown(f, raw): add(f, f'L{line}', t)
    for s in skipped: print('SKIPPED', s, file=sys.stderr)
    with open(a.o, 'w', encoding='utf-8') as fh:
        json.dump(recs, fh, indent=1, ensure_ascii=False)
    print(f"{len(recs)} turns, {sum(r['own_words'] for r in recs)} own words, "
          f"{sum('likely_paste' in r['flags'] for r in recs)} flagged likely_paste, "
          f"{sum('relayed_from_model' in r['flags'] for r in recs)} flagged relayed_from_model -> {a.o}")
    if not recs:
        sys.exit('no user turns extracted' + (f'; {len(skipped)} file(s) skipped' if skipped else ''))

if __name__ == '__main__':
    main()
```