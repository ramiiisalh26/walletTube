'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let currentVideoId   = null;
let panelOpen        = false;
let streamId         = 0;
let busy             = false;
let cardCount        = 0;
let lastSection      = null;
let indexing         = false;
let postIndexTimer   = null;
let channelQueued    = false;   // true once we've fired the background channel queue

// ── AI answer state ───────────────────────────────────────────────────────────
let aiMode        = false;  // user opt-in; generation costs money, so off by default
let sessionId     = null;   // stable per-install id — backs the server-side daily cap
let answerEl      = null;   // the answer card for the in-flight search
let answerBuf     = '';     // raw streamed text, before citations are linkified
let answerStarted = false;  // first token has landed → skeleton removed

chrome.storage.local.get(['yts_ai_mode', 'yts_session_id'], store => {
  aiMode = store.yts_ai_mode === true;
  sessionId = store.yts_session_id || crypto.randomUUID();
  if (!store.yts_session_id) chrome.storage.local.set({ yts_session_id: sessionId });
  syncAiToggle();
});

// ── DOM helpers ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function getVideoId()    { return new URLSearchParams(location.search).get('v'); }
function getPageTitle()  { return document.title.replace(' - YouTube', '').trim(); }

function getChannelName() {
  return (
    document.querySelector('#channel-name a')?.textContent?.trim() ||
    document.querySelector('ytd-channel-name a')?.textContent?.trim() ||
    null
  );
}

function getChannelHandle() {
  const link =
    document.querySelector('ytd-channel-name a') ||
    document.querySelector('#channel-name a');
  if (!link) return null;
  const href = link.href || '';
  const mHandle = href.match(/youtube\.com\/(@[\w.-]+)/);
  if (mHandle) return mHandle[1];
  const mUC = href.match(/youtube\.com\/channel\/(UC[\w-]{20,})/);
  if (mUC) return mUC[1];
  return null;
}

