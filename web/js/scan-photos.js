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
  return minutes > 0 ? `${minutes}m ${seconds % 60}s elapsed` : `${seconds}s elapsed`;
}

function setSpinner(id, active) {
  $(id).classList.toggle('active', !!active);
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
    $('scanAllMessage').textContent = scanAllRunning
      ? `Running ${STEP_LABELS[scanAll.step] || '…'}…`
      : (scanAll.message || 'Not run yet — runs every scan below, back to back.');
    $('startScanAll').disabled = scanAllRunning;
    $('pauseScanAll').disabled = !scanAllRunning;
    setSpinner('scanAllSpinner', scanAllRunning);
    $('scanAllElapsed').textContent = scanAllRunning ? elapsedText(scanAll.started_at) : '';
    const c = diagnostics.counts || {};
    $('healthSummary').replaceChildren(
      metric('Library files', c.assets),
      metric('Mapped photos', c.mapped, c.mapped ? () => { window.location.href = '/map'; } : null),
      metric('People to review', c.people_pending, c.people_pending ? () => { window.location.href = '/people-review'; } : null),
      metric('OCR complete', c.ocr_complete),
      metric('Meaning indexed', c.semantic_indexed),
      metric('Review Bin', c.review_bin),
    );
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
    $('ocrMetrics').replaceChildren(
      metric('Remaining', c.ocr_pending), metric('This pass', ocr.attempted),
      metric('Text found', ocr.with_text), metric('Errors', Math.max(c.ocr_errors || 0, ocr.errors || 0)),
    );
    const ocrRunning = ocr.state === 'running';
    $('startOcr').disabled = ocrRunning || scanAllRunning;
    $('pauseOcr').disabled = !ocrRunning || scanAllRunning;
    setSpinner('ocrSpinner', ocrRunning);
    $('ocrElapsed').textContent = ocrRunning ? elapsedText(ocr.started_at) : '';

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
      : (semanticRunning ? elapsedText(semantic.started_at) : '');

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
    } else {
      $('faceScanMessage').textContent = faceScan.message || 'Ready. Scan for faces below to find people in photos LensLedger has not looked at yet.';
      $('faceScanMetrics').replaceChildren(
        metric('Faces found', faceScan.faces_found), metric('Remaining photos', faceScan.remaining),
        metric('This pass', faceScan.processed), metric('Errors', faceScan.errors),
      );
    }
    const faceScanRunning = faceScan.state === 'running';
    $('startFaceScan').disabled = faceScanRunning || scanAllRunning;
    $('pauseFaceScan').disabled = !faceScanRunning || scanAllRunning;
    setSpinner('faceScanSpinner', faceScanRunning || faceInstalling);
    $('faceScanElapsed').textContent = faceInstalling ? elapsedText(faceInstall.started_at)
      : (faceScanRunning ? elapsedText(faceScan.started_at) : '');

    const anyActive = locationScanning || ocrRunning || semanticRunning || semanticInstalling || faceScanRunning || faceInstalling || scanAllRunning;
    setTimeout(refresh, anyActive ? 700 : 4000);
  } catch (error) {
    $('ocrMessage').textContent = error.message;
    setTimeout(refresh, 4000);
  }
}

$('startScanAll').onclick = async () => {
  $('startScanAll').disabled = true;
  try { await api('/api/scan-all/start', {}); refresh(); }
  catch (error) { $('scanAllMessage').textContent = error.message; $('startScanAll').disabled = false; }
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

refresh();
