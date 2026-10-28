#!/usr/bin/env python3
'''ThreadSeed helper: turn index with relay attribution, and pointer/quote verification.

Input: a chat-capture transcript (.json preferred, .md accepted).
  index:   python3 -I seed_index.py CAPTURE [-o index.md] [--names ChatGPT=Ada]
  verify:  python3 -I seed_index.py CAPTURE --verify SEED.md [CONTINUITY.md ...]

Index: one row per message, ids T<n>u (user) and T<n>a, T<n>a2... (replies), the speaker
resolved through relays (a user turn that opens with "FROM: X" or "X here" is X's message,
carried by the user), local time, words, topic line, and flags (relay, paste, duplicate,
ends abruptly).
Verify: every quoted span followed within ~60 characters by a turn ref, e.g.
  "make it consistent by increasing the number of possible solutions" [T6a2]
must appear in exactly that message (whitespace, quotes and markdown emphasis normalised).
T6u is the user side, T6a the first reply, T6a2 the second; a bare T6 searches both sides of
exchange 6. Every T-ref must name a message or exchange that exists. Quotes with no ref are
listed as UNREFERENCED (warnings). Reports pass/fail counts; exit code 1 on any failure.
Normalized .json is the canonical input; .md works when it was rendered by chat-capture.
'''
import argparse, json, re, sys

FROM = re.compile(r'^\W*FROM:\s*\**\s*([^\n*→>]+?)\s*(?:→|->|\*|$)', re.I)
PASTE = re.compile(r'^>\s*["“”]{3}|^\W*FROM:\s', re.M)   # >""" paste, or a FROM: block after the first line
ASSISTANTS = ['Claude', 'ChatGPT', 'Gemini', 'Copilot']   # --names adds the new labels (ChatGPT=Ada adds Ada)
def here_pattern(names):
    return re.compile(r'\b(' + '|'.join(map(re.escape, names)) + r')(?:\s*\([^)]*\))?\s+here\b', re.I)
HERE = here_pattern(ASSISTANTS)
TREF = re.compile(r'\bT(\d+)(u\d*|a\d*)?\b')
SCHEMA = 'relay-kit/conversation@1'
QUOTE = re.compile(r'["“]([^"”\n]{12,400})["”]([^"“\n\[]{0,60}?)[\[(]\s*((?:T\d+(?:u|a\d*)?(?:\s*[,/–-]\s*)?)+)')
ANYQ = re.compile(r'["“]([^"”\n]{25,400})["”]')

def norm(s):
    s = s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    s = re.sub(r'[*_`\\]', '', s)
    return re.sub(r'\s+', ' ', s).strip().lower()

def load(path):
    '''Return list of dicts: n, k, role, text, ts, label.'''
    raw = open(path, encoding='utf-8').read()
    msgs = []
    if path.endswith('.json'):
        j = json.loads(raw)
        if j.get('schema', SCHEMA) != SCHEMA:
            sys.exit(f"{path}: schema {j.get('schema')!r}; this script reads {SCHEMA} (or older captures with no schema field)")
        for t in j['turns']:
            for k, m in enumerate(t['messages'], 1):
                msgs.append(dict(n=t['n'], k=k, role=t['role'], label=t['label'], mid=m.get('mid'),
                                 text=m['text'], ts=(m.get('ts') or '')[:16]))
        return msgs
    cur, hdr = None, None
    for line in raw.split('\n'):
        h = re.match(r'^###\s+(.+?) said:\s*$', line)
        c = re.match(r'^<!--\s*(.*?)\s*-->\s*$', line)
        if h:
            hdr, cur = dict(n=None, k=0, role=None, label=h.group(1), text='', ts=''), None
            continue
        if c and (hdr is not None or cur is not None):
            tags = [x.strip() for x in c.group(1).split('·')]
            idm = next((re.fullmatch(r'T(\d+)([ua])(\d*)', x) for x in tags if re.fullmatch(r'T\d+[ua]\d*', x)), None)
            tn = idm and int(idm.group(1))                       # current markers: T3u, T3a, T3a2
            if tn is None:                                       # older markers: T3 [· msg 2]
                tn = next((int(x[1:]) for x in tags if re.fullmatch(r'T\d+', x)), None)
            km = (int(idm.group(3) or 1) if idm else
                  next((int(x.split()[1]) for x in tags if re.fullmatch(r'msg \d+', x)), None))
            ts = next((x for x in tags if re.match(r'\d{4}-\d\d-\d\d[ T]\d\d:\d\d', x)), '')
            ts = ts.replace('T', ' ')[:16]
            if hdr is not None and tn is not None:      # turn header comment
                cur = dict(hdr, n=tn, k=km or 1, ts=ts)
                if idm: cur['role'] = 'user' if idm.group(2) == 'u' else 'assistant'
                else: cur['role'] = 'user' if cur['label'] == (msgs[0]['label'] if msgs else cur['label']) else 'assistant'
                msgs.append(cur); hdr = None
                continue
            if cur is not None and (km is not None or (ts and tn is None)):   # later message in the same turn
                nxt = dict(cur, k=km if (km and km > cur['k']) else cur['k'] + 1, text='', ts=ts)
                msgs.append(nxt); cur = nxt
                continue
        if line.startswith('<details><summary>Hidden'):
            cur = None if cur is None else dict(cur, _skip=True)
        if cur is not None and msgs and msgs[-1] is cur and not cur.get('_skip'):
            cur['text'] += line + '\n'
    return msgs