// ── Panel build ───────────────────────────────────────────────────────────────
function buildPanel() {
  if ($('yts-panel')) return;

  const toggle = document.createElement('button');
  toggle.id = 'yts-toggle';
  toggle.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>
    <span>Search</span>`;
  toggle.onclick = () => setPanelOpen(!panelOpen);
  document.body.appendChild(toggle);

  const panel = document.createElement('div');
  panel.id = 'yts-panel';
  panel.innerHTML = `
    <div class="yts-header">
      <div class="yts-logo">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
      </div>
      <span class="yts-title">YTSearch</span>
      <button class="yts-close" id="yts-close" title="Close">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>

    <div class="yts-status-bar" id="yts-status-bar">
      <div class="yts-dot grey" id="yts-dot"></div>
      <span id="yts-status-text">Loading…</span>
    </div>

    <div class="yts-channel-banner" id="yts-channel-banner" style="display:none">
      <div class="yts-channel-icon" id="yts-channel-icon">▶</div>
      <div class="yts-channel-info">
        <div class="yts-channel-name" id="yts-channel-name"></div>
        <div class="yts-channel-sub" id="yts-channel-sub"></div>
      </div>
      <div class="yts-channel-badge" id="yts-channel-badge">NEW</div>
    </div>

    <div class="yts-search-wrap">
      <div class="yts-input-row">
        <input class="yts-input" id="yts-input"
          placeholder="Ask anything about this video…"
          autocomplete="off" spellcheck="false" />
        <button class="yts-ai-toggle" id="yts-ai-toggle"
                title="Synthesise an answer from the top results">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
               stroke-linejoin="round">
            <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>
            <path d="M18.5 16.5l.75 2 2 .75-2 .75-.75 2-.75-2-2-.75 2-.75z"/>
          </svg>
          <span>AI</span>
        </button>
        <button class="yts-send" id="yts-send" disabled title="Search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="22" y1="2" x2="11" y2="13"/>
            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
        </button>
      </div>
    </div>

    <div class="yts-results" id="yts-results"></div>
  `;
  document.body.appendChild(panel);

  $('yts-close').onclick = () => setPanelOpen(false);
  $('yts-send').onclick  = onSearch;
  $('yts-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); onSearch(); }
  });
  $('yts-input').addEventListener('input', () => {
    $('yts-send').disabled = !$('yts-input').value.trim();
  });
  $('yts-ai-toggle').onclick = () => {
    aiMode = !aiMode;
    chrome.storage.local.set({ yts_ai_mode: aiMode });
    syncAiToggle();
  };
  syncAiToggle();
}

function syncAiToggle() {
  const btn = $('yts-ai-toggle');
  if (!btn) return;
  btn.classList.toggle('on', aiMode);
  btn.title = aiMode
    ? 'AI answer on — synthesised from the top results, with citations'
    : 'AI answer off — results only';
}

function setPanelOpen(open) {
  panelOpen = open;
  $('yts-panel')?.classList.toggle('open', open);
  $('yts-toggle')?.classList.toggle('open', open);
}

// ── Video change ──────────────────────────────────────────────────────────────
function onVideoChanged(videoId) {
  currentVideoId = videoId;
  busy = false;
  indexing = false;
  channelQueued = false;
  streamId++;

  const inp = $('yts-input');
  if (inp) inp.value = '';
  setStatus('grey', 'Checking…');
  clearResults();
  setSendEnabled(false);
  hideChannelBanner();

  chrome.runtime.sendMessage(
    { type: 'API_STATUS', yt_id: videoId },
    response => {
      if (chrome.runtime.lastError || !response) {
        handleStatus({ status: 'error' });
        return;
      }
      handleStatus(response.ok ? response.data : { status: 'error' });
    }
  );
}

document.addEventListener('yt-navigate-finish', () => {
  const vid = getVideoId();
  if (vid && vid !== currentVideoId) onVideoChanged(vid);
});

const _initVid = getVideoId();
if (_initVid) {
  buildPanel();
  onVideoChanged(_initVid);
} else {
  document.addEventListener('yt-navigate-finish', () => {
    buildPanel();
    const vid = getVideoId();
    if (vid) onVideoChanged(vid);
  }, { once: true });
}

// ── Stream events from background ────────────────────────────────────────────
chrome.runtime.onMessage.addListener(msg => {
  if (msg.type === 'STREAM_EVENT') handleStreamEvent(msg.stream_id, msg.event);
});

// ── Status handling ───────────────────────────────────────────────────────────
function handleStatus(data) {
  if (data.status === 'error') {
    setStatus('red', 'Cannot reach YTSearch server');
    return;
  }

  // If this channel is new → queue it silently in background.
  // YouTube renders the channel link lazily — retry up to 10× every 500ms
  // so we catch it even if the DOM wasn't ready when the status API responded.
  if (data.channel_known === false && !channelQueued) {
    let attempts = 0;
    const tryQueue = () => {
      const handle = getChannelHandle();
      if (handle && !channelQueued) {
        channelQueued = true;
        queueChannelBackground(handle, getChannelName());
      } else if (!channelQueued && attempts++ < 10) {
        setTimeout(tryQueue, 500);
      }
    };
    tryQueue();
  }

  if (data.indexed) {
    const ind = data.industry ? ` · ${data.industry}` : '';
    setStatus('green', `Indexed · ${data.chunks} chunks${ind}`);
    setSendEnabled(true);
    if (data.channel_video_count > 1) {
      showChannelBanner(
        data.channel || getChannelName(),
        `${data.channel_video_count} videos indexed`,
        'known'
      );
    }
  } else {
    setStatus('yellow', 'Not indexed — indexing now…');
    startIndexing();
  }
}

// ── Background channel queue ──────────────────────────────────────────────────
function queueChannelBackground(handle, channelName) {
  showChannelBanner(channelName || handle, 'Queuing channel videos…', 'queuing');

  chrome.runtime.sendMessage(
    { type: 'API_POST', url: `/api/ingestion/channel-background?channel_id=${encodeURIComponent(handle)}` },
    response => {
      if (response?.ok) {
        showChannelBanner(
          channelName || handle,
          'Videos queued — will appear in search as they index',
          'queued'
        );
      }
    }
  );
}

// ── Channel banner ────────────────────────────────────────────────────────────
function showChannelBanner(name, sub, state) {
  const banner = $('yts-channel-banner');
  const nameEl = $('yts-channel-name');
  const subEl  = $('yts-channel-sub');
  const badge  = $('yts-channel-badge');
  const icon   = $('yts-channel-icon');
  if (!banner) return;

  nameEl.textContent = name || 'Channel';
  subEl.textContent  = sub  || '';

  banner.className = 'yts-channel-banner';
  if (state === 'queuing') {
    badge.textContent = 'NEW';
    badge.className   = 'yts-channel-badge yts-badge-new';
    icon.textContent  = '⏳';
  } else if (state === 'queued') {
    badge.textContent = 'QUEUED';
    badge.className   = 'yts-channel-badge yts-badge-queued';
    icon.textContent  = '✓';
  } else {
    badge.textContent = `${sub}`;
    badge.className   = 'yts-channel-badge yts-badge-known';
    icon.textContent  = '▶';
    subEl.textContent = '';
  }

  banner.style.display = 'flex';
}

function hideChannelBanner() {
  const b = $('yts-channel-banner');
  if (b) b.style.display = 'none';
}

// ── Indexing ──────────────────────────────────────────────────────────────────
function startIndexing() {
  if (indexing) return;
  indexing = true;
  const sid     = ++streamId;
  const title   = encodeURIComponent(getPageTitle());
  const channel = encodeURIComponent(getChannelName() || '');
  showIndexProgress();
  chrome.runtime.sendMessage({
    type: 'API_STREAM',
    url: `/api/video/${currentVideoId}/index/stream?title=${title}&channel=${channel}`,
    stream_id: sid,
  });
}

function showIndexProgress() {
  const r = $('yts-results');
  if (r) r.innerHTML = '<div id="yts-index-steps"></div>';
}

function addIndexStep(message, state = 'active') {
  const c = $('yts-index-steps');
  if (!c) return;
  const last = c.lastElementChild;
  if (last && state === 'active') last.className = 'yts-step yts-step-done';
  const step = document.createElement('div');
  step.className = `yts-step yts-step-${state}`;
  step.innerHTML = state === 'active'
    ? `<div class="yts-dots"><span></span><span></span><span></span></div><span>${esc(message)}</span>`
    : `<svg class="yts-step-icon" width="12" height="12" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="3" stroke-linecap="round">
         <polyline points="20 6 9 17 4 12"/>
       </svg><span>${esc(message)}</span>`;
  c.appendChild(step);
}

// ── Stream event dispatcher ───────────────────────────────────────────────────
function handleStreamEvent(sid, event) {
  if (sid !== streamId) return;

  if (event.type === 'already_indexed') {
    setStatus('green', `Indexed · ${event.chunks} chunks`);
    setSendEnabled(true);
    clearResults();
    return;
  }
  if (event.type === 'progress') {
    if (event.step) addIndexStep(event.message, 'active');
    setStatus('yellow', event.message);
    return;
  }
  if (event.type === 'done' && event.chunks !== undefined) {
    indexing = false;
    addIndexStep('Done!', 'done');
    setStatus('green', `Indexed · ${event.chunks} chunks`);
    setSendEnabled(true);
    postIndexTimer = setTimeout(() => { postIndexTimer = null; clearResults(); }, 1400);
    return;
  }
  if (event.type === 'error') {
    indexing = false;
    busy = false;
    const isWhisper = (event.message || '').includes('Whisper');
    setStatus(isWhisper ? 'yellow' : 'red', event.message || 'Error');
    addIndexStep((isWhisper ? '⏳ ' : '✗ ') + (event.message || 'Error'), 'done');
    return;
  }
  if (event.type === 'answer_token')   { pushAnswerToken(event.text); return; }
  if (event.type === 'answer_done')    { finishAnswer(event.answer, event.citations); return; }
  if (event.type === 'answer_skipped') { skipAnswer(event.reason); return; }

  if (event.type === 'meta') {
    if (event.answer_pending) {
      mountAnswerCard();
    } else if (aiMode) {
      // Toggle is on but the server can't generate — say so rather than
      // silently returning plain results.
      mountAnswerCard();
      skipAnswer('unavailable');
    }
    if (event.video_hits === 0) {
      const r = $('yts-results');
      if (r) {
        r.querySelector('.yts-loading')?.remove();
        const msg = document.createElement('div');
        msg.className = 'yts-no-match';
        msg.innerHTML = `
          <div class="yts-no-match-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
          </div>
          <div>
            <strong>Not found in this video</strong>
            <span>Showing results from other indexed videos</span>
          </div>`;
        r.appendChild(msg);
      }
    }
    busy = false;
    return;
  }
  if (event.type === 'result') { appendResultCard(event); return; }
  if (event.type === 'done' && event.chunks === undefined) { setSendEnabled(true); return; }
}

// ── Search ────────────────────────────────────────────────────────────────────
function onSearch() {
  const q = $('yts-input')?.value.trim();
  if (!q || busy || indexing || !currentVideoId) return;
  if (postIndexTimer) { clearTimeout(postIndexTimer); postIndexTimer = null; }
  busy = true;
  setSendEnabled(false);
  clearResults();
  const sid = ++streamId;
  setResultsLoading();
  let url = `/api/video/${currentVideoId}/search/stream?q=${encodeURIComponent(q)}`;
  if (aiMode) {
    url += `&generate_answer=true`;
    if (sessionId) url += `&session_id=${encodeURIComponent(sessionId)}`;
  }
  chrome.runtime.sendMessage({ type: 'API_STREAM', url, stream_id: sid });
}

function setResultsLoading() {
  const r = $('yts-results');
  if (!r) return;
  r.innerHTML = `
    <div class="yts-loading">
      <div class="yts-dots"><span></span><span></span><span></span></div>
      <span>Searching…</span>
    </div>`;
}

// ── AI answer card ────────────────────────────────────────────────────────────
const SKIP_COPY = {
  low_confidence: 'The results weren’t a strong enough match to answer from confidently — read the clips below instead.',
  limit_reached:  'You’ve used all of today’s AI answers. Results below are unaffected.',
  error:          'Couldn’t generate an answer just now. Results below are unaffected.',
  unavailable:    'AI answers aren’t enabled on the server.',
  disabled:       null,
};

function mountAnswerCard() {
  const r = $('yts-results');
  if (!r || answerEl) return;

  answerBuf = '';
  answerStarted = false;

  const card = document.createElement('div');
  card.className = 'yts-answer streaming';
  card.innerHTML = `
    <div class="yts-answer-head">
      <div class="yts-answer-spark">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>
        </svg>
      </div>
      <span class="yts-answer-label">Answer</span>
      <div class="yts-answer-tools"></div>
    </div>
    <div class="yts-answer-body">
      <div class="yts-answer-think">
        <div class="yts-dots"><span></span><span></span><span></span></div>
        <span>Reading the top clips…</span>
      </div>
      <div class="yts-skel"></div>
      <div class="yts-skel"></div>
      <div class="yts-skel"></div>
    </div>`;
  r.insertBefore(card, r.firstChild);
  answerEl = card;
}

function pushAnswerToken(text) {
  if (!answerEl) mountAnswerCard();
  if (!answerEl) return;
  const body = answerEl.querySelector('.yts-answer-body');

  if (!answerStarted) {
    answerStarted = true;
    body.innerHTML = '<span class="yts-txt"></span><span class="yts-cursor"></span>';
  }
  answerBuf += text;
  // textContent during the stream: cheap, and it keeps the raw [n] markers
  // visible until the server confirms which ones are real.
  body.querySelector('.yts-txt').textContent = answerBuf;
}

function finishAnswer(answer, citations) {
  if (!answerEl) return;
  answerEl.classList.remove('streaming');
  const body = answerEl.querySelector('.yts-answer-body');
  const cites = citations || [];

  // Re-render from the server's text — it has already stripped any citation
  // the model invented, so what renders can always be clicked through.
  body.innerHTML = linkifyCitations(answer || answerBuf, cites);

  if (cites.length) {
    const sources = document.createElement('div');
    sources.className = 'yts-answer-sources';
    sources.innerHTML = cites.map(c => `
      <div class="yts-src" data-video-id="${esc(c.video_id)}" data-start="${c.start_time}">
        <span class="yts-src-n">${c.marker}</span>
        <span class="yts-src-t" title="${esc(c.title)}">${esc(c.title)}</span>
        <span class="yts-src-time">${fmtTime(c.start_time)}</span>
      </div>`).join('');
    answerEl.appendChild(sources);
    sources.querySelectorAll('.yts-src').forEach(el => {
      el.onclick = () => gotoSource(el.dataset.videoId, parseFloat(el.dataset.start));
    });
  }

  body.querySelectorAll('.yts-cite').forEach(el => {
    el.onclick = () => gotoSource(el.dataset.videoId, parseFloat(el.dataset.start));
  });

  addCopyButton(answer || answerBuf);
}

function skipAnswer(reason) {
  const copy = SKIP_COPY[reason] ?? SKIP_COPY.error;
  // Nothing useful to say, and no partial text worth keeping → drop the card.
  if (!copy && !answerStarted) { removeAnswerCard(); return; }
  if (!answerEl) return;

  answerEl.classList.remove('streaming');
  if (answerStarted) {
    // Partial text already on screen — keep it, just stop the cursor.
    answerEl.querySelector('.yts-cursor')?.remove();
    return;
  }
  answerEl.querySelector('.yts-answer-body').innerHTML = `
    <div class="yts-answer-note">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
        <circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="13"/>
        <line x1="12" y1="16.5" x2="12" y2="16.6"/>
      </svg>
      <span>${esc(copy)}</span>
    </div>`;
}

function removeAnswerCard() {
  answerEl?.remove();
  answerEl = null;
}

/** Escape the answer, then turn each [n] into a clickable chip. */
function linkifyCitations(textIn, cites) {
  const byMarker = new Map(cites.map(c => [c.marker, c]));
  return esc(textIn).replace(/\[(\d{1,2})\]/g, (whole, n) => {
    const c = byMarker.get(parseInt(n, 10));
    if (!c) return whole;
    return `<span class="yts-cite" data-video-id="${esc(c.video_id)}"` +
           ` data-start="${c.start_time}" title="${esc(c.title)} · ${fmtTime(c.start_time)}">${n}</span>`;
  });
}

function addCopyButton(text) {
  const tools = answerEl?.querySelector('.yts-answer-tools');
  if (!tools || tools.querySelector('.yts-answer-copy')) return;
  const btn = document.createElement('button');
  btn.className = 'yts-answer-copy';
  btn.title = 'Copy answer';
  btn.innerHTML = `
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
         stroke-linejoin="round">
      <rect x="9" y="9" width="11" height="11" rx="2"/>
      <path d="M5 15V5a2 2 0 0 1 2-2h10"/>
    </svg>`;
  btn.onclick = () => {
    navigator.clipboard.writeText(text).then(() => {
      btn.classList.add('done');
      btn.innerHTML = `
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="3" stroke-linecap="round">
          <polyline points="20 6 9 17 4 12"/>
        </svg>`;
      setTimeout(() => {
        btn.classList.remove('done');
        addCopyButtonIcon(btn);
      }, 1600);
    }).catch(() => {});
  };
  tools.appendChild(btn);
}

function addCopyButtonIcon(btn) {
  btn.innerHTML = `
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
         stroke-linejoin="round">
      <rect x="9" y="9" width="11" height="11" rx="2"/>
      <path d="M5 15V5a2 2 0 0 1 2-2h10"/>
    </svg>`;
}

/** Citation click → scroll to the matching card, flash it, and play from there. */
function gotoSource(videoId, start) {
  const card = $('yts-results')?.querySelector(
    `.yts-card[data-video-id="${CSS.escape(videoId)}"]`
  );
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.remove('yts-flash');
    void card.offsetWidth;               // restart the animation
    card.classList.add('yts-flash');
  }
  if (videoId === currentVideoId) {
    seekVideo(start);
  } else {
    window.open(`https://www.youtube.com/watch?v=${videoId}&t=${Math.floor(start)}`, '_blank');
  }
}

