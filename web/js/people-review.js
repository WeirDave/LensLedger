const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const initialPersonId = LL.initialPersonId;
const batchSize = 8;
let queue = null;
let batch = [];
let rejected = new Set();
let skipped = new Set();
let corrections = new Map();
let dispositions = new Map();
const DISPOSITION_LABELS = { not_a_person: 'Not a person', unknown_person: 'Unknown person' };
let history = [];
let knownPeople = [];
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
    $('reviewArea').innerHTML = '<div class="empty"><div><h2>People review complete</h2><p>There are no face suggestions waiting for review.</p><a class="button" href="/">Return to the photo library</a></div></div>';
    $('actionbar').hidden = true;
    $('globalProgress').textContent = 'No suggestions remaining';
    return;
  }
  const available = availableSuggestions();
  if (!available.length) {
    loadQueue(queue.person.id, true).catch(showError);
    return;
  }
  batch = available.slice(0, batchSize);
  $('globalProgress').textContent = queue.remaining_total.toLocaleString() + ' photos · ' + queue.people_remaining.toLocaleString() + ' people remaining';
  knownPeople = queue.people_options;
  const section = document.createElement('section');
  section.innerHTML = '<div class="review-head"><div><h1></h1><p>These photos may contain this person. Mark the incorrect ones, then save and publish the group.</p></div><div class="person-count"></div></div><div class="photo-grid"></div>';
  section.querySelector('h1').textContent = 'Does this photo contain ' + queue.person.name + '?';
  section.querySelector('.person-count').textContent = batch.length + ' shown · ' + queue.suggestions.length + ' suggestions for ' + queue.person.name;
  const grid = section.querySelector('.photo-grid');
  batch.forEach(item => grid.append(buildCard(item)));
  $('reviewArea').replaceChildren(section);
  $('actionbar').hidden = false;
  $('undoBatch').disabled = !history.length;
  $('status').textContent = '';
  updateSummary();
}

