// chat-relay fill: put TEXT into the chat composer without sending. Returns what it found.
(async (TEXT) => {
  const vis = (e) => e && e.offsetParent !== null && e.getClientRects().length > 0;
  const cands = [
    '#prompt-textarea', 'textarea[name="prompt"]', 'textarea#mobile-composer-prompt',
    '[data-testid="chat-input"]', 'div.ProseMirror[contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]', 'fieldset [contenteditable="true"]', 'textarea',
  ];
  let box = null, via = null;
  for (const s of cands) { const e = [...document.querySelectorAll(s)].find(vis); if (e) { box = e; via = s; break; } }
  if (!box) throw new Error('Composer not found. Use the find tool (e.g. "Ask", "Reply", "Message") and type instead.');
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
  let sendBtn = null;
  for (let el = box, i = 0; el && !sendBtn && i < 8; i++, el = el.parentElement)
    if (el.querySelectorAll) sendBtn = [...el.querySelectorAll('button')].find(isSend) || null;
  sendBtn = sendBtn || [...document.querySelectorAll('button')].find(isSend) || null;
  return { composer: via, method, expected_chars: TEXT.length, got_chars: got.length,
           matches: norm(got) === norm(TEXT), send_button: !!sendBtn, send_enabled: !!sendBtn && !sendBtn.disabled };
})(TEXT_PLACEHOLDER)
