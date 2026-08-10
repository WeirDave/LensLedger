const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const $ = id => document.getElementById(id);
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

function choice(item, labelSuffix = '') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'suggestion';
  const strong = document.createElement('strong');
  strong.textContent = item.label + labelSuffix;
  const small = document.createElement('small');
  small.textContent = item.path;
  button.append(strong, small);
  button.onclick = () => { $('libraryPath').value = item.path; $('start').focus(); };
  return button;
}

async function loadOptions() {
  const response = await fetch('/api/library/options');
  const data = await response.json();
  const seen = new Set();
  const buttons = [];
  for (const item of data.known || []) {
    seen.add(item.path.toLowerCase());
    buttons.push(choice(item, ' · indexed before'));
  }
  for (const item of data.suggestions || [])
    if (!seen.has(item.path.toLowerCase())) buttons.push(choice(item));
  $('suggestions').replaceChildren(...buttons);
  if (!$('libraryPath').value && buttons.length) buttons[0].click();
}

function number(value) {
  return Number(value || 0).toLocaleString();
}

function showProgress(job) {
  $('progressPanel').classList.add('open');
  for (const key of ['scanned', 'changed', 'unchanged', 'placeholders', 'errors'])
    $(key).textContent = number(job[key]);
  $('progressMessage').textContent = job.message || job.state;
  if (job.state === 'error') {
    $('progressMessage').className = 'error';
    $('cancel').hidden = true;
    $('start').textContent = 'Try again';
    $('start').disabled = false;
    polling = false;
    return;
  }
  if (job.state === 'cancelled') {
    $('progressTitle').textContent = 'Scan paused safely';
    $('cancel').hidden = true;
    $('start').textContent = 'Resume scan';
    $('start').disabled = false;
    polling = false;
    return;
  }
  if (job.state === 'complete') {
    polling = false;
    $('progressPanel').classList.add('complete');
    $('progressTitle').textContent = 'Your library is ready';
    $('cancel').hidden = true;
    $('start').textContent = 'Scan complete';
    $('start').disabled = true;
    const summary = job.summary || {};
    const values = [
      ['Media files', summary.assets], ['Images', summary.images], ['Videos', summary.videos],
      ['RAW files', summary.raw_files], ['Metadata ready', summary.metadata_ready],
      ['Cloud-only', summary.placeholders],
    ];
    $('completeGrid').replaceChildren(...values.map(([label, value]) => {
      const box = document.createElement('div');
      const strong = document.createElement('strong');
      strong.textContent = number(value);
      const span = document.createElement('span');
      span.textContent = label;
      box.append(strong, span);
      return box;
    }));
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

async function startScan() {
  const path = $('libraryPath').value.trim();
  if (!path) return;
  $('start').disabled = true;
  $('start').textContent = 'Scanning…';
  $('cancel').hidden = false;
  $('progressPanel').className = 'progress-panel open';
  $('progressTitle').textContent = 'Building your library';
  $('progressMessage').className = '';
  try {
    await api('/api/library/open', { path });
    polling = true;
    poll();
  } catch (error) {
    $('progressPanel').classList.add('open');
    $('progressMessage').textContent = error.message;
    $('progressMessage').className = 'error';
    $('start').disabled = false;
  }
}

$('browse').onclick = async () => {
  $('browse').disabled = true;
  try {
    const result = await api('/api/library/browse', {});
    if (result.path) $('libraryPath').value = result.path;
  } catch (error) {
    $('progressPanel').classList.add('open');
    $('progressMessage').textContent = error.message;
    $('progressMessage').className = 'error';
  } finally {
    $('browse').disabled = false;
  }
};
$('start').onclick = startScan;
$('cancel').onclick = async () => {
  $('cancel').disabled = true;
  try {
    await api('/api/library/cancel', {});
  } catch (error) {
    $('progressMessage').textContent = error.message;
  }
};
$('enterLibrary').onclick = () => location.href = '/?sort=newest';
loadOptions();
fetch('/api/library/status').then(r => r.json()).then(job => {
  if (job.state === 'scanning') { polling = true; showProgress(job); }
});