function hasFaceBox(item) {
  return [item.box_left, item.box_top, item.box_right, item.box_bottom].every(value => Number.isFinite(value));
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

function buildCard(item) {
  const card = document.createElement('article');
  card.className = 'review-card';
  card.dataset.id = item.id;
  card.innerHTML = '<div class="photo-box"><img loading="lazy" alt="Suggested photo"><span class="state-badge">✓ Contains person</span><button type="button" class="expand" title="Show the full photo larger">⛶ Enlarge</button></div><div class="card-info"><div class="file-line"><div><strong></strong><small></small></div><span class="confidence"></span></div><button type="button" class="toggle-wrong">This photo contains ' + escapeText(queue.person.name) + '</button><div class="correction"><label>If you know who it is, choose the correct name (optional)</label><div class="correction-picker"></div><div class="correction-alt"><button type="button" class="disposition-btn" data-disposition="not_a_person">Not a person</button><button type="button" class="disposition-btn" data-disposition="unknown_person">Unknown person</button></div></div></div>';
  const img = card.querySelector('img');
  img.src = '/media?id=' + item.id;
  markFace(card.querySelector('.photo-box'), img, item);
  card.querySelector('strong').textContent = item.filename;
  card.querySelector('small').textContent = (item.capture_date || 'Date unknown') + ' · ' + item.folder;
  card.querySelector('.confidence').textContent = Math.round((item.confidence || 0) * 100) + '%';
  card.querySelector('.toggle-wrong').onclick = () => toggleCard(card, item);
  card.querySelector('.expand').onclick = () => openLarge(item);
  img.ondblclick = () => openLarge(item);
  card.querySelectorAll('.disposition-btn').forEach(button => {
    button.onclick = () => setDisposition(card, item, button.dataset.disposition);
  });
  card.correctionPicker = createPersonPicker({
    container: card.querySelector('.correction-picker'),
    getNames: () => knownPeople,
    placeholder: 'Correct name',
    onChoose: name => {
      registerKnownPerson(name);
      corrections.set(item.id, name);
      dispositions.delete(item.id);
      clearDispositionButtons(card);
      card.querySelector('.state-badge').textContent = '✕ Does not contain ' + queue.person.name;
    },
  });
  return card;
}

function escapeText(value) {
  const span = document.createElement('span');
  span.textContent = value;
  return span.innerHTML;
}

function clearDispositionButtons(card) {
  card.querySelectorAll('.disposition-btn').forEach(button => button.classList.remove('active'));
}

function markWrong(card, item) {
  if (rejected.has(item.id)) return;
  rejected.add(item.id);
  card.classList.add('wrong');
  card.querySelector('.state-badge').textContent = '✕ Does not contain ' + queue.person.name;
  card.querySelector('.toggle-wrong').textContent = 'Marked as not containing this person';
}

function setDisposition(card, item, value) {
  markWrong(card, item);
  dispositions.set(item.id, value);
  corrections.delete(item.id);
  card.correctionPicker.reset();
  clearDispositionButtons(card);
  card.querySelector('[data-disposition="' + value + '"]').classList.add('active');
  card.querySelector('.state-badge').textContent = DISPOSITION_LABELS[value];
  updateSummary();
}

function toggleCard(card, item) {
  const key = item.id;
  if (rejected.has(key)) {
    rejected.delete(key);
    corrections.delete(key);
    dispositions.delete(key);
    card.correctionPicker.reset();
    clearDispositionButtons(card);
    card.classList.remove('wrong');
    card.querySelector('.state-badge').textContent = '✓ Contains person';
    card.querySelector('.toggle-wrong').textContent = 'This photo contains ' + queue.person.name;
  } else {
    markWrong(card, item);
  }
  updateSummary();
}

function updateSummary() {
  const wrong = rejected.size;
  const matches = batch.length - wrong;
  $('selectionSummary').textContent = matches + ' will be confirmed and published' + (wrong ? ' · ' + wrong + ' marked incorrect' : '');
  $('confirmBatch').textContent = 'Save & publish ' + batch.length + ' decision' + (batch.length === 1 ? '' : 's');
}

let openItem = null;

function openLarge(item) {
  openItem = item;
  $('largePhoto').src = '/media?id=' + item.id;
  markFace($('largePhotoBox'), $('largePhoto'), item, false);
  $('lightbox').classList.add('open');
}

function closeLarge() {
  openItem = null;
  $('lightbox').classList.remove('open');
  $('largePhoto').removeAttribute('src');
  $('largePhotoBox').querySelectorAll('.face-box,.location-note').forEach(node => node.remove());
}

function setDispositionFromLightbox(value) {
  if (!openItem) return;
  const card = document.querySelector('.review-card[data-id="' + openItem.id + '"]');
  if (card) setDisposition(card, openItem, value);
  closeLarge();
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
      return {
        asset_id: item.id,
        action,
        corrected_name: disposition ? '' : corrected,
      };
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

async function runLearning() {
  const button = $('learnMore');
  button.disabled = true;
  $('globalProgress').textContent = 'Learning from confirmed faces…';
  try {
    const result = await api('/api/people/learn', {});
    skipped.clear();
    history = [];
    await loadQueue();
    if (result.auto_confirmed) {
      $('globalProgress').textContent = result.auto_confirmed + ' near-certain match' + (result.auto_confirmed === 1 ? '' : 'es') + ' confirmed automatically' + (result.suggestions ? '; more added to this queue' : '');
    } else if (!result.suggestions) {
      $('globalProgress').textContent = 'No additional strong matches found';
    }
  } catch (error) {
    $('globalProgress').textContent = error.message || String(error);
  } finally {
    button.disabled = false;
  }
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

$('confirmBatch').onclick = submitBatch;
$('skipBatch').onclick = skipBatch;
$('nextPerson').onclick = () => loadQueue(queue?.person?.id, true).catch(showError);
$('deferPerson').onclick = deferPerson;
$('undoBatch').onclick = undoBatch;
$('learnMore').onclick = runLearning;
$('closeLightbox').onclick = closeLarge;
$('lightboxNotAPerson').onclick = () => setDispositionFromLightbox('not_a_person');
$('lightboxUnknownPerson').onclick = () => setDispositionFromLightbox('unknown_person');
$('lightbox').onclick = event => { if (event.target === $('lightbox')) closeLarge(); };
function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about')alert('LensLedger v'+LL.appVersion+'\n'+LL.appTagline+'\n\nLocal-first photo and video indexing, search, people review, and safe metadata publishing for Windows.')});
$('menuToggle').onclick = e => { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', e => { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeLarge(); closeMenu(); } });
loadQueue(initialPersonId).catch(error => {
  $('reviewArea').innerHTML = '<div class="empty"><div><h2>People review could not open</h2><p></p></div></div>';
  $('reviewArea').querySelector('p').textContent = error.message;
});
