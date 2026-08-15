const LL = JSON.parse(document.body.dataset.ll);
const csrf = LL.csrf;
const $ = id => document.getElementById(id);

async function api(path, payload) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, csrf }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function metric(label, value, onClick) {
  const box = document.createElement(onClick ? 'button' : 'div');
  if (onClick) { box.type = 'button'; box.onclick = onClick; }
  const strong = document.createElement('strong');
  strong.textContent = Number(value || 0).toLocaleString();
  const span = document.createElement('span');
  span.textContent = label;
  box.append(strong, span);
  return box;
}

function elapsedText(startedAt) {
  if (!startedAt) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  if (hours > 0) return `${hours}h ${minutes % 60}m ${seconds % 60}s elapsed`;
  return minutes > 0 ? `${minutes}m ${seconds % 60}s elapsed` : `${seconds}s elapsed`;
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const hours = Math.floor(minutes / 60);
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  if (minutes > 0) return `${minutes}m ${totalSeconds % 60}s`;
  return `${totalSeconds}s`;
}

// Appends "· 42% · ~3m remaining" to an elapsed-time string, using the
// done/total counts every job already reports each poll. ETA is withheld
// for the first few seconds, since a rate from one tiny sample is noise.
function progressSuffix(done, total, startedAt) {
  if (!total) return '';
  const pct = Math.min(100, Math.round((done / total) * 100));
  const elapsedMs = startedAt ? Date.now() - new Date(startedAt).getTime() : 0;
  let eta = '';
  if (done > 0 && done < total && elapsedMs > 3000) {
    const remainingMs = (elapsedMs / done) * (total - done);
    eta = ` · ~${formatDuration(remainingMs)} remaining`;
  }
  return ` · ${pct}%${eta}`;
}

function setBar(id, done, total) {
  const wrap = $(id + 'Wrap');
  wrap.classList.remove('indeterminate');
  if (!total) { wrap.hidden = true; return; }
  wrap.hidden = false;
  $(id).style.width = Math.max(0, Math.min(100, Math.round((done / total) * 100))) + '%';
}

function setSpinner(id, active) {
  $(id).classList.toggle('active', !!active);
}

function closeHelp() {
  document.querySelectorAll('.help-popover.open').forEach(el => el.classList.remove('open'));
}

function openScanModal(title, node) {
  $('scanModalTitle').textContent = title;
  $('scanModalBody').replaceChildren(node);
  $('scanModalBackdrop').classList.add('open');
}

function errorListNode(errors) {
  const box = document.createElement('div');
  box.className = 'error-list';
  if (!errors.length) {
    const empty = document.createElement('p');
    empty.className = 'error-empty';
    empty.textContent = 'No errors on record.';
    box.append(empty);
    return box;
  }
  errors.forEach(({ path, error }) => {
    const row = document.createElement('div');
    row.className = 'error-row';
    const pathEl = document.createElement('span');
    pathEl.className = 'path';
    pathEl.textContent = path;
    const msgEl = document.createElement('span');
    msgEl.className = 'msg';
    msgEl.textContent = error;
    row.append(pathEl, msgEl);
    box.append(row);
  });
  return box;
}

async function showScanErrors(endpoint, title) {
  openScanModal(title, Object.assign(document.createElement('p'), { className: 'error-empty', textContent: 'Loading…' }));
  try {
    const data = await fetch(endpoint).then(r => r.json());
    openScanModal(title, errorListNode(data.errors || []));
  } catch (error) {
    openScanModal(title, Object.assign(document.createElement('p'), { className: 'error-empty', textContent: error.message }));
  }
}

