const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const $ = id => document.getElementById(id);

async function api(path, data) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...data, csrf }),
  });
  if (response.status === 403) { location.reload(); return; }
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}

async function loadPending() {
  try {
    const response = await fetch('/api/publish/pending');
    const data = await response.json();
    renderPending(data);
  } catch (e) {
    $('publishSummary').textContent = 'Failed to load publish status.';
  }
}

function renderPending(data) {
  const { people, total_photos } = data;
  const summary = $('publishSummary');
  if (!people.length) {
    summary.innerHTML = '';
    const nothing = document.createElement('div');
    nothing.className = 'nothing-pending';
    nothing.textContent = 'All confirmed names have been published to photo metadata. Nothing to do.';
    summary.after(nothing);
    $('publishTableWrap').hidden = true;
    $('publishActions').hidden = true;
    $('globalProgress').textContent = 'Up to date';
    return;
  }
  const totalTags = people.reduce((s, p) => s + p.pending, 0);
  summary.innerHTML = '<span class="count">' + total_photos.toLocaleString() + '</span>'
    + '<span class="label">photo' + (total_photos === 1 ? '' : 's') + ' need metadata updates'
    + ' (' + totalTags.toLocaleString() + ' name tag' + (totalTags === 1 ? '' : 's')
    + ' across ' + people.length.toLocaleString() + ' ' + (people.length === 1 ? 'person' : 'people') + ')</span>';
  const wrap = $('publishTableWrap');
  const table = document.createElement('table');
  table.className = 'publish-table';
  table.innerHTML = '<thead><tr><th>Person</th><th class="count">Unpublished photos</th></tr></thead>';
  const tbody = document.createElement('tbody');
  people.forEach(p => {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td class="name"></td><td class="count"></td>';
    tr.querySelector('.name').textContent = p.name;
    tr.querySelector('.count').textContent = p.pending.toLocaleString();
    tbody.append(tr);
  });
  table.append(tbody);
  wrap.innerHTML = '';
  wrap.append(table);
  wrap.hidden = false;
  $('publishActions').hidden = false;
  $('globalProgress').textContent = total_photos.toLocaleString() + ' photo' + (total_photos === 1 ? '' : 's') + ' pending';
}

$('publishAll').onclick = async () => {
  $('publishAll').disabled = true;
  $('publishActions').hidden = true;
  $('publishTableWrap').hidden = true;
  const progress = $('publishProgress');
  progress.hidden = false;
  $('progressText').textContent = 'Starting publish…';
  $('progressFill').style.width = '0%';
  $('globalProgress').textContent = 'Publishing…';
  const pollId = setInterval(async () => {
    try {
      const r = await fetch('/api/publish/progress');
      const p = await r.json();
      if (p.state === 'running' && p.total > 0) {
        const pct = Math.round((p.done / p.total) * 100);
        $('progressFill').style.width = pct + '%';
        $('progressText').textContent = p.done + ' of ' + p.total + ' photos';
        $('globalProgress').textContent = p.done + '/' + p.total;
      }
    } catch {}
  }, 500);
  try {
    const result = await api('/api/publish/run', {});
    clearInterval(pollId);
    $('progressFill').style.width = '100%';
    $('progressText').textContent = '';
    progress.hidden = true;
    const done = $('publishDone');
    done.hidden = false;
    $('publishSummary').innerHTML = '';
    const failed = result.failed || [];
    if (failed.length === 0) {
      done.textContent = 'Published metadata to ' + result.published.toLocaleString()
        + ' of ' + result.total.toLocaleString() + ' photo' + (result.total === 1 ? '' : 's') + '.';
      $('globalProgress').textContent = 'Up to date';
    } else {
      done.innerHTML = '';
      const p = document.createElement('p');
      p.textContent = 'Published metadata to ' + result.published.toLocaleString()
        + ' of ' + result.total.toLocaleString() + ' photo' + (result.total === 1 ? '' : 's') + '.';
      done.append(p);
      const details = document.createElement('details');
      details.className = 'publish-failures';
      details.open = true;
      const summary = document.createElement('summary');
      summary.textContent = failed.length + ' photo' + (failed.length === 1 ? '' : 's') + ' failed';
      details.append(summary);
      const table = document.createElement('table');
      table.className = 'publish-table';
      table.innerHTML = '<thead><tr><th>File</th><th>Reason</th><th></th></tr></thead>';
      const tbody = document.createElement('tbody');
      failed.forEach(f => {
        const tr = document.createElement('tr');
        const pathTd = document.createElement('td');
        const link = document.createElement('button');
        link.className = 'reveal-link';
        link.textContent = f.path;
        link.title = 'Open containing folder';
        link.onclick = () => api('/api/reveal-path', { path: f.path }).catch(() => {});
        pathTd.append(link);
        const reasonTd = document.createElement('td');
        reasonTd.textContent = f.reason;
        const actionTd = document.createElement('td');
        const ext = (f.path.match(/\.[^.]+$/) || [''])[0].toLowerCase();
        if (['.jpg', '.jpeg', '.heic', '.heif'].includes(ext)) {
          const btn = document.createElement('button');
          btn.className = 'repair-btn';
          btn.textContent = 'Repair';
          btn.title = 'Re-save through Pillow to fix corrupt/truncated image data (backup created first)';
          btn.onclick = async () => {
            btn.disabled = true;
            btn.textContent = 'Repairing…';
            try {
              await api('/api/publish/repair', { path: f.path });
              btn.textContent = 'Repaired';
              btn.classList.add('repaired');
              reasonTd.textContent = 'Repaired — re-publish to write metadata';
              reasonTd.style.color = 'var(--accent)';
            } catch (e) {
              btn.textContent = 'Failed';
              btn.disabled = false;
              btn.title = e.message;
            }
          };
          actionTd.append(btn);
        }
        tr.append(pathTd, reasonTd, actionTd);
        tbody.append(tr);
      });
      table.append(tbody);
      details.append(table);
      done.append(details);
      $('globalProgress').textContent = failed.length + ' failed';
    }
  } catch (e) {
    clearInterval(pollId);
    $('progressText').textContent = 'Error: ' + e.message;
    $('publishAll').disabled = false;
    $('publishActions').hidden = false;
    $('globalProgress').textContent = 'Publish failed';
  }
};

