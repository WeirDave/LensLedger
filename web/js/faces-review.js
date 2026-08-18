const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const $ = id => document.getElementById(id);
const PAGE_SIZE = 30;
let loading = false;
let remaining = 0;
// Face ids currently parked inside a match group (not resolved, just pulled
// out of the grid). loadMore() must skip these or the next top-up fetch
// re-adds the very card a match group just removed.
const pending = new Set();
let knownPeople = [];

// Optimistic local add so a name just typed in one card's "+ New person" is
// immediately selectable from every other open/future picker on this page,
// without waiting for the next loadMore() poll to refresh the list.
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
  if (!response.ok) {
    const error = new Error(value.error || 'Request failed');
    error.busy = value.busy === true;
    throw error;
  }
  return value;
}

// The server already blocks a write for up to 30s (SQLITE_BUSY_TIMEOUT_MS)
// before reporting the catalog busy, so a busy failure usually means a
// background scan is mid-write right this moment, not that it's stuck. One
// automatic retry after a short pause gives it a chance to land in the next
// gap between the scan's own commits, instead of making the user notice the
// error and click the same button again themselves.
async function apiRetry(path, data, onWaiting) {
  try {
    return await api(path, data);
  } catch (error) {
    if (!error.busy) throw error;
    if (onWaiting) onWaiting('A scan is using the catalog right now -- retrying in a few seconds…');
    await new Promise(resolve => setTimeout(resolve, 3000));
    return await api(path, data);
  }
}

function updateProgress() {
  $('globalProgress').textContent = remaining.toLocaleString() + ' unidentified face' + (remaining === 1 ? '' : 's');
}

let initialLoadDone = false;
function checkEmpty() {
  $('emptyState').hidden = !initialLoadDone || $('faceGrid').children.length > 0 || $('matchGroups').children.length > 0;
}

function removeCard(card) {
  card.remove();
  remaining = Math.max(0, remaining - 1);
  updateProgress();
  if ($('faceGrid').children.length < 12 && remaining > pending.size) loadMore();
  checkEmpty();
}

// Releases face ids back into the normal queue -- called on dismiss, and on
// any match that didn't end up confirmed -- and tops the grid back up.
function releasePending(ids) {
  ids.forEach(id => pending.delete(id));
  if ($('faceGrid').children.length < 12 && remaining > pending.size) loadMore();
  checkEmpty();
}

// After naming a face, other unidentified faces the backend judged similar
// (see _find_similar_unidentified_faces in photo_search.py) get grouped here
// with that name pre-filled, so confirming a repeat is one click instead of
// picking the same name from the dropdown over and over -- the behavior the
// user asked for, modeled on Google Photos' "is this also X?" grouping.
function addMatchGroup(name, matches) {
  const ids = matches.map(match => match.face_id);
  ids.forEach(id => pending.add(id));
  ids.forEach(id => {
    const existing = $('faceGrid').querySelector(`[data-face-id="${id}"]`);
    if (existing) existing.remove();
  });
  const group = document.createElement('div');
  group.className = 'match-group';
  group.innerHTML = '<div class="match-group-head"><strong></strong><span class="match-count"></span>'
    + '<span class="spacer"></span>'
    + '<button type="button" class="secondary dismiss">Not these</button>'
    + '<button type="button" class="confirm-all">Confirm all</button></div>'
    + '<div class="match-thumbs"></div><div class="match-status"></div>';
  group.querySelector('strong').textContent = 'Also looks like ' + name;
  group.querySelector('.match-count').textContent = matches.length + (matches.length === 1 ? ' photo' : ' photos');
  const thumbs = group.querySelector('.match-thumbs');
  matches.forEach(match => {
    const thumb = document.createElement('label');
    thumb.className = 'match-thumb';
    thumb.dataset.faceId = match.face_id;
    thumb.innerHTML = '<input type="checkbox" checked><img loading="lazy" alt="Possible match">';
    thumb.querySelector('img').src = '/media-face?face_id=' + match.face_id;
    thumbs.append(thumb);
  });
  const status = group.querySelector('.match-status');
  group.querySelector('.dismiss').onclick = () => { group.remove(); releasePending(ids); };
  group.querySelector('.confirm-all').onclick = async () => {
    const allThumbs = [...thumbs.querySelectorAll('.match-thumb')];
    const checked = allThumbs.filter(t => t.querySelector('input').checked && !t.classList.contains('failed'));
    const skipped = allThumbs.filter(t => !t.querySelector('input').checked).map(t => Number(t.dataset.faceId));
    if (!checked.length) { group.remove(); releasePending(skipped); return; }
    group.querySelectorAll('button').forEach(b => b.disabled = true);
    let done = 0, failed = 0;
    for (let i = 0; i < checked.length; i++) {
      const thumb = checked[i];
      thumb.classList.remove('failed');
      status.textContent = `Confirming ${i + 1} of ${checked.length}…`;
      try {
        await apiRetry('/api/faces/name', { face_id: Number(thumb.dataset.faceId), name },
          message => status.textContent = message);
        remaining = Math.max(0, remaining - 1);
        pending.delete(Number(thumb.dataset.faceId));
        thumb.remove();
        done += 1;
      } catch (error) {
        failed += 1;
        thumb.classList.add('failed');
        thumb.title = error.message;
      }
    }
    updateProgress();
    if (!failed) {
      status.textContent = `All ${done} confirmed.`;
      group.remove();
      releasePending(skipped);
    } else {
      status.textContent = `${done} confirmed, ${failed} failed -- the failed ones are outlined below; click "Confirm all" again to retry just those.`;
      group.querySelectorAll('button').forEach(b => b.disabled = false);
    }
  };
  $('matchGroups').prepend(group);
  if ($('faceGrid').children.length < 12 && remaining > pending.size) loadMore();
  checkEmpty();
}

