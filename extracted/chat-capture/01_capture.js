// chat-capture: run in the page of the conversation (chatgpt.com or claude.ai).
// Fetches the full conversation JSON from the site's own API (no scrolling), wraps it
// with capture metadata, saves it as a download, and returns a short summary.
(async () => {
  const href = location.href, host = location.hostname;
  const UUID = '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})';
  const m = (re) => (href.match(new RegExp(re)) || [])[1];
  const get = async (url, opts) => {
    const r = await fetch(url, Object.assign({ credentials: 'include' }, opts || {}));
    if (!r.ok) throw new Error(`${r.status} on ${url.split('?')[0]}`);
    return r.json();
  };
  let platform, kind, id, raw, endpoint;
  if (/chatgpt\.com|chat\.openai\.com/.test(host)) {
    platform = 'chatgpt';
    if ((id = m('/share/' + UUID))) {
      kind = 'share'; endpoint = '/backend-api/share/' + id;
      raw = await get(endpoint);
    } else if ((id = m('/c/' + UUID))) {
      kind = 'conversation'; endpoint = '/backend-api/conversation/' + id;
      const s = await get('/api/auth/session').catch(() => ({}));
      if (!s.accessToken) throw new Error('Not signed in to ChatGPT in this browser. Sign in, reload, rerun.');
      raw = await get(endpoint, { headers: { Authorization: 'Bearer ' + s.accessToken } });
    }
  } else if (/claude\.ai/.test(host)) {
    platform = 'claude';
    const q = '?tree=True&rendering_mode=messages&render_all_tools=true';
    if ((id = m('/share/' + UUID))) {
      kind = 'share'; endpoint = '/api/chat_snapshots/' + id + q;
      raw = await get(endpoint);
    } else if ((id = m('/chat/' + UUID))) {
      kind = 'conversation';
      const orgs = await get('/api/organizations').catch(() => []);
      if (!orgs.length) throw new Error('Not signed in to Claude in this browser. Sign in, reload, rerun.');
      for (const o of orgs) {
        try { endpoint = `/api/organizations/${o.uuid}/chat_conversations/${id}${q}`; raw = await get(endpoint); break; }
        catch (e) { raw = null; }
      }
      if (!raw) throw new Error('Conversation not found in any of your Claude organizations.');
    }
  }
  if (!raw) throw new Error('Unrecognized URL. Expected chatgpt.com/share|c/<id> or claude.ai/share|chat/<id>.');
  const wrapped = {
    _capture: {
      tool: 'chat-capture', version: '0.1.0', platform, kind, id,
      source_url: href, endpoint: endpoint.split('?')[0],
      retrieved_at: new Date().toISOString(),
      browser_tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    },
    data: raw,
  };
  const text = JSON.stringify(wrapped);
  const stamp = wrapped._capture.retrieved_at.replace(/[:.]/g, '-').slice(0, 19);
  const fname = `${platform}-${kind}-${id.slice(0, 8)}-${stamp}.raw.json`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  a.download = fname; document.body.appendChild(a); a.click(); a.remove();
  window.__chatcap = wrapped;   // kept in page for chunked reads if the download is blocked
  const nodes = platform === 'chatgpt' ? Object.keys(raw.mapping || {}).length : (raw.chat_messages || []).length;
  return { saved_as: fname, bytes: text.length, platform, kind, id,
           title: raw.title || raw.name || raw.snapshot_name || null, nodes };
})()
