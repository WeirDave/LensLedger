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
  try {
    const result = await api('/api/publish/run', {});
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
      let html = '<p>Published metadata to ' + result.published.toLocaleString()
        + ' of ' + result.total.toLocaleString() + ' photo' + (result.total === 1 ? '' : 's') + '.</p>'
        + '<details class="publish-failures"><summary>' + failed.length
        + ' photo' + (failed.length === 1 ? '' : 's') + ' failed</summary>'
        + '<table class="publish-table"><thead><tr><th>File</th><th>Reason</th></tr></thead><tbody>';
      failed.forEach(f => {
        const pathEl = document.createElement('td');
        pathEl.textContent = f.path;
        const reasonEl = document.createElement('td');
        reasonEl.textContent = f.reason;
        const tr = document.createElement('tr');
        tr.append(pathEl, reasonEl);
        html += tr.outerHTML;
      });
      html += '</tbody></table></details>';
      done.innerHTML = html;
      $('globalProgress').textContent = failed.length + ' failed';
    }
  } catch (e) {
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

loadPending();
