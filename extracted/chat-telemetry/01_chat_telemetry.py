#!/usr/bin/env python3
"""chat-telemetry: timing and size metrics for captured conversations, plus a self-contained dashboard.

Usage: python3 -I chat_telemetry.py FILE [FILE ...] -o OUTDIR [--tz America/Chicago]
                [--hang-seconds 120] [--relay-pattern '^FROM:'] [--title T]

FILE can be:
  - a chat-capture raw file ({"_capture":..., "data":...}) from ChatGPT or claude.ai
  - a bare ChatGPT conversation payload (has "mapping") or claude.ai payload (has "chat_messages")
  - a Claude Code session transcript (.jsonl, one event per line)
Writes OUTDIR/telemetry.json and OUTDIR/dashboard.html. Standard library only.

What the numbers mean (they are stated in the dashboard too):
  reply latency   user message -> first visible assistant text
  turn duration   user message -> last assistant-side event before the next user message
  return gap      last assistant-side event -> the user's next message (wall clock, not effort)
  hang            a silence inside a turn longer than --hang-seconds between consecutive events
ChatGPT stores when a message was created, not when streaming finished, so turn ends are
approximate (the last recorded event in the turn). Timestamps that run backwards are reported.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ENGAGED_S = 900   # "engaged" return gaps: under 15 minutes. One definition, shared with the dashboard.
BANDS = [("live", 120), ("short break", ENGAGED_S), ("stepped away", 3 * 3600), ("came back later", None)]
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def to_dt(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def words(t: str) -> int:
    return len(re.findall(r"\S+", t or ""))


def ev(ts, actor, kind, text="", name=None):
    return {"ts": ts, "actor": actor, "kind": kind, "words": words(text), "text": text, "name": name}


# ---------- loaders: each returns (title, platform, events) ----------
SCHEMA = "relay-kit/conversation@1"


def load_capture(d):
    """chat-capture's normalized JSON. Visible messages only: no tool or hidden events, so tool
    steps and hangs inside turns can't be measured from it; use the raw capture for those."""
    if d.get("schema", SCHEMA) != SCHEMA:
        raise ValueError(f"schema {d.get('schema')!r}; this script reads {SCHEMA}")
    out = []
    for t in d["turns"]:
        actor = "user" if t["role"] == "user" else "assistant"
        for m in t["messages"]:
            ts = to_dt(m.get("ts"))
            if ts is not None:
                out.append(ev(ts, actor, "message", m.get("text", "")))
    meta = d.get("meta") or {}
    return meta.get("title"), (meta.get("platform") or "capture") + " (normalized)", out

def load_chatgpt(d):
    mp, n, chain = d["mapping"], d.get("current_node"), []
    while n and n in mp:
        chain.append(mp[n]); n = mp[n].get("parent")
    out = []
    for node in reversed(chain):
        m = node.get("message")
        if not m:
            continue
        role = (m.get("author") or {}).get("role")
        c = m.get("content") or {}
        md = m.get("metadata") or {}
        text = "\n".join(p if isinstance(p, str) else (p.get("text") or "") for p in (c.get("parts") or []) if p).strip()
        if not text and isinstance(c.get("text"), str):
            text = c["text"]
        ts = to_dt(m.get("create_time")) or to_dt(m.get("update_time"))
        if ts is None or role in (None, "system"):
            continue
        visible = (role in ("user", "assistant") and c.get("content_type") in ("text", "multimodal_text")
                   and m.get("recipient", "all") == "all" and not md.get("is_visually_hidden_from_conversation") and text)
        if role == "user":
            if visible:
                out.append(ev(ts, "user", "message", text))
        elif role == "assistant":
            out.append(ev(ts, "assistant", "message" if visible else "hidden", text if visible else "",
                          name=None if visible else (m.get("recipient") or c.get("content_type"))))
        else:  # tool
            out.append(ev(ts, "tool", "tool", "", name=(m.get("author") or {}).get("name")))
    return d.get("title"), "chatgpt", out


