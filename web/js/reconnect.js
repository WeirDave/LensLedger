const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const mode = LL.mode || 'reconnect';
const currentRoot = LL.currentRoot || '';
const $ = id => document.getElementById(id);
let selectedPath = '';
let polling = false;

async function api(path, payload) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, csrf }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function esc(s) { const d = document.createElement('span'); d.textContent = s; return d.innerHTML; }

function number(value) { return Number(value || 0).toLocaleString(); }

function selectLibrary(path) {
  selectedPath = path;
  document.querySelectorAll('.library-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.path === path);
  });
  const input = $('libraryPath');
  if (input) input.value = path;
  const btn = $('reconnect') || $('openSelected');
  if (btn) btn.disabled = false;
}

async function loadLibraries() {
  const response = await fetch('/api/library/reconnect-options');
  const data = await response.json();
  const list = $('libraryList');
  list.innerHTML = '';
  let firstAccessible = '';
  for (const lib of data.libraries || []) {
    const item = document.createElement('div');
    item.className = 'library-item' + (lib.accessible ? '' : ' unavailable');
    item.dataset.path = lib.path;
    const radio = document.createElement('span');
    radio.className = 'radio';
    const info = document.createElement('div');
    info.className = 'info';
    info.innerHTML = '<span class="label">' + esc(lib.label) + '</span>'
      + '<span class="path">' + esc(lib.path) + '</span>';
    const badges = document.createElement('span');
    if (lib.is_current) {
      const badge = document.createElement('span');
      badge.className = 'badge current';
      badge.textContent = 'Last used';
      badges.appendChild(badge);
    }
    if (lib.has_database) {
      const badge = document.createElement('span');
      badge.className = 'badge db';
      badge.textContent = 'Indexed';
      badges.appendChild(badge);
    }
    const status = document.createElement('span');
    status.className = 'status ' + (lib.accessible ? 'ok' : 'missing');
    status.textContent = lib.accessible ? 'Ready' : 'Not found';
    item.append(radio, info, badges, status);
    if (lib.accessible) {
      if (!firstAccessible) firstAccessible = lib.path;
      item.onclick = () => selectLibrary(lib.path);
    }
    list.appendChild(item);
  }
  if (mode === 'picker' && currentRoot) {
    selectLibrary(currentRoot);
  } else if (firstAccessible) {
    selectLibrary(firstAccessible);
  }
}

function showProgress(job) {
  $('progressPanel').classList.add('open');
  for (const key of ['scanned', 'changed', 'unchanged', 'placeholders', 'errors']) {
    const el = $(key);
    if (el) el.textContent = number(job[key]);
  }
  const bar = document.querySelector('.bar span');
  if (bar) {
    const total = job.total_estimate || 0;
    const done = job.scanned || 0;
    if (total > 0 && job.state === 'scanning') {
      bar.parentElement.classList.remove('indeterminate');
      const pct = Math.min(100, Math.round((done / total) * 100));
      bar.style.width = pct + '%';
      $('progressMessage').textContent = pct + '% · ' + number(done) + ' of ~' + number(total) + ' files';
    } else if (job.state === 'scanning') {
      bar.style.width = '';
      bar.parentElement.classList.add('indeterminate');
      $('progressMessage').textContent = job.message || job.state;
    } else {
      bar.parentElement.classList.remove('indeterminate');
      $('progressMessage').textContent = job.message || job.state;
    }
  }
  if (job.state === 'error') {
    $('progressMessage').className = 'error';
    const cancel = $('cancel');
    if (cancel) cancel.hidden = true;
    polling = false;
    return;
  }
  if (job.state === 'cancelled') {
    $('progressTitle').textContent = 'Scan paused';
    const cancel = $('cancel');
    if (cancel) cancel.hidden = true;
    polling = false;
    return;
  }
  if (job.state === 'complete') {
    polling = false;
    $('progressPanel').classList.add('complete');
    $('progressTitle').textContent = 'Library reconnected';
    const cancel = $('cancel');
    if (cancel) cancel.hidden = true;
    return;
  }
  if (polling) setTimeout(poll, 350);
}

async function poll() {
  try {
    const response = await fetch('/api/library/status');
    showProgress(await response.json());
  } catch (error) {
    $('progressMessage').textContent = error.message;
    $('progressMessage').className = 'error';
    polling = false;
  }
}

async function openLibrary(path) {
  const btn = $('reconnect') || $('openSelected');
  if (btn) { btn.disabled = true; btn.textContent = mode === 'picker' ? 'Opening…' : 'Reconnecting…'; }
  try {
    await api('/api/library/open', { path });
    if (mode === 'picker') {
      location.href = '/?sort=newest';
      return;
    }
    polling = true;
    poll();
  } catch (error) {
    if (btn) { btn.disabled = false; btn.textContent = mode === 'picker' ? 'Open library' : 'Reconnect'; }
    const panel = $('progressPanel');
    if (panel) {
      panel.classList.add('open');
      $('progressMessage').textContent = error.message;
      $('progressMessage').className = 'error';
    }
  }
}

const reconnectBtn = $('reconnect');
if (reconnectBtn) reconnectBtn.onclick = () => { if (selectedPath) openLibrary(selectedPath); };

const openBtn = $('openSelected');
if (openBtn) openBtn.onclick = () => { if (selectedPath) openLibrary(selectedPath); };

const browseBtn = $('browse');
if (browseBtn) {
  browseBtn.onclick = async () => {
    browseBtn.disabled = true;
    try {
      const result = await api('/api/library/browse', {});
      if (result.path) {
        $('libraryPath').value = result.path;
        selectedPath = result.path;
        document.querySelectorAll('.library-item').forEach(el => el.classList.remove('selected'));
        const btn = $('reconnect');
        if (btn) btn.disabled = false;
      }
    } catch (error) {
      const panel = $('progressPanel');
      if (panel) {
        panel.classList.add('open');
        $('progressMessage').textContent = error.message;
        $('progressMessage').className = 'error';
      }
    } finally {
      browseBtn.disabled = false;
    }
  };
}

const startFreshBtn = $('startFresh');
if (startFreshBtn) {
  startFreshBtn.onclick = () => { location.href = '/?fresh=1'; };
}

const cancelBtn = $('cancel');
if (cancelBtn) {
  cancelBtn.onclick = async () => {
    cancelBtn.disabled = true;
    try { await api('/api/library/cancel', {}); } catch (e) { /* */ }
  };
}

const enterBtn = $('enterLibrary');
if (enterBtn) enterBtn.onclick = () => location.href = '/?sort=newest';

const alwaysAsk = $('alwaysAsk');
if (alwaysAsk) {
  alwaysAsk.onchange = async () => {
    try {
      const res = await fetch('/api/settings');
      const settings = await res.json();
      settings.startup = settings.startup || {};
      settings.startup.show_library_picker = alwaysAsk.checked;
      await api('/api/settings/save', { settings });
    } catch (e) { /* */ }
  };
}

loadLibraries();
