// Regression tests for the relay kit's browser scripts, run against mocked DOMs (no browser, no
// network). Adapted from the 2026-10-28 ChatGPT review's probes. Run from the kit root:
//   node tests/browser_tests.cjs
// These check the scripts' logic only; they say nothing about current chatgpt.com / claude.ai markup.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const KIT = path.join(__dirname, '..');
// The scripts live as code blocks in the SKILL.md files; extract them fresh for this run (nothing generated is kept in git).
const os = require('node:os');
const EXTRACTED = fs.mkdtempSync(path.join(os.tmpdir(), 'relaykit-browser-'));
require('node:child_process').execFileSync('python3', ['-I', path.join(KIT, 'tools_extract.py'), '--out', EXTRACTED], { stdio: ['ignore', 'ignore', 'inherit'] });
process.on('exit', () => fs.rmSync(EXTRACTED, { recursive: true, force: true }));
let passed = 0, failed = 0;
const check = (name, ok, detail) => {
  ok ? passed++ : failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? '  -- ' + JSON.stringify(detail) : ''}`);
};

function button(label, onClick) {
  return { offsetParent: {}, disabled: false, getClientRects: () => [1],
           getAttribute: (a) => (a === 'aria-label' ? label : null), click: onClick };
}

async function sendWait(s) {
  let elapsed = 0, polls = 0;
  const clicked = [];
  const attrs = { 'data-message-complete': s.completeValue, 'data-is-streaming': s.streaming };
  const reply = {
    parentElement: { closest: () => null },
    get innerText() { return polls ? (s.textAt ? s.textAt(polls) : s.text) : ''; },
    hasAttribute: (a) => attrs[a] !== undefined,
    getAttribute: (a) => (attrs[a] === undefined ? null : attrs[a]),
    closest: () => null,
  };
  const send = button('Send message', () => clicked.push('message'));
  const other = button('Send feedback', () => clicked.push('feedback'));
  const buttons = s.noSendInForm ? [] : s.feedbackFirst ? [other, send] : [send];
  if (s.stopButton) buttons.push(button('Stop streaming', () => {}));
  const outside = button('Send message', () => clicked.push('outside-form'));   // e.g. a Send in some other widget
  const form = { querySelectorAll: (sel) => (sel === 'button' ? buttons.filter((b) => b !== other || s.feedbackInForm) : []) };
  const composerEl = { offsetParent: {}, getClientRects: () => [1], closest: (sel) => (/form/.test(sel) ? form : null) };
  const composerSels = /prompt|chat-input|ProseMirror|textbox|textarea|contenteditable/;
  const form2 = { querySelectorAll: () => [button('Send message', () => clicked.push('form2'))] };
  const composerEl2 = { offsetParent: {}, getClientRects: () => [1], closest: (sel) => (/form/.test(sel) ? form2 : null) };
  const wb = { offsetParent: {}, getClientRects: () => [1], closest: (sel) => (/form|fieldset/.test(sel) ? null : {}) };   // a writing block
  const context = {
    document: { querySelectorAll: (sel) => (sel === 'button' ? [outside, ...buttons]
      : composerSels.test(sel) ? (s.writingBlockFirst && /ProseMirror/.test(sel) ? [wb]
        : s.noComposer || sel !== 'textarea' ? [] : s.twoForms ? [composerEl, composerEl2] : [composerEl])
      : polls ? [reply] : []) },
    location: { href: 'https://example.invalid/fixture' },
    Date: { now: () => elapsed },
    setTimeout: (cb) => { elapsed += 1500; polls += 1; cb(); },
  };
  const src = fs.readFileSync(path.join(EXTRACTED, 'chat-relay', '02_send_wait.js'), 'utf8')
    .replace('MODE_PLACEHOLDER', JSON.stringify('send')).replace('BASELINE_PLACEHOLDER', '0');
  try { return { ...(await vm.runInNewContext(src, context, { timeout: 2000 })), clicked }; }
  catch (e) { return { error: String(e.message || e), clicked }; }
}

async function scrape(messages) {
  const els = messages.map(([role, text, id]) => ({
    innerText: text, parentElement: { closest: () => null },
    getAttribute: (a) => (a === 'data-message-author-role' ? role : a === 'data-message-id' ? id || null : null),
  }));
  const context = {
    document: { body: { appendChild: () => {} }, title: 'Fixture', scrollingElement: { scrollTop: 0 },
                querySelector: () => els[0], querySelectorAll: () => els,
                createElement: () => ({ click: () => {}, remove: () => {} }) },
    location: { hostname: 'chatgpt.com', pathname: '/c/00000000-0000-0000-0000-000000000000', href: 'https://example.invalid' },
    window: {}, Date, Intl, Blob, URL: { createObjectURL: () => 'blob:x' },
    getComputedStyle: () => ({ overflowY: 'visible' }), setTimeout: (cb) => cb(),
  };
  const src = fs.readFileSync(path.join(EXTRACTED, 'chat-capture', '02_scrape_fallback.js'), 'utf8');
  await vm.runInNewContext(src, context, { timeout: 2000 });
  return context.window.__chatcap.data.messages;
}

// A tiny page: `els` maps a selector to the elements it returns. Each element knows its form
// (or none) and whether it sits inside a message, which is all the composer rules look at.
function mkForm(name, buttons = [button('Send message', () => {})]) {
  return { name, querySelectorAll: (sel) => (sel === 'button' ? buttons : []) };
}

function textarea(form, track) {
  let value = '';
  const proto = { set value(t) { value = t; track.push(['set', form && form.name]); }, get value() { return value; } };
  return Object.assign(Object.create(proto), { tagName: 'TEXTAREA', offsetParent: {}, getClientRects: () => [1], className: '',
    focus: () => track.push(['focus', form && form.name]), dispatchEvent: () => {}, parentElement: null,
    closest: (sel) => (/form/.test(sel) ? form : null) });
}

function writingBlock(track) {   // editable, visible, inside a message, not in any form (CH-011)
  return { tagName: 'DIV', offsetParent: {}, getClientRects: () => [1], className: 'ProseMirror content-xaiQhS', innerText: 'an earlier block',
    focus: () => track.push(['focus', 'writing-block']), dispatchEvent: () => track.push(['write', 'writing-block']), parentElement: null,
    closest: (sel) => (/form/.test(sel) ? null : /data-message-author-role|article/.test(sel) ? {} : null) };
}

async function fill(els = null) {
  const track = [];
  const form = mkForm('composer-form');
  const page = els ? els(track) : { '#prompt-textarea': [textarea(form, track)] };
  const context = {
    document: { querySelectorAll: (s) => (s === 'button' ? [button('Send feedback', () => {}), button('Send message', () => {})] : page[s] || []),
                execCommand: () => track.push(['execCommand']) },
    window: { getSelection: () => ({ selectAllChildren: () => {}, deleteFromDocument: () => {} }) },
    Event: class {}, DataTransfer: class { setData(_, v) { this.v = v; } },
    ClipboardEvent: class { constructor(_, o) { this.text = o && o.clipboardData && o.clipboardData.v; } }, setTimeout: (cb) => cb(),
  };
  const src = fs.readFileSync(path.join(EXTRACTED, 'chat-relay', '01_fill.js'), 'utf8')
    .replace('TEXT_PLACEHOLDER', JSON.stringify('A bounded test.\nSecond line.'));
  try { return { ...(await vm.runInNewContext(src, context, { timeout: 2000 })), track }; }
  catch (e) { return { error: String(e.message || e), track }; }
}

(async () => {
  const f = await fill();
  check('fill: textarea filled and the real Send button found', f.matches && f.send_enabled, f);
  // CH-011: a visible writing block comes first in the selector order; the composer is a textarea in the form.
  let w = await fill((t) => ({ 'div.ProseMirror[contenteditable="true"]': [writingBlock(t)], 'textarea': [textarea(mkForm('f1'), t)] }));
  check('fill: skips a writing block outside the form and fills the composer in it',
    w.matches && w.composer === 'textarea' && w.skipped_outside_form === 1 && !w.track.some(([, who]) => who === 'writing-block'), w);
  w = await fill((t) => ({ 'div.ProseMirror[contenteditable="true"]': [writingBlock(t)] }));
  check('fill: with only a writing block on the page it refuses, touching nothing',
    /not found inside an input form/.test(w.error || '') && w.track.length === 0, w);
  w = await fill((t) => ({ '#prompt-textarea': [textarea(mkForm('f1'), t)], 'textarea': [textarea(mkForm('f2'), t)] }));
  check('fill: two input forms with composers: it refuses to guess', /2 input forms/.test(w.error || '') && w.track.length === 0, w);
  // An edit box inside a form but inside a message (ChatGPT's edit-message box) isn't the composer.
  w = await fill((t) => {
    const edit = textarea(mkForm('edit-form'), t);
    edit.closest = (sel) => (/form/.test(sel) ? { name: 'edit-form', querySelectorAll: () => [] } : /article|data-turn/.test(sel) ? {} : null);
    return { '#prompt-textarea': [edit], 'textarea': [textarea(mkForm('f1'), t)] };
  });
  check('fill: an editable inside a form but inside a message is skipped',
    w.matches && w.composer === 'textarea' && !w.track.some(([, who]) => who === 'edit-form'), w);
  // Claude.ai-style: a contenteditable composer in a fieldset is still found and filled.
  w = await fill((t) => {
    const fs_ = mkForm('fieldset');
    const ce = { tagName: 'DIV', offsetParent: {}, getClientRects: () => [1], className: 'ProseMirror', innerText: '',
      focus: () => t.push(['focus', 'fieldset']), closest: (sel) => (/fieldset/.test(sel) ? fs_ : null),
      dispatchEvent(ev) { this.innerText = ev.text || ''; t.push(['paste', 'fieldset']); } };
    return { 'fieldset [contenteditable="true"]': [ce] };
  });
  check('fill: a composer in a fieldset (Claude.ai style) is found and filled', w.matches && w.composer === 'fieldset [contenteditable="true"]', w);
  // A Send button outside the composer's form doesn't count as the composer's Send.
  w = await fill((t) => ({ 'textarea': [textarea(mkForm('f1', []), t)] }));
  check('fill: no Send in the composer\'s form means send_button false, even with one elsewhere', w.matches && w.send_button === false, w);

  let r = await sendWait({ text: 'Finished response.', completeValue: '' });
  check('relay: explicit ChatGPT completion -> done', r.status === 'done', r);
  r = await sendWait({ text: 'Finished response.', streaming: 'false' });
  check('relay: Claude streaming="false" -> done', r.status === 'done', r);
  r = await sendWait({ text: 'I will now check the evidence.' });
  check('relay: text that stops changing without a signal is never "done"', r.status !== 'done', r);
  check('relay: ...it comes back "unconfirmed" with the text', r.status === 'unconfirmed' && r.reply, r);
  r = await sendWait({ text: 'Still streaming.', completeValue: 'false', streaming: 'true' });
  check('relay: data-message-complete="false" while streaming stays pending', r.status === 'pending', r);
  r = await sendWait({ text: 'Done but stop button visible.', completeValue: '', stopButton: true });
  check('relay: a visible stop button overrides a completion attribute', r.status !== 'done', r);
  r = await sendWait({ text: 'Unrelated.', completeValue: '', feedbackFirst: true });
  check('relay: clicks "Send message", never "Send feedback"', r.clicked.join() === 'message', r.clicked);
  r = await sendWait({ text: 'x', completeValue: '', noSendInForm: true });
  check('relay: never clicks a Send button outside the composer\'s form', r.error && r.clicked.length === 0, r);
  r = await sendWait({ text: 'Finished.', completeValue: '', writingBlockFirst: true });
  check('relay: a writing block ahead of the composer doesn\'t stop it finding the right Send', r.clicked.join() === 'message', r);
  r = await sendWait({ text: 'x', completeValue: '', twoForms: true });
  check('relay: two input forms with composers: it clicks nothing', /More than one input form/.test(r.error || '') && r.clicked.length === 0, r);
  r = await sendWait({ text: 'x', completeValue: '', noComposer: true });
  check('relay: with no composer in a form it clicks nothing', /Composer not found/.test(r.error || '') && r.clicked.length === 0, r);
  r = await sendWait({ text: 'Generating.', streaming: 'true' });
  check('relay: streaming reply stays pending', r.status === 'pending', r);

  // 2026-10 ChatGPT markup: assistant units keyed "...:assistant", completion on an ancestor's data-*turn-state.
  for (const [state, want] of [['complete', 'done'], ['streaming', 'pending'], ['in_progress', 'pending']]) {
    let elapsed = 0, polls = 0;
    const turn = { attributes: [{ name: 'data-talvt-turn-state', value: state }], parentElement: null };
    const md = { innerText: 'RELAY-OK 1' };
    const unit = { attributes: [], parentElement: { closest: () => null, attributes: [], parentElement: turn },
      innerText: 'ChatGPT said:\nRELAY-OK 1', querySelector: (s) => (/assistant-message/.test(s) ? md : null),
      hasAttribute: () => false, getAttribute: () => null, closest: () => null };
    const send = button('Send', () => {});
    const context = {
      document: { body: {}, querySelectorAll: (sel) => (sel === 'button' ? [send] : polls && /:assistant/.test(sel) ? [unit] : []) },
      location: { href: 'https://example.invalid/c/x' }, Date: { now: () => elapsed },
      setTimeout: (cb) => { elapsed += 1500; polls += 1; cb(); },
    };
    const src = fs.readFileSync(path.join(EXTRACTED, 'chat-relay', '02_send_wait.js'), 'utf8')
      .replace('MODE_PLACEHOLDER', JSON.stringify('wait')).replace('BASELINE_PLACEHOLDER', '0');
    const out = await vm.runInNewContext(src, context, { timeout: 2000 });
    check(`relay (2026-10 markup): turn-state "${state}" -> ${want}`, out.status === want && (want !== 'done' || out.reply === 'RELAY-OK 1'), out);
  }

  {
    const unitEl = (key, ids, text) => ({ innerText: (key.endsWith('user') ? 'You said:\n' : 'ChatGPT said:\n') + text,
      parentElement: { closest: () => null },
      getAttribute: (a) => (a === 'data-content-search-unit-key' ? key : a === 'data-chatgpt-search-message-ids' ? ids : null),
      querySelector: () => null });
    const els = [unitEl('t0:0:user', 'u1', 'Yes.'), unitEl('t0:2:assistant', 'a1 a1b', 'First.'),
                 unitEl('t1:0:user', 'u2', 'Yes.'), unitEl('t1:2:assistant', 'a2', 'Second.')];
    const context = {
      document: { body: { appendChild: () => {} }, title: 'Fixture', scrollingElement: { scrollTop: 0 },
                  querySelector: () => els[0], querySelectorAll: () => els, createElement: () => ({ click: () => {}, remove: () => {} }) },
      location: { hostname: 'chatgpt.com', pathname: '/c/00000000-0000-0000-0000-000000000000', href: 'x' },
      window: {}, Date, Intl, Blob, URL: { createObjectURL: () => 'blob:x' },
      getComputedStyle: () => ({ overflowY: 'visible' }), setTimeout: (cb) => cb(),
    };
    await vm.runInNewContext(fs.readFileSync(path.join(EXTRACTED, 'chat-capture', '02_scrape_fallback.js'), 'utf8'), context, { timeout: 2000 });
    const got = context.window.__chatcap.data.messages;
    check('scrape (2026-10 markup): roles, ids and text from unit keys',
      got.map((x) => x.role).join() === 'user,assistant,user,assistant' && got[1].id === 'a1' && got[1].text === 'First.' && got[0].text === 'Yes.', got);
  }

  const m = await scrape([['user', 'Yes.'], ['assistant', 'First.'], ['user', 'Yes.'], ['assistant', 'Second.']]);
  check('scrape: repeated "Yes." turns are both kept', m.length === 4, m.map((x) => x.role));
  const d = await scrape([['user', 'Hi', 'id1'], ['user', 'Hi', 'id1'], ['assistant', 'Hello', 'id2']]);
  check('scrape: the same message id twice is kept once', d.length === 2, d);

  {
    // cockpit: the page's own script, from a page built over the synthetic records in tests/test_cockpit.py
    const out = fs.mkdtempSync(path.join(os.tmpdir(), 'relaykit-cockpit-js-'));
    process.on('exit', () => fs.rmSync(out, { recursive: true, force: true }));
    require('node:child_process').execFileSync('python3', ['-I', '-B', '-c', [
      'import importlib.util, sys, pathlib',
      's = importlib.util.spec_from_file_location("tc", sys.argv[1]); t = importlib.util.module_from_spec(s); s.loader.exec_module(t)',
      'o = pathlib.Path(sys.argv[2]); t.ck.ex.repo_visibility = lambda r: "unknown"',
      't.ck.build(t.build_root(o), o / "site")'].join('\n'), path.join(KIT, 'tests', 'test_cockpit.py'), out], { stdio: ['ignore', 'ignore', 'inherit'] });
    const page = fs.readFileSync(path.join(out, 'site', 'index.html'), 'utf8');
    const unesc = (s) => s.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    const el = (attrs = {}) => {
      const ls = {}, cls = new Set();
      return { attrs, value: '', textContent: '', hidden: false, returnValue: '', ls,
        classList: { add: (...c) => c.forEach((x) => cls.add(x)), remove: (...c) => c.forEach((x) => cls.delete(x)),
                     toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)), contains: (c) => cls.has(c) },
        getAttribute: (a) => (a in attrs ? attrs[a] : null), setAttribute: (a, v) => { attrs[a] = v; },
        addEventListener: (ev, f) => { (ls[ev] = ls[ev] || []).push(f); },
        fire(ev) { (ls[ev] || []).forEach((f) => f({ preventDefault() {} })); },
        focus() {}, select() {}, scrollIntoView() {} };
    };
    const ids = {}, byId = (id) => (ids[id] = ids[id] || el({ id }));
    const dlg = byId('settle');
    dlg.showModal = () => { dlg.open = true; };
    const close = (v) => { dlg.open = false; dlg.returnValue = v; dlg.fire('close'); };
    // a card holding the owner's words (DEC-113 part 3); without them a settling line is refused
    const words = el({}), note = el({}), decider = el({}), locator = el({});
    words.value = 'Owner words, quoted.'; decider.value = 'sam'; locator.value = 'synthetic chat';
    const card = el();
    card.querySelector = (s) => ({ '.in-words': words, '.words-note': note, '.in-decider': decider, '.in-locator': locator }[s] || null);
    const attr = (rest, name) => { const m = rest.match(new RegExp(name + '="([^"]*)"')); return m ? unesc(m[1]) : undefined; };
    const buttons = [...page.matchAll(/<button type="button" class="act (\w+)" data-key="([^"]*)" data-act="\w+" data-line="([^"]*)"([^>]*)>/g)]
      .map(([, act, key, line, rest]) => {
        const a = { 'data-act': act, 'data-key': unesc(key), 'data-line': unesc(line) };
        if (attr(rest, 'data-template')) { a['data-template'] = attr(rest, 'data-template'); a['data-kind'] = attr(rest, 'data-kind'); }
        return Object.assign(el(a), { closest: () => card });
      });
    const nav = el({ 'data-goto': 'overview', 'data-name': 'Overview' });
    const document = { documentElement: el(), getElementById: (id) => (id === 'pf-go' ? null : byId(id)), querySelector: () => null,
      querySelectorAll: (sel) => (sel === '.nav-link' ? [nav] : sel === 'button[data-line]' ? buttons : []), execCommand: () => true };
    const context = { document, location: { hash: '' }, history: { replaceState() {} }, navigator: {}, setTimeout: () => {} };
    context.window = Object.assign(context, { scrollTo() {}, confirm: () => false });
    const m2 = page.match(/<script>([\s\S]*)<\/script>/);
    let err = null;
    try { vm.runInNewContext(m2[1], context, { timeout: 2000 }); } catch (e) { err = String(e.message || e); }
    const box = byId('apply');
    const btn = (key, act) => buttons.find((b) => b.attrs['data-key'] === key && b.attrs['data-act'] === act);
    const choose = (key, act) => { btn(key, act).fire('click'); if (act !== 'defer') close('ok'); };
    // the line a choice composes, as the page shows it in the dialog (with the owner's words filled in)
    const lineOf = (key, act) => {
      if (act === 'defer') return btn(key, act).attrs['data-line'];
      btn(key, act).fire('click'); const l = byId('settle-line').textContent; close('cancel'); return l;
    };
    const [A, B] = ['decision:DEC-002', 'challenge:CH-001'];
    check('cockpit page script: runs against the built page', !err && buttons.length === 9 && btn(A, 'accept') && btn(B, 'accept'), { err, n: buttons.length });
    if (!err && btn(A, 'accept') && btn(B, 'accept')) {
      choose(A, 'accept');
      choose(A, 'reject');   // unedited: a new pick for the same item replaces its line
      check('cockpit To apply: an unedited box is rebuilt from the picks', box.value === lineOf(A, 'reject') + '\n', box.value);
      box.value = box.value.replace('"decider":"sam"', '"decider":"OWNER-EDIT"');
      box.fire('input');
      const edited = box.value;
      choose(B, 'accept');
      check('cockpit To apply: selecting another item keeps the owner\'s edits',
        box.value.startsWith(edited.trimEnd()) && box.value.includes(lineOf(B, 'accept')) && box.value.includes('OWNER-EDIT'), box.value);
      choose(B, 'defer');   // its line was not edited: replaced in place
      check('cockpit To apply: an unedited line in an edited box is replaced in place',
        box.value.includes('OWNER-EDIT') && !box.value.includes(lineOf(B, 'accept')) && box.value.includes(lineOf(B, 'defer')), box.value);
      choose(A, 'defer');   // its line was edited: kept, and the new one added
      check('cockpit To apply: an edited line is never overwritten by a later pick',
        box.value.includes('OWNER-EDIT') && box.value.includes(lineOf(A, 'defer')), box.value);
      const before = box.value;
      byId('clear').fire('click');
      const asked = dlg.open && box.value === before;
      close('cancel');
      const kept = box.value === before;
      byId('clear').fire('click');
      close('ok');
      check('cockpit To apply: Clear asks before discarding edits', asked && kept && box.value === '', { asked, kept, after: box.value });
      // your words go into the line; without them nothing is composed
      const filledLine = lineOf(A, 'accept');
      const obj = JSON.parse(filledLine.slice(filledLine.indexOf("'") + 1, filledLine.lastIndexOf("'")).replace(/'"'"'/g, "'"));
      check('cockpit words: the basis in a composed line is the owner\'s words, never a default', obj.basis === 'Owner words, quoted.' && obj.supersedes === 'DEC-002', obj);
      words.value = '  ';
      dlg.open = false; btn(A, 'accept').fire('click');
      check('cockpit words: settling without your words composes nothing and says why', dlg.open !== true && /Write your words first/.test(note.textContent), { open: dlg.open, note: note.textContent });
      words.value = 'Owner words, quoted.';
      check('cockpit words: decider and locator are filled from their inputs', obj.decider === 'sam' && obj.locator === 'synthetic chat', obj);
      locator.value = ''; dlg.open = false; note.textContent = ''; btn(A, 'accept').fire('click');
      check('cockpit words: a cleared locator composes nothing and says so', dlg.open !== true && /locator/.test(note.textContent), note.textContent);
      locator.value = 'synthetic chat';
      check('cockpit dialog: named and described by its title and body',
        /<dialog id="settle" aria-labelledby="settle-title" aria-describedby="settle-body">/.test(page));
    }
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exitCode = failed ? 1 : 0;
})().catch((e) => { console.error(e.stack); process.exitCode = 1; });