async function refresh() {
  try {
    const [diagnostics, locationStatus, ocr, semantic, faceScan, scanAll] = await Promise.all([
      fetch('/api/diagnostics').then(r => r.json()),
      fetch('/api/library/status').then(r => r.json()),
      fetch('/api/ocr/status').then(r => r.json()),
      fetch('/api/semantic/status').then(r => r.json()),
      fetch('/api/faces/status').then(r => r.json()),
      fetch('/api/scan-all/status').then(r => r.json()),
    ]);
    const scanAllRunning = scanAll.state === 'running';
    const STEP_LABELS = { location: 'photo locations', ocr: 'OCR', semantic: 'meaning search', face: 'face detection' };
    const scanAllSteps = ['location', 'ocr', ...(semantic.installed ? ['semantic'] : []), ...(faceScan.installed ? ['face'] : [])];
    const scanAllStepIndex = scanAllSteps.indexOf(scanAll.step) + 1;
    $('scanAllMessage').textContent = scanAllRunning
      ? `Step ${scanAllStepIndex} of ${scanAllSteps.length}: running ${STEP_LABELS[scanAll.step] || '…'}…`
      : (scanAll.message || 'Not run yet — runs every scan below, back to back.');
    $('startScanAll').disabled = scanAllRunning;
    $('pauseScanAll').disabled = !scanAllRunning;
    setSpinner('scanAllSpinner', scanAllRunning);
    let scanAllStepFraction = 0;
    let stepDone = 0, stepTotal = 0, stepStartedAt = null;
    if (scanAll.step === 'ocr') { stepDone = ocr.attempted; stepTotal = ocr.total; stepStartedAt = ocr.started_at; }
    else if (scanAll.step === 'semantic') { stepDone = semantic.indexed_this_pass; stepTotal = semantic.total; stepStartedAt = semantic.started_at; }
    else if (scanAll.step === 'face') { stepDone = faceScan.processed; stepTotal = faceScan.total; stepStartedAt = faceScan.started_at; }
    if (stepTotal) scanAllStepFraction = stepDone / stepTotal;
    $('scanAllElapsed').textContent = scanAllRunning
      ? elapsedText(scanAll.started_at) + ` · step ${scanAllStepIndex} of ${scanAllSteps.length}` + progressSuffix(stepDone, stepTotal, stepStartedAt)
      : '';
    setBar('scanAllBar', scanAllRunning ? (scanAllStepIndex - 1 + scanAllStepFraction) : 0, scanAllRunning ? scanAllSteps.length : 0);
    const c = diagnostics.counts || {};
    $('healthSummary').replaceChildren(
      metric('Library files', c.assets),
      metric('Mapped photos', c.mapped, c.mapped ? () => { window.location.href = '/map'; } : null),
      metric('People to review', c.people_pending, c.people_pending ? () => { window.location.href = '/people-review'; } : null),
      metric('Faces to name', c.unidentified_faces, c.unidentified_faces ? () => { window.location.href = '/faces-review'; } : null),
      metric('OCR complete', c.ocr_complete),
      metric('Meaning indexed', c.semantic_indexed),
      metric('Review Bin', c.review_bin),
    );
    $('cloudScope').textContent = c.cloud_only
      ? `Scans below only cover the ${Number(c.metadata_ready || 0).toLocaleString()} of ${Number(c.assets || 0).toLocaleString()} files already downloaded to this computer. The other ${Number(c.cloud_only).toLocaleString()} are cloud-only placeholders (OneDrive/Dropbox files not yet synced locally) — LensLedger never opens those automatically, since doing so would silently trigger a large download. "Complete" below means complete for the downloaded files, not your whole library.`
      : '';
    $('healthPaths').replaceChildren(
      Object.assign(document.createElement('div'), { textContent: 'Database health: ' + diagnostics.integrity + ' · schema ' + diagnostics.schema_version + '/' + diagnostics.current_schema + ' · ' + (diagnostics.database_bytes / 1048576).toFixed(1) + ' MB' }),
      Object.assign(document.createElement('div'), { textContent: 'Library: ' + diagnostics.library }),
      Object.assign(document.createElement('div'), { textContent: 'Database: ' + diagnostics.database }),
    );

    // Photo locations (reuses the general incremental library scan)
    const locationScanning = locationStatus.state === 'scanning';
    $('locationMessage').textContent = locationStatus.message || 'Not run yet — click below to find GPS coordinates in your photos.';
    $('locationMetrics').replaceChildren(
      metric('Scanned', locationStatus.scanned), metric('Changed', locationStatus.changed),
      metric('Unchanged', locationStatus.unchanged), metric('Errors', locationStatus.errors),
    );
    $('startLocation').disabled = locationScanning || scanAllRunning;
    $('pauseLocation').disabled = !locationScanning || scanAllRunning;
    setSpinner('locationSpinner', locationScanning);
    $('locationElapsed').textContent = locationScanning ? elapsedText(locationStatus.started_at) : '';

    // OCR
    $('ocrMessage').textContent = ocr.message || 'OCR has not run in this session.';
    const ocrErrorCount = Math.max(c.ocr_errors || 0, ocr.errors || 0);
    $('ocrMetrics').replaceChildren(
      metric('Remaining', c.ocr_pending), metric('This pass', ocr.attempted),
      metric('Text found', ocr.with_text),
      metric('Errors', ocrErrorCount, ocrErrorCount ? () => showScanErrors('/api/ocr/errors', 'OCR errors') : null),
    );
    const ocrRunning = ocr.state === 'running';
    $('startOcr').disabled = ocrRunning || scanAllRunning;
    $('pauseOcr').disabled = !ocrRunning || scanAllRunning;
    setSpinner('ocrSpinner', ocrRunning);
    $('ocrElapsed').textContent = ocrRunning
      ? elapsedText(ocr.started_at) + progressSuffix(ocr.attempted, ocr.total, ocr.started_at) : '';
    setBar('ocrBar', ocr.attempted, ocrRunning ? ocr.total : 0);

    // Meaning search
    const semanticInstall = semantic.install || {};
    const semanticInstalling = semanticInstall.state === 'installing';
    $('semanticInstallActions').hidden = !!semantic.installed;
    $('semanticBuildActions').hidden = !semantic.installed;
    if (!semantic.installed) {
      $('semanticMessage').textContent = semanticInstalling ? semanticInstall.message
        : (semanticInstall.state === 'error' ? semanticInstall.message
          : 'Not set up yet — click below to install the local meaning-search model software (a large one-time download).');
      $('installSemantic').disabled = semanticInstalling || scanAllRunning;
      $('semanticMetrics').replaceChildren();
      $('semanticBarWrap').hidden = !semanticInstalling;
      $('semanticBarWrap').classList.toggle('indeterminate', semanticInstalling);
    } else {
      $('semanticMessage').textContent = semantic.message || 'Ready. Build the index below to make your photos searchable by meaning.';
      $('semanticMetrics').replaceChildren(
        metric('Indexed', semantic.indexed), metric('Remaining', semantic.remaining),
        metric('This pass', semantic.indexed_this_pass), metric('Errors', semantic.errors),
      );
    }
    const semanticRunning = semantic.state === 'running';
    $('startSemantic').disabled = semanticRunning || scanAllRunning;
    $('pauseSemantic').disabled = !semanticRunning || scanAllRunning;
    setSpinner('semanticSpinner', semanticRunning || semanticInstalling);
    $('semanticElapsed').textContent = semanticInstalling ? elapsedText(semanticInstall.started_at)
      : (semanticRunning
        ? elapsedText(semantic.started_at) + progressSuffix(semantic.indexed_this_pass, semantic.total, semantic.started_at)
        : '');
    if (!semanticInstalling) setBar('semanticBar', semantic.indexed_this_pass, semanticRunning ? semantic.total : 0);

    // Face detection
    const faceInstall = faceScan.install || {};
    const faceInstalling = faceInstall.state === 'installing';
    $('faceInstallActions').hidden = !!faceScan.installed;
    $('faceScanActions').hidden = !faceScan.installed;
    if (!faceScan.installed) {
      $('faceScanMessage').textContent = faceInstalling ? faceInstall.message
        : (faceInstall.state === 'error' ? faceInstall.message
          : 'Not set up yet — click below to install the local face-detection model software (a one-time download).');
      $('installFaceScan').disabled = faceInstalling || scanAllRunning;
      $('faceScanMetrics').replaceChildren();
      $('faceScanBarWrap').hidden = !faceInstalling;
      $('faceScanBarWrap').classList.toggle('indeterminate', faceInstalling);
    } else {
      $('faceScanMessage').textContent = faceScan.message || 'Ready. Scan for faces below to find people in photos LensLedger has not looked at yet.';
      const faceErrorCount = Math.max(c.face_scan_errors || 0, faceScan.errors || 0);
      $('faceScanMetrics').replaceChildren(
        metric('Faces found', faceScan.faces_found, faceScan.faces_found ? () => { window.location.href = '/faces-review'; } : null),
        metric('Remaining photos', faceScan.remaining),
        metric('This pass', faceScan.processed),
        metric('Errors', faceErrorCount, faceErrorCount ? () => showScanErrors('/api/faces/errors', 'Face detection errors') : null),
      );
    }
    const faceScanRunning = faceScan.state === 'running';
    $('startFaceScan').disabled = faceScanRunning || scanAllRunning;
    $('pauseFaceScan').disabled = !faceScanRunning || scanAllRunning;
    setSpinner('faceScanSpinner', faceScanRunning || faceInstalling);
    $('faceScanElapsed').textContent = faceInstalling ? elapsedText(faceInstall.started_at)
      : (faceScanRunning
        ? elapsedText(faceScan.started_at) + progressSuffix(faceScan.processed, faceScan.total, faceScan.started_at)
        : '');
    if (!faceInstalling) setBar('faceScanBar', faceScan.processed, faceScanRunning ? faceScan.total : 0);

    const anyActive = locationScanning || ocrRunning || semanticRunning || semanticInstalling || faceScanRunning || faceInstalling || scanAllRunning;
    setTimeout(refresh, anyActive ? 700 : 4000);
  } catch (error) {
    $('ocrMessage').textContent = error.message;
    setTimeout(refresh, 4000);
  }
}

