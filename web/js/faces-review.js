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
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}

function updateProgress() {
  $('globalProgress').textContent = remaining.toLocaleString() + ' unidentified face' + (remaining === 1 ? '' : 's');
}

function checkEmpty() {
  $('emptyState').hidden = $('faceGrid').children.length > 0 || $('matchGroups').children.length > 0;
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
    const checked = allThumbs.filter(t => t.querySelector('input').checked);
    const skipped = allThumbs.filter(t => !t.querySelector('input').checked).map(t => Number(t.dataset.faceId));
    if (!checked.length) { group.remove(); releasePending(ids); return; }
    group.querySelectorAll('button').forEach(b => b.disabled = true);
    status.textContent = 'Confirming…';
    let failed = 0;
    for (const thumb of checked) {
      try {
        await api('/api/faces/name', { face_id: Number(thumb.dataset.faceId), name });
        remaining = Math.max(0, remaining - 1);
        pending.delete(Number(thumb.dataset.faceId));
        thumb.remove();
      } catch (error) {
        failed += 1;
        status.textContent = error.message;
      }
    }
    updateProgress();
    if (!failed) { group.remove(); releasePending(skipped); }
    else group.querySelectorAll('button').forEach(b => b.disabled = false);
  };
  $('matchGroups').prepend(group);
  if ($('faceGrid').children.length < 12 && remaining > pending.size) loadMore();
  checkEmpty();
}

function buildCard(face) {
  const card = document.createElement('article');
  card.className = 'face-card';
  card.dataset.faceId = face.face_id;
  card.innerHTML = '<div class="face-photo"><img loading="lazy" alt="Detected face"></div>'
    + '<div class="face-info"><small></small>'
    + '<div class="face-form"><div class="face-picker"></div></div>'
    + '<button type="button" class="not-person">Not a person</button>'
    + '<div class="face-status"></div></div>';
  card.querySelector('img').src = '/media-face?face_id=' + face.face_id;
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
  const status = card.querySelector('.face-status');
  const picker = createPersonPicker({
    container: card.querySelector('.face-picker'),
    getNames: () => knownPeople,
    placeholder: 'Who is this?',
    onChoose: async name => {
      notPersonButton.disabled = true;
      status.textContent = 'Saving…';
      try {
        registerKnownPerson(name);
        const result = await api('/api/faces/name', { face_id: face.face_id, name });
        removeCard(card);
        if (result.matches && result.matches.length) addMatchGroup(name, result.matches);
      } catch (error) {
        status.textContent = error.message;
        notPersonButton.disabled = false;
      }
    },
  });
  notPersonButton.onclick = async () => {
    picker.close(); notPersonButton.disabled = true;
    status.textContent = 'Marking…';
    try {
      await api('/api/faces/ignore', { face_id: face.face_id });
      removeCard(card);
    } catch (error) {
      status.textContent = error.message;
      notPersonButton.disabled = false;
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

loadMore();
