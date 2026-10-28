// chat-capture fallback: only when capture.js fails. Scrolls the thread to the top until no
// new turns load, then scrapes roles + text from the DOM. Selectors are UI-dependent: verify.
(async () => {
  const isGPT = /chatgpt\.com|chat\.openai\.com/.test(location.hostname);
  const sel = isGPT ? '[data-content-search-unit-key], [data-message-author-role]'   // 2026-10 markup, older markup
                    : '[data-testid="user-message"], .font-claude-response, [data-is-streaming]';
  const scroller = (el) => { while (el && el !== document.body) { const s = getComputedStyle(el);
    if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) return el; el = el.parentElement; }
    return document.scrollingElement; };
  const first = document.querySelector(sel);
  if (!first) throw new Error('No messages found with selector: ' + sel);
  const box = scroller(first);
  let prev = -1, stable = 0;
  for (let i = 0; i < 200 && stable < 4; i++) {
    box.scrollTop = 0; await new Promise(r => setTimeout(r, 700));
    const n = document.querySelectorAll(sel).length;
    stable = n === prev ? stable + 1 : 0; prev = n;
  }
  // Keep every message element, including repeated text ("Yes." twice is two turns).
  // Drop only elements nested inside another match and repeats of the same message id.
  const seenIds = new Set();
  const messages = [...document.querySelectorAll(sel)].filter(el => !el.parentElement.closest(sel)).map(el => {
    const unitKey = el.getAttribute('data-content-search-unit-key');           // e.g. "turn-0:2:assistant"
    const role = isGPT ? (el.getAttribute('data-message-author-role') || (unitKey || '').split(':').pop())
                       : (el.matches('[data-testid="user-message"]') ? 'user' : 'assistant');
    const id = el.getAttribute('data-message-id')
      || (el.getAttribute('data-chatgpt-search-message-ids') || '').split(/\s+/)[0] || null;
    const body = isGPT && el.querySelector
      && el.querySelector('[data-markdown-text-style="assistant-message"], [data-user-message-bubble] [dir=auto]');
    const text = (body || el).innerText.replace(/^\s*(You|ChatGPT) said:\s*/, '').trim();
    return { id, role, text };
  }).filter(m => (m.role === 'user' || m.role === 'assistant') && m.text
    && !(m.id && seenIds.has(m.id)) && (m.id ? seenIds.add(m.id) : true));
  const UUID = /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/;
  const id = (location.pathname.match(UUID) || [])[1] || 'unknown';
  const wrapped = { _capture: { tool: 'chat-capture', version: '0.1.0', platform: isGPT ? 'chatgpt' : 'claude',
      kind: 'dom', id, source_url: location.href, endpoint: 'DOM scrape (fallback)',
      retrieved_at: new Date().toISOString(), browser_tz: Intl.DateTimeFormat().resolvedOptions().timeZone },
    data: { title: document.title.replace(/\s*[-|]\s*(ChatGPT|Claude)\s*$/, ''), messages } };
  window.__chatcap = wrapped;
  const text = JSON.stringify(wrapped);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  a.download = `${wrapped._capture.platform}-dom-${id.slice(0, 8)}.raw.json`; document.body.appendChild(a); a.click(); a.remove();
  return { saved_as: a.download, messages: messages.length, first_role: messages[0] && messages[0].role };
})()
