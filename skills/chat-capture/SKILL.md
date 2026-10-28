---
name: "chat-capture"
description: "Capture a full ChatGPT or Claude conversation from its URL (share or private link) into labeled, timestamped Markdown and JSON, without lazy-load truncation."
---

# Chat Capture

Pull a whole conversation from a chatgpt.com or claude.ai URL, labeled turn by turn, with timestamps and capture metadata, ready to use as an artifact or as input to other skills (selective-self-distillation, thread-map, meta-distill).

**Core idea: don't scroll, ask the site for its data.** Share pages render only the last few turns, and scrolling does not load the rest (tested on a ChatGPT share: the page showed 5 of 28 turns, and scrolling loaded nothing). The full conversation comes from the site's own JSON API, called from inside a browser tab on that site, so it uses the browser's own sign-in.

## Inputs

- One or more URLs:
  - `chatgpt.com/share/<id>` (public, no sign-in)
  - `chatgpt.com/c/<id>` (also under `/g/.../c/<id>`; needs sign-in)
  - `claude.ai/share/<id>` (public)
  - `claude.ai/chat/<id>` (needs sign-in)
- Options:
  - user label: default `You`, which gives headers `You said:`; a name like `Sam` works too (pass `--user-names Sam` to selective-self-distillation)
  - time zone: default `America/Chicago`
  - include hidden items: reasoning, tool calls, system context; off by default
  - output folder: default, next to the raw download

## Procedure

1. **Browser.** Use whichever browser tools are present:
   - the built-in browser pane (`mcp__Claude_Browser__*`, or `mcp__remote-devices__Claude_Browser__*` from a cloud session)
   - Claude in Chrome (`mcp__claude-in-chrome__*`)

   Read that browser's skill first. If a site isn't allowed yet, call `request_access` (scope `site` if the user will capture often), then retry.
2. **Open the URL** with `navigate` (or `preview_start`).
3. **Run `capture.js`** (below) with the browser's `javascript_tool`, pasting the whole script. It works out the platform and link type from the URL, then GETs the full conversation JSON. It wraps the result as `{"_capture": {...metadata}, "data": <raw>}`, saves it as a download named `<platform>-<kind>-<id8>-<UTC stamp>.raw.json`, keeps it in `window.__chatcap`, and returns a summary.
   - `Not signed in` error: ask the user to sign in **in that browser pane or window** themselves, then reload and rerun. Never type or ask for passwords. Sign-ins in the built-in pane persist across sessions.
   - Any other error (404/403, endpoint changed): use the fallback in step 6.
4. **Bring the raw file into the workspace.**
   - **Linked computer:** find it in Downloads (`device_bash` `ls -t ~/mnt/Downloads | head`, or `device_list_dir`), then `device_stage_files` it. The built-in pane can hold a download as `<GUID>.tmp` until the user approves the download prompt. If the newest `.tmp` starts with `{"_capture"`, its content is complete, so stage that and tell the user what to approve or clean up.
   - **No file access:** read `JSON.stringify(window.__chatcap)` in 40,000-character `slice` chunks into a workspace file, then check the total length matches. If it's very large (ChatGPT shares with tool use run about 8 MB), ask the user to attach the downloaded file instead.
5. **Render.** Write `render.py` (below) to a scripts folder, then run `python3 -I render.py RAW.json -o OUTDIR --user-label You --tz America/Chicago [--include-hidden] [--no-tidy]`. It writes `<title-slug>--<platform>-<id8>.md` and `.json` and prints a summary. **Read `checks` and report it.** `possible truncation` means the first visible turn isn't the user's. A branch note means only the active branch was rendered.
6. **Fallback (API failed).** Run `scrape.js` (below). It scrolls to the top until the turn count stops changing, scrapes roles and text from the page, and saves `kind: dom`. Render it the same way. Tell the user it has **no timestamps or models** and may be truncated; share pages usually are.
7. **Deliver.**
   - Put the `.md` and `.json` next to the raw file in the user's folder (`device_commit_files`), or send them with SendUserFile.
   - Keep the raw `.raw.json`; it is the evidence copy, and the Markdown frontmatter holds its SHA-256.
   - Report one line: title, turns (user/assistant), date range, hidden-item count, checks.
   - For a shareable artifact, build it from the Markdown.

## Output format

