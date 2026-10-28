// chat-relay send + wait. MODE 'send' clicks Send, then waits; MODE 'wait' only waits (use it when a
// previous call returned 'pending' or 'unconfirmed', passing that baseline). Stays under the browser
// tool's ~45 s limit.
// Returns 'done' only on an explicit completion signal from the page:
//   ChatGPT (2026-10 markup): the turn container's data-*turn-state attribute is "complete"
//     (seen as data-talvt-turn-state; the prefix looks build-generated, so any *turn-state matches);
//   ChatGPT (older markup): data-message-complete present and not "false";
//   Claude: data-is-streaming="false".
// Text that merely stops changing returns 'unconfirmed', never 'done': a model can pause mid-turn
// (tool calls, "I'll check that now...") and look finished. Treat 'unconfirmed' as pending.
(async (MODE, BASELINE) => {
  const vis = (e) => e && e.offsetParent !== null && e.getClientRects().length > 0;
  const SEND_LABEL = /^send( message| prompt)?$/i;
  const isSend = (b) => vis(b) && (SEND_LABEL.test((b.getAttribute('aria-label') || '').trim())
    || /^send-button$/.test(b.getAttribute('data-testid') || ''));
  const composerSel = ['#prompt-textarea', 'textarea[name="prompt"]', '[data-testid="chat-input"]',
    'div.ProseMirror[contenteditable="true"]', '[contenteditable="true"][role="textbox"]'];
  const composer = () => { for (const s of composerSel) { const e = [...document.querySelectorAll(s)].find(vis); if (e) return e; } return null; };
  const sendButton = () => {
    // Prefer a Send button near the composer, so "Send feedback" and similar elsewhere are never clicked.
    let el = composer();
    for (let i = 0; el && i < 8; i++, el = el.parentElement) {
      if (!el.querySelectorAll) continue;
      const b = [...el.querySelectorAll('button')].find(isSend);
      if (b) return b;
    }
    return [...document.querySelectorAll('button')].find(isSend) || null;
  };
  const replySel = [
    '[data-content-search-unit-key$=":assistant"]',                                 // ChatGPT, 2026-10 markup
    '[data-message-author-role="assistant"]', '[data-message-role="assistant"]',   // ChatGPT, older layouts
    '.font-claude-response', '[data-is-streaming]',                                 // Claude
  ].join(', ');
  const replies = () => [...document.querySelectorAll(replySel)].filter(el => !el.parentElement.closest(replySel));
  const turnState = (el) => {   // nearest ancestor carrying a data-*turn-state attribute
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const a = e.attributes && [...e.attributes].find(x => /^data-[\w-]*turn-state$/.test(x.name));
      if (a) return a.value;
    }
    return null;
  };
  const replyText = (el) => {
    const md = el.querySelector && el.querySelector('[data-markdown-text-style="assistant-message"]');
    return (md || el).innerText;
  };
  const stopBtn = () => [...document.querySelectorAll('button')].find(b => vis(b) &&
    (/^stop/i.test(b.getAttribute('aria-label') || '') || /stop-button/.test(b.getAttribute('data-testid') || '')));
  const clean = (t) => t.replace(/^(ChatGPT|Claude) said:\s*/, '').trim();
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const streamingAttr = (el) => el.getAttribute('data-is-streaming')
    ?? (el.closest('[data-is-streaming]') || { getAttribute: () => null }).getAttribute('data-is-streaming');
  const state = (el) => {
    const s = streamingAttr(el);
    if (s === 'true' || stopBtn()) return 'streaming';
    const ts = turnState(el);
    if (ts !== null) return ts === 'complete' ? 'complete' : 'streaming';   // ChatGPT: only "complete" counts
    if (el.hasAttribute('data-message-complete') && el.getAttribute('data-message-complete') !== 'false')
      return 'complete';                                     // ChatGPT: present and not "false"
    if (s === 'false') return 'complete';                    // Claude: streaming explicitly ended
    return 'unknown';
  };
  let base = BASELINE;
  if (MODE === 'send') {
    base = replies().length;
    const send = sendButton();
    if (!send || send.disabled) throw new Error('Send button missing or disabled: fill the composer first, or press Enter with the computer tool.');
    send.click();
  }
  const t0 = Date.now(); let last = null, same = 0;
  while (Date.now() - t0 < 35000) {
    await sleep(1500);
    const r = replies();
    if (r.length <= base) continue;
    const el = r[r.length - 1];
    const txt = clean(replyText(el));
    const st = state(el);
    if (st === 'complete' && txt)
      return { status: 'done', signal: 'explicit', baseline: base, replies: r.length, url: location.href, chars: txt.length, reply: txt };
    same = (txt && txt === last && st === 'unknown') ? same + 1 : 0; last = txt;
  }
  if (same >= 3)
    return { status: 'unconfirmed', baseline: base, url: location.href, chars: (last || '').length, reply: last,
             note: 'Text stopped changing but the page gave no completion signal. Check the page (stop button, tool activity) or run again with MODE "wait" before relaying this.' };
  return { status: 'pending', baseline: base, url: location.href, partial_chars: (last || '').length,
           note: 'Still generating: run again with MODE "wait" and this baseline.' };
})(MODE_PLACEHOLDER, BASELINE_PLACEHOLDER)