// ── Result card ───────────────────────────────────────────────────────────────
function clearResults() {
  const r = $('yts-results');
  if (r) r.innerHTML = '';
  cardCount = 0; lastSection = null;
  answerEl = null; answerBuf = ''; answerStarted = false;
}

function appendResultCard(r) {
  const container = $('yts-results');
  if (!container) return;
  if (cardCount === 0) container.querySelector('.yts-loading')?.remove();

  const section = r.source === 'video' ? 'this-video' : 'other-videos';
  if (section !== lastSection) {
    const lbl = document.createElement('div');
    lbl.className = 'yts-section' + (section === 'other-videos' ? ' yts-section-other' : '');
    lbl.innerHTML = section === 'other-videos'
      ? `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              stroke-width="2.5" stroke-linecap="round">
           <line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/>
         </svg> From other indexed videos`
      : 'This video';
    container.appendChild(lbl);
    lastSection = section;
  }

  cardCount++;
  const pct    = Math.round(r.similarity * 100);
  const simCls = r.similarity >= .74 ? 'hi' : r.similarity >= .60 ? 'mid' : 'lo';
  const ts     = fmtTime(r.start_time);
  const isSame = r.video_id === currentVideoId;
  const thumb  = r.thumbnail_url ||
    `https://img.youtube.com/vi/${r.video_id}/mqdefault.jpg`;

  const ctxRange = r.context
    ? `${fmtTime(r.context.start_time)} – ${fmtTime(r.context.end_time)}` : '';

  const card = document.createElement('div');
  card.className = 'yts-card';
  card.style.animationDelay = `${(cardCount - 1) * 35}ms`;
  // Citation chips in the answer card resolve to these.
  card.dataset.videoId = r.video_id;
  card.dataset.start   = r.start_time;

  card.innerHTML = `
    <div class="yts-card-header">
      ${!isSame
        ? `<img class="yts-thumb" src="${esc(thumb)}"
              onerror="this.style.display='none'" loading="lazy" />`
        : ''}
      <div class="yts-card-meta">
        <div class="yts-card-title" title="${esc(r.title)}">${esc(r.title)}</div>
        <div class="yts-card-sub">
          <span class="yts-channel-tag">${esc(r.channel_name || 'Unknown')}</span>
          <span class="yts-dot-sep">·</span>
          <span>⏱ ${ts}</span>
        </div>
      </div>
      <div class="yts-sim yts-sim-${simCls}">${pct}%</div>
    </div>
    <div class="yts-excerpt"><span class="yts-txt"></span><span class="yts-cursor"></span></div>
    <div class="yts-actions">
      ${isSame
        ? `<button class="yts-btn yts-btn-jump" data-t="${r.start_time}">
             <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
               <polygon points="5 3 19 12 5 21 5 3"/>
             </svg>
             Jump ${ts}
           </button>`
        : `<a class="yts-btn yts-btn-jump" href="${esc(r.youtube_url)}"
              target="_blank" rel="noopener">
             <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
               <polygon points="5 3 19 12 5 21 5 3"/>
             </svg>
             Open ${ts}
           </a>`}
      <button class="yts-btn yts-btn-clip" data-embed="${esc(r.embed_url)}">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <rect x="2" y="3" width="20" height="14" rx="2"/>
          <line x1="8" y1="21" x2="16" y2="21"/>
          <line x1="12" y1="17" x2="12" y2="21"/>
        </svg>
        Clip
      </button>
      ${r.context
        ? `<button class="yts-btn yts-btn-ctx">
             <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
               <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
             </svg>
             Context
           </button>`
        : ''}
    </div>
    <div class="yts-context-panel" style="display:none">
      <div class="yts-ctx-range">${esc(ctxRange)}</div>
      <div class="yts-ctx-text">${esc(r.context?.text || '')}</div>
    </div>
    <div class="yts-embed-wrap"></div>
  `;
  container.appendChild(card);

  const jumpBtn = card.querySelector('.yts-btn-jump[data-t]');
  if (jumpBtn) jumpBtn.onclick = () => seekVideo(parseFloat(jumpBtn.dataset.t));

  const ctxBtn = card.querySelector('.yts-btn-ctx');
  if (ctxBtn) ctxBtn.onclick = function () {
    const panel = card.querySelector('.yts-context-panel');
    const open  = panel.style.display !== 'none';
    panel.style.display = open ? 'none' : 'block';
    this.querySelector('svg').style.stroke = open ? '' : '#a78bfa';
  };

  const clipBtn = card.querySelector('.yts-btn-clip');
  clipBtn.onclick = function () {
    const wrap = card.querySelector('.yts-embed-wrap');
    if (wrap.classList.contains('open')) {
      wrap.classList.remove('fade');
      setTimeout(() => wrap.classList.remove('open'), 400);
      this.querySelector('svg').style.stroke = '';
    } else {
      if (!wrap.querySelector('iframe')) {
        wrap.innerHTML = `<iframe src="${this.dataset.embed}"
          allow="accelerometer;autoplay;clipboard-write;encrypted-media;gyroscope"
          allowfullscreen loading="lazy"></iframe>`;
      }
      wrap.classList.add('open');
      requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.add('fade')));
      this.querySelector('svg').style.stroke = '#a78bfa';
    }
  };

  if (isSame && r.similarity >= 0.75 && cardCount === 1) seekVideo(r.start_time);
  typewrite(card.querySelector('.yts-txt'), card.querySelector('.yts-cursor'), r.text, 13);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function seekVideo(seconds) {
  const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
  if (v) { v.currentTime = seconds; v.play().catch(() => {}); }
}

function typewrite(el, cursor, text, speed) {
  let i = 0;
  (function tick() {
    if (i < text.length) {
      const burst = Math.min(Math.floor(Math.random() * 3) + 1, text.length - i);
      el.textContent += text.slice(i, i + burst);
      i += burst;
      setTimeout(tick, speed + Math.random() * 8);
    } else { if (cursor) cursor.remove(); }
  })();
}

function setStatus(color, msg) {
  const dot  = $('yts-dot');
  const text = $('yts-status-text');
  if (dot)  dot.className = `yts-dot ${color}`;
  if (text) text.textContent = msg;
}

function setSendEnabled(on) {
  const btn = $('yts-send');
  if (btn) btn.disabled = !on;
}

function fmtTime(secs) {
  const s = Math.floor(+secs);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
    : `${m}:${String(sec).padStart(2,'0')}`;
}

function esc(s) {
  return String(s || '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