NAMES = {}

def mid(m):
    '''The schema's message id when the capture has one; otherwise the same rule chat-capture uses.'''
    if m.get('mid'): return m['mid']
    return f"T{m['n']}" + ('u' if m['role'] == 'user' else 'a') + (str(m['k']) if m['k'] > 1 else '')

def relayed(m):
    first = next((l for l in m['text'].split('\n') if l.strip()), '')
    if m['role'] != 'user': return None
    f = FROM.match(first)
    if f: return f.group(1).strip().rstrip(':')
    h = HERE.search(first)
    return h.group(1) if h else None

def speaker(m):
    r = relayed(m)
    if r: return r + ' (relayed)'
    first = next((l for l in m['text'].split('\n') if l.strip()), '')
    f = FROM.match(first)
    if f: return f.group(1).strip().rstrip(':')
    return NAMES.get(m['label'], m['label'])

def topic(m):
    t = re.search(r'^\W*TOPIC:\s*\**\s*(.+)$', m['text'], re.M | re.I)
    s = t.group(1) if t else next((l for l in m['text'].split('\n') if l.strip()), '')
    return re.sub(r'[*#|]', '', s).strip()[:90]

def index(msgs):
    seen, rows = {}, ['| id | speaker | time | words | topic / first line | flags |', '|---|---|---|---:|---|---|']
    for m in msgs:
        fl = []
        if relayed(m): fl.append('relay')
        body = m['text'].lstrip().split('\n', 1)[1] if relayed(m) and '\n' in m['text'].strip() else m['text']
        if PASTE.search(body): fl.append('contains paste')
        key = norm(m['text'])[:300]
        if key and key in seen: fl.append('duplicate of ' + seen[key])
        seen.setdefault(key, mid(m))
        if re.search(r'\n#+\s*\w+\s*$', m['text'].rstrip()[-40:]): fl.append('ends abruptly')
        rows.append(f"| {mid(m)} | {speaker(m)} | {m['ts']} | {len(m['text'].split())} | {topic(m)} | {', '.join(fl)} |")
    return '\n'.join(rows)

def resolve(msgs, n, k):
    '''Messages a ref names: T<n> -> the whole exchange; T<n>u / T<n>a / T<n>a2 -> exactly one message.'''
    if not k:
        return [m for m in msgs if m['n'] == n]
    want = f'T{n}{k[0]}' if k in ('a1', 'u1') else f'T{n}{k}'
    return [m for m in msgs if m['n'] == n and mid(m) == want]

def verify(msgs, files):
    ok = bad = 0
    for f in files:
        txt = open(f, encoding='utf-8').read()
        for r in TREF.finditer(txt):
            if not resolve(msgs, int(r.group(1)), r.group(2)):
                print(f'FAIL ref {r.group(0)} in {f}: no such message'); bad += 1
        for q in QUOTE.finditer(txt):
            span, refs = norm(q.group(1)), q.group(3)
            hay = ' '.join(norm(m['text']) for rr in TREF.finditer(refs)
                           for m in resolve(msgs, int(rr.group(1)), rr.group(2)))
            if span in hay: ok += 1
            else:
                print(f'FAIL quote in {f}: "{q.group(1)[:70]}..." not found in {refs.strip()}'); bad += 1
        refd = {q.group(1) for q in QUOTE.finditer(txt)}
        for q in ANYQ.finditer(txt):
            if q.group(1) not in refd: print(f'UNREFERENCED quote in {f}: "{q.group(1)[:70]}"')
    print(f'verified {ok} quotes; {bad} failures')
    return bad == 0

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('capture'); ap.add_argument('-o'); ap.add_argument('--verify', nargs='+')
    ap.add_argument('--names', default='', help='rename labels, e.g. ChatGPT=Ada')
    a = ap.parse_args()
    NAMES.update(x.split('=', 1) for x in a.names.split(',') if '=' in x)
    HERE = here_pattern(ASSISTANTS + list(NAMES.values()))
    msgs = load(a.capture)
    if a.verify:
        sys.exit(0 if verify(msgs, a.verify) else 1)
    out = index(msgs)
    if a.o: open(a.o, 'w', encoding='utf-8').write(out + '\n'); print(f'{len(msgs)} messages -> {a.o}')
    else: print(out)
