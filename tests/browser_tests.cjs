// Regression tests for the relay kit's browser scripts, run against mocked DOMs (no browser, no
// network). Adapted from the 2026-10-28 ChatGPT review's probes. Run from the kit root:
//   node tests/browser_tests.cjs
// These check the scripts' logic only; they say nothing about current chatgpt.com / claude.ai markup.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const KIT = path.join(__dirname, '..');
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
  const src = fs.readFileSync(path.join(KIT, 'extracted', 'chat-relay', '02_send_wait.js'), 'utf8')
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
  const src = fs.readFileSync(path.join(KIT, 'extracted', 'chat-capture', '02_scrape_fallback.js'), 'utf8');
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
  const src = fs.readFileSync(path.join(KIT, 'extracted', 'chat-relay', '01_fill.js'), 'utf8')
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
    const src = fs.readFileSync(path.join(KIT, 'extracted', 'chat-relay', '02_send_wait.js'), 'utf8')
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
    await vm.runInNewContext(fs.readFileSync(path.join(KIT, 'extracted', 'chat-capture', '02_scrape_fallback.js'), 'utf8'), context, { timeout: 2000 });
    const got = context.window.__chatcap.data.messages;
    check('scrape (2026-10 markup): roles, ids and text from unit keys',
      got.map((x) => x.role).join() === 'user,assistant,user,assistant' && got[1].id === 'a1' && got[1].text === 'First.' && got[0].text === 'Yes.', got);
  }

  const m = await scrape([['user', 'Yes.'], ['assistant', 'First.'], ['user', 'Yes.'], ['assistant', 'Second.']]);
  check('scrape: repeated "Yes." turns are both kept', m.length === 4, m.map((x) => x.role));
  const d = await scrape([['user', 'Hi', 'id1'], ['user', 'Hi', 'id1'], ['assistant', 'Hello', 'id2']]);
  check('scrape: the same message id twice is kept once', d.length === 2, d);

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exitCode = failed ? 1 : 0;
})().catch((e) => { console.error(e.stack); process.exitCode = 1; });
