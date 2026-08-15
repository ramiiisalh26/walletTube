/**
 * Service worker — proxies all API calls to localhost:8080.
 *
 * API_STATUS  → GET status, reply via sendResponse
 * API_STREAM  → SSE stream relayed via chrome.tabs.sendMessage
 * API_POST    → POST request, reply via sendResponse
 */

const API = 'http://localhost:8080';

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  if (msg.type === 'API_STATUS') {
    const controller = new AbortController();
    const timeout    = setTimeout(() => controller.abort(), 9000);
    const url = `${API}/api/video/${msg.yt_id}/status` +
      (msg.channel_id ? `?channel_id=${encodeURIComponent(msg.channel_id)}` : '');
    fetch(url, { signal: controller.signal })
      .then(r  => r.json())
      .then(data => { clearTimeout(timeout); sendResponse({ ok: true, data }); })
      .catch(e  => { clearTimeout(timeout); sendResponse({ ok: false, error: e.name === 'AbortError' ? 'Server not reachable' : e.message }); });
    return true;
  }

  if (msg.type === 'API_POST') {
    fetch(`${API}${msg.url}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then(r  => r.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(e  => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (msg.type === 'API_STREAM') {
    fetchStream(msg.url, msg.stream_id, sender.tab.id);
  }
});

async function fetchStream(url, stream_id, tabId) {
  function send(event) {
    chrome.tabs.sendMessage(tabId, { type: 'STREAM_EVENT', stream_id, event });
  }
  try {
    const res = await fetch(`${API}${url}`);
    if (!res.ok) { send({ type: 'error', message: `HTTP ${res.status}` }); return; }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let   buf     = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try { send(JSON.parse(line.slice(6))); } catch {}
        }
      }
    }
    if (buf.startsWith('data: ')) {
      try { send(JSON.parse(buf.slice(6))); } catch {}
    }
  } catch (e) {
    send({ type: 'error', message: e.message });
  }
}