- **Frontmatter:**
  - identity: `title`, `platform`, `kind`, `source_url`, `conversation_id`, `share_id`
  - record timestamps: `record_created` / `record_updated` (for shares, when the share was made)
  - message timestamps: `first_message_at`, `last_message_at`
  - capture: `retrieved_at`, `retrieval` (endpoint), `raw_sha256`, `timezone`
  - models: `default_model`, `models_seen`
  - counts: `turns`, `user_turns`, `assistant_turns`, `visible_messages`, `hidden_items`, `nodes_total`, `dictated_user_turns`
  - `checks`
- **Turns:** `### You said:` / `### ChatGPT said:` / `### Claude said:`. Each is followed by a comment line led by the message id: `<!-- T3a · 2026-10-20 10:52:14 CDT · model · dictated · 2 messages -->`.
- **Message ids** (`T3u`, `T3a`, `T3a2`): the exchange number, the side, and the position within the turn. A user turn and the reply after it share one number; a reply before the first user turn is `T0a`. A turn can hold several messages, for example ChatGPT's "I'll check X" note and its answer; each later one starts with its own marker (`<!-- T3a2 · time -->`).
- **SSD compatibility:** the headers match the selective-self-distillation extractor (`You said` or `<Name> said`).
- **Tidy (ChatGPT replies, on by default):** UI components (`<box>`, `<grid>`, `<Cite/>`, `<Entity/>`, `<Link/>`) become plain Markdown, and `<Chart>` becomes a fenced `json chart` block. The JSON keeps the untouched text.
- **Sources:** per turn, as a collapsed `<details>` list with `utm_source` stripped. ChatGPT's in-text `<Cite>` refs can't be mapped to URLs, so they are removed.
- **Hidden items** (with `--include-hidden`): collapsed blocks attached to the reply they lead into. Examples: ChatGPT `thoughts`, `reasoning_recap`, `web.run`, `code`, tool output, system context; Claude `thinking`, `tool_use`, `tool_result`.
- **JSON** (schema `relay-kit/conversation@1`, shared by every reader in the kit): `schema`, `meta`, `hidden_counts`, and `turns[].messages[]` with `mid` (the message id above), `id` (the platform's), `ts` (UTC), `model`, `dictated`, `text` (raw) and `sources`.

## Known endpoints (read-only GETs)

| Link | Endpoint | Status |
| --- | --- | --- |
| ChatGPT share | `/backend-api/share/<id>` | Verified 2026-10-22 |
| ChatGPT private | `/api/auth/session` → `accessToken`, then `/backend-api/conversation/<id>` with `Authorization: Bearer` | Verified 2026-10-28 (signed in, `/c/<id>` chat) |
| Claude share | `/api/chat_snapshots/<id>?rendering_mode=messages&render_all_tools=true` (the id is the snapshot id from the `/share/` URL, not the conversation id) | Not yet verified here |
| Claude private | `/api/organizations` → each `uuid`, then `/api/organizations/<org>/chat_conversations/<id>?tree=True&rendering_mode=messages&render_all_tools=true` | Not yet verified here |

The private and Claude routes match two independent exporter projects (daymade read-claude-web-conversation; Emnolope claude-conversation-export).

On the **first successful** run of an unverified route, tell the user it worked so this table can be updated. If a route fails, report the HTTP status and use the fallback. Don't guess new endpoints.

**Schema notes.**
- **ChatGPT:** the conversation record also carries `safe_urls`, links from *other* conversations in the account. Don't keep it in anything shared; the renderer ignores it. `mapping` is a tree, and the active branch is the walk from `current_node` up through `parent`. Visible turns are user or assistant messages with `content_type` `text` (or `multimodal_text`), `recipient == "all"`, not `is_visually_hidden_from_conversation`, and non-empty text. `create_time` is epoch seconds. A user turn's `metadata.dictation` is true when it was dictated.
- **Claude:** `chat_messages[]` holds `uuid`, `parent_message_uuid`, `sender` (`human`/`assistant`), `created_at` (ISO) and `content[]` blocks. The active branch is the walk back from `current_leaf_message_uuid`.

## Rules

- Never filter turns by length. A short turn (e.g. "Uh... it probably shouldn't take this long, bro") is evidence. An earlier ad-hoc export lost exactly that turn.
- Capture only conversations the user owns or that were shared with them. GET only: no POST, DELETE or edits on either site.
- Never handle credentials. The user signs in in the browser.
- Report what the capture covers: say if anything was truncated, branched or scraped rather than fetched.

## capture.js

```javascript
// chat-capture: run in the page of the conversation (chatgpt.com or claude.ai).
// Fetches the full conversation JSON from the site's own API (no scrolling), wraps it
// with capture metadata, saves it as a download, and returns a short summary.
(async () => {
  const href = location.href, host = location.hostname;
  const UUID = '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})';
  const m = (re) => (href.match(new RegExp(re)) || [])[1];
  const get = async (url, opts) => {
    const r = await fetch(url, Object.assign({ credentials: 'include' }, opts || {}));
    if (!r.ok) throw new Error(`${r.status} on ${url.split('?')[0]}`);
    return r.json();
  };
  let platform, kind, id, raw, endpoint;
  if (/chatgpt\.com|chat\.openai\.com/.test(host)) {
    platform = 'chatgpt';
    if ((id = m('/share/' + UUID))) {
      kind = 'share'; endpoint = '/backend-api/share/' + id;
      raw = await get(endpoint);
    } else if ((id = m('/c/' + UUID))) {
      kind = 'conversation'; endpoint = '/backend-api/conversation/' + id;
      const s = await get('/api/auth/session').catch(() => ({}));
      if (!s.accessToken) throw new Error('Not signed in to ChatGPT in this browser. Sign in, reload, rerun.');
      raw = await get(endpoint, { headers: { Authorization: 'Bearer ' + s.accessToken } });
    }
  } else if (/claude\.ai/.test(host)) {
    platform = 'claude';
    const q = '?tree=True&rendering_mode=messages&render_all_tools=true';
    if ((id = m('/share/' + UUID))) {
      kind = 'share'; endpoint = '/api/chat_snapshots/' + id + q;
      raw = await get(endpoint);
    } else if ((id = m('/chat/' + UUID))) {
      kind = 'conversation';
      const orgs = await get('/api/organizations').catch(() => []);
      if (!orgs.length) throw new Error('Not signed in to Claude in this browser. Sign in, reload, rerun.');
      for (const o of orgs) {
        try { endpoint = `/api/organizations/${o.uuid}/chat_conversations/${id}${q}`; raw = await get(endpoint); break; }
        catch (e) { raw = null; }
      }
      if (!raw) throw new Error('Conversation not found in any of your Claude organizations.');
    }
  }
  if (!raw) throw new Error('Unrecognized URL. Expected chatgpt.com/share|c/<id> or claude.ai/share|chat/<id>.');
  const wrapped = {
    _capture: {
      tool: 'chat-capture', version: '0.1.0', platform, kind, id,
      source_url: href, endpoint: endpoint.split('?')[0],
      retrieved_at: new Date().toISOString(),
      browser_tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    },
    data: raw,
  };
  const text = JSON.stringify(wrapped);
  const stamp = wrapped._capture.retrieved_at.replace(/[:.]/g, '-').slice(0, 19);
  const fname = `${platform}-${kind}-${id.slice(0, 8)}-${stamp}.raw.json`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  a.download = fname; document.body.appendChild(a); a.click(); a.remove();
  window.__chatcap = wrapped;   // kept in page for chunked reads if the download is blocked
  const nodes = platform === 'chatgpt' ? Object.keys(raw.mapping || {}).length : (raw.chat_messages || []).length;
  return { saved_as: fname, bytes: text.length, platform, kind, id,
           title: raw.title || raw.name || raw.snapshot_name || null, nodes };
})()
```

## scrape.js (fallback)

```javascript
// chat-capture fallback: only when capture.js fails. Scrolls the thread to the top until no
// new turns load, then scrapes roles + text from the DOM. Selectors are UI-dependent: verify.
(async () => {
  const isGPT = /chatgpt\.com|chat\.openai\.com/.test(location.hostname);
  const sel = isGPT ? '[data-content-search-unit-key], [data-message-author-role]'   // 2026-10 markup, older markup
                    : '[data-testid="user-message"], .font-claude-response, [data-is-streaming]';
  const scroller = (el) => { while (el && el !== document.body) { const s = getComputedStyle(el);
    if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) return el; el = el.parentElement; }
    return document.scrollingElement; };
  const first = document.querySelector(sel);
  if (!first) throw new Error('No messages found with selector: ' + sel);
  const box = scroller(first);
  let prev = -1, stable = 0;
  for (let i = 0; i < 200 && stable < 4; i++) {
    box.scrollTop = 0; await new Promise(r => setTimeout(r, 700));
    const n = document.querySelectorAll(sel).length;
    stable = n === prev ? stable + 1 : 0; prev = n;
  }
  // Keep every message element, including repeated text ("Yes." twice is two turns).
  // Drop only elements nested inside another match and repeats of the same message id.
  const seenIds = new Set();
  const messages = [...document.querySelectorAll(sel)].filter(el => !el.parentElement.closest(sel)).map(el => {
    const unitKey = el.getAttribute('data-content-search-unit-key');           // e.g. "turn-0:2:assistant"
    const role = isGPT ? (el.getAttribute('data-message-author-role') || (unitKey || '').split(':').pop())
                       : (el.matches('[data-testid="user-message"]') ? 'user' : 'assistant');
    const id = el.getAttribute('data-message-id')
      || (el.getAttribute('data-chatgpt-search-message-ids') || '').split(/\s+/)[0] || null;
    const body = isGPT && el.querySelector
      && el.querySelector('[data-markdown-text-style="assistant-message"], [data-user-message-bubble] [dir=auto]');
    const text = (body || el).innerText.replace(/^\s*(You|ChatGPT) said:\s*/, '').trim();
    return { id, role, text };
  }).filter(m => (m.role === 'user' || m.role === 'assistant') && m.text
    && !(m.id && seenIds.has(m.id)) && (m.id ? seenIds.add(m.id) : true));
  const UUID = /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/;
  const id = (location.pathname.match(UUID) || [])[1] || 'unknown';
  const wrapped = { _capture: { tool: 'chat-capture', version: '0.1.0', platform: isGPT ? 'chatgpt' : 'claude',
      kind: 'dom', id, source_url: location.href, endpoint: 'DOM scrape (fallback)',
      retrieved_at: new Date().toISOString(), browser_tz: Intl.DateTimeFormat().resolvedOptions().timeZone },
    data: { title: document.title.replace(/\s*[-|]\s*(ChatGPT|Claude)\s*$/, ''), messages } };
  window.__chatcap = wrapped;
  const text = JSON.stringify(wrapped);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  a.download = `${wrapped._capture.platform}-dom-${id.slice(0, 8)}.raw.json`; document.body.appendChild(a); a.click(); a.remove();
  return { saved_as: a.download, messages: messages.length, first_role: messages[0] && messages[0].role };
})()
```

## render.py

```python
#!/usr/bin/env python3
"""chat-capture render: raw ChatGPT/Claude conversation JSON -> labeled Markdown + normalized JSON.

Usage: python3 -I render.py RAW.json [-o OUTDIR] [--user-label You] [--tz America/Chicago]
                                    [--include-hidden] [--no-tidy]
RAW.json is the file capture.js saves ({"_capture":..., "data":...}); a bare API payload also works.
"""
import argparse, hashlib, json, os, re, sys
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# ---------- helpers ----------
def to_dt(v):
    if v in (None, ''): return None
    if isinstance(v, (int, float)): return datetime.fromtimestamp(v, tz=timezone.utc)
    try: return datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except ValueError: return None

def iso(dt): return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if dt else None

TZ_FAILED = []

def local(dt, tz):
    '''Local time as "YYYY-MM-DD HH:MM:SS ZONE"; UTC in the same shape if tz data is missing.'''
    if not dt: return ''
    if tz and ZoneInfo:
        try: return dt.astimezone(ZoneInfo(tz)).strftime('%Y-%m-%d %H:%M:%S %Z')
        except Exception:
            if not TZ_FAILED: TZ_FAILED.append(tz)
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

def slug(s, n=60):
    s = re.sub(r'[^\w\s-]', '', s or 'untitled').strip().lower()
    return re.sub(r'[\s_-]+', '-', s)[:n].strip('-') or 'untitled'

def strip_utm(u): return re.sub(r'[?&]utm_source=chatgpt\.com$', '', u)

def tidy_chatgpt(t):
    """Turn ChatGPT's in-text UI components into plain Markdown. Raw text stays in the JSON."""
    t = re.sub(r'<Chart content=\{(\{.*?\})\}\s*/>', lambda m: '\n```json chart\n' + m.group(1) + '\n```\n', t, flags=re.S)
    t = re.sub(r'<svg\b.*?</svg>', '', t, flags=re.S)
    t = re.sub(r'<Link url="([^"]+)" title="([^"]*)"\s*/>', r'[\2](\1)', t)
    t = re.sub(r'<Entity\b[^>]*?value="([^"]*)"[^>]*/>', r'\1', t)
    t = re.sub(r'<Cite\b[^>]*/>', '', t)
    t = re.sub(r'<divider\b[^>]*/>', '\n---\n', t)
    t = re.sub(r'</?(box|grid|grid-item|row|list|list-item|text|title|caption|col|stack)\b[^>]*>', '\n', t)
    t = re.sub(r'[ \t]+\n', '\n', t)
    return re.sub(r'\n{3,}', '\n\n', t).strip()

# ---------- ChatGPT ----------
def chatgpt_messages(d):
    mp, cur = d['mapping'], d.get('current_node')
    if not cur or cur not in mp:
        leaves = [k for k, v in mp.items() if not (v.get('children') or [])]
        if len(leaves) != 1:
            sys.exit(f"render: no usable current_node and {len(leaves)} leaf nodes; can't tell which branch is active")
        cur = leaves[0]
    chain, n = [], cur
    while n and n in mp:
        chain.append(mp[n]); n = mp[n].get('parent')
    chain.reverse()
    branches = sum(1 for x in mp.values() if len(x.get('children') or []) > 1)
    out = []
    for node in chain:
        msg = node.get('message')
        if not msg: continue
        role = (msg.get('author') or {}).get('role')
        c = msg.get('content') or {}
        ctype = c.get('content_type')
        md = msg.get('metadata') or {}
        parts = c.get('parts') or []
        text = '\n'.join(p if isinstance(p, str) else (p.get('text') or '') for p in parts if p).strip()
        if not text and isinstance(c.get('text'), str): text = c['text'].strip()
        hidden = bool(md.get('is_visually_hidden_from_conversation'))
        visible = (role in ('user', 'assistant') and ctype in ('text', 'multimodal_text')
                   and not hidden and msg.get('recipient', 'all') == 'all' and bool(text))
        kind = ('message' if visible else
                f"{role}:{ctype}" + (f"->{msg.get('recipient')}" if msg.get('recipient') not in (None, 'all') else '')
                + (':hidden' if hidden else ''))
        srcs = []
        for r in md.get('content_references') or []:
            srcs += [strip_utm(u) for u in (r.get('safe_urls') or []) if 'images.openai.com' not in u]
            for it in r.get('items') or []:
                if it.get('url'): srcs.append(strip_utm(it['url']))
        out.append(dict(id=msg.get('id'), role=role, visible=visible, kind=kind, text=text,
                        ts=to_dt(msg.get('create_time')),
                        model=md.get('model_slug') or md.get('resolved_model_slug'),
                        dictated=bool(md.get('dictation')) if role == 'user' else None,
                        sources=list(dict.fromkeys(srcs))))
    meta = dict(title=d.get('title'), conversation_id=d.get('conversation_id'),
                created=to_dt(d.get('create_time')), updated=to_dt(d.get('update_time')),
                default_model=d.get('default_model_slug'), nodes_total=len(mp),
                branch_points=branches)
    return out, meta, 'ChatGPT'

# ---------- Claude ----------
def claude_messages(d):
    msgs = d.get('chat_messages') or d.get('messages') or []
    by = {m.get('uuid'): m for m in msgs}
    leaf = d.get('current_leaf_message_uuid')
    if leaf and leaf in by:
        chain, n = [], leaf
        while n and n in by:
            chain.append(by[n]); n = by[n].get('parent_message_uuid')
        chain.reverse()
    else:
        chain = sorted(msgs, key=lambda m: (m.get('index', 0), m.get('created_at') or ''))
    kids = {}
    for m in msgs: kids.setdefault(m.get('parent_message_uuid'), []).append(m)
    branches = sum(1 for v in kids.values() if len(v) > 1)
    out = []
    for m in chain:
        role = 'user' if m.get('sender') == 'human' else 'assistant'
        blocks = m.get('content') or []
        texts = [b.get('text', '') for b in blocks if b.get('type') == 'text']
        text = '\n\n'.join(t for t in texts if t).strip() or (m.get('text') or '').strip()
        ts = to_dt(m.get('created_at'))
        out.append(dict(id=m.get('uuid'), role=role, visible=bool(text), kind='message' if text else f'{role}:empty',
                        text=text, ts=ts, model=m.get('model'), dictated=None,
                        sources=list(dict.fromkeys(c.get('url') for b in blocks for c in (b.get('citations') or []) if c.get('url'))),
                        attachments=[a.get('file_name') for a in (m.get('attachments') or []) + (m.get('files') or []) if a.get('file_name')]))
        for b in blocks:   # hidden blocks: thinking, tool_use, tool_result
            if b.get('type') != 'text':
                body = b.get('thinking') or b.get('text') or json.dumps(b.get('input') or b.get('content') or '', ensure_ascii=False)[:4000]
                out.append(dict(id=m.get('uuid'), role=role, visible=False, kind=f"{role}:{b.get('type')}",
                                text=str(body), ts=ts, model=None, dictated=None, sources=[]))
    meta = dict(title=d.get('name') or d.get('snapshot_name'), conversation_id=d.get('conversation_uuid') or d.get('uuid'),
                created=to_dt(d.get('created_at')), updated=to_dt(d.get('updated_at')),
                default_model=d.get('model'), nodes_total=len(msgs), branch_points=branches)
    return out, meta, 'Claude'

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('raw'); ap.add_argument('-o', '--outdir', default='.')
    ap.add_argument('--user-label', default='You'); ap.add_argument('--tz', default='America/Chicago')
    ap.add_argument('--include-hidden', action='store_true'); ap.add_argument('--no-tidy', action='store_true')
    a = ap.parse_args()
    blob = open(a.raw, 'rb').read()
    j = json.loads(blob)
    cap, d = (j.get('_capture') or {}), (j.get('data') if '_capture' in j else j)
    platform = cap.get('platform') or ('chatgpt' if 'mapping' in d else 'claude')
    if cap.get('kind') == 'dom':   # scroll-and-scrape fallback: roles and text only, no timestamps
        msgs = [dict(id=x.get('id'), role=x['role'], visible=bool(x.get('text')), kind='message', text=x.get('text', ''),
                     ts=None, model=None, dictated=None, sources=[]) for x in d.get('messages', [])]
        meta = dict(title=d.get('title'), conversation_id=cap.get('id'), created=None, updated=None,
                    default_model=None, nodes_total=len(msgs), branch_points=0)
        alabel = 'ChatGPT' if platform == 'chatgpt' else 'Claude'
    else:
        msgs, meta, alabel = (chatgpt_messages if platform == 'chatgpt' else claude_messages)(d)

    # group consecutive visible messages by role into turns
    # hidden items (reasoning, tool calls) attach to the assistant turn they lead into
    turns, pending = [], []
    for m in msgs:
        if not m['visible']:
            if a.include_hidden: pending.append(m)
            continue
        if not turns or turns[-1]['role'] != m['role']:
            turns.append(dict(role=m['role'], msgs=[], hidden=[]))
        if pending:
            tgt = turns[-1] if m['role'] == 'assistant' or len(turns) == 1 else turns[-2]
            tgt['hidden'] += pending; pending = []
        turns[-1]['msgs'].append(m)
    if pending and turns: turns[-1]['hidden'] += pending
    ex = 0   # exchange numbering: a user turn and the reply after it share T<n>;
    for t in turns:   # replies before the first user turn are exchange 0 (T0a), so ids never collide
        if t['role'] == 'user': ex += 1
        t['n'] = ex
        side = 'u' if t['role'] == 'user' else 'a'
        for k, m in enumerate(t['msgs'], 1):   # message id shared by every relay-kit tool: T3u, T3a, T3a2
            m['mid'] = f"T{t['n']}{side}" + (str(k) if k > 1 else '')

    vis = [m for m in msgs if m['visible']]
    hidden_counts = {}
    for m in msgs:
        if not m['visible']: hidden_counts[m['kind']] = hidden_counts.get(m['kind'], 0) + 1
    if not vis:
        sys.exit('render: no visible messages found; refusing to write an empty transcript')
    checks = []
    if turns and turns[0]['role'] != 'user': checks.append('first visible turn is not the user: possible truncation')
    if meta['branch_points']: checks.append(f"{meta['branch_points']} branch point(s): only the active branch is rendered")
    uturns = [t for t in turns if t['role'] == 'user']
    models = sorted({m['model'] for m in vis if m.get('model')})

    title = meta['title'] or 'Untitled'
    base = f"{slug(title)}--{platform}-{(cap.get('id') or meta['conversation_id'] or 'x')[:8]}"
    os.makedirs(a.outdir, exist_ok=True)
    tz = a.tz
    fm = {
        'title': title, 'platform': platform, 'kind': cap.get('kind'),
        'source_url': cap.get('source_url'), 'conversation_id': meta['conversation_id'], 'share_id': cap.get('id') if cap.get('kind') == 'share' else None,
        'record_created': iso(meta['created']), 'record_updated': iso(meta['updated']),
        'first_message_at': iso(next((m['ts'] for m in vis if m['ts']), None)),
        'last_message_at': iso(next((m['ts'] for m in reversed(vis) if m['ts']), None)),
        'retrieved_at': cap.get('retrieved_at'), 'retrieval': cap.get('endpoint'),
        'default_model': meta['default_model'], 'models_seen': models,
        'turns': len(turns), 'user_turns': len(uturns), 'assistant_turns': len(turns) - len(uturns),
        'visible_messages': len(vis), 'hidden_items': sum(hidden_counts.values()), 'nodes_total': meta['nodes_total'],
        'dictated_user_turns': sum(1 for t in uturns if any(m.get('dictated') for m in t['msgs'])),
        'raw_sha256': hashlib.sha256(blob).hexdigest(), 'timezone': tz,
        'checks': checks or ['ok'],
    }
    L = ['---'] + [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fm.items()] + ['---', '', f'# {title}', '']
    for t in turns:
        label = f"{a.user_label} said:" if t['role'] == 'user' else f"{alabel} said:"
        first = t['msgs'][0]
        tags = [first['mid'], local(first['ts'], tz)]   # the message id (T3u, T3a) leads every marker
        if first.get('model'): tags.append(first['model'])
        if any(m.get('dictated') for m in t['msgs']): tags.append('dictated')
        if len(t['msgs']) > 1: tags.append(f"{len(t['msgs'])} messages")
        L += [f'### {label}', f"<!-- {' · '.join(x for x in tags if x)} -->", '']
        for k, m in enumerate(t['msgs']):
            if k:   # every later message carries its own id (T3a2), so ids survive without timestamps
                tags_k = [m['mid'], local(m['ts'], tz)]
                L += [f"<!-- {' · '.join(x for x in tags_k if x)} -->", '']
            body = m['text'] if (a.no_tidy or platform != 'chatgpt' or m['role'] == 'user') else tidy_chatgpt(m['text'])
            L += [body, '']
            if m.get('attachments'): L += ['*Attachments: ' + ', '.join(m['attachments']) + '*', '']
        srcs = list(dict.fromkeys(s for m in t['msgs'] for s in m['sources']))
        if srcs:
            L += [f'<details><summary>Sources ({len(srcs)})</summary>', ''] + [f'- <{s}>' for s in srcs] + ['', '</details>', '']
        if t['hidden']:
            L += [f"<details><summary>Hidden items ({len(t['hidden'])})</summary>", '']
            for h in t['hidden']:
                L += [f"**{h['kind']}**", '', '```', h['text'][:3000], '```', '']
            L += ['</details>', '']
        L += ['---', '']
    if TZ_FAILED:
        checks.append(f'timezone {TZ_FAILED[0]} unavailable (install tzdata); times shown in UTC')
        fm['checks'] = checks
        L[1:1 + len(fm)] = [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fm.items()]
    md_path = os.path.join(a.outdir, base + '.md')
    open(md_path, 'w', encoding='utf-8').write('\n'.join(L))
    norm = dict(schema='relay-kit/conversation@1', meta=fm, hidden_counts=hidden_counts, turns=[
        dict(n=t['n'], role=t['role'], label=(a.user_label if t['role'] == 'user' else alabel),
             messages=[dict(mid=m['mid'], id=m['id'], ts=iso(m['ts']), model=m.get('model'), dictated=m.get('dictated'),
                            text=m['text'], sources=m['sources']) for m in t['msgs']]) for t in turns])
    js_path = os.path.join(a.outdir, base + '.json')
    json.dump(norm, open(js_path, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print(json.dumps(dict(markdown=md_path, json=js_path, title=title, turns=len(turns), user_turns=len(uturns),
                          visible_messages=len(vis), hidden=hidden_counts, checks=checks or ['ok']), indent=1))

if __name__ == '__main__':
    main()
```