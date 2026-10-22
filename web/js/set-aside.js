const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const $ = id => document.getElementById(id);
const PAGE_SIZE = 60;
const KIND_LABEL = { unknown: 'Unknown person', not_a_person: 'Not a person' };
let kind = 'unknown';
let shown = 0;
let total = 0;
let loading = false;
let knownPeople = [];
let knownAliasMap = null;
const selected = new Set();

async function api(path, data) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...data, csrf }),
  });
  if (response.status === 403) { location.reload(); return; }
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(value.error || 'Request failed');
    error.busy = value.busy === true;
    throw error;
  }
  return value;
}

async function apiRetry(path, data) {
  try {
    return await api(path, data);
  } catch (error) {
    if (!error.busy) throw error;
    await new Promise(resolve => setTimeout(resolve, 3000));
    return await api(path, data);
  }
}

function registerKnownPerson(name) {
  if (knownPeople.some(existing => existing.toLowerCase() === name.toLowerCase())) return;
  knownPeople.push(name);
  knownPeople.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

function updateCounts(counts) {
  $('countUnknown').textContent = counts.unknown.toLocaleString();
  $('countNotPerson').textContent = counts.not_a_person.toLocaleString();
}

function updateProgress() {
  const label = KIND_LABEL[kind].toLowerCase();
  $('globalProgress').textContent = total.toLocaleString() + ' marked ' + label;
  $('loadMore').hidden = shown >= total;
}

function checkEmpty() {
  const empty = $('faceGrid').children.length === 0 && !loading;
  $('emptyState').hidden = !empty;
  if (!empty) return;
  $('emptyHeading').textContent = 'No faces marked "' + KIND_LABEL[kind] + '"';
  $('emptyText').textContent = kind === 'unknown'
    ? 'Faces you mark as an unknown person will be listed here.'
    : 'Faces you mark as not a person will be listed here.';
}

function removeCard(card) {
  selected.delete(Number(card.dataset.faceId));
  card.remove();
  shown = Math.max(0, shown - 1);
  total = Math.max(0, total - 1);
  const countEl = kind === 'unknown' ? $('countUnknown') : $('countNotPerson');
  countEl.textContent = total.toLocaleString();
  updateProgress();
  updateBulkCount();
  checkEmpty();
}

let openFace = null;
let openCard = null;
const lbZoom = initLightboxZoom('largePhotoBox');
if (lbZoom) {
  const resetBtn = document.querySelector('#largePhotoBox .lb-zoom-reset');
  if (resetBtn) resetBtn.onclick = function() { lbZoom.reset(); lbZoom.repositionFaceBoxes(); };
}

function markFace(container, img, face) {
  container.querySelectorAll('.face-box,.location-note').forEach(node => node.remove());
  const marker = document.createElement('div');
  marker.className = 'face-box';
  marker.innerHTML = '<span>This face</span>';
  marker._faceData = face;
  container.append(marker);
  const position = () => {
    if (!img.naturalWidth || !img.naturalHeight) return;
    const scale = Math.min(img.clientWidth / img.naturalWidth, img.clientHeight / img.naturalHeight);
    const shownWidth = img.naturalWidth * scale, shownHeight = img.naturalHeight * scale;
    const offsetX = (img.clientWidth - shownWidth) / 2, offsetY = (img.clientHeight - shownHeight) / 2;
    const left = offsetX + face.box_left * shownWidth, top = offsetY + face.box_top * shownHeight;
    placeFaceBox(marker, left, top, (face.box_right - face.box_left) * shownWidth, (face.box_bottom - face.box_top) * shownHeight);
    marker.classList.toggle('label-right', left + 90 > container.clientWidth);
    marker.classList.toggle('label-below', top < 34);
  };
  img.addEventListener('load', position, { once: true });
  if (img.complete) position();
  new ResizeObserver(position).observe(container);
}

function openLarge(face, card) {
  openFace = face;
  openCard = card;
  const photo = $('largePhoto');
  photo.src = '/media?id=' + face.asset_id;
  if (lbZoom) lbZoom.attach(photo);
  markFace($('largePhotoBox'), photo, face);
  $('lightboxPathText').textContent = face.relative_path;
  $('lightboxPathText').title = face.relative_path;
  lbPicker.reset();
  $('lightbox').classList.add('open');
}

function closeLarge() {
  openFace = null;
  openCard = null;
  lbPicker.close();
  if (lbZoom) lbZoom.reset();
  $('lightbox').classList.remove('open');
  $('largePhoto').removeAttribute('src');
  $('largePhotoBox').querySelectorAll('.face-box,.location-note').forEach(node => node.remove());
}

function setBusy(card, busy) {
  card.querySelectorAll('button').forEach(b => { b.disabled = busy; });
}

async function nameFace(face, card, name) {
  const status = card.querySelector('.face-status');
  setBusy(card, true);
  status.textContent = 'Saving ' + name + '…';
  try {
    registerKnownPerson(name);
    await apiRetry('/api/faces/name', { face_id: face.face_id, name });
    removeCard(card);
  } catch (error) {
    status.textContent = error.message;
    setBusy(card, false);
  }
}

async function restoreFaces(ids, cards) {
  await apiRetry('/api/faces/restore', { face_ids: ids });
  cards.forEach(removeCard);
}

function buildCard(face) {
  const card = document.createElement('article');
  card.className = 'face-card';
  card.dataset.faceId = face.face_id;
  card.innerHTML = '<div class="bulk-check"></div><div class="face-photo"><img loading="lazy" alt="Marked face"><button type="button" class="expand" title="Show the full photo larger">⛶ Enlarge</button></div>'
    + '<div class="face-info"><small></small>'
    + '<div class="face-form"><div class="face-picker"></div></div>'
    + '<div class="face-actions"><button type="button" class="restore">Put back for review</button></div>'
    + '<div class="face-status"></div></div>';
  const img = card.querySelector('img');
  img.src = '/media-face?face_id=' + face.face_id;
  img.ondblclick = () => api('/api/reveal-file', { id: face.asset_id });
  card.querySelector('.expand').onclick = () => openLarge(face, card);
  const folderParts = (face.folder || '').split(/[/\\]/).filter(Boolean);
  const sep = (face.folder || '').includes('\\') ? '\\' : '/';
  const small = card.querySelector('small');
  small.textContent = folderParts.length
    ? '…' + sep + folderParts[folderParts.length - 1] + sep + face.filename
    : face.filename;
  small.title = (face.capture_date || 'Date unknown') + ' · ' + face.relative_path;
  createPersonPicker({
    container: card.querySelector('.face-picker'),
    getNames: () => knownPeople,
    getAliasMap: () => knownAliasMap,
    placeholder: 'Who is this?',
    onChoose: name => nameFace(face, card, name),
  });
  card.querySelector('.restore').onclick = async () => {
    const status = card.querySelector('.face-status');
    setBusy(card, true);
    status.textContent = 'Putting back…';
    try {
      await restoreFaces([face.face_id], [card]);
    } catch (error) {
      status.textContent = error.message;
      setBusy(card, false);
    }
  };
  return card;
}

async function load(reset) {
  if (loading) return;
  loading = true;
  const requested = kind;
  if (reset) {
    shown = 0;
    $('faceGrid').replaceChildren();
    selected.clear();
    updateBulkCount();
  }
  try {
    const data = await fetch('/api/faces/set-aside?kind=' + requested + '&limit=' + PAGE_SIZE + '&offset=' + shown).then(r => r.json());
    if (requested !== kind) return;
    total = data.total;
    updateCounts(data.counts);
    knownPeople = data.people_options;
    knownAliasMap = data.people_alias_map || null;
    const present = new Set([...$('faceGrid').children].map(el => el.dataset.faceId));
    data.faces.forEach(face => {
      if (!present.has(String(face.face_id))) $('faceGrid').append(buildCard(face));
    });
    shown += data.faces.length;
    updateProgress();
  } catch (error) {
    $('globalProgress').textContent = error.message;
  } finally {
    loading = false;
    checkEmpty();
  }
}

function switchKind(next) {
  if (next === kind) return;
  kind = next;
  $('tabUnknown').classList.toggle('active', kind === 'unknown');
  $('tabNotPerson').classList.toggle('active', kind === 'not_a_person');
  $('emptyState').hidden = true;
  load(true);
}

$('tabUnknown').onclick = () => switchKind('unknown');
$('tabNotPerson').onclick = () => switchKind('not_a_person');
$('loadMore').onclick = () => load(false);

const lbPicker = createPersonPicker({
  container: $('lightboxPicker'),
  getNames: () => knownPeople,
  getAliasMap: () => knownAliasMap,
  placeholder: 'Who is this?',
  onChoose: name => {
    if (!openFace || !openCard) return;
    const face = openFace, card = openCard;
    closeLarge();
    nameFace(face, card, name);
  },
});

$('closeLightbox').onclick = closeLarge;
$('lightbox').onclick = event => { if (event.target === $('lightbox')) closeLarge(); };
$('lightboxReveal').onclick = () => { if (openFace) api('/api/reveal-file', { id: openFace.asset_id }); };
$('lightboxRestore').onclick = () => {
  if (!openFace || !openCard) return;
  const face = openFace, card = openCard;
  closeLarge();
  card.querySelector('.restore').click();
};

// --- Bulk select mode ---
function updateBulkCount() {
  $('bulkCount').textContent = selected.size + ' selected';
  $('bulkRestore').disabled = selected.size === 0;
}

function setSelected(card, on) {
  const faceId = Number(card.dataset.faceId);
  if (on) selected.add(faceId); else selected.delete(faceId);
  card.classList.toggle('bulk-selected', on);
  card.querySelector('.bulk-check').textContent = on ? '✓' : '';
}

function clearSelection() {
  document.querySelectorAll('.face-card.bulk-selected').forEach(card => setSelected(card, false));
  selected.clear();
  updateBulkCount();
}

$('bulkSelectBtn').onclick = () => {
  const active = $('faceGrid').classList.toggle('bulk-active');
  $('bulkBar').hidden = !active;
  $('bulkSelectBtn').textContent = active ? 'Exit select' : 'Select';
  if (!active) clearSelection(); else updateBulkCount();
};

$('faceGrid').addEventListener('click', event => {
  if (!$('faceGrid').classList.contains('bulk-active')) return;
  const card = event.target.closest('.face-card');
  if (!card) return;
  event.preventDefault();
  event.stopPropagation();
  setSelected(card, !selected.has(Number(card.dataset.faceId)));
  updateBulkCount();
});

$('bulkClear').onclick = clearSelection;
$('bulkAll').onclick = () => {
  document.querySelectorAll('.face-card').forEach(card => setSelected(card, true));
  updateBulkCount();
};

const bulkPicker = createPersonPicker({
  container: $('bulkPicker'),
  getNames: () => knownPeople,
  getAliasMap: () => knownAliasMap,
  placeholder: 'Assign to…',
  onChoose: async name => {
    if (!selected.size) return;
    registerKnownPerson(name);
    const ids = [...selected];
    bulkPicker.reset();
    let done = 0;
    try {
      for (const faceId of ids) {
        $('bulkCount').textContent = 'Saving ' + (done + 1) + ' of ' + ids.length + '…';
        await apiRetry('/api/faces/name', { face_id: faceId, name });
        const card = $('faceGrid').querySelector(`[data-face-id="${faceId}"]`);
        if (card) removeCard(card);
        done += 1;
      }
    } catch (error) {
      $('bulkCount').textContent = error.message;
      return;
    }
    updateBulkCount();
  },
});

$('bulkRestore').onclick = async () => {
  if (!selected.size) return;
  const ids = [...selected];
  $('bulkRestore').disabled = true;
  try {
    const cards = ids.map(id => $('faceGrid').querySelector(`[data-face-id="${id}"]`)).filter(Boolean);
    await restoreFaces(ids, cards);
  } catch (error) {
    $('bulkCount').textContent = error.message;
  }
  updateBulkCount();
};

function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};
$('menuToggle').onclick = e => { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', e => { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeLarge(); closeMenu(); } });

load(true);