def load_claude_ai(d):
    msgs = d.get("chat_messages") or []
    by = {m.get("uuid"): m for m in msgs}
    leaf, chain = d.get("current_leaf_message_uuid"), []
    if leaf in by:
        n = leaf
        while n in by:
            chain.append(by[n]); n = by[n].get("parent_message_uuid")
        chain.reverse()
    else:
        chain = sorted(msgs, key=lambda m: m.get("created_at") or "")
    out = []
    for m in chain:
        ts = to_dt(m.get("created_at"))
        if m.get("sender") == "human":
            text = "\n".join(b.get("text", "") for b in m.get("content") or [] if b.get("type") == "text") or m.get("text", "")
            out.append(ev(ts, "user", "message", text))
            continue
        blocks = m.get("content") or []
        if not blocks:
            out.append(ev(ts, "assistant", "message", m.get("text", "")))
        for b in blocks:
            bts = to_dt(b.get("start_timestamp")) or ts
            if b.get("type") == "text":
                out.append(ev(bts, "assistant", "message", b.get("text", "")))
            elif b.get("type") == "tool_result":
                out.append(ev(bts, "tool", "tool", "", name=b.get("name")))
            else:
                out.append(ev(bts, "assistant", "hidden", "", name=b.get("type")))
            end = to_dt(b.get("stop_timestamp"))
            if end and bts and end > bts:
                out.append(ev(end, "assistant", "marker", "", name="block-end"))   # timing only, not a step
    return d.get("name"), "claude", out


def load_claude_code(lines):
    out, title = [], None
    for l in lines:
        try:
            d = json.loads(l)
        except json.JSONDecodeError:
            continue
        ts = to_dt(d.get("timestamp"))
        if ts is None or d.get("isSidechain"):
            continue
        title = title or (f"Claude Code session {d['sessionId'][:8]}" if d.get("sessionId") else None)
        t = d.get("type")
        msg = d.get("message") or {}
        content = msg.get("content")
        if t == "user":
            if (d.get("origin") or {}).get("kind") == "human":
                text = content if isinstance(content, str) else " ".join(
                    x.get("text", "") for x in content or [] if x.get("type") == "text")
                text = REMINDER.sub("", text).strip()
                out.append(ev(ts, "user", "message", text))
            elif d.get("isMeta") and isinstance(content, str) and "stopped by" in content:
                out.append(ev(ts, "meta", "interruption", "", name="stopped"))
            elif isinstance(content, list) and any(x.get("type") == "tool_result" for x in content):
                out.append(ev(ts, "tool", "tool", "", name="tool_result"))
        elif t == "assistant" and isinstance(content, list):
            for x in content:
                if x.get("type") == "text" and x.get("text", "").strip():
                    out.append(ev(ts, "assistant", "message", x["text"]))
                elif x.get("type") == "tool_use":
                    out.append(ev(ts, "assistant", "hidden", "", name=x.get("name")))
                elif x.get("type") == "thinking":
                    out.append(ev(ts, "assistant", "hidden", "", name="thinking"))
    return title, "claude-code", out


def load(path: Path):
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return load_claude_code(raw.splitlines())
    j = json.loads(raw)
    if isinstance(j, dict) and isinstance(j.get("turns"), list) and "meta" in j:
        return load_capture(j)
    cap = j.get("_capture") if isinstance(j, dict) else None
    d = j["data"] if cap else j
    if "mapping" in d:
        return load_chatgpt(d)
    if "chat_messages" in d:
        return load_claude_ai(d)
    raise ValueError(f"{path}: not a ChatGPT, claude.ai or Claude Code transcript")


# ---------- metrics ----------
def band(seconds):
    for name, limit in BANDS:
        if limit is None or seconds < limit:
            return name


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "median": statistics.median(vals), "p90": pct(vals, 0.9), "max": max(vals),
            "total": sum(vals)}


