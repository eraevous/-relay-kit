// chat-relay fill: put TEXT into the chat composer without sending. Returns what it found.
(async (TEXT) => {
  const vis = (e) => e && e.offsetParent !== null && e.getClientRects().length > 0;
  const cands = [
    '#prompt-textarea', 'textarea[name="prompt"]', 'textarea#mobile-composer-prompt',
    '[data-testid="chat-input"]', 'div.ProseMirror[contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]', 'fieldset [contenteditable="true"]', 'textarea',
  ];
  // The composer sits in the input form. Editable areas elsewhere are not it: ChatGPT's writing
  // blocks are editable, autosaved ProseMirror editors inside messages, and pasting into one
  // changes an earlier message for good (CH-011, 2026-11-01). Skip them, and refuse to guess.
  const MSG = '[data-message-author-role], [data-content-search-unit-key], [data-turn], article, .font-claude-response';
  const area = (e) => (e.closest && e.closest('form, fieldset')) || null;
  const usable = (e) => vis(e) && area(e) && !e.closest(MSG);
  let box = null, via = null;
  const others = [], skippedSet = new Set();
  for (const s of cands) for (const e of document.querySelectorAll(s)) {
    if (!vis(e)) continue;
    if (!usable(e)) { skippedSet.add(e); continue; }
    if (!box) { box = e; via = s; } else if (area(e) !== area(box) && !others.includes(e)) others.push(e);
  }
  const skipped = skippedSet.size;
  if (!box) throw new Error(`Composer not found inside an input form (${skipped} editable area(s) elsewhere skipped, e.g. writing blocks). Use the find tool ("Ask", "Message") and check the target before typing.`);
  if (others.length) throw new Error(`${others.length + 1} input forms have a composer; not guessing which. Use the find tool and check the target.`);
  box.focus();
  let method;
  if (box.tagName === 'TEXTAREA' || box.tagName === 'INPUT') {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(box), 'value').set;
    setter.call(box, TEXT);
    box.dispatchEvent(new Event('input', { bubbles: true }));
    method = 'native-setter';
  } else {
    // rich editors (ProseMirror/Tiptap): a synthetic paste keeps line breaks; fall back to insertText
    const sel = window.getSelection(); sel.selectAllChildren(box); sel.deleteFromDocument();
    const dt = new DataTransfer(); dt.setData('text/plain', TEXT);
    box.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 300));
    method = 'paste-event';
    if ((box.innerText || '').trim().length < Math.min(TEXT.trim().length, 20)) {
      document.execCommand('insertText', false, TEXT); method = 'execCommand';
    }
  }
  await new Promise(r => setTimeout(r, 300));
  const got = box.tagName === 'TEXTAREA' || box.tagName === 'INPUT' ? box.value : box.innerText;
  const norm = (s) => s.replace(/\s+/g, ' ').trim();
  const isSend = (b) => vis(b) && (/^send( message| prompt)?$/i.test((b.getAttribute('aria-label') || '').trim())
    || /^send-button$/.test(b.getAttribute('data-testid') || ''));
  // Only the composer's own form counts: a Send button elsewhere is someone else's.
  const sendBtn = [...area(box).querySelectorAll('button')].find(isSend) || null;
  return { composer: via, skipped_outside_form: skipped, method, expected_chars: TEXT.length, got_chars: got.length,
           matches: norm(got) === norm(TEXT), send_button: !!sendBtn, send_enabled: !!sendBtn && !sendBtn.disabled };
})(TEXT_PLACEHOLDER)