$('startScanAll').onclick = async () => {
  $('startScanAll').disabled = true;
  $('startScanAll').textContent = 'Starting scan...';
  try { await api('/api/scan-all/start', {}); refresh(); }
  catch (error) { $('scanAllMessage').textContent = error.message; $('startScanAll').disabled = false; $('startScanAll').textContent = 'Start Scan All'; }
};

$('pauseScanAll').onclick = async () => {
  try { await api('/api/scan-all/cancel', {}); $('scanAllMessage').textContent = 'Stopping after the current step…'; }
  catch (error) { $('scanAllMessage').textContent = error.message; }
};

$('startLocation').onclick = async () => {
  $('startLocation').disabled = true;
  try { await api('/api/library/open', { path: LL.currentLibrary }); refresh(); }
  catch (error) { $('locationMessage').textContent = error.message; $('startLocation').disabled = false; }
};
$('pauseLocation').onclick = async () => {
  try { await api('/api/library/cancel', {}); $('locationMessage').textContent = 'Pausing after the current file…'; }
  catch (error) { $('locationMessage').textContent = error.message; }
};

$('startOcr').onclick = async () => {
  $('startOcr').disabled = true;
  try { await api('/api/ocr/start', { since: $('ocrSince').value, workers: 4 }); refresh(); }
  catch (error) { $('ocrMessage').textContent = error.message; $('startOcr').disabled = false; }
};
$('pauseOcr').onclick = async () => {
  try { await api('/api/ocr/cancel', {}); $('ocrMessage').textContent = 'Pausing after active images finish…'; }
  catch (error) { $('ocrMessage').textContent = error.message; }
};