let openFace = null;
let openCard = null;

// Ports people-review.js's markFace geometry (letterboxed object-fit:contain
// math) to draw a box on the full photo in the lightbox, given the face's
// own fractional box coordinates -- now included in /api/faces/unidentified
// specifically so this page can do the same "see it in context" the crop
// alone can't for a turned, blurry, or dark face.
function markFace(container, img, face) {
  container.querySelectorAll('.face-box,.location-note').forEach(node => node.remove());
  const hasBox = [face.box_left, face.box_top, face.box_right, face.box_bottom].every(Number.isFinite);
  if (!hasBox) {
    const note = document.createElement('span');
    note.className = 'location-note';
    note.textContent = 'Exact face location not recovered yet';
    container.append(note);
    return;
  }
  const marker = document.createElement('div');
  marker.className = 'face-box';
  marker.innerHTML = '<span>This face</span>';
  container.append(marker);
  const position = () => {
    if (!img.naturalWidth || !img.naturalHeight) return;
    const scale = Math.min(img.clientWidth / img.naturalWidth, img.clientHeight / img.naturalHeight);
    const shownWidth = img.naturalWidth * scale, shownHeight = img.naturalHeight * scale;
    const offsetX = (img.clientWidth - shownWidth) / 2, offsetY = (img.clientHeight - shownHeight) / 2;
    const left = offsetX + face.box_left * shownWidth, top = offsetY + face.box_top * shownHeight;
    marker.style.left = left + 'px';
    marker.style.top = top + 'px';
    marker.style.width = ((face.box_right - face.box_left) * shownWidth) + 'px';
    marker.style.height = ((face.box_bottom - face.box_top) * shownHeight) + 'px';
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
  $('largePhoto').src = '/media?id=' + face.asset_id;
  markFace($('largePhotoBox'), $('largePhoto'), face);
  $('lightbox').classList.add('open');
}

function closeLarge() {
  openFace = null;
  openCard = null;
  $('lightbox').classList.remove('open');
  $('largePhoto').removeAttribute('src');
  $('largePhotoBox').querySelectorAll('.face-box,.location-note').forEach(node => node.remove());
}

async function actOnOpenFace(endpoint) {
  if (!openFace) return;
  const face = openFace, card = openCard;
  closeLarge();
  if (card) { card.querySelector('.not-person').disabled = true; card.querySelector('.unknown-person').disabled = true; }
  try {
    await apiRetry(endpoint, { face_id: face.face_id });
    if (card) removeCard(card);
  } catch (error) {
    if (card) {
      card.querySelector('.face-status').textContent = error.message;
      card.querySelector('.not-person').disabled = false;
      card.querySelector('.unknown-person').disabled = false;
    }
  }
}

function buildCard(face) {
  const card = document.createElement('article');
  card.className = 'face-card';
  card.dataset.faceId = face.face_id;
  card.innerHTML = '<div class="face-photo"><img loading="lazy" alt="Detected face"><button type="button" class="expand" title="Show the full photo larger">⛶ Enlarge</button><button type="button" class="trash" title="Move to Trash">✕ Trash</button></div>'
    + '<div class="face-info"><small></small>'
    + '<div class="face-form"><div class="face-picker"></div></div>'
    + '<div class="face-actions"><button type="button" class="not-person">Not a person</button>'
    + '<button type="button" class="unknown-person">Unknown person</button></div>'
    + '<div class="face-status"></div></div>';
  const img = card.querySelector('img');
  img.src = '/media-face?face_id=' + face.face_id;
  img.ondblclick = () => api('/api/reveal-file', { id: face.asset_id });
  card.querySelector('.expand').onclick = () => openLarge(face, card);
  card.querySelector('.trash').onclick = () => trashPhoto(card, face);
  // The filename identifies which exact photo this is (folder + date alone
  // often don't, e.g. several faces from the same burst); show it first so
  // ellipsis truncation eats the folder path instead of the useful part.
  const folderParts = (face.folder || '').split('/').filter(Boolean);
  const shortPath = folderParts.length
    ? '…/' + folderParts[folderParts.length - 1] + '/' + face.filename
    : face.filename;
  const small = card.querySelector('small');
  small.textContent = shortPath;
  small.title = (face.capture_date || 'Date unknown') + ' · ' + (face.folder ? face.folder + '/' : '') + face.filename;
  const notPersonButton = card.querySelector('.not-person');
  const unknownButton = card.querySelector('.unknown-person');
  const status = card.querySelector('.face-status');
  const picker = createPersonPicker({
    container: card.querySelector('.face-picker'),
    getNames: () => knownPeople,
    placeholder: 'Who is this?',
    onChoose: async name => {
      notPersonButton.disabled = true; unknownButton.disabled = true;
      status.textContent = 'Saving…';
      try {
        registerKnownPerson(name);
        const result = await apiRetry('/api/faces/name', { face_id: face.face_id, name },
          message => status.textContent = message);
        removeCard(card);
        if (result.matches && result.matches.length) addMatchGroup(name, result.matches);
      } catch (error) {
        status.textContent = error.message;
        notPersonButton.disabled = false; unknownButton.disabled = false;
      }
    },
  });
  notPersonButton.onclick = async () => {
    picker.close(); notPersonButton.disabled = true; unknownButton.disabled = true;
    status.textContent = 'Marking…';
    try {
      await apiRetry('/api/faces/ignore', { face_id: face.face_id }, message => status.textContent = message);
      removeCard(card);
    } catch (error) {
      status.textContent = error.message;
      notPersonButton.disabled = false; unknownButton.disabled = false;
    }
  };
  unknownButton.onclick = async () => {
    picker.close(); notPersonButton.disabled = true; unknownButton.disabled = true;
    status.textContent = 'Marking…';
    try {
      await apiRetry('/api/faces/unknown', { face_id: face.face_id }, message => status.textContent = message);
      removeCard(card);
    } catch (error) {
      status.textContent = error.message;
      notPersonButton.disabled = false; unknownButton.disabled = false;
    }
  };
  return card;
}

async function loadMore() {
  if (loading) return;
  loading = true;
  try {
    const data = await fetch('/api/faces/unidentified?limit=' + PAGE_SIZE).then(r => r.json());
    remaining = data.total;
    updateProgress();
    knownPeople = data.people_options;
    const existing = new Set([...$('faceGrid').children].map(el => el.dataset.faceId));
    data.faces
      .filter(face => !existing.has(String(face.face_id)) && !pending.has(face.face_id))
      .forEach(face => $('faceGrid').append(buildCard(face)));
    checkEmpty();
  } catch (error) {
    $('globalProgress').textContent = error.message;
  } finally {
    loading = false;
    initialLoadDone = true;
    $('loadingOverlay').classList.add('done');
    document.querySelector('.intro').hidden = false;
    $('matchGroups').hidden = false;
    $('faceGrid').hidden = false;
    checkEmpty();
  }
}

$('findMatches').onclick = async () => {
  $('findMatches').disabled = true;
  const previous = $('globalProgress').textContent;
  $('globalProgress').textContent = 'Learning from confirmed faces…';
  try {
    const result = await api('/api/people/learn', {});
    $('globalProgress').textContent = result.auto_confirmed || result.suggestions
      ? (result.auto_confirmed ? result.auto_confirmed + ' near-certain match' + (result.auto_confirmed === 1 ? '' : 'es') + ' confirmed automatically' : '')
        + (result.auto_confirmed && result.suggestions ? '; ' : '')
        + (result.suggestions ? result.suggestions + ' new suggestion' + (result.suggestions === 1 ? '' : 's') + ' ready in People review' : '')
      : 'No additional strong matches found yet';
  } catch (error) {
    $('globalProgress').textContent = error.message;
  } finally {
    $('findMatches').disabled = false;
    setTimeout(updateProgress, 4000);
  }
};

async function trashPhoto(card, face) {
  if (!confirm('Move "' + face.filename + '" to Trash?\n\nIt will leave the photo library and disappear from search.')) return;
  try {
    const result = await api('/api/review-bin', { id: face.asset_id });
    card.remove();
    showTrashUndo(result.review_id, face.filename);
  } catch (error) { alert(error.message); }
}

function showTrashUndo(reviewId, name) {
  const t = $('toast');
  t.replaceChildren(document.createTextNode('Moved ' + name + ' to Trash. '));
  const b = document.createElement('button');
  b.textContent = 'Undo';
  b.onclick = async () => { try { await api('/api/review-bin/restore', { review_id: reviewId }); location.reload(); } catch (e) { alert(e.message); } };
  t.append(b);
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 12000);
}

$('closeLightbox').onclick = closeLarge;
$('lightboxNotAPerson').onclick = () => actOnOpenFace('/api/faces/ignore');
$('lightboxUnknownPerson').onclick = () => actOnOpenFace('/api/faces/unknown');
$('lightbox').onclick = event => { if (event.target === $('lightbox')) closeLarge(); };
function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about')alert('LensLedger v'+LL.appVersion+'\n'+LL.appTagline+'\n\nLocal-first photo and video indexing, search, people review, and safe metadata publishing for Windows.')});
$('menuToggle').onclick = e => { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', e => { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeLarge(); closeMenu(); } });

loadMore();