// Menu, about, and theme (shared pattern)
function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}
function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
$('menuToggle').onclick = e => { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', e => { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};
document.querySelector('.theme-toggle')?.addEventListener('click', () => {
  const html = document.documentElement;
  const current = html.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  try { localStorage.setItem('theme', next); } catch {}
});

if ($('classifyPhotos')) {
  $('classifyPhotos').onclick = async () => {
    $('classifyPhotos').disabled = true;
    const status = $('classifyStatus');
    status.hidden = false;
    status.textContent = 'Starting classification…';
    try {
      await api('/api/classify/start', {});
      const poll = setInterval(async () => {
        try {
          const r = await fetch('/api/classify/status');
          const s = await r.json();
          status.textContent = s.message || s.state;
          if (s.state !== 'running') {
            clearInterval(poll);
            $('classifyPhotos').disabled = false;
          }
        } catch {}
      }, 1000);
    } catch (e) {
      status.textContent = 'Error: ' + e.message;
      $('classifyPhotos').disabled = false;
    }
  };
}

function fileReport(title, className, items) {
  if (!items || !items.length) return null;
  const box = document.createElement('div');
  box.className = 'write-tags-group ' + className;
  const heading = document.createElement('h4');
  heading.textContent = items.length === 1 ? title.replace('photos', 'photo') : title;
  heading.textContent = heading.textContent.replace('{n}', items.length.toLocaleString());
  box.append(heading);
  const list = document.createElement('ul');
  items.slice(0, 50).forEach(item => {
    const row = document.createElement('li');
    const name = document.createElement('strong');
    name.textContent = item.path || ('photo ' + item.asset_id);
    const why = document.createElement('span');
    why.textContent = ' — ' + (item.error || item.reason || 'no reason recorded');
    row.append(name, why);
    list.append(row);
  });
  box.append(list);
  if (items.length > 50) {
    const more = document.createElement('p');
    more.className = 'write-tags-more';
    more.textContent = (items.length - 50).toLocaleString() + ' more not listed.';
    box.append(more);
  }
  return box;
}

function renderWriteTagsJob(job) {
  const status = $('writeTagsStatus');
  const report = $('writeTagsReport');
  const barWrap = $('writeTagsBarWrap');
  const running = job.state === 'running';

  status.hidden = false;
  status.textContent = job.message || job.state || '';
  $('writeAllTags').disabled = running;
  $('cancelWriteTags').hidden = !running;

  if (running && job.total) {
    barWrap.hidden = false;
    $('writeTagsBar').style.width =
      Math.min(100, Math.round(((job.done || 0) / job.total) * 100)) + '%';
  } else {
    barWrap.hidden = true;
  }

  const groups = [
    fileReport('{n} photos could not be written', 'write-tags-failed', job.failed),
    fileReport('{n} photos got a sidecar file instead', 'write-tags-fallback', job.fell_back),
    fileReport('{n} photos could not carry everything', 'write-tags-partial', job.incomplete),
  ].filter(Boolean);

  if (!groups.length) {
    report.hidden = true;
    report.replaceChildren();
    return;
  }
  report.hidden = false;
  report.replaceChildren(...groups);
}

let writeTagsPoll = null;

async function pollWriteTags() {
  try {
    const job = await fetch('/api/write-tags/status').then(r => r.json());
    renderWriteTagsJob(job);
    if (job.state !== 'running' && writeTagsPoll) {
      clearInterval(writeTagsPoll);
      writeTagsPoll = null;
    }
  } catch {}
}

if ($('writeAllTags')) {
  $('writeAllTags').onclick = async () => {
    $('writeAllTags').disabled = true;
    const status = $('writeTagsStatus');
    status.hidden = false;
    status.textContent = 'Starting…';
    $('writeTagsReport').hidden = true;
    try {
      await api('/api/write-tags/batch', { scope: 'all' });
      if (!writeTagsPoll) writeTagsPoll = setInterval(pollWriteTags, 1000);
      pollWriteTags();
    } catch (e) {
      status.textContent = 'Error: ' + e.message;
      $('writeAllTags').disabled = false;
    }
  };
}

if ($('cancelWriteTags')) {
  $('cancelWriteTags').onclick = async () => {
    $('cancelWriteTags').disabled = true;
    try { await api('/api/write-tags/cancel', {}); }
    catch (e) { $('writeTagsStatus').textContent = 'Error: ' + e.message; }
    $('cancelWriteTags').disabled = false;
  };
}

// Pick a run back up if the page was reloaded while it was working.
if ($('writeAllTags')) {
  fetch('/api/write-tags/status').then(r => r.json()).then(job => {
    if (job.state === 'running') {
      renderWriteTagsJob(job);
      if (!writeTagsPoll) writeTagsPoll = setInterval(pollWriteTags, 1000);
    }
  }).catch(() => {});
}

loadPending();