$('installSemantic').onclick = async () => {
  if (!confirm('This downloads and installs the local meaning-search model software (roughly 1-2 GB depending on your system) and may take several minutes. It runs entirely on this computer and nothing is uploaded. Continue?')) return;
  $('installSemantic').disabled = true;
  try { await api('/api/semantic/install', {}); refresh(); }
  catch (error) { $('semanticMessage').textContent = error.message; $('installSemantic').disabled = false; }
};
$('startSemantic').onclick = async () => {
  $('startSemantic').disabled = true;
  try { await api('/api/semantic/start', { batch_size: 16 }); refresh(); }
  catch (error) { $('semanticMessage').textContent = error.message; $('startSemantic').disabled = false; }
};
$('pauseSemantic').onclick = async () => {
  try { await api('/api/semantic/cancel', {}); $('semanticMessage').textContent = 'Pausing after the active image batch…'; }
  catch (error) { $('semanticMessage').textContent = error.message; }
};

$('installFaceScan').onclick = async () => {
  if (!confirm('This downloads and installs the local face-detection model software (roughly 500 MB) and may take several minutes. It runs entirely on this computer and nothing is uploaded. Continue?')) return;
  $('installFaceScan').disabled = true;
  try { await api('/api/faces/install', {}); refresh(); }
  catch (error) { $('faceScanMessage').textContent = error.message; $('installFaceScan').disabled = false; }
};
$('startFaceScan').onclick = async () => {
  $('startFaceScan').disabled = true;
  try { await api('/api/faces/start', {}); refresh(); }
  catch (error) { $('faceScanMessage').textContent = error.message; $('startFaceScan').disabled = false; }
};
$('pauseFaceScan').onclick = async () => {
  try { await api('/api/faces/cancel', {}); $('faceScanMessage').textContent = 'Pausing after the active photo finishes…'; }
  catch (error) { $('faceScanMessage').textContent = error.message; }
};

$('backupDatabase').onclick = async () => {
  $('backupDatabase').disabled = true;
  $('backupStatus').textContent = 'Creating verified backup…';
  try { const result = await api('/api/database/backup', {}); $('backupStatus').textContent = 'Saved ' + result.path; }
  catch (error) { $('backupStatus').textContent = error.message; }
  finally { $('backupDatabase').disabled = false; }
};

document.querySelectorAll('[data-help]').forEach(button => button.onclick = e => {
  e.stopPropagation();
  const target = $(button.dataset.help);
  const opening = !target.classList.contains('open');
  closeHelp();
  if (opening) target.classList.add('open');
});
$('scanModalClose').onclick = () => $('scanModalBackdrop').classList.remove('open');
$('scanModalBackdrop').onclick = e => { if (e.target === $('scanModalBackdrop')) $('scanModalBackdrop').classList.remove('open'); };
document.addEventListener('click', e => {
  if (!e.target.closest('.help-popover') && !e.target.closest('.info-button')) closeHelp();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('scanModalBackdrop').classList.remove('open'); closeHelp(); }
});

refresh();
