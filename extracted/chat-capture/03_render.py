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
