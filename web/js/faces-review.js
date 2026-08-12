const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const $ = id => document.getElementById(id);
const PAGE_SIZE = 30;
let loading = false;
let remaining = 0;

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
  $('emptyState').hidden = $('faceGrid').children.length > 0;
}

function removeCard(card) {
  card.remove();
  remaining = Math.max(0, remaining - 1);
  updateProgress();
  if ($('faceGrid').children.length < 12 && remaining > 0) loadMore();
  checkEmpty();
}

function buildCard(face) {
  const card = document.createElement('article');
  card.className = 'face-card';
  card.dataset.faceId = face.face_id;
  card.innerHTML = '<div class="face-photo"><img loading="lazy" alt="Detected face"></div>'
    + '<div class="face-info"><small></small>'
    + '<div class="face-form"><input list="peopleOptions" placeholder="Who is this?"><button type="button" class="save">Save</button></div>'
    + '<button type="button" class="not-person">Not a person</button>'
    + '<div class="face-status"></div></div>';
  card.querySelector('img').src = '/media-face?face_id=' + face.face_id;
  card.querySelector('small').textContent = (face.capture_date || 'Date unknown') + ' · ' + face.folder;
  const input = card.querySelector('input');
  const saveButton = card.querySelector('.save');
  const notPersonButton = card.querySelector('.not-person');
  const status = card.querySelector('.face-status');

  const save = async () => {
    const name = input.value.trim();
    if (!name) { input.focus(); return; }
    input.disabled = true; saveButton.disabled = true; notPersonButton.disabled = true;
    status.textContent = 'Saving…';
    try {
      await api('/api/faces/name', { face_id: face.face_id, name });
      removeCard(card);
    } catch (error) {
      status.textContent = error.message;
      input.disabled = false; saveButton.disabled = false; notPersonButton.disabled = false;
    }
  };
  input.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); save(); } };
  // Clicking a datalist suggestion fires 'input' immediately (that's the
  // event the HTML spec defines for it) -- 'change' only fires later, on
  // blur, which is why picking a name felt like it needed an extra click
  // away before. Auto-save the instant the value exactly matches a known
  // person. A brand-new typed name won't exactly match anything until
  // fully typed, so it still waits for Enter or Save -- creating a new
  // person is a more deliberate action than picking an existing one.
  input.oninput = () => {
    const options = [...document.getElementById('peopleOptions').options].map(o => o.value);
    if (options.includes(input.value.trim())) save();
  };
  saveButton.onclick = save;
  notPersonButton.onclick = async () => {
    input.disabled = true; saveButton.disabled = true; notPersonButton.disabled = true;
    status.textContent = 'Marking…';
    try {
      await api('/api/faces/ignore', { face_id: face.face_id });
      removeCard(card);
    } catch (error) {
      status.textContent = error.message;
      input.disabled = false; saveButton.disabled = false; notPersonButton.disabled = false;
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
    $('peopleOptions').replaceChildren(...data.people_options.map(name => {
      const option = document.createElement('option');
      option.value = name;
      return option;
    }));
    const existing = new Set([...$('faceGrid').children].map(el => el.dataset.faceId));
    data.faces
      .filter(face => !existing.has(String(face.face_id)))
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