def analyze(title, platform, events, source, tz, hang_s, relay_re, backwards=0):
    starts = [i for i, e in enumerate(events) if e["actor"] == "user"]
    turns = []
    for k, i in enumerate(starts):
        j = starts[k + 1] if k + 1 < len(starts) else len(events)
        u, rest = events[i], events[i + 1:j]
        first = next((e for e in rest if e["actor"] == "assistant" and e["kind"] == "message"), None)
        times = [u["ts"]] + sorted(e["ts"] for e in rest)
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        end = max(times)
        hangs = []
        for a, b in zip(times, times[1:]):
            g = (b - a).total_seconds()
            if g >= hang_s:
                hangs.append({"at": a.isoformat(), "seconds": round(g, 1)})
        turns.append({
            "n": k + 1,
            "start": u["ts"].isoformat(),
            "local_hour": u["ts"].astimezone(tz).hour if tz else u["ts"].hour,
            "local_weekday": u["ts"].astimezone(tz).weekday() if tz else u["ts"].weekday(),
            "relay": bool(relay_re and relay_re.search(u["text"])),
            "user_words": u["words"],
            "user_preview": u["text"][:90].replace("\n", " "),
            "assistant_words": sum(e["words"] for e in rest if e["actor"] == "assistant" and e["kind"] == "message"),
            "assistant_messages": sum(1 for e in rest if e["actor"] == "assistant" and e["kind"] == "message"),
            "tool_steps": sum(1 for e in rest if e["actor"] == "assistant" and e["kind"] == "hidden"
                              and e["name"] not in ("thinking", "block-end")),
            "interruptions": sum(1 for e in rest if e["kind"] == "interruption"),
            "reply_latency_s": round((first["ts"] - u["ts"]).total_seconds(), 1) if first else None,
            "turn_duration_s": round((end - u["ts"]).total_seconds(), 1),
            "longest_silence_s": round(max(gaps), 1) if gaps else 0.0,
            "hangs": hangs,
            "end": end.isoformat(),
            "answered": first is not None,
        })
    for a, b in zip(turns, turns[1:]):
        g = (datetime.fromisoformat(b["start"]) - datetime.fromisoformat(a["end"])).total_seconds()
        a["return_gap_s"] = round(max(g, 0.0), 1)
        a["return_band"] = band(max(g, 0.0))
    if turns:
        turns[-1]["return_gap_s"] = None
        turns[-1]["return_band"] = None
    returns = [t["return_gap_s"] for t in turns if t["return_gap_s"] is not None]
    bands = {name: sum(1 for t in turns if t["return_band"] == name) for name, _ in BANDS}
    live_returns = [g for g in returns if g < ENGAGED_S]  # live + short break: what "how fast I reply" means
    checks = []
    if backwards:
        checks.append(f"{backwards} timestamp(s) run backwards (recorded order differs from time order)")
    if platform == "chatgpt":
        checks.append("ChatGPT records message creation, not completion: turn ends and durations are approximate")
    if not turns:
        checks.append("no user turns found")
    if platform.endswith("(normalized)"):
        checks.append("read from a normalized capture: tool steps and in-turn hangs aren't available")
    return {
        "title": title or (f"Claude Code session {Path(source).stem[:8]}" if platform == "claude-code" else Path(source).stem), "platform": platform, "source": str(source),
        "first": turns[0]["start"] if turns else None, "last": turns[-1]["end"] if turns else None,
        "turns": turns, "checks": checks or ["ok"],
        "summary": {
            "turns": len(turns), "relay_turns": sum(t["relay"] for t in turns),
            "unanswered_turns": sum(not t["answered"] for t in turns),
            "user_words": summarize([t["user_words"] for t in turns]),
            "assistant_words": summarize([t["assistant_words"] for t in turns]),
            "reply_latency_s": summarize([t["reply_latency_s"] for t in turns]),
            "turn_duration_s": summarize([t["turn_duration_s"] for t in turns]),
            "return_gap_s": summarize(returns),
            "return_gap_engaged_s": summarize(live_returns),
            "return_bands": bands,
            "tool_steps": sum(t["tool_steps"] for t in turns),
            "hangs": sum(len(t["hangs"]) for t in turns),
            "interruptions": sum(t["interruptions"] for t in turns),
        },
    }


