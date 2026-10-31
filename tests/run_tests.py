#!/usr/bin/env python3
"""Regression tests for the relay kit. Standard library only; no network, no accounts.

Run from the kit root:  python3 -I -B tests/run_tests.py   (and: node tests/browser_tests.cjs)
Each case is adapted from the 2026-10-28 ChatGPT review's probes, rewritten to assert the
behavior the kit should have. Exit code 1 if any case fails."""
import contextlib, csv, datetime, importlib.util, io, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
X = KIT / "extracted"
LEDGER = KIT / "skills" / "handoff-ledger" / "scripts" / "handoff.py"
FIXTURE = KIT / "tests" / "fixtures" / "HO-20261028-001.md"   # frozen prepared copy
HID = FIXTURE.stem
TMP = Path(tempfile.mkdtemp(prefix="relaykit-tests-"))
RESULTS = []


def case(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))


def run(script, *args, env=None):
    e = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    e.update(env or {})
    p = subprocess.run([sys.executable, "-I", "-B", str(script), *map(str, args)], capture_output=True,
                       encoding="utf-8", env=e, timeout=30)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------- handoff ledger ----------------
def ledger_dir(name, text=None):
    d = TMP / "ledger" / name
    (d / "outputs").mkdir(parents=True)
    (d / FIXTURE.name).write_text(text if text is not None else FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (d / ".ledger.json").write_text('{"owner": "sam"}', encoding="utf-8")   # the fixture's owner
    return d


def setstate(d, state, by, note=None, hid=HID):
    args = ["--dir", d, "set", hid, state, "--by", by] + (["--note", note] if note is not None else [])
    return run(LEDGER, *args)


def ledger_tests():
    if (KIT / "handoffs").is_dir():   # present in the private working copy, not in the public repo
        rc, out, _ = run(LEDGER, "--dir", KIT / "handoffs", "check")
        case("ledger: shipped handoff folder passes check", rc == 0, out)

    d = TMP / "ledger" / "no-config"
    rc, hid, _ = run(LEDGER, "--dir", d, "new", "--from", "claude:a", "--to", "chatgpt:b", "--title", "T")
    r1 = run(LEDGER, "--dir", d, "set", hid, "cancelled", "--by", "sam", "--note", "x")
    r2 = run(LEDGER, "--dir", d, "set", hid, "cancelled", "--by", "owner", "--note", "x")
    case("ledger: with no .ledger.json the owner is 'owner'", r1[0] != 0 and r2[0] == 0, r1[2] + r2[2])
    d = TMP / "ledger" / "named-owner"
    run(LEDGER, "--dir", d, "new", "--from", "claude:a", "--to", "chatgpt:b", "--title", "T")
    (d / ".ledger.json").write_text('{"owner": "Sam"}', encoding="utf-8")
    hid = next(d.glob("HO-*.md")).stem
    r3 = run(LEDGER, "--dir", d, "set", hid, "cancelled", "--by", "sam", "--note", "x")
    case("ledger: .ledger.json names the owner (case-insensitive)", r3[0] == 0, r3[2])
    d = TMP / "ledger" / "bad-config"
    run(LEDGER, "--dir", d, "new", "--from", "claude:a", "--to", "chatgpt:b", "--title", "T")
    (d / ".ledger.json").write_text('{"owner": "two words"}', encoding="utf-8")
    case("ledger: a malformed owner setting is refused", run(LEDGER, "--dir", d, "list")[0] != 0)

    d = ledger_dir("lifecycle")
    steps = [setstate(d, "sent", "claude:relay-kit", "local test delivery"),
             setstate(d, "acknowledged", "chatgpt:ada", "read")]
    (d / "outputs" / FIXTURE.name).write_text("HO-20261028-001 · chatgpt:ada · test\n", encoding="utf-8")
    steps.append(setstate(d, "completed", "chatgpt:ada", "outputs/HO-20261028-001.md"))
    steps.append(run(LEDGER, "--dir", d, "check"))
    case("ledger: prepared -> sent -> acknowledged -> completed, then check", all(s[0] == 0 for s in steps),
         str([s[2] for s in steps if s[0]]))
    case("ledger: completed can't reopen", setstate(d, "in_progress", "chatgpt:ada")[0] != 0)

    for name, state, by, note in [("can't skip delivery", "acknowledged", "chatgpt:ada", "x"),
                                  ("wrong sender refused", "sent", "chatgpt:ada", "x"),
                                  ("sent needs a note", "sent", "claude:relay-kit", None),
                                  ("only the owner cancels", "cancelled", "claude:relay-kit", "x")]:
        case(f"ledger: {name}", setstate(ledger_dir(name.replace(' ', '-').replace("'", '')), state, by, note)[0] != 0)

    d = ledger_dir("output-required")
    setstate(d, "sent", "claude:relay-kit", "x"); setstate(d, "acknowledged", "chatgpt:ada", "x")
    case("ledger: completed needs the output file", setstate(d, "completed", "chatgpt:ada", "x")[0] != 0)
    case("ledger: duplicate acknowledgement refused", setstate(d, "acknowledged", "chatgpt:ada", "x")[0] != 0)

    d = ledger_dir("blocked-return")
    a = setstate(d, "blocked", "claude:relay-kit", "waiting on Sam")
    b = setstate(d, "sent", "claude:relay-kit", "skips the return")
    c = setstate(d, "prepared", "claude:relay-kit", "unblocked")
    case("ledger: blocked returns only to the state it left", a[0] == 0 and b[0] != 0 and c[0] == 0, str([a, b, c]))

    d = ledger_dir("sent-no-note")
    p = d / FIXTURE.name
    p.write_text(p.read_text(encoding="utf-8").replace("status: prepared", "status: sent")
                 + "- 2026-10-28T14:06Z | sent | claude:relay-kit |\n", encoding="utf-8")
    case("ledger: check flags a sent line with no note", run(LEDGER, "--dir", d, "check")[0] != 0)

    d = ledger_dir("status-log-disagree")
    p = d / FIXTURE.name
    p.write_text(p.read_text(encoding="utf-8").replace("status: prepared", "status: cancelled"), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    r = setstate(d, "sent", "claude:relay-kit", "x")
    case("ledger: set refuses an already-inconsistent file, unchanged", r[0] != 0 and p.read_text(encoding="utf-8") == before, r[2])

    d = TMP / "ledger" / "unfinished"
    rc, hid, _ = run(LEDGER, "--dir", d, "new", "--from", "claude:test", "--to", "chatgpt:test", "--title", "Draft")
    r = setstate(d, "sent", "claude:test", "x", hid=hid)
    case("ledger: a TODO-filled draft can't be sent", rc == 0 and r[0] != 0, r[2])

    d = ledger_dir("log-not-last")
    p = d / FIXTURE.name
    p.write_text(p.read_text(encoding="utf-8") + "\n## Appendix\nMore.\n", encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    r = setstate(d, "sent", "claude:relay-kit", "x")
    case("ledger: refuses a file whose Log isn't the last section", r[0] != 0 and p.read_text(encoding="utf-8") == before)

    d = ledger_dir("multiline-note")
    r = setstate(d, "sent", "claude:relay-kit", "ok\n- 2026-10-28T14:07Z | acknowledged | chatgpt:ada | injected")
    case("ledger: multi-line note rejected", r[0] != 0 and "acknowledged" not in (d / FIXTURE.name).read_text(encoding="utf-8").split("## Log")[1])

    d = ledger_dir("traversal")
    outside = d.parent / "outside-sentinel.md"
    shutil.copy2(FIXTURE, outside)
    r = run(LEDGER, "--dir", d, "set", "../outside-sentinel", "sent", "--by", "claude:relay-kit", "--note", "x")
    case("ledger: id can't escape the folder", r[0] != 0 and "status: prepared" in outside.read_text(encoding="utf-8"), r[2])

    d = ledger_dir("locked")
    (d / ".handoff.lock").write_text("other\n")
    r = setstate(d, "sent", "claude:relay-kit", "x")
    case("ledger: a held lock refuses the update", r[0] != 0 and "lock" in r[2])
    case("ledger: check reports a leftover lock", run(LEDGER, "--dir", d, "check")[0] != 0)

    d = ledger_dir("at-flag")
    a = run(LEDGER, "--dir", d, "set", HID, "sent", "--by", "sam", "--at", "2026-10-28T17:50Z", "--note", "approx.")
    last = (d / FIXTURE.name).read_text(encoding="utf-8").rstrip().splitlines()[-1]
    case("ledger: --at logs the event time and keeps the recording time", a[0] == 0 and last.startswith("- 2026-10-28T17:50Z | sent | sam |") and "(recorded " in last, last)
    b = run(LEDGER, "--dir", d, "set", HID, "acknowledged", "--by", "sam", "--at", "2026-10-28T17:40Z")
    case("ledger: --at can't go earlier than the previous line", b[0] != 0, b[2])
    c = run(LEDGER, "--dir", d, "set", HID, "acknowledged", "--by", "sam", "--at", "2099-01-01T00:00Z")
    case("ledger: --at can't be in the future", c[0] != 0, c[2])

    d = ledger_dir("out-of-order")
    p = d / FIXTURE.name
    p.write_text(p.read_text(encoding="utf-8").replace("status: prepared", "status: sent")
                 + "- 2026-10-28T09:00Z | sent | claude:relay-kit | before it was drafted\n", encoding="utf-8")
    case("ledger: check flags log lines out of time order", run(LEDGER, "--dir", d, "check")[0] != 0)

    # The 2026-10-29 collision: two sessions, two clones, the same new id.
    origin = TMP / "ids-origin.git"
    g = lambda cwd, *a: subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], capture_output=True)
    A, B = TMP / "ids-A", TMP / "ids-B"
    for c in (A, B):
        subprocess.run(["git", "clone", "-q", str(origin), str(c)], capture_output=True)
        g(c, "config", "user.name", "t"); g(c, "config", "user.email", "t@t")
    (B / "handoffs").mkdir()
    (B / "handoffs" / ".keep").write_text("")
    g(B, "add", "."); g(B, "commit", "-qm", "init"); g(B, "push", "-q", "origin", "main")
    g(A, "pull", "-q", "origin", "main")
    rc, hid_a, _ = run(LEDGER, "--dir", A / "handoffs", "new", "--from", "claude:aec", "--to", "codex:aec", "--title", "AEC work")
    g(A, "add", "."); g(A, "commit", "-qm", "aec handoff"); g(A, "push", "-q", "origin", "main")
    rc2, hid_b, err_b = run(LEDGER, "--dir", B / "handoffs", "new", "--from", "claude:rk", "--to", "claude:builder", "--title", "Pilot")
    case("ledger: new skips an id another clone already pushed", rc == 0 and rc2 == 0 and hid_a != hid_b and hid_b.endswith("-002"),
         f"{hid_a} / {hid_b} {err_b}")
    tpl = (A / "handoffs" / f"{hid_a}.md").read_text(encoding="utf-8")
    case("ledger: an unfilled Authorized section says not to start", "NOT AUTHORIZED YET" in tpl)

    d = ledger_dir("renumbered-output")
    p = d / FIXTURE.name
    p.write_text(p.read_text(encoding="utf-8").replace("outputs/HO-20261028-001.md", "outputs/HO-20261028-009.md"), encoding="utf-8")
    rc, so, _ = run(LEDGER, "--dir", d, "check")
    r = setstate(d, "sent", "claude:relay-kit", "x")
    case("ledger: check warns (doesn't block) on an Expected output naming another handoff's file",
         rc == 0 and "WARNING" in so and "HO-20261028-009" in so and r[0] == 0, so + r[2])

    d = TMP / "ledger" / "block-draft"
    rc, hid, _ = run(LEDGER, "--dir", d, "new", "--from", "claude:a", "--to", "chatgpt:b", "--title", "T")
    r = run(LEDGER, "--dir", d, "set", hid, "blocked", "--by", "claude:a", "--note", "reported run without authorization")
    case("ledger: an unfilled draft can be marked blocked", r[0] == 0 and run(LEDGER, "--dir", d, "check")[0] == 0, r[2])

    d = ledger_dir("log-inside")
    setstate(d, "sent", "claude:relay-kit", "x")
    txt = (d / FIXTURE.name).read_text(encoding="utf-8")
    case("ledger: new log line lands inside Log, file ends cleanly", txt.rstrip().endswith("| sent | claude:relay-kit | x")
         and not list(d.glob(".*.tmp")))


# ---------------- capture -> seed / SSD ----------------
def chatgpt_fixture(times=True, leaf=True):
    msgs = [("u1", "user", "Please preserve the exact reply pointers."),
            ("a1", "assistant", "First assistant statement belongs only to a1."),
            ("a2", "assistant", "Second assistant statement belongs only to a2."),
            ("u2", "user", "I approve only the bounded review."),
            ("a3", "assistant", "Acknowledged bounded review.")]
    stamps = [1793196000, 1793196005, 1793196010, 1793197810, 1793197815]
    mp = {}
    for i, (mid, role, text) in enumerate(msgs):
        m = dict(id=mid, author=dict(role=role), content=dict(content_type="text", parts=[text]), recipient="all", metadata={})
        if times: m["create_time"] = stamps[i]
        mp[mid] = dict(id=mid, parent=msgs[i - 1][0] if i else None,
                       children=[msgs[i + 1][0]] if i + 1 < len(msgs) else [], message=m)
    d = dict(title="Fixture", conversation_id="fixture00", mapping=mp)
    if leaf: d["current_node"] = "a3"
    return d


def wrap(d):
    return dict(_capture=dict(platform="chatgpt", kind="conversation", id="fixture00"), data=d)


def render(name, data, extra_env=None):
    raw = TMP / f"{name}.raw.json"
    raw.write_text(json.dumps(data), encoding="utf-8")
    out = TMP / f"{name}-rendered"
    rc, so, se = run(X / "chat-capture" / "03_render.py", raw, "-o", out, env=extra_env)
    return rc, so, se, out


def capture_tests():
    rc, so, se, out = render("fixture", wrap(chatgpt_fixture()))
    case("capture: renders the fixture", rc == 0, se)
    cap_json, cap_md = next(out.glob("*.json")), next(out.glob("*.md"))
    seed = load("seed_index", X / "thread-seed" / "01_seed_index.py")
    want = ["T1u", "T1a", "T1a2", "T2u", "T2a"]
    case("seed: JSON index keeps every message id", [seed.mid(m) for m in seed.load(str(cap_json))] == want)
    case("seed: Markdown index keeps every message id", [seed.mid(m) for m in seed.load(str(cap_md))] == want)

    rc2, _, _, out2 = render("no-time", wrap(chatgpt_fixture(times=False)))
    md2 = next(out2.glob("*.md"))
    case("seed: timestamp-free Markdown keeps T1a2", [seed.mid(m) for m in seed.load(str(md2))] == want,
         str([seed.mid(m) for m in seed.load(str(md2))]))

    mixed_msgs = [dict(n=1, k=1, role="user", label="You", text="Compare this.\nFROM: Ada\nanswer\nEND FROM", ts="")]
    case("seed: a FROM block inside your message is flagged as a paste, speaker stays you",
         "contains paste" in seed.index(mixed_msgs) and "(relayed)" not in seed.index(mixed_msgs))
    q = TMP / "quote.md"
    q.write_text('"First assistant statement belongs only to a1." [T1a2]\n', encoding="utf-8")
    case("seed: quote cited to the wrong message fails", run(X / "thread-seed" / "01_seed_index.py", cap_json, "--verify", q)[0] == 1)
    q.write_text('"First assistant statement belongs only to a1." [T1a]\n', encoding="utf-8")
    case("seed: quote cited to the right message passes", run(X / "thread-seed" / "01_seed_index.py", cap_json, "--verify", q)[0] == 0)
    sparse = json.loads(cap_json.read_text(encoding="utf-8"))
    for t in sparse["turns"]:
        if t["n"] == 2: t["n"] = 3
    sp = TMP / "sparse.json"; sp.write_text(json.dumps(sparse), encoding="utf-8")
    q.write_text("Missing exchange [T2].\n", encoding="utf-8")
    case("seed: a ref to a missing exchange fails", run(X / "thread-seed" / "01_seed_index.py", sp, "--verify", q)[0] == 1)

    rc3, _, se3, _ = render("no-leaf", chatgpt_fixture(leaf=False) | {"mapping": {**chatgpt_fixture()["mapping"],
        "a3b": dict(id="a3b", parent="u2", children=[], message=dict(id="a3b", author=dict(role="assistant"),
        content=dict(content_type="text", parts=["Alternate branch."]), recipient="all", metadata={}))}})
    case("capture: ambiguous active branch is an error, not an empty 'ok'", rc3 != 0, se3)
    rc4, so4, _, _ = render("no-leaf-single", chatgpt_fixture(leaf=False))
    case("capture: missing current_node with one leaf still renders", rc4 == 0 and '"visible_messages": 5' in so4)

    ssd = X / "selective-self-distillation" / "01_extract_turns.py"
    o = TMP / "ssd-json.json"
    rc, so, se = run(ssd, cap_json, "-o", o)
    recs = json.loads(o.read_text(encoding="utf-8"))
    case("ssd: reads chat-capture normalized JSON", rc == 0 and len(recs) == 2, so + se)
    o = TMP / "ssd-md.json"
    run(ssd, cap_md, "-o", o)
    recs = json.loads(o.read_text(encoding="utf-8"))
    case("ssd: Markdown own-text has no capture comments or separators",
         len(recs) == 2 and all("<!--" not in r["own"] and not r["own"].rstrip().endswith("---") for r in recs),
         str([r["own"] for r in recs]))
    bad = TMP / "unsupported.json"; bad.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    rc, so, se = run(ssd, bad, "-o", TMP / "ssd-bad.json")
    case("ssd: unsupported JSON fails loudly", rc != 0 and "SKIPPED" in se, se)
    relay_md = TMP / "relay.md"
    relay_md.write_text("### You said:\nFROM: Claude\nPlease implement this proposed change.\n\n### ChatGPT said:\nOK\n", encoding="utf-8")
    o = TMP / "ssd-relay.json"; run(ssd, relay_md, "-o", o)
    case("ssd: short relay without a paste delimiter is flagged", "relayed_from_model" in json.loads(o.read_text(encoding="utf-8"))[0]["flags"])
    mixed = TMP / "mixed.md"
    mixed.write_text("### You said:\nCompare this with our plan.\nFROM: Ada (ChatGPT)\nKate's long answer here.\nEND FROM\nThen tell me which wins.\n", encoding="utf-8")
    o = TMP / "ssd-mixed.json"; run(ssd, mixed, "-o", o)
    r = json.loads(o.read_text(encoding="utf-8"))[0]
    case("ssd: a FROM ... END FROM block after your words is stripped from own text",
         "Ada's long answer" not in r["own"] and "Compare this" in r["own"] and "which wins" in r["own"]
         and "relayed_from_model" not in r["flags"], r["own"])
    named = TMP / "named.md"
    named.write_text("### Sam said:\nMy own idea.\n\n### ChatGPT said:\nA reply.\n\n### Sam said:\nAda here, relaying.\n", encoding="utf-8")
    o = TMP / "ssd-named.json"; run(ssd, named, "-o", o, "--user-names", "Sam", "--assistant-names", "Ada")
    recs = json.loads(o.read_text(encoding="utf-8"))
    case("ssd: --user-names and --assistant-names configure who is who",
         [r["own"] for r in recs] == ["My own idea.", "Ada here, relaying."] and "relayed_from_model" in recs[1]["flags"], str(recs))
    smart = TMP / "smart.md"
    smart.write_text("### You said:\nMy note.\n>“””\npasted from phone\n“””\n", encoding="utf-8")
    o = TMP / "ssd-smart.json"; run(ssd, smart, "-o", o)
    case("ssd: smart-quoted >\"\"\" paste delimiter still works", "pasted from phone" not in json.loads(o.read_text(encoding="utf-8"))[0]["own"])
    nonascii = TMP / "na.md"; nonascii.write_text("### You said:\nCafé — naïve “quotes”\n", encoding="utf-8")
    o = TMP / "ssd-na.json"; run(ssd, nonascii, "-o", o, env={"PYTHONUTF8": "0"})
    try:
        ok = "Café" in json.loads(o.read_bytes().decode("utf-8"))[0]["own"]
    except Exception:
        ok = False
    src = ssd.read_text(encoding="utf-8")
    # This host's locale is UTF-8, so the run alone can't show a CP1252 failure; also check the source.
    case("ssd: output written as UTF-8 (run + source check)", ok and "open(a.o, 'w', encoding='utf-8')" in src)


def live_fixture_tests():
    """A real ChatGPT capture from the 2026-10-28 live check (redacted)."""
    f = KIT / "tests" / "fixtures" / "live" / "chatgpt-live-2026-10-28.raw.json"
    out = TMP / "live"
    rc, so, se = run(X / "chat-capture" / "03_render.py", f, "-o", out)
    j = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8")) if rc == 0 else {}
    ok = (rc == 0 and [t["role"] for t in j["turns"]] == ["user", "assistant"]
          and j["turns"][1]["messages"][0]["text"] == "RELAY-OK 1"
          and j["meta"]["models_seen"] == ["gpt-6-thinking"] and j["hidden_counts"] == {"assistant:reasoning_recap": 1})
    case("live capture: renders the real ChatGPT conversation", ok, se or so)


def schema_tests():
    """One conversation format, relay-kit/conversation@1, with one message id per message."""
    rc, so, se, out = render("schema", wrap(chatgpt_fixture()))
    cap = next(out.glob("*.json")); md = next(out.glob("*.md"))
    j = json.loads(cap.read_text(encoding="utf-8"))
    mids = [m["mid"] for tt in j["turns"] for m in tt["messages"]]
    case("schema: renderer tags the version and gives every message a unique mid",
         j.get("schema") == "relay-kit/conversation@1" and mids == ["T1u", "T1a", "T1a2", "T2u", "T2a"], str(mids))

    d = chatgpt_fixture()   # a conversation that opens with the assistant
    mp = d["mapping"]
    greet = dict(id="g0", parent=None, children=["u1"], message=dict(id="g0", author=dict(role="assistant"),
                 content=dict(content_type="text", parts=["Hi, how can I help?"]), recipient="all", metadata={}, create_time=1793195990))
    mp["u1"]["parent"] = "g0"; mp["g0"] = greet
    rc, so, se, out2 = render("lead-assistant", wrap(d))
    j2 = json.loads(next(out2.glob("*.json")).read_text(encoding="utf-8"))
    m2 = [m["mid"] for tt in j2["turns"] for m in tt["messages"]]
    case("schema: a reply before the first user turn is T0a, ids stay unique",
         m2[0] == "T0a" and len(set(m2)) == len(m2), str(m2))
    md2 = next(out2.glob("*.md"))
    got = [(seed_mod.mid(m), m["role"]) for m in seed_mod.load(str(md2))] if (seed_mod := load("seed_lead", X / "thread-seed" / "01_seed_index.py")) else []
    case("schema: Markdown of an assistant-first conversation keeps ids and roles",
         got == [("T0a", "assistant"), ("T1u", "user"), ("T1a", "assistant"), ("T1a2", "assistant"), ("T2u", "user"), ("T2a", "assistant")], str(got))

    seed = load("seed_index_schema", X / "thread-seed" / "01_seed_index.py")
    case("schema: thread-seed's ids match the capture's, from JSON and Markdown",
         [seed.mid(m) for m in seed.load(str(cap))] == mids == [seed.mid(m) for m in seed.load(str(md))])

    old = TMP / "old-markers.md"
    old.write_text("# Old\n\n### You said:\n<!-- T1 · 2026-10-27 09:00:00 CDT -->\n\nHi\n\n---\n\n"
                   "### ChatGPT said:\n<!-- T1 · 2026-10-27 09:00:05 CDT · 2 messages -->\n\nOne\n\n"
                   "<!-- 2026-10-27 09:00:09 CDT -->\n\nTwo\n\n---\n", encoding="utf-8")
    case("schema: captures written before the schema still parse",
         [seed.mid(m) for m in seed.load(str(old))] == ["T1u", "T1a", "T1a2"])

    ssd = X / "selective-self-distillation" / "01_extract_turns.py"
    o = TMP / "ssd-schema.json"; run(ssd, cap, "-o", o)
    case("schema: selective-self-distillation locates turns by mid",
         [r["loc"] for r in json.loads(o.read_text(encoding="utf-8"))] == ["T1u", "T2u"])

    tel = X / "chat-telemetry" / "01_chat_telemetry.py"
    rc, so, se = run(tel, cap, "-o", TMP / "tel-schema")
    th = json.loads((TMP / "tel-schema" / "telemetry.json").read_text(encoding="utf-8"))["threads"][0] if rc == 0 else {}
    case("schema: chat-telemetry reads a normalized capture and says what it can't measure",
         rc == 0 and th["summary"]["turns"] == 2 and any("normalized" in c for c in th["checks"]), se)

    bad = dict(j, schema="relay-kit/conversation@2")
    bp = TMP / "future.json"; bp.write_text(json.dumps(bad), encoding="utf-8")
    r_seed = run(X / "thread-seed" / "01_seed_index.py", bp)
    r_ssd = run(ssd, bp, "-o", TMP / "ssd-future.json")
    r_tel = run(tel, bp, "-o", TMP / "tel-future")
    case("schema: an unknown version is refused by seed, SSD and telemetry",
         r_seed[0] != 0 and r_ssd[0] != 0 and r_tel[0] != 0, str([r_seed[2][-80:], r_ssd[2][-80:], r_tel[2][-80:]]))


def governance_tests():
    """The architecture's own rules: contracts, invariants, ledgers."""
    test_src = (KIT / "tests" / "run_tests.py").read_text(encoding="utf-8") + (KIT / "tests" / "browser_tests.cjs").read_text(encoding="utf-8")
    def test_exists(name):
        tail = name.split(": ", 1)[-1]
        return tail.replace('\\"', '"') in test_src.replace('\\"', '"')

    reg = json.loads((KIT / "governance" / "contracts.json").read_text(encoding="utf-8"))
    missing = []
    for c in reg["contracts"]:
        for f in [c["spec"]] + c["implementers"]:
            path = KIT / f
            if not path.exists():
                missing.append(f"{c['id']}: {f} missing")
            elif c["id"] not in path.read_text(encoding="utf-8"):
                missing.append(f"{c['id']}: {f} doesn't name this version")
        missing += [f"{c['id']}: no test '{n}'" for n in c["tests"] if not test_exists(n)]
    case("governance: every contract's spec and implementers carry its version; its tests exist",
         not missing, "; ".join(missing))

    purpose = (KIT / "governance" / "purpose.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", purpose, flags=re.M)[1:]
    pids = [s.split()[0] for s in sections]
    bad = [s.split()[0] for s in sections if "- Enforced by:" not in s]
    cited = re.findall(r"`([a-z]+(?: \([^)]*\))?: [^`]+)`", purpose)
    gone = [n for n in cited if not test_exists(n)]
    case("governance: every invariant states its enforcement and cites real tests",
         pids and not bad and not gone, f"no enforcement: {bad}; missing tests: {gone}")
    arch = (KIT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    case("governance: ARCHITECTURE.md lists exactly the invariants in purpose.md",
         re.findall(r"^\| (P-\d\d) \|", arch, re.M) == pids, str(pids))

    chk = KIT / "tools" / "check_ledgers.py"
    g = TMP / "gov-ok"; g.mkdir()
    dec = [dict(id="DEC-001", date="2026-10-28", status="adopted", decision="d", scope="s", decider="owner", basis='"yes"', locator="chat"),
           dict(id="DEC-002", date="2026-10-28", status="proposed", decision="d2", scope="s")]
    ch = [dict(id="CH-001", date="2026-10-28", status="open", concern="c", raised_by="x")]
    (g / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in dec), encoding="utf-8")
    (g / "challenges.jsonl").write_text("".join(json.dumps(c) + "\n" for c in ch), encoding="utf-8")
    case("ledgers: a valid decision and challenge ledger passes", run(chk, "--dir", g)[0] == 0)
    b = TMP / "gov-bad"; b.mkdir()
    bad_dec = [dict(id="DEC-001", date="2026-10-28", status="adopted", decision="d", scope="s"),
               dict(id="DEC-003", date="28 Oct", status="maybe", decision="d", scope="s")]
    (b / "decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in bad_dec), encoding="utf-8")
    (b / "challenges.jsonl").write_text(json.dumps(dict(id="CH-001", date="2026-10-28", status="resolved", concern="c", raised_by="x")) + "\n", encoding="utf-8")
    rc, so, _ = run(chk, "--dir", b)
    case("ledgers: missing basis, skipped id, bad date/status and unexplained resolution are all caught",
         rc != 0 and all(k in so for k in ("'basis' is required", "expected DEC-002", "YYYY-MM-DD", "status must be", "'resolution' is required")), so)
    gi = TMP / "gov-git"; gi.mkdir()
    (gi / "decisions.jsonl").write_text(json.dumps(dec[0]) + "\n", encoding="utf-8")
    git = lambda *a: subprocess.run(["git", "-C", str(gi), *a], capture_output=True, text=True)
    git("init", "-q"); git("add", "."); git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    (gi / "decisions.jsonl").write_text(json.dumps(dict(dec[0], decision="rewritten")) + "\n", encoding="utf-8")
    rc, so, _ = run(chk, "--dir", gi, "--git")
    (gi / "decisions.jsonl").write_text(json.dumps(dec[0]) + "\n" + json.dumps(dec[1]) + "\n", encoding="utf-8")
    rc2, so2, _ = run(chk, "--dir", gi, "--git")
    case("ledgers: --git catches an edited earlier line and allows appending", rc != 0 and "append-only" in so and rc2 == 0, so + so2)

    d = ledger_dir("newer-contract")
    p = d / FIXTURE.name
    p.write_text(p.read_text(encoding="utf-8").replace("status: prepared\n", "status: prepared\ncontract: relay-kit/handoff@2\n"), encoding="utf-8")
    case("ledger: a handoff from a newer contract version is refused",
         run(LEDGER, "--dir", d, "check")[0] != 0 and setstate(d, "sent", "claude:relay-kit", "x")[0] != 0)


# ---------------- status (starting back up) ----------------
def status_tests():
    st = KIT / "tools" / "status.py"
    r = TMP / "status-root"
    d = ledger_dir("status")                      # fixture: prepared, Authorized filled
    shutil.copytree(d, r / "handoffs")
    fx = FIXTURE.read_text(encoding="utf-8")
    unauth = re.sub(r"(## Authorized[ \t]*\n).*?(?=^## )", r"\1TODO: NOT AUTHORIZED YET.\n\n", fx.replace(HID, "HO-20261028-002"), flags=re.S | re.M)
    (r / "handoffs" / "HO-20261028-002.md").write_text(unauth, encoding="utf-8")
    (r / "handoffs" / "outputs" / f"{HID}.md").write_text("result\n", encoding="utf-8")
    (r / "governance").mkdir()
    rows = [dict(id="DEC-001", date="2026-10-28", status="proposed", decision="old idea", scope="s"),
            dict(id="DEC-002", date="2026-10-29", status="adopted", decision="replaces old idea", scope="s", supersedes="DEC-001",
                 decider="owner", basis='"yes"', locator="chat"),
            dict(id="DEC-003", date="2026-10-30", status="proposed", decision="pending idea", scope="s")]
    (r / "governance" / "decisions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    before = {p: p.read_bytes() for p in r.rglob("*") if p.is_file()}
    rc, out, err = run(st, "--root", r, "--no-fetch")
    waiting = out.split("== Waiting on the owner")[1].split("==")[0] if "== Waiting on the owner" in out else ""
    case("status: lists unsuperseded proposals and unauthorized handoffs as waiting on the owner",
         rc == 0 and "DEC-003" in waiting and "DEC-001" not in waiting and "HO-20261028-002" in waiting and HID not in waiting, out + err)
    case("status: flags a handoff whose output is filed but whose state was never closed",
         re.search(rf"{HID} .*OUTPUT FILED, state not closed", out) is not None, out)
    case("status: says what it couldn't check (no git, no challenges, no index) and changes nothing",
         all(k in out for k in ("not a git repository", "challenges.jsonl", "threads.md"))
         and before == {p: p.read_bytes() for p in r.rglob("*") if p.is_file()}, out)

    g = lambda cwd, *a: subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                        capture_output=True, text=True)
    remote, c1, c2 = TMP / "st-remote.git", TMP / "st-c1", TMP / "st-c2"
    g(TMP, "init", "-q", "--bare", "-b", "main", str(remote))
    g(TMP, "clone", "-q", str(remote), str(c1))
    g(c1, "checkout", "-q", "-b", "main"); (c1 / "threads.md").write_text("# index\n- 2026-10-31 | x | created\n", encoding="utf-8")
    g(c1, "add", "."); g(c1, "commit", "-qm", "a"); g(c1, "push", "-q", "-u", "origin", "main")
    g(TMP, "clone", "-q", str(remote), str(c2))
    (c1 / "threads.md").write_text("# index\n- 2026-10-31 | x | created\n- 2026-10-31 | y | moved\n", encoding="utf-8")
    g(c1, "commit", "-qam", "b"); g(c1, "push", "-q")
    rc, out, err = run(st, "--root", c2)
    case("status: a copy behind the shared one is told to pull first",
         rc == 0 and "1 behind" in out and "PULL FIRST" in out and "last change: - 2026-10-31 | x | created" in out, out + err)


# ---------------- telemetry ----------------
def telemetry_tests():
    tel = load("telemetry", X / "chat-telemetry" / "01_chat_telemetry.py")
    title, platform, events = tel.load_chatgpt(chatgpt_fixture())
    th = tel.analyze(title, platform, events, "fixture", datetime.timezone.utc, 120, None)
    case("telemetry: a 30-minute gap is not 'engaged'", th["summary"]["return_gap_engaged_s"].get("n", 0) == 0,
         str(th["summary"]["return_gap_engaged_s"]))
    conv = dict(name="No tools", chat_messages=[
        dict(uuid="u", sender="human", created_at="2026-10-28T14:00:00Z", content=[dict(type="text", text="Hello")]),
        dict(uuid="a", sender="assistant", created_at="2026-10-28T14:00:01Z",
             content=[dict(type="text", text="Hello back", start_timestamp="2026-10-28T14:00:01Z", stop_timestamp="2026-10-28T14:00:02Z")])])
    title, platform, events = tel.load_claude_ai(conv)
    th = tel.analyze(title, platform, events, "fixture", datetime.timezone.utc, 120, None)
    case("telemetry: a text block's end time isn't a tool step", th["summary"]["tool_steps"] == 0, str(th["summary"]["tool_steps"]))
    raw = TMP / "skew.raw.json"
    d = chatgpt_fixture()
    d["mapping"]["a1"]["message"]["create_time"] = 1793197811   # clock skew: after u2
    raw.write_text(json.dumps(wrap(d)), encoding="utf-8")
    out = TMP / "tel-skew"
    rc, so, se = run(X / "chat-telemetry" / "01_chat_telemetry.py", raw, "-o", out)
    t = json.loads((out / "telemetry.json").read_text(encoding="utf-8"))["threads"][0]
    case("telemetry: clock skew doesn't move a reply into the next exchange",
         rc == 0 and [x["assistant_messages"] for x in t["turns"]] == [2, 1] and any("backwards" in c for c in t["checks"]),
         str([x["assistant_messages"] for x in t["turns"]]) + se)
    rc, so, se = run(X / "chat-telemetry" / "01_chat_telemetry.py", TMP / "fixture.raw.json", "-o", TMP / "tel-badtz", "--tz", "Nowhere/Missing")
    t = json.loads((TMP / "tel-badtz" / "telemetry.json").read_text(encoding="utf-8")) if rc == 0 else {}
    case("telemetry: missing timezone data falls back to UTC with a note",
         rc == 0 and any("unavailable" in c for c in t["threads"][0]["checks"]), se)


# ---------------- evidence ----------------
def evidence_tests():
    if not (KIT / "evidence").is_dir():   # private material; absent from the public repo
        return
    html = (KIT / "evidence" / "coordination-telemetry-dashboard.html").read_text(encoding="utf-8")
    dash = json.loads(re.search(r"const DATA=(.*?);\n", html).group(1))
    turns = list(csv.DictReader((KIT / "evidence" / "turns.csv").open(encoding="utf-8")))
    note = (KIT / "evidence" / "README.md").read_text(encoding="utf-8") if (KIT / "evidence" / "README.md").exists() else ""
    rel_csv = sum(r["author"] == "model-relayed" for r in turns)
    rel_dash = sum(t["relay"] for th in dash["threads"] for t in th["turns"])
    case("evidence: relay-count difference between dashboard and CSV is documented",
         rel_csv == rel_dash or (str(rel_csv) in note and str(rel_dash) in note), f"csv {rel_csv}, dashboard {rel_dash}")


if __name__ == "__main__":
    ledger_tests()
    (TMP / "fixture.raw.json").write_text(json.dumps(wrap(chatgpt_fixture())), encoding="utf-8")
    capture_tests()
    live_fixture_tests()
    schema_tests()
    governance_tests()
    status_tests()
    telemetry_tests()
    evidence_tests()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed  (scratch: {TMP})")
    sys.exit(1 if failed else 0)
