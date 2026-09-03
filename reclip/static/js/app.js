/**
 * ReClip Plus — Frontend Client Application
 * Vanilla JS, Server-Sent Events (SSE), LocalStorage Presets & History, Responsive UI
 */

(() => {
  'use strict';

  // Application State
  const state = {
    format: 'video', // 'video' | 'audio'
    videoQuality: 'best', // 'best' | 'balanced' | 'fastest' | format_id
    audioQuality: 'original', // 'original' | '320' | '256' | '192' | '128'
    options: {
      start_time: '',
      end_time: '',
      sub_lang: '',
      embed_subs: false,
      auto_subs: false,
      embed_metadata: true,
      embed_thumbnail: true,
      embed_chapters: false,
      filename_strategy: 'title',
    },
    cards: [], // list of card objects
    eventSources: {}, // jobId -> EventSource
    pollFallbacks: {}, // jobId -> intervalId
    queueStats: { active: 0, queued: 0 },
    notifications: false,
  };

  // DOM Elements
  const elUrls = document.getElementById('urls');
  const elUrlStats = document.getElementById('urlStats');
  const elCards = document.getElementById('cards');
  const elFetchBtn = document.getElementById('fetchBtn');
  const elClearBtn = document.getElementById('clearBtn');
  const elPasteBtn = document.getElementById('pasteBtn');
  const elThemeBtn = document.getElementById('themeBtn');
  const elNotifyBtn = document.getElementById('notifyBtn');
  const elAdvToggle = document.getElementById('advToggle');
  const elAdvPanel = document.getElementById('advPanel');
  const elQueueStatusBar = document.getElementById('queueStatusBar');
  const elHistoryContainer = document.getElementById('historyItems');
  const elToastContainer = document.getElementById('toastContainer');

  // ==========================================
  // Helper & Utility Functions
  // ==========================================

  function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function showToast(msg, duration = 3000) {
    if (!elToastContainer) return;
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    elToastContainer.appendChild(t);
    setTimeout(() => {
      t.style.opacity = '0';
      t.style.transition = 'opacity 0.3s ease';
      setTimeout(() => t.remove(), 300);
    }, duration);
  }

  function sendBrowserNotification(title, body) {
    if (!state.notifications || !('Notification' in window)) return;
    if (Notification.permission === 'granted') {
      try {
        new Notification(title, { body, icon: '/static/favicon.svg' });
      } catch (e) {
        // Fallback or ignore in restricted contexts
      }
    }
  }

  function fmtDur(seconds) {
    if (!seconds && seconds !== 0) return '';
    const s = Math.floor(seconds);
    const m = Math.floor(s / 60);
    const sec = s % 60;
    const h = Math.floor(m / 60);
    if (h > 0) {
      return `${h}:${(m % 60).toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
    }
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  // ==========================================
  // Theme & Notifications
  // ==========================================

  function initTheme() {
    const saved = localStorage.getItem('reclip_theme') || 'auto';
    applyTheme(saved);
  }

  function applyTheme(theme) {
    localStorage.setItem('reclip_theme', theme);
    if (theme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
      if (elThemeBtn) elThemeBtn.innerHTML = '🌙 Dark';
    } else if (theme === 'light') {
      document.documentElement.removeAttribute('data-theme');
      if (elThemeBtn) elThemeBtn.innerHTML = '☀️ Light';
    } else {
      // Auto
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      if (prefersDark) {
        document.documentElement.setAttribute('data-theme', 'dark');
      } else {
        document.documentElement.removeAttribute('data-theme');
      }
      if (elThemeBtn) elThemeBtn.innerHTML = '🌓 Auto';
    }
  }

  function toggleTheme() {
    const current = localStorage.getItem('reclip_theme') || 'auto';
    const next = current === 'auto' ? 'dark' : current === 'dark' ? 'light' : 'auto';
    applyTheme(next);
  }

  async function toggleNotifications() {
    if (!('Notification' in window)) {
      showToast('Notifications are not supported in this browser.');
      return;
    }
    if (Notification.permission === 'granted') {
      state.notifications = !state.notifications;
      showToast(state.notifications ? 'Notifications enabled.' : 'Notifications disabled.');
      updateNotifyBtn();
    } else if (Notification.permission !== 'denied') {
      const perm = await Notification.requestPermission();
      if (perm === 'granted') {
        state.notifications = true;
        showToast('Notifications enabled!');
        updateNotifyBtn();
      }
    } else {
      showToast('Notifications blocked in browser settings.');
    }
  }

  function updateNotifyBtn() {
    if (!elNotifyBtn) return;
    elNotifyBtn.innerHTML = state.notifications ? '🔔 Alerts On' : '🔕 Alerts Off';
  }

  // ==========================================
  // Smart URL Parser & Input Handler
  // ==========================================

  function parseUrls(text) {
    if (!text) return [];
    // Split on spaces, newlines, commas
    const tokens = text.split(/[\s,]+/).map(t => t.trim()).filter(Boolean);
    const valid = [];
    let dups = 0;
    const seen = new Set();

    for (const t of tokens) {
      if (t.startsWith('http://') || t.startsWith('https://')) {
        if (seen.has(t)) {
          dups++;
        } else {
          seen.add(t);
          valid.push(t);
        }
      }
    }

    return { urls: valid, duplicates: dups, totalTokens: tokens.length };
  }

  function updateUrlStats() {
    if (!elUrls || !elUrlStats) return;
    const { urls, duplicates, totalTokens } = parseUrls(elUrls.value);
    if (!totalTokens) {
      elUrlStats.textContent = '';
      return;
    }
    const count = urls.length;
    const dupStr = duplicates > 0 ? ` (${duplicates} duplicate${duplicates > 1 ? 's' : ''} removed)` : '';
    const invalidCount = totalTokens - (count + duplicates);
    const invStr = invalidCount > 0 ? ` · ${invalidCount} non-URL item${invalidCount > 1 ? 's' : ''} ignored` : '';
    elUrlStats.textContent = `${count} URL${count === 1 ? '' : 's'} detected${dupStr}${invStr}`;
  }

  async function handlePaste() {
    try {
      if (!navigator.clipboard || !navigator.clipboard.readText) {
        showToast('Clipboard access not allowed.');
        return;
      }
      const text = await navigator.clipboard.readText();
      if (!text) return;
      if (elUrls.value.trim()) {
        elUrls.value = elUrls.value.trim() + '\n' + text.trim();
      } else {
        elUrls.value = text.trim();
      }
      updateUrlStats();
      showToast('Pasted from clipboard.');
    } catch (e) {
      showToast('Please paste manually using Ctrl+V / Cmd+V.');
    }
  }

  // ==========================================
  // Presets & Per-Site Defaults Management
  // ==========================================

  const PRESETS = {
    'best-video': { label: 'Best Quality', format: 'video', videoQuality: 'best' },
    '1080p': { label: '1080p MP4', format: 'video', videoQuality: '1080' },
    '720p': { label: '720p Mobile', format: 'video', videoQuality: '720' },
    'mp3-320': { label: '320k MP3', format: 'audio', audioQuality: '320' },
    'audio-orig': { label: 'Original Audio', format: 'audio', audioQuality: 'original' },
    'fastest': { label: 'Fastest (Direct)', format: 'video', videoQuality: 'fastest' },
  };

  function getCustomPresets() {
    try {
      const raw = localStorage.getItem('reclip_custom_presets');
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function renderCustomPresets() {
    const bar = document.getElementById('presetsBar');
    if (!bar) return;
    document.querySelectorAll('.custom-chip').forEach(el => el.remove());
    const custom = getCustomPresets();
    const addBtn = document.getElementById('addPresetBtn');
    Object.entries(custom).forEach(([key, p]) => {
      const btn = document.createElement('button');
      btn.className = 'preset-chip custom-chip';
      btn.innerHTML = `${esc(p.label)} <span class="chip-del" title="Delete preset" onclick="window.deleteCustomPreset('${esc(key)}', event)">&times;</span>`;
      btn.onclick = () => {
        state.format = p.format || 'video';
        if (p.videoQuality) state.videoQuality = p.videoQuality;
        if (p.audioQuality) state.audioQuality = p.audioQuality;
        syncFormatUI();
        document.querySelectorAll('.preset-chip').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        showToast(`Preset: ${p.label}`);
      };
      if (addBtn) {
        bar.insertBefore(btn, addBtn);
      } else {
        bar.appendChild(btn);
      }
    });
  }

  window.deleteCustomPreset = (name, evt) => {
    if (evt) evt.stopPropagation();
    const custom = getCustomPresets();
    delete custom[name];
    try {
      localStorage.setItem('reclip_custom_presets', JSON.stringify(custom));
      renderCustomPresets();
      showToast(`Removed preset "${name}"`);
    } catch (e) {}
  };

  function saveNewCustomPreset() {
    const name = prompt('Enter a name for this custom preset:');
    if (!name || !name.trim()) return;
    const cleanName = name.trim();
    const custom = getCustomPresets();
    custom[cleanName] = {
      label: cleanName,
      format: state.format,
      videoQuality: state.videoQuality,
      audioQuality: state.audioQuality,
    };
    try {
      localStorage.setItem('reclip_custom_presets', JSON.stringify(custom));
      renderCustomPresets();
      showToast(`Saved preset "${cleanName}"`);
    } catch (e) {
      showToast('Could not save preset.');
    }
  }

  function getSiteDefaults() {
    try {
      const raw = localStorage.getItem('reclip_site_defaults');
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function saveCurrentSiteDefault() {
    const { urls } = parseUrls(elUrls ? elUrls.value : '');
    const targetUrl = urls[0] || '';
    if (!targetUrl) {
      showToast('Enter a URL first to remember defaults for that site.');
      return;
    }
    try {
      const u = new URL(targetUrl);
      const domain = u.hostname.replace(/^www\./, '');
      const defs = getSiteDefaults();
      defs[domain] = {
        format: state.format,
        videoQuality: state.videoQuality,
        audioQuality: state.audioQuality,
      };
      localStorage.setItem('reclip_site_defaults', JSON.stringify(defs));
      showToast(`Saved default settings for ${domain}`);
    } catch (e) {
      showToast('Invalid URL for site defaults.');
    }
  }

  function applySiteDefaultsIfAny(url) {
    if (!url) return;
    try {
      const u = new URL(url);
      const domain = u.hostname.replace(/^www\./, '');
      const defs = getSiteDefaults();
      if (defs[domain]) {
        const d = defs[domain];
        if (d.format) state.format = d.format;
        if (d.videoQuality) state.videoQuality = d.videoQuality;
        if (d.audioQuality) state.audioQuality = d.audioQuality;
        syncFormatUI();
        showToast(`Loaded default preferences for ${domain}`);
      }
    } catch (e) {}
  }

  async function updateQueueStatus() {
    try {
      const res = await fetch('/api/jobs');
      if (!res.ok) return;
      const data = await res.json();
      state.queueStats = { active: data.active_count, queued: data.queued_count };
      if (elQueueStatusBar) {
        if (data.active_count > 0 || data.queued_count > 0) {
          elQueueStatusBar.style.display = 'flex';
          const elActive = document.getElementById('queueActiveText');
          const elWaiting = document.getElementById('queueWaitingText');
          if (elActive) elActive.textContent = `${data.active_count} active download${data.active_count === 1 ? '' : 's'}`;
          if (elWaiting) elWaiting.textContent = `${data.queued_count} queued`;
        } else {
          elQueueStatusBar.style.display = 'none';
        }
      }
    } catch (e) {}
  }

  function applyPreset(key) {
    const p = PRESETS[key];
    if (!p) return;

    document.querySelectorAll('.preset-chip').forEach(c => c.classList.remove('active'));
    const btn = document.querySelector(`.preset-chip[data-preset="${key}"]`);
    if (btn) btn.classList.add('active');

    state.format = p.format;
    if (p.videoQuality) state.videoQuality = p.videoQuality;
    if (p.audioQuality) state.audioQuality = p.audioQuality;

    syncFormatUI();
    showToast(`Preset: ${p.label}`);
  }

  function syncFormatUI() {
    // Sync main format tabs
    document.querySelectorAll('.format-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.format === state.format);
    });

    const videoRow = document.getElementById('videoStrategyRow');
    const audioRow = document.getElementById('audioStrategyRow');

    if (state.format === 'video') {
      if (videoRow) videoRow.style.display = 'flex';
      if (audioRow) audioRow.style.display = 'none';
      document.querySelectorAll('#videoStrategyRow .strat-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.quality === state.videoQuality);
      });
    } else {
      if (videoRow) videoRow.style.display = 'none';
      if (audioRow) audioRow.style.display = 'flex';
      document.querySelectorAll('#audioStrategyRow .strat-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.quality === state.audioQuality);
      });
    }
  }

  // ==========================================
  // Fetch & Batch Progressive Metadata
  // ==========================================

  async function fetchMetadataForBatch(urls) {
    if (!urls.length) return;

    elFetchBtn.disabled = true;
    elFetchBtn.innerHTML = '<span class="spin"></span> Fetching...';

    // Check for playlists and expand
    const expandedUrls = [];
    for (const u of urls) {
      if (u.includes('list=')) {
        try {
          const res = await fetch('/api/playlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: u, limit: 50 }),
          });
          const pl = await res.json();
          if (pl.urls && pl.urls.length > 0) {
            showPlaylistModal(pl);
            continue;
          }
        } catch (e) {
          // Fallback to regular single URL
        }
      }
      expandedUrls.push(u);
    }

    if (!expandedUrls.length) {
      elFetchBtn.disabled = false;
      elFetchBtn.textContent = 'Fetch Info';
      return;
    }

    // Controlled concurrent fetching (concurrency = 3)
    const startIndex = state.cards.length;
    expandedUrls.forEach(url => {
      const idx = state.cards.length;
      state.cards.push({
        idx,
        url,
        status: 'loading',
        format: state.format,
        selectedQuality: state.format === 'video' ? state.videoQuality : state.audioQuality,
      });
      renderCard(idx);
    });

    const concurrency = 3;
    let currentIdx = 0;

    async function worker() {
      while (currentIdx < expandedUrls.length) {
        const itemIdx = startIndex + currentIdx++;
        const card = state.cards[itemIdx];
        if (!card) break;

        try {
          const res = await fetch('/api/info', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: card.url }),
          });
          const data = await res.json();
          if (res.ok && !data.error) {
            Object.assign(card, {
              status: 'ready',
              title: data.title || 'Untitled Video',
              uploader: data.uploader || '',
              duration: data.duration,
              thumbnail: data.thumbnail || '',
              formats: data.formats || [],
              chapters: data.chapters || [],
              subtitles: data.subtitles || [],
              selectedFormatId: data.formats?.[0]?.id || 'best',
            });
          } else {
            card.status = 'info-error';
            card.error = data.error || 'Failed to retrieve media information.';
          }
        } catch (err) {
          card.status = 'info-error';
          card.error = err.message || 'Network connection failed.';
        }
        renderCard(itemIdx);
      }
    }

    const workers = Array.from({ length: Math.min(concurrency, expandedUrls.length) }, () => worker());
    await Promise.all(workers);

    elFetchBtn.disabled = false;
    elFetchBtn.textContent = 'Fetch Info';
    renderDownloadAllBar();
  }

  function showPlaylistModal(pl) {
    const banner = document.createElement('div');
    banner.className = 'playlist-banner';
    banner.innerHTML = `
      <div class="playlist-header">
        <div class="playlist-title">${esc(pl.title)}</div>
        <div class="playlist-count">${pl.count} Videos Found</div>
      </div>
      <div class="playlist-actions-row" style="margin-bottom:8px">
        <div>
          <button class="btn-sm btn-outline" id="plSelectAll">Select All</button>
          <button class="btn-sm btn-outline" id="plDeselectAll">Deselect All</button>
        </div>
        <button class="btn-sm btn-accent" id="plAddSelected">Add Selected (${pl.count})</button>
      </div>
      <div class="playlist-items" id="plItemsContainer">
        ${pl.items.map((it, i) => `
          <label class="playlist-item-row">
            <input type="checkbox" class="pl-checkbox" data-url="${esc(it.url)}" checked>
            <span>${i + 1}. ${esc(it.title)}</span>
            <span style="color:var(--muted); margin-left:auto">${esc(it.duration_str)}</span>
          </label>
        `).join('')}
      </div>
    `;

    document.getElementById('cards').prepend(banner);

    const checkboxes = banner.querySelectorAll('.pl-checkbox');
    const updateCountBtn = () => {
      const selected = banner.querySelectorAll('.pl-checkbox:checked').length;
      banner.querySelector('#plAddSelected').textContent = `Add Selected (${selected})`;
    };

    checkboxes.forEach(cb => cb.addEventListener('change', updateCountBtn));

    banner.querySelector('#plSelectAll').onclick = () => {
      checkboxes.forEach(cb => (cb.checked = true));
      updateCountBtn();
    };

    banner.querySelector('#plDeselectAll').onclick = () => {
      checkboxes.forEach(cb => (cb.checked = false));
      updateCountBtn();
    };

    banner.querySelector('#plAddSelected').onclick = () => {
      const selectedUrls = Array.from(banner.querySelectorAll('.pl-checkbox:checked')).map(cb => cb.dataset.url);
      banner.remove();
      if (selectedUrls.length) {
        fetchMetadataForBatch(selectedUrls);
      }
    };
  }

  // ==========================================
  // Render Cards
  // ==========================================

  function renderCard(idx) {
    const c = state.cards[idx];
    if (!c) return;

    let el = document.getElementById(`card-${idx}`);
    if (!el) {
      el = document.createElement('div');
      el.id = `card-${idx}`;
      el.className = 'card';
      elCards.appendChild(el);
    }

    // Loading Shimmer State
    if (c.status === 'loading') {
      el.className = 'card';
      el.innerHTML = `
        <div class="card-thumb loading"></div>
        <div class="card-body">
          <div class="skeleton-line medium"></div>
          <div class="skeleton-line short"></div>
        </div>
      `;
      return;
    }

    // Info Error State
    if (c.status === 'info-error') {
      el.className = 'card card-error';
      el.innerHTML = `
        <div class="card-thumb">
          <div class="card-error-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
          </div>
        </div>
        <div class="card-body">
          <div class="card-title" style="color:var(--error)">Could not fetch video info</div>
          <div style="font-size:0.72rem; color:var(--error); line-height:1.4">${esc(c.error)}</div>
          <div style="font-size:0.65rem; color:var(--muted); word-break:break-all; margin-top:4px">${esc(c.url)}</div>
        </div>
      `;
      return;
    }

    // Ready / In-Flight / Done State
    el.className = 'card';
    const isAudio = c.format === 'audio';

    let thumbHtml;
    if (isAudio) {
      thumbHtml = `<div class="thumb-placeholder" style="color:var(--accent)"><svg width="32" height="32" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg></div>`;
    } else if (c.thumbnail) {
      thumbHtml = `<img src="${esc(c.thumbnail)}" alt="" loading="lazy" decoding="async">`;
    } else {
      thumbHtml = `<div class="thumb-placeholder"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m21 15-5-5L5 21"/></svg></div>`;
    }

    // Format Chips on Ready Card
    let qualityChips = '';
    if (!isAudio && c.formats && c.formats.length > 1 && c.status === 'ready') {
      qualityChips = c.formats.slice(0, 4).map(f =>
        `<button class="q-chip ${f.id === c.selectedFormatId ? 'active' : ''}" onclick="window.pickCardFormat(${idx}, '${esc(f.id)}')">${esc(f.label)}</button>`
      ).join('');
    }

    // Direct Thumbnail Download Link
    let thumbBtn = '';
    if (c.thumbnail && c.status === 'ready') {
      thumbBtn = `<a href="${esc(c.thumbnail)}" target="_blank" rel="noopener noreferrer" download class="btn-sm btn-outline" style="text-decoration:none;" title="Save thumbnail image directly">🖼️ Thumb</a>`;
    }

    // Subtitles discovery & selection
    let subSelectHtml = '';
    if (c.subtitles && c.subtitles.length > 0 && c.status === 'ready') {
      subSelectHtml = `
        <div class="card-details-row">
          <span style="color:var(--muted)">Subtitles:</span>
          <select class="sub-select" onchange="window.pickCardSubtitle(${idx}, this.value)">
            <option value="">None</option>
            ${c.subtitles.map(s => `<option value="${esc(s.code)}" ${c.selectedSub === s.code ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}
          </select>
          ${c.selectedSub ? `
            <label style="display:inline-flex; align-items:center; gap:4px; font-size:0.68rem; color:var(--muted)">
              <input type="checkbox" ${c.subOnly ? 'checked' : ''} onchange="window.toggleSubOnly(${idx}, this.checked)"> SRT File Only
            </label>
          ` : ''}
        </div>
      `;
    }

    // Chapters discovery & selection
    let chaptersHtml = '';
    if (c.chapters && c.chapters.length > 0 && c.status === 'ready') {
      const chCount = c.chapters.length;
      const isOpen = c.showChapters || false;
      chaptersHtml = `
        <div class="card-details-row">
          <button class="chapter-btn" type="button" onclick="window.toggleCardChapters(${idx})">
            📑 Chapters (${chCount}) ${isOpen ? '▲' : '▼'}
          </button>
          ${c.selectedChapter ? `<span style="color:var(--accent); font-size:0.68rem;">Selected: ${esc(c.selectedChapter.title)} (${esc(c.selectedChapter.start_str)}-${esc(c.selectedChapter.end_str)})</span>` : ''}
        </div>
        ${isOpen ? `
          <div class="chapters-list">
            ${c.chapters.map((ch, chIdx) => `
              <div class="chapter-item" onclick="window.pickCardChapter(${idx}, ${chIdx})">
                <span>${chIdx + 1}. ${esc(ch.title)}</span>
                <span style="color:var(--muted); font-size:0.65rem;">${esc(ch.start_str)} - ${esc(ch.end_str)}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
      `;
    }

    // Action / Progress elements
    let actionHtml = '';
    let progressHtml = '';

    if (c.status === 'ready') {
      actionHtml = `
        <button class="btn-sm btn-accent" onclick="window.startDownloadJob(${idx})">${c.subOnly ? 'Download Subtitles' : 'Download'}</button>
        ${qualityChips}
        ${thumbBtn}
      `;
    } else if (c.status === 'queued') {
      actionHtml = `
        <span class="card-status-text queued">🕒 In Queue</span>
        <button class="reorder-btn" title="Move Up in Queue" onclick="window.reorderJob(${idx}, -1)">▲</button>
        <button class="reorder-btn" title="Move Down in Queue" onclick="window.reorderJob(${idx}, 1)">▼</button>
        <button class="btn-sm btn-outline" onclick="window.cancelJob(${idx})">Cancel</button>
      `;
    } else if (c.status === 'downloading' || c.status === 'merging' || c.status === 'extracting_audio' || c.status === 'processing') {
      const p = c.progress || {};
      const pct = p.percent || 0;
      const isMerge = c.status === 'merging' || c.status === 'processing';
      const stageClass = isMerge ? 'merging' : '';
      const speedEta = [p.speed_str, p.eta_str ? `ETA: ${p.eta_str}` : ''].filter(Boolean).join(' · ');
      const sizeStr = [p.downloaded_str, p.total_str].filter(Boolean).join(' / ');

      progressHtml = `
        <div class="progress-container">
          <div class="progress-bar-bg">
            <div class="progress-bar-fill ${stageClass}" style="width: ${pct}%"></div>
          </div>
          <div class="progress-details">
            <span>${pct > 0 ? `${pct}%` : ''} ${sizeStr ? `(${sizeStr})` : ''}</span>
            <span>${speedEta}</span>
          </div>
        </div>
      `;

      actionHtml = `
        <span class="card-status-text ${c.status}">
          <span class="spin"></span> ${esc(p.status_msg || 'Downloading...')}
        </span>
        <button class="btn-sm btn-outline" onclick="window.cancelJob(${idx})">Cancel</button>
      `;
    } else if (c.status === 'done') {
      progressHtml = `
        <div class="progress-container">
          <div class="progress-bar-bg">
            <div class="progress-bar-fill done" style="width: 100%"></div>
          </div>
        </div>
      `;
      actionHtml = `
        <button class="btn-sm btn-success" onclick="window.saveJobFile(${idx})">Save File</button>
        <span class="card-status-text done">✓ ${esc(c.filename || 'Finished')}</span>
      `;
    } else if (c.status === 'error') {
      actionHtml = `
        <button class="btn-sm btn-danger" onclick="window.retryJob(${idx})">Retry</button>
        <span class="card-status-text error">✕ ${esc(c.error || 'Failed')}</span>
      `;
    } else if (c.status === 'cancelled') {
      actionHtml = `
        <button class="btn-sm btn-outline" onclick="window.retryJob(${idx})">Restart</button>
        <span class="card-status-text" style="color:var(--muted)">Cancelled</span>
      `;
    }

    el.innerHTML = `
      <div class="card-thumb">${thumbHtml}</div>
      <div class="card-body">
        <div>
          <div class="card-title">${esc(c.title || 'Untitled Media')}</div>
          <div class="card-meta">${esc(c.uploader || '')}${c.duration ? ' · ' + fmtDur(c.duration) : ''}</div>
          ${subSelectHtml}
          ${chaptersHtml}
        </div>
        ${progressHtml}
        <div class="card-actions">${actionHtml}</div>
      </div>
    `;
  }

  function renderDownloadAllBar() {
    const existing = document.getElementById('dlAllBar');
    if (existing) existing.remove();

    const readyCards = state.cards.filter(c => c.status === 'ready');
    if (readyCards.length > 1) {
      const bar = document.createElement('div');
      bar.id = 'dlAllBar';
      bar.className = 'dl-all-bar';
      bar.innerHTML = `<button class="dl-all-btn" id="dlAllBtn" onclick="window.downloadAllReady()">Download All (${readyCards.length})</button>`;
      elCards.appendChild(bar);
    }
  }

  // ==========================================
  // Download Queue & SSE Event Streaming
  // ==========================================

  window.pickCardFormat = (idx, formatId) => {
    if (state.cards[idx]) {
      state.cards[idx].selectedFormatId = formatId;
      renderCard(idx);
    }
  };

  window.pickCardSubtitle = (idx, subCode) => {
    const c = state.cards[idx];
    if (!c) return;
    c.selectedSub = subCode;
    renderCard(idx);
  };

  window.toggleSubOnly = (idx, checked) => {
    const c = state.cards[idx];
    if (!c) return;
    c.subOnly = checked;
    renderCard(idx);
  };

  window.toggleCardChapters = (idx) => {
    const c = state.cards[idx];
    if (!c) return;
    c.showChapters = !c.showChapters;
    renderCard(idx);
  };

  window.pickCardChapter = (idx, chIdx) => {
    const c = state.cards[idx];
    if (!c || !c.chapters?.[chIdx]) return;
    const ch = c.chapters[chIdx];
    c.selectedChapter = ch;
    c.showChapters = false;
    showToast(`Selected chapter: ${ch.title}`);
    renderCard(idx);
  };

  window.reorderJob = async (idx, delta) => {
    const c = state.cards[idx];
    if (!c || !c.jobId) return;

    // Find all queued cards in current local order
    const queuedCards = state.cards.filter(card => card.status === 'queued');
    const curIdx = queuedCards.findIndex(card => card.jobId === c.jobId);
    if (curIdx < 0) return;

    const targetIdx = Math.max(0, curIdx + delta);
    try {
      const res = await fetch('/api/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: c.jobId, new_index: targetIdx }),
      });
      if (res.ok) {
        showToast(`Job moved ${delta < 0 ? 'up' : 'down'} in queue`);
        updateQueueStatus();
      }
    } catch (e) {}
  };

  window.startDownloadJob = async (idx) => {
    const c = state.cards[idx];
    if (!c) return;

    c.status = 'queued';
    c.error = null;
    renderCard(idx);

    try {
      const isSubOnly = !!(c.subOnly && c.selectedSub);
      const downloadFormat = isSubOnly ? 'subtitles' : c.format;

      const opts = {
        ...state.options,
        start_time: c.selectedChapter ? c.selectedChapter.start_str : (document.getElementById('advStartTime')?.value || ''),
        end_time: c.selectedChapter ? c.selectedChapter.end_str : (document.getElementById('advEndTime')?.value || ''),
        sub_lang: c.selectedSub || (document.getElementById('advSubLang')?.value || ''),
        embed_subs: !isSubOnly && (!!c.selectedSub || (document.getElementById('advEmbedSubs')?.checked || false)),
        auto_subs: document.getElementById('advAutoSubs')?.checked || false,
        embed_metadata: document.getElementById('advEmbedMeta')?.checked || true,
        embed_thumbnail: document.getElementById('advEmbedThumb')?.checked || false,
        embed_chapters: document.getElementById('advEmbedChapters')?.checked || false,
        filename_strategy: document.getElementById('advFilenameStrategy')?.value || 'title',
        chapter_title: c.selectedChapter ? c.selectedChapter.title : '',
      };

      let finalTitle = c.title || '';
      if (c.selectedChapter && finalTitle) {
        finalTitle = `${finalTitle} - ${c.selectedChapter.title}`;
      }

      const payload = {
        url: c.url,
        format: downloadFormat,
        video_quality: c.selectedFormatId || state.videoQuality,
        audio_quality: state.audioQuality,
        title: finalTitle,
        uploader: c.uploader || '',
        duration: c.duration,
        filesize: c.filesize,
        options: opts,
      };

      const res = await fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (!res.ok || data.error) {
        c.status = 'error';
        c.error = data.error || 'Failed to start download.';
        renderCard(idx);
        updateQueueStatus();
        return;
      }

      c.jobId = data.job_id;
      c.status = data.status || 'queued';
      renderCard(idx);
      updateQueueStatus();
      connectJobEvents(idx, data.job_id);
    } catch (err) {
      c.status = 'error';
      c.error = err.message || 'Failed to connect to server.';
      renderCard(idx);
      updateQueueStatus();
    }
  };

  function connectJobEvents(idx, jobId) {
    if (state.eventSources[jobId]) {
      state.eventSources[jobId].close();
    }

    // Primary: Server-Sent Events (SSE)
    if ('EventSource' in window) {
      const es = new EventSource(`/api/events/${jobId}`);
      state.eventSources[jobId] = es;

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          handleJobUpdate(idx, data);
        } catch (e) {
          // Ignore keep-alive or bad JSON
        }
      };

      es.onerror = () => {
        es.close();
        delete state.eventSources[jobId];
        // Graceful fallback to HTTP polling
        startPollingFallback(idx, jobId);
      };
    } else {
      // Fallback directly to polling
      startPollingFallback(idx, jobId);
    }
  }

  function startPollingFallback(idx, jobId) {
    if (state.pollFallbacks[jobId]) return;

    const iv = setInterval(async () => {
      try {
        const res = await fetch(`/api/status/${jobId}`);
        if (!res.ok) throw new Error('Status poll failed');
        const data = await res.json();
        handleJobUpdate(idx, data);
      } catch (err) {
        clearInterval(iv);
        delete state.pollFallbacks[jobId];
      }
    }, 1500);

    state.pollFallbacks[jobId] = iv;
  }

  function handleJobUpdate(idx, data) {
    const c = state.cards[idx];
    if (!c) return;

    c.status = data.status;
    c.progress = data.progress || {};
    c.error = data.error;
    c.filename = data.filename;

    if (data.status === 'done') {
      cleanupJobConnection(data.id);
      saveToHistory({
        title: c.title || data.filename,
        url: c.url,
        format: c.format,
        filename: data.filename,
        date: new Date().toISOString(),
      });
      sendBrowserNotification('ReClip Plus Download Complete', c.title || data.filename);
      // Automatically prompt save
      window.saveJobFile(idx);
    } else if (data.status === 'error' || data.status === 'cancelled') {
      cleanupJobConnection(data.id);
    }

    updateQueueStatus();
    renderCard(idx);
  }

  function cleanupJobConnection(jobId) {
    if (state.eventSources[jobId]) {
      state.eventSources[jobId].close();
      delete state.eventSources[jobId];
    }
    if (state.pollFallbacks[jobId]) {
      clearInterval(state.pollFallbacks[jobId]);
      delete state.pollFallbacks[jobId];
    }
  }

  window.cancelJob = async (idx) => {
    const c = state.cards[idx];
    if (!c || !c.jobId) return;

    cleanupJobConnection(c.jobId);
    c.status = 'cancelled';
    renderCard(idx);
    updateQueueStatus();

    try {
      await fetch(`/api/cancel/${c.jobId}`, { method: 'POST' });
    } catch (e) {
      // Best effort cancel
    }
    updateQueueStatus();
  };

  window.retryJob = async (idx) => {
    const c = state.cards[idx];
    if (!c || !c.jobId) return;

    try {
      const res = await fetch(`/api/retry/${c.jobId}`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        c.status = 'queued';
        c.error = null;
        renderCard(idx);
        updateQueueStatus();
        connectJobEvents(idx, c.jobId);
      } else {
        showToast(data.error || 'Cannot retry this job.');
      }
    } catch (err) {
      showToast('Retry request failed.');
    }
  };

  window.saveJobFile = (idx) => {
    const c = state.cards[idx];
    if (!c || !c.jobId) return;
    const a = document.createElement('a');
    a.href = `/api/file/${c.jobId}`;
    a.download = c.filename || 'download';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  window.downloadAllReady = async () => {
    const btn = document.getElementById('dlAllBtn');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spin"></span> Queuing...';
    }

    for (let i = 0; i < state.cards.length; i++) {
      if (state.cards[i].status === 'ready') {
        await window.startDownloadJob(i);
      }
    }

    if (btn) {
      btn.disabled = false;
      btn.textContent = 'All Queued';
    }
  };

  // ==========================================
  // Local History (localStorage)
  // ==========================================

  function loadHistory() {
    try {
      const raw = localStorage.getItem('reclip_history');
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function saveToHistory(item) {
    try {
      const list = loadHistory();
      // Keep most recent 50
      list.unshift(item);
      const trimmed = list.slice(0, 50);
      localStorage.setItem('reclip_history', JSON.stringify(trimmed));
      renderHistory();
    } catch (e) {
      // LocalStorage quota or access issue
    }
  }

  function clearHistory() {
    try {
      localStorage.removeItem('reclip_history');
      renderHistory();
      showToast('Download history cleared.');
    } catch (e) {}
  }

  function renderHistory() {
    if (!elHistoryContainer) return;
    const list = loadHistory();
    if (!list.length) {
      elHistoryContainer.innerHTML = '<div style="font-size:0.75rem; color:var(--muted); padding:8px 0">No recent downloads.</div>';
      return;
    }

    elHistoryContainer.innerHTML = list.map((it, i) => `
      <div class="history-card">
        <div class="history-info">
          <div class="history-name">${esc(it.title || it.filename || 'Media')}</div>
          <div class="history-sub">${esc(it.format?.toUpperCase())} · ${new Date(it.date).toLocaleDateString()}</div>
        </div>
        <button class="btn-sm btn-outline" onclick="window.redownloadUrl('${esc(it.url)}')">Re-download</button>
      </div>
    `).join('');
  }

  window.redownloadUrl = (url) => {
    if (!url) return;
    elUrls.value = url;
    updateUrlStats();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    fetchMetadataForBatch([url]);
  };

  // ==========================================
  // Copy yt-dlp Command Feature
  // ==========================================

  async function copyYtDlpCommand() {
    const urls = parseUrls(elUrls.value).urls;
    const url = urls[0] || 'https://www.youtube.com/watch?v=...';

    const payload = {
      url,
      format: state.format,
      video_quality: state.videoQuality,
      audio_quality: state.audioQuality,
      options: {
        ...state.options,
        start_time: document.getElementById('advStartTime')?.value || '',
        end_time: document.getElementById('advEndTime')?.value || '',
        sub_lang: document.getElementById('advSubLang')?.value || '',
        embed_subs: document.getElementById('advEmbedSubs')?.checked || false,
        auto_subs: document.getElementById('advAutoSubs')?.checked || false,
        embed_metadata: document.getElementById('advEmbedMeta')?.checked || true,
        embed_thumbnail: document.getElementById('advEmbedThumb')?.checked || false,
        embed_chapters: document.getElementById('advEmbedChapters')?.checked || false,
        filename_strategy: document.getElementById('advFilenameStrategy')?.value || 'title',
      },
    };

    try {
      const res = await fetch('/api/generate-command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.command) {
        await navigator.clipboard.writeText(data.command);
        showToast('yt-dlp command copied to clipboard!');
      }
    } catch (e) {
      showToast('Could not copy command.');
    }
  }

  // ==========================================
  // Initialization & Event Listeners
  // ==========================================

  function init() {
    initTheme();
    renderHistory();
    renderCustomPresets();
    updateQueueStatus();
    setInterval(updateQueueStatus, 4000);

    // Input events
    elUrls.addEventListener('input', () => {
      updateUrlStats();
      const { urls } = parseUrls(elUrls.value);
      if (urls.length > 0) {
        applySiteDefaultsIfAny(urls[0]);
      }
    });

    elUrls.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const { urls } = parseUrls(elUrls.value);
        if (urls.length) fetchMetadataForBatch(urls);
      }
    });

    if (elPasteBtn) elPasteBtn.addEventListener('click', handlePaste);
    if (elThemeBtn) elThemeBtn.addEventListener('click', toggleTheme);
    if (elNotifyBtn) elNotifyBtn.addEventListener('click', toggleNotifications);

    // Format pills
    document.querySelectorAll('.format-tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        state.format = btn.dataset.format;
        syncFormatUI();
      });
    });

    // Strategy buttons
    document.querySelectorAll('#videoStrategyRow .strat-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        state.videoQuality = btn.dataset.quality;
        syncFormatUI();
      });
    });

    document.querySelectorAll('#audioStrategyRow .strat-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        state.audioQuality = btn.dataset.quality;
        syncFormatUI();
      });
    });

    // Preset chips
    document.querySelectorAll('.preset-chip[data-preset]').forEach(chip => {
      chip.addEventListener('click', () => {
        applyPreset(chip.dataset.preset);
      });
    });

    const elAddPreset = document.getElementById('addPresetBtn');
    if (elAddPreset) elAddPreset.addEventListener('click', saveNewCustomPreset);

    const elSaveSite = document.getElementById('saveSiteDefaultBtn');
    if (elSaveSite) elSaveSite.addEventListener('click', saveCurrentSiteDefault);

    // Action buttons
    if (elFetchBtn) {
      elFetchBtn.addEventListener('click', () => {
        const { urls } = parseUrls(elUrls.value);
        if (urls.length) {
          fetchMetadataForBatch(urls);
        } else {
          showToast('Please enter at least one valid media URL.');
        }
      });
    }

    if (elClearBtn) {
      elClearBtn.addEventListener('click', () => {
        elUrls.value = '';
        updateUrlStats();
        state.cards = [];
        elCards.innerHTML = '';
        const bar = document.getElementById('dlAllBar');
        if (bar) bar.remove();
      });
    }

    // Advanced Options accordion
    if (elAdvToggle && elAdvPanel) {
      elAdvToggle.addEventListener('click', () => {
        const isOpen = elAdvPanel.classList.toggle('open');
        elAdvToggle.classList.toggle('open', isOpen);
      });
    }

    const elCopyCmd = document.getElementById('copyCmdBtn');
    if (elCopyCmd) elCopyCmd.addEventListener('click', copyYtDlpCommand);

    const elClearHistory = document.getElementById('clearHistoryBtn');
    if (elClearHistory) elClearHistory.addEventListener('click', clearHistory);
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
