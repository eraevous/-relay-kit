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
  const buttons = s.feedbackFirst ? [other, send] : [send];
  if (s.stopButton) buttons.push(button('Stop streaming', () => {}));
  const context = {
    document: { querySelectorAll: (sel) => (sel === 'button' ? buttons : polls && !/prompt|chat-input|ProseMirror|textbox/.test(sel) ? [reply] : []) },
    location: { href: 'https://example.invalid/fixture' },
    Date: { now: () => elapsed },
    setTimeout: (cb) => { elapsed += 1500; polls += 1; cb(); },
  };
  const src = fs.readFileSync(path.join(KIT, 'extracted', 'chat-relay', '02_send_wait.js'), 'utf8')
    .replace('MODE_PLACEHOLDER', JSON.stringify('send')).replace('BASELINE_PLACEHOLDER', '0');
  const out = await vm.runInNewContext(src, context, { timeout: 2000 });
  return { ...out, clicked };
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

async function fill() {
  let value = '';
  const proto = { set value(t) { value = t; }, get value() { return value; } };
  const box = Object.assign(Object.create(proto), { tagName: 'TEXTAREA', offsetParent: {}, getClientRects: () => [1],
    focus: () => {}, dispatchEvent: () => {}, parentElement: null });
  const context = {
    document: { querySelectorAll: (s) => (s === 'button' ? [button('Send feedback', () => {}), button('Send message', () => {})] : s === '#prompt-textarea' ? [box] : []) },
    Event: class {}, setTimeout: (cb) => cb(),
  };
  const src = fs.readFileSync(path.join(KIT, 'extracted', 'chat-relay', '01_fill.js'), 'utf8')
    .replace('TEXT_PLACEHOLDER', JSON.stringify('A bounded test.\nSecond line.'));
  return vm.runInNewContext(src, context, { timeout: 2000 });
}

(async () => {
  const f = await fill();
  check('fill: textarea filled and the real Send button found', f.matches && f.send_enabled, f);

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
