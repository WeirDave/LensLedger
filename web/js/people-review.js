const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const initialPersonId = LL.initialPersonId;
const maxVisible = 100;
let queue = null;
let batch = [];
let rejected = new Set();
let skipped = new Set();
let corrections = new Map();
let dispositions = new Map();
let history = [];
let knownPeople = [];
let autoLearnDone = false;
const $ = id => document.getElementById(id);

function registerKnownPerson(name) {
  if (knownPeople.some(existing => existing.toLowerCase() === name.toLowerCase())) return;
  knownPeople.push(name);
  knownPeople.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

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

function availableSuggestions() {
  return (queue?.suggestions || []).filter(item => !skipped.has(queue.person.id + ':' + item.id));
}

function showDoneState() {
  const faces = queue?.unidentified_faces || 0;
  $('globalProgress').textContent = faces ? faces.toLocaleString() + ' unidentified face' + (faces === 1 ? '' : 's') : 'No suggestions remaining';
  if (faces) {
    $('reviewArea').innerHTML = '<div class="empty"><div><h2>People review complete</h2>'
      + '<p>' + faces.toLocaleString() + ' unidentified face' + (faces === 1 ? ' remains' : 's remain')
      + ' — name a few more to generate new suggestions.</p>'
      + '<a class="button primary-action" href="/faces-review">Name faces (' + faces.toLocaleString() + ')</a></div></div>';
  } else {
    $('reviewArea').innerHTML = '<div class="empty"><div><h2>People review complete</h2><p>There are no face suggestions waiting for review.</p><a class="button" href="/">Return to the photo library</a></div></div>';
  }
}

async function loadQueue(personId = null, advance = false) {
  let url = '/api/people/review/queue';
  const query = new URLSearchParams();
  if (personId) query.set('person_id', personId);
  if (advance) query.set('advance', '1');
  if (query.size) url += '?' + query;
  const response = await fetch(url);
  queue = await response.json();
  if (!response.ok) throw new Error(queue.error || 'Could not load People review');
  render();
}

function render() {
  rejected.clear();
  corrections.clear();
  dispositions.clear();
  $('confirmBatch').disabled = false;
  if (!queue?.person) {
    $('actionbar').hidden = true;
    if (!autoLearnDone) {
      autoLearnDone = true;
      autoLearnOnEmpty();
      return;
    }
    showDoneState();
    return;
  }
  const available = availableSuggestions();
  if (!available.length) {
    loadQueue(queue.person.id, true).catch(showError);
    return;
  }
  batch = available.slice(0, maxVisible);
  let progressText = queue.remaining_total.toLocaleString() + ' photos · ' + queue.people_remaining.toLocaleString() + ' people remaining';
  if (queue.unidentified_faces) progressText += ' · ' + queue.unidentified_faces.toLocaleString() + ' unnamed face' + (queue.unidentified_faces === 1 ? '' : 's');
  $('globalProgress').textContent = progressText;
  knownPeople = queue.people_options;
  const section = document.createElement('section');
  section.innerHTML = '<div class="review-head"><div><h1></h1><p>Click a face to mark it wrong. Use ⛶ Enlarge to see the full photo or set corrections.</p></div><div class="person-count"></div></div><div class="thumb-grid"></div>';
  section.querySelector('h1').textContent = 'Does this photo contain ' + queue.person.name + '?';
  const countText = batch.length + (available.length > maxVisible ? ' of ' + available.length : '') + ' suggestion' + (batch.length === 1 ? '' : 's') + ' for ' + queue.person.name;
  section.querySelector('.person-count').textContent = countText;
  const grid = section.querySelector('.thumb-grid');
  batch.forEach(item => grid.append(buildCard(item)));
  $('reviewArea').replaceChildren(section);
  $('actionbar').hidden = false;
  $('undoBatch').disabled = !history.length;
  $('status').textContent = '';
  updateSummary();
}

function buildCard(item) {
  const card = document.createElement('div');
  card.className = 'review-thumb';
  card.dataset.id = item.id;
  const photo = document.createElement('div');
  photo.className = 'thumb-photo';
  const img = document.createElement('img');
  img.loading = 'lazy';
  img.alt = 'Suggested face';
  const canCrop = item.face_id && hasFaceBox(item);
  img.src = canCrop ? '/media-face?face_id=' + item.face_id : '/media?id=' + item.id;
  if (canCrop) img.onerror = () => { img.onerror = null; img.src = '/media?id=' + item.id; };
  const badge = document.createElement('span');
  badge.className = 'state-badge';
  badge.textContent = '✓';
  const expand = document.createElement('button');
  expand.type = 'button';
  expand.className = 'expand';
  expand.title = 'Show the full photo larger';
  expand.textContent = '⛶ Enlarge';
  expand.onclick = e => { e.stopPropagation(); openLarge(item); };
  const trash = document.createElement('button');
  trash.type = 'button';
  trash.className = 'trash';
  trash.title = 'Move to Trash';
  trash.innerHTML = '<svg viewBox="0 0 16 16"><path d="M5.5 0h5a.5.5 0 0 1 .5.5V2h4v2h-1l-1 11.5a.5.5 0 0 1-.5.5h-9a.5.5 0 0 1-.5-.5L2 4H1V2h4V.5a.5.5 0 0 1 .5-.5zM6 1v1h4V1H6zm-2.9 3 .9 11h8l.9-11H3.1zM5.5 5v8h1V5h-1zm2 0v8h1V5h-1zm2 0v8h1V5h-1z"/></svg>';
  trash.onclick = e => { e.stopPropagation(); trashPhoto(card, item); };
  photo.append(img, badge, expand, trash);
  card.append(photo);
  card.onclick = () => toggleCard(card, item);
  img.ondblclick = e => { e.stopPropagation(); api('/api/reveal-file', { id: item.id }); };
  return card;
}

function markWrong(card, item) {
  if (rejected.has(item.id)) return;
  rejected.add(item.id);
  card.classList.add('wrong');
  card.querySelector('.state-badge').textContent = '✗';
}

function toggleCard(card, item) {
  if (rejected.has(item.id)) {
    rejected.delete(item.id);
    corrections.delete(item.id);
    dispositions.delete(item.id);
    card.classList.remove('wrong');
    card.querySelector('.state-badge').textContent = '✓';
  } else {
    markWrong(card, item);
  }
  updateSummary();
}

function updateSummary() {
  const wrong = rejected.size;
  const matches = batch.length - wrong;
  $('selectionSummary').textContent = matches + ' confirmed' + (wrong ? ' · ' + wrong + ' rejected' : '');
  $('confirmBatch').textContent = 'Save & publish ' + batch.length + ' decision' + (batch.length === 1 ? '' : 's');
}

let openItem = null;
let lightboxPicker = null;

function initLightboxPicker() {
  const container = $('lightboxPicker');
  if (!container || lightboxPicker) return;
  lightboxPicker = createPersonPicker({
    container,
    getNames: () => knownPeople,
    placeholder: 'Correct name',
    onChoose: name => {
      if (!openItem) return;
      registerKnownPerson(name);
      corrections.set(openItem.id, name);
      dispositions.delete(openItem.id);
      const card = document.querySelector('.review-thumb[data-id="' + openItem.id + '"]');
      if (card) markWrong(card, openItem);
      updateSummary();
      updateLightboxState();
    },
  });
}

function hasFaceBox(item) {
  return [item.box_left, item.box_top, item.box_right, item.box_bottom].every(v => Number.isFinite(v));
}

function markFace(container, img, item) {
  container.querySelectorAll('.face-box,.location-note').forEach(node => node.remove());
  if (!hasFaceBox(item)) {
    const note = document.createElement('span');
    note.className = 'location-note';
    note.textContent = 'Exact face location not recovered yet';
    container.append(note);
    return;
  }
  const marker = document.createElement('div');
  marker.className = 'face-box';
  marker.innerHTML = '<span>Face being checked</span>';
  container.append(marker);
  const position = () => {
    if (!img.naturalWidth || !img.naturalHeight) return;
    const scale = Math.min(img.clientWidth / img.naturalWidth, img.clientHeight / img.naturalHeight);
    const shownWidth = img.naturalWidth * scale, shownHeight = img.naturalHeight * scale;
    const offsetX = (img.clientWidth - shownWidth) / 2, offsetY = (img.clientHeight - shownHeight) / 2;
    const left = offsetX + item.box_left * shownWidth, top = offsetY + item.box_top * shownHeight;
    marker.style.left = left + 'px';
    marker.style.top = top + 'px';
    marker.style.width = ((item.box_right - item.box_left) * shownWidth) + 'px';
    marker.style.height = ((item.box_bottom - item.box_top) * shownHeight) + 'px';
    marker.classList.toggle('label-right', left + 130 > container.clientWidth);
    marker.classList.toggle('label-below', top < 34);
  };
  img.addEventListener('load', position, { once: true });
  if (img.complete) position();
  new ResizeObserver(position).observe(container);
}

function openLarge(item) {
  openItem = item;
  $('largePhoto').src = '/media?id=' + item.id;
  markFace($('largePhotoBox'), $('largePhoto'), item);
  const info = $('lightboxInfo');
  info.innerHTML = '';
  const strong = document.createElement('strong');
  strong.textContent = item.filename;
  const small = document.createElement('small');
  small.textContent = (item.capture_date || 'Date unknown') + ' · ' + item.folder;
  info.append(strong, small);
  initLightboxPicker();
  if (lightboxPicker) lightboxPicker.reset();
  updateLightboxState();
  $('lightbox').classList.add('open');
}

function updateLightboxState() {
  if (!openItem) return;
  const isWrong = rejected.has(openItem.id);
  $('lightboxToggle').textContent = isWrong ? 'Mark as correct' : 'Mark as wrong';
  $('lightboxCorrectionArea').hidden = !isWrong;
}

function closeLarge() {
  openItem = null;
  $('lightbox').classList.remove('open');
  $('largePhoto').removeAttribute('src');
  $('largePhotoBox').querySelectorAll('.face-box,.location-note').forEach(node => node.remove());
}

function setDispositionFromLightbox(value) {
  if (!openItem) return;
  const card = document.querySelector('.review-thumb[data-id="' + openItem.id + '"]');
  if (card) {
    markWrong(card, openItem);
    dispositions.set(openItem.id, value);
    corrections.delete(openItem.id);
    if (lightboxPicker) lightboxPicker.reset();
    updateSummary();
  }
  updateLightboxState();
}

async function submitBatch() {
  const button = $('confirmBatch');
  button.disabled = true;
  $('savingOverlay').classList.add('active');
  try {
    const decisions = batch.map(item => {
      const disposition = dispositions.get(item.id);
      const corrected = (corrections.get(item.id) || '').trim();
      let action = 'confirmed';
      if (rejected.has(item.id)) action = disposition || (corrected ? 'corrected' : 'rejected');
      return { asset_id: item.id, action, corrected_name: disposition ? '' : corrected };
    });
    const result = await api('/api/people/review/batch', { person_id: queue.person.id, decisions });
    history.push({ person_id: queue.person.id, action_ids: result.action_ids });
    batch.forEach(item => skipped.delete(queue.person.id + ':' + item.id));
    await loadQueue(queue.person.id);
  } catch (error) {
    showError(error);
    button.disabled = false;
  } finally {
    $('savingOverlay').classList.remove('active');
  }
}

function skipBatch() {
  batch.forEach(item => skipped.add(queue.person.id + ':' + item.id));
  render();
}

async function undoBatch() {
  const prior = history.pop();
  if (!prior) return;
  $('undoBatch').disabled = true;
  try {
    await api('/api/people/review/batch-undo', { action_ids: prior.action_ids });
    await loadQueue(prior.person_id);
  } catch (error) {
    history.push(prior);
    showError(error);
    $('undoBatch').disabled = false;
  }
}

function showError(error) {
  $('status').textContent = error.message || String(error);
}

async function autoLearnOnEmpty() {
  $('reviewArea').innerHTML = '<div class="empty"><div><div class="saving-spinner"></div><h2>Checking for more matches…</h2><p>Analyzing confirmed faces to find new suggestions. This may take a moment with large libraries.</p></div></div>';
  $('globalProgress').textContent = 'Learning from confirmed faces…';
  try {
    const result = await api('/api/people/learn', {});
    const autoCount = result.auto_confirmed || 0;
    const sugCount = result.suggestions || 0;
    if (autoCount || sugCount) {
      const parts = [];
      if (sugCount) parts.push(sugCount.toLocaleString() + ' new suggestion' + (sugCount === 1 ? '' : 's'));
      if (autoCount) parts.push(autoCount.toLocaleString() + ' auto-confirmed');
      $('globalProgress').textContent = 'Found ' + parts.join(', ') + ' — loading…';
    }
    skipped.clear();
    history = [];
    await loadQueue();
    if (queue?.person) return;
    $('globalProgress').textContent = 'No suggestions remaining';
  } catch (error) {
    $('globalProgress').textContent = error.message || String(error);
  }
  showDoneState();
}

async function deferPerson() {
  if (!queue?.person) return;
  const person = queue.person;
  try {
    await api('/api/people/review/defer', { person_id: person.id, days: 7 });
    skipped.clear();
    await loadQueue(person.id, true);
    $('globalProgress').textContent = person.name + ' deferred for 7 days';
  } catch (error) {
    showError(error);
  }
}

async function trashPhoto(card, item) {
  if (!confirm('Move "' + item.filename + '" to Trash?\n\nIt will leave the photo library and disappear from search.')) return;
  try {
    const result = await api('/api/review-bin', { id: item.id });
    card.remove();
    const i = batch.indexOf(item);
    if (i >= 0) batch.splice(i, 1);
    rejected.delete(item.id);
    corrections.delete(item.id);
    dispositions.delete(item.id);
    updateSummary();
    showTrashUndo(result.review_id, item.filename);
  } catch (error) { showError(error); }
}

function showTrashUndo(reviewId, name) {
  const t = $('toast');
  t.replaceChildren(document.createTextNode('Moved ' + name + ' to Trash. '));
  const b = document.createElement('button');
  b.textContent = 'Undo';
  b.onclick = async () => { try { await api('/api/review-bin/restore', { review_id: reviewId }); location.reload(); } catch (e) { showError(e); } };
  t.append(b);
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 12000);
}