# ---------- dashboard ----------
HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
:root{color-scheme:light;--bg:#f6f6f4;--surface:#fcfcfb;--line:#e4e3de;--text:#0b0b0b;--text2:#52514e;--muted:#7a7974;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--seq1:#cfe0f6;--seq2:#8db6ea;--seq3:#4f8fde;--seq4:#1f5fae}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#121211;--surface:#1a1a19;--line:#33322f;
--text:#f0efec;--text2:#c3c2b7;--muted:#8d8c86;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--seq1:#1f3550;--seq2:#2c5a92;--seq3:#3f7fcf;--seq4:#8db6ea}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--line:#33322f;--text:#f0efec;--text2:#c3c2b7;--muted:#8d8c86;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--seq1:#1f3550;--seq2:#2c5a92;--seq3:#3f7fcf;--seq4:#8db6ea}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 48px}h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px}
.sub{color:var(--text2);margin:0 0 16px}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
select,button{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:6px 10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.tile,.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile .k{color:var(--text2);font-size:12px}.tile .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}.tile .h{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media (max-width:760px){.grid{grid-template-columns:1fr}}
.card{margin-bottom:12px;overflow:hidden}svg{display:block;width:100%;height:auto}
.axis{stroke:var(--line)}.lbl{fill:var(--text2);font-size:11px}.legend{display:flex;gap:14px;color:var(--text2);font-size:12px;margin-top:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--text2);font-weight:500}.tw{overflow-x:auto}.note{color:var(--muted);font-size:12px}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;max-width:320px;z-index:5}
</style></head><body><main>
<h1>__TITLE__</h1><p class="sub" id="sub"></p>
<div class="row"><label>Thread <select id="thread"></select></label><label><input type="checkbox" id="relay"> exclude relayed turns</label><button id="theme" type="button">Toggle theme</button></div>
<div class="tiles" id="tiles"></div>
<div class="card"><h2>Turn timeline</h2><div id="timeline"></div><div class="legend"><span><i class="sw" style="background:var(--s1)"></i>waiting for first reply</span><span><i class="sw" style="background:var(--s3)"></i>rest of the turn (tools, follow-ups)</span><span><i class="sw" style="background:var(--s2)"></i>hang (silence over threshold)</span></div><p class="note">One row per turn. Hover for details. Duration axis is clipped at the 95th percentile; longer turns run to the edge.</p></div>
<div class="grid">
<div class="card"><h2>How long until you replied</h2><div id="bands"></div><p class="note">Wall-clock gaps, not effort: sleep and errands count the same as reading.</p></div>
<div class="card"><h2>When turns started (local hour)</h2><div id="hours"></div></div>
</div>
<div class="grid">
<div class="card"><h2>Message length per turn (words)</h2><div id="lengths"></div><div class="legend"><span><i class="sw" style="background:var(--s2)"></i>you</span><span><i class="sw" style="background:var(--s1)"></i>assistant</span></div></div>
<div class="card"><h2>Threads</h2><div class="tw" id="threads"></div></div>
</div>
<div class="card"><h2>Hangs and longest silences</h2><div class="tw" id="hangs"></div></div>
<div class="card"><h2>All turns</h2><div class="tw" id="turns"></div></div>
<p class="note" id="checks"></p>
</main><div id="tip"></div>
<script>
const DATA=__DATA__;
const $=s=>document.querySelector(s), tip=$("#tip");
const fmt=s=>s==null?"–":s<60?Math.round(s)+"s":s<3600?(s/60).toFixed(s<600?1:0)+"m":(s/3600).toFixed(1)+"h";
const esc=t=>String(t).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const tz=DATA.timezone;
const local=iso=>new Date(iso).toLocaleString([], {timeZone:tz, month:"short", day:"numeric", hour:"numeric", minute:"2-digit"});
function hover(el,html){el.addEventListener("mousemove",e=>{tip.innerHTML=html;tip.style.display="block";tip.style.left=Math.min(e.clientX+12,innerWidth-330)+"px";tip.style.top=(e.clientY+12)+"px"});el.addEventListener("mouseleave",()=>tip.style.display="none")}
const NS="http://www.w3.org/2000/svg";
function svg(w,h){const s=document.createElementNS(NS,"svg");s.setAttribute("viewBox",`0 0 ${w} ${h}`);return s}
function add(p,tag,a){const e=document.createElementNS(NS,tag);for(const k in a)e.setAttribute(k,a[k]);p.appendChild(e);return e}
function txt(p,x,y,t,a={}){const e=add(p,"text",Object.assign({x,y,class:"lbl"},a));e.textContent=t;return e}
const sel=$("#thread");
sel.innerHTML=`<option value="all">All threads (${DATA.threads.length})</option>`+DATA.threads.map((t,i)=>`<option value="${i}">${esc(t.title)} · ${t.platform}</option>`).join("");
try{const v=localStorage.getItem("ct-thread");if(v&&[...sel.options].some(o=>o.value===v))sel.value=v}catch(e){}
function turnsNow(){const ts=sel.value==="all"?DATA.threads:[DATA.threads[+sel.value]];let out=[];ts.forEach(t=>t.turns.forEach(x=>out.push(Object.assign({thread:t.title},x))));if($("#relay").checked)out=out.filter(x=>!x.relay);return out}
const med=a=>{a=a.filter(v=>v!=null).sort((x,y)=>x-y);if(!a.length)return null;const m=a.length>>1;return a.length%2?a[m]:(a[m-1]+a[m])/2};
const q=(a,p)=>{a=a.filter(v=>v!=null).sort((x,y)=>x-y);return a.length?a[Math.min(a.length-1,Math.floor(p*(a.length-1)))]:null};
function render(){
 try{localStorage.setItem("ct-thread",sel.value)}catch(e){}
 const T=turnsNow(), ENG=DATA.engaged_seconds||900, eng=T.map(t=>t.return_gap_s).filter(g=>g!=null&&g<ENG);
 $("#sub").textContent=`${T.length} turns · timezone ${tz} · hang threshold ${DATA.hang_seconds}s · generated ${local(DATA.generated)}`;
 const tiles=[["Turns",T.length,`${T.filter(t=>t.relay).length} relayed`],["Median reply latency",fmt(med(T.map(t=>t.reply_latency_s))),`p90 ${fmt(q(T.map(t=>t.reply_latency_s),.9))}`],
  ["Median turn duration",fmt(med(T.map(t=>t.turn_duration_s))),`longest ${fmt(Math.max(0,...T.map(t=>t.turn_duration_s)))}`],
  ["Your reply time, engaged",fmt(med(eng)),`median of ${eng.length} gaps under 15m`],
  ["Hangs",T.reduce((a,t)=>a+t.hangs.length,0),`silences ≥ ${fmt(DATA.hang_seconds)} inside turns`],
  ["Median words",`${med(T.map(t=>t.user_words))??"–"} / ${med(T.map(t=>t.assistant_words))??"–"}`,"you / assistant"]];
 $("#tiles").innerHTML=tiles.map(([k,v,h])=>`<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="h">${h}</div></div>`).join("");
 // timeline
 const W=1000,rowH=12,H=Math.max(60,T.length*rowH+30),L=60;const s=svg(W,H);const cap=Math.max(60,q(T.map(t=>t.turn_duration_s),.95)||60);
 const x=v=>L+Math.min(v,cap)/cap*(W-L-10);
 for(let i=0;i<=4;i++){const v=cap*i/4;add(s,"line",{x1:x(v),x2:x(v),y1:0,y2:H-20,class:"axis"});txt(s,x(v),H-6,fmt(v),{"text-anchor":"middle"})}
 T.forEach((t,i)=>{const y=i*rowH+4,lat=t.reply_latency_s??t.turn_duration_s;
  if(i%Math.ceil(T.length/20)===0)txt(s,L-6,y+8,"T"+t.n,{"text-anchor":"end"});
  add(s,"rect",{x:x(0),y,width:Math.max(2,x(lat)-x(0)),height:rowH-4,rx:3,fill:"var(--s1)"});
  if(t.turn_duration_s>lat)add(s,"rect",{x:x(lat)+2,y,width:Math.max(2,x(t.turn_duration_s)-x(lat)-2),height:rowH-4,rx:3,fill:"var(--s3)"});
  t.hangs.forEach(h=>{const off=(new Date(h.at)-new Date(t.start))/1000;add(s,"rect",{x:x(off),y:y-1,width:Math.max(3,x(off+h.seconds)-x(off)),height:rowH-2,rx:2,fill:"var(--s2)",opacity:.85})});
  const hit=add(s,"rect",{x:0,y:y-2,width:W,height:rowH,fill:"transparent"});
  hover(hit,`<b>${esc(t.thread)} · T${t.n}</b> ${local(t.start)}<br>${esc(t.user_preview)}<br>first reply ${fmt(t.reply_latency_s)} · turn ${fmt(t.turn_duration_s)} · ${t.tool_steps} tool steps<br>longest silence ${fmt(t.longest_silence_s)}${t.relay?" · relayed":""}`)});
 $("#timeline").replaceChildren(s);
 // return bands
 const names=DATA.bands, counts=names.map(n=>T.filter(t=>t.return_band===n).length), mx=Math.max(1,...counts);
 const b=svg(480,names.length*34+10), shades=["var(--seq4)","var(--seq3)","var(--seq2)","var(--seq1)"];
 names.forEach((n,i)=>{const y=i*34+6,w=counts[i]/mx*300;txt(b,0,y+15,n);add(b,"rect",{x:120,y,width:Math.max(2,w),height:20,rx:4,fill:shades[i]});txt(b,126+w,y+15,counts[i]);
  hover(add(b,"rect",{x:0,y,width:480,height:24,fill:"transparent"}),`${n}: ${counts[i]} turns (${["under 2m","2–15m","15m–3h","over 3h"][i]})`)});
 $("#bands").replaceChildren(b);
 // hours
 const hc=Array(24).fill(0);T.forEach(t=>hc[t.local_hour]++);const hm=Math.max(1,...hc),hs=svg(480,150);
 hc.forEach((c,h)=>{const bw=18,xx=12+h*19.5,bh=c/hm*110;add(hs,"rect",{x:xx,y:120-bh,width:bw,height:Math.max(c?2:0,bh),rx:3,fill:"var(--s1)"});if(h%3===0)txt(hs,xx+bw/2,138,h,{"text-anchor":"middle"});
  hover(add(hs,"rect",{x:xx,y:0,width:bw,height:120,fill:"transparent"}),`${h}:00–${h}:59 · ${c} turns`)});
 add(hs,"line",{x1:10,x2:480,y1:120,y2:120,class:"axis"});$("#hours").replaceChildren(hs);
 // lengths
 const lm=Math.max(1,...T.map(t=>Math.max(t.user_words,t.assistant_words))),ls=svg(480,170),bw2=Math.max(1,460/Math.max(1,T.length)/2-1);
 T.forEach((t,i)=>{const xx=10+i*460/Math.max(1,T.length);[[t.user_words,"var(--s2)",0],[t.assistant_words,"var(--s1)",bw2+1]].forEach(([v,c,o])=>{const h=v/lm*140;add(ls,"rect",{x:xx+o,y:150-h,width:bw2,height:Math.max(v?1:0,h),rx:Math.min(2,bw2/2),fill:c})});
  hover(add(ls,"rect",{x:xx,y:0,width:2*bw2+2,height:150,fill:"transparent"}),`T${t.n}: you ${t.user_words} words, assistant ${t.assistant_words}`)});
 add(ls,"line",{x1:8,x2:480,y1:150,y2:150,class:"axis"});txt(ls,10,166,`max ${lm} words`);$("#lengths").replaceChildren(ls);
 // threads table
 $("#threads").innerHTML=`<table><tr><th>Thread</th><th>Turns</th><th>Reply</th><th>Turn</th><th>You, engaged</th><th>Hangs</th></tr>`+DATA.threads.map(t=>{const s=t.summary;return `<tr><td>${esc(t.title)}<br><span class="note">${t.platform}</span></td><td>${s.turns}</td><td>${fmt(s.reply_latency_s.median)}</td><td>${fmt(s.turn_duration_s.median)}</td><td>${fmt(s.return_gap_engaged_s.median)}</td><td>${s.hangs}</td></tr>`}).join("")+`</table>`;
 // hangs
 const H2=T.map(t=>({t,s:t.longest_silence_s})).sort((a,b)=>b.s-a.s).slice(0,12);
 $("#hangs").innerHTML=`<table><tr><th>Turn</th><th>Started</th><th>Longest silence</th><th>Turn total</th><th>Tool steps</th><th>Message</th></tr>`+H2.map(({t,s})=>`<tr><td>${esc(t.thread)} T${t.n}</td><td>${local(t.start)}</td><td>${fmt(s)}${t.interruptions?" · interrupted":""}</td><td>${fmt(t.turn_duration_s)}</td><td>${t.tool_steps}</td><td>${esc(t.user_preview)}</td></tr>`).join("")+`</table>`;
 $("#turns").innerHTML=`<table><tr><th>Turn</th><th>Started</th><th>You (words)</th><th>Reply</th><th>Turn</th><th>Assistant (words)</th><th>Then you replied after</th></tr>`+T.map(t=>`<tr><td>${esc(t.thread)} T${t.n}${t.relay?" ↪":""}</td><td>${local(t.start)}</td><td>${t.user_words}</td><td>${fmt(t.reply_latency_s)}</td><td>${fmt(t.turn_duration_s)}</td><td>${t.assistant_words}</td><td>${fmt(t.return_gap_s)}${t.return_band?" · "+t.return_band:""}</td></tr>`).join("")+`</table>`;
 $("#checks").textContent="Checks: "+DATA.threads.map(t=>`${t.title}: ${t.checks.join("; ")}`).join(" · ");
}
sel.onchange=render;$("#relay").onchange=render;
$("#theme").onclick=()=>{const r=document.documentElement,d=matchMedia("(prefers-color-scheme: dark)").matches,cur=r.dataset.theme||(d?"dark":"light");r.dataset.theme=cur==="dark"?"light":"dark"};
render();
</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--outdir", default=".")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--hang-seconds", type=float, default=120)
    # relay-kit/paste-markers@1: a turn opening with FROM: is relayed
    ap.add_argument("--relay-pattern", default=r"^\**FROM:\s")
    ap.add_argument("--title", default="Conversation telemetry")
    a = ap.parse_args(argv)
    tz, tz_note = None, None
    try:
        tz = ZoneInfo(a.tz) if ZoneInfo else None
    except Exception:   # no IANA data on this host (common on Windows without the tzdata package)
        tz_note = f"timezone {a.tz} unavailable (pip install tzdata); local hours shown in UTC"
        tz = timezone.utc
    relay_re = re.compile(a.relay_pattern, re.M) if a.relay_pattern else None
    threads = []
    for f in a.files:
        title, platform, events = load(Path(f))
        events = [e for e in events if e["ts"] is not None]
        backwards = sum(1 for x, y in zip(events, events[1:]) if y["ts"] < x["ts"])  # in recorded order
        # Keep recorded (causal) order so a reply stays in the exchange it answers even when clocks
        # run backwards; analyze() sorts times only inside each turn.
        th = analyze(title, platform, events, f, tz, a.hang_seconds, relay_re, backwards)
        if tz_note:
            th["checks"] = [c for c in th["checks"] if c != "ok"] + [tz_note]
        threads.append(th)
    out = {"generated": datetime.now(timezone.utc).isoformat(), "timezone": a.tz if not tz_note else "UTC",
           "hang_seconds": a.hang_seconds, "engaged_seconds": ENGAGED_S,
           "bands": [b for b, _ in BANDS], "threads": threads}
    od = Path(a.outdir); od.mkdir(parents=True, exist_ok=True)
    (od / "telemetry.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    page = HTML.replace("__TITLE__", a.title.replace("<", "&lt;")).replace("__DATA__", json.dumps(out).replace("</", "<\\/"))
    (od / "dashboard.html").write_text(page, encoding="utf-8")
    for t in threads:
        s = t["summary"]
        print(json.dumps({"thread": t["title"], "platform": t["platform"], "turns": s["turns"],
                          "reply_latency_median_s": s["reply_latency_s"].get("median"),
                          "turn_duration_median_s": s["turn_duration_s"].get("median"),
                          "return_bands": s["return_bands"], "hangs": s["hangs"], "checks": t["checks"]}))
    print(f"wrote {od / 'telemetry.json'} and {od / 'dashboard.html'}")


if __name__ == "__main__":
    main()