$('confirmBatch').onclick = submitBatch;
$('skipBatch').onclick = skipBatch;
$('nextPerson').onclick = () => loadQueue(queue?.person?.id, true).catch(showError);
$('deferPerson').onclick = deferPerson;
$('undoBatch').onclick = undoBatch;
$('closeLightbox').onclick = closeLarge;
$('lightboxToggle').onclick = () => {
  if (!openItem) return;
  const card = document.querySelector('.review-thumb[data-id="' + openItem.id + '"]');
  if (card) toggleCard(card, openItem);
  updateLightboxState();
};
$('lightboxNotAPerson').onclick = () => setDispositionFromLightbox('not_a_person');
$('lightboxUnknownPerson').onclick = () => setDispositionFromLightbox('unknown_person');
$('lightbox').onclick = event => { if (event.target === $('lightbox')) closeLarge(); };
function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};
$('menuToggle').onclick = e => { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', e => { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeLarge(); closeMenu(); } });
loadQueue(initialPersonId).catch(error => {
  $('reviewArea').innerHTML = '<div class="empty"><div><h2>People review could not open</h2><p></p></div></div>';
  $('reviewArea').querySelector('p').textContent = error.message;
});

function checkServerVersion(){
  fetch('/api/version',{cache:'no-store'}).then(r=>r.json()).then(info=>{
    const existing=document.getElementById('staleBanner');
    if(!info.restartReady){if(existing)existing.remove();return}
    if(existing)return;
    const b=document.createElement('div');b.id='staleBanner';b.className='stale-banner';
    b.innerHTML='<span class="stale-icon">⚠</span>'
      +'<span class="stale-msg"><b>Server is running v'+info.version+'</b> but v'+info.onDiskVersion+' is on disk. Click <b>Restart</b> to load it.</span>'
      +'<button type="button" class="stale-restart">Restart now</button>'
      +'<button type="button" class="stale-close" title="Dismiss">×</button>';
    b.querySelector('.stale-close').onclick=()=>b.remove();
    b.querySelector('.stale-restart').onclick=()=>{
      const btn=b.querySelector('.stale-restart');const msg=b.querySelector('.stale-msg');
      btn.disabled=true;btn.textContent='Restarting…';
      msg.innerHTML='<b>Restarting server…</b> Page will reload when the new version is ready.';
      const oldStarted=info.startedAt;
      api('/api/update/restart-source',{}).catch(()=>{});
      const deadline=Date.now()+20000;
      (function poll(){
        if(Date.now()>deadline){msg.innerHTML='<b>Server did not restart.</b> Close and reopen LensLedger manually.';return}
        fetch('/api/version',{cache:'no-store'}).then(r=>r.json()).then(j=>{
          if(j&&j.startedAt&&j.startedAt!==oldStarted)setTimeout(()=>location.reload(),200);
          else setTimeout(poll,500);
        }).catch(()=>setTimeout(poll,700));
      })();
    };
    document.body.prepend(b);
  }).catch(()=>{});
}
checkServerVersion();setInterval(checkServerVersion,30000);
