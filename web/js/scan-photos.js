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

function metric(label, value, onClick, tip) {
  const box = document.createElement(onClick ? 'button' : 'div');
  if (onClick) { box.type = 'button'; box.onclick = onClick; }
  if (tip) box.title = tip;
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

const ERROR_HINTS = [
  { pattern: /AggregateException|Exception calling.*Wait/i, hint: 'Windows could not decode this image for text recognition. The photo itself is fine — it just uses an encoding the OCR engine cannot read. No action needed.' },
  { pattern: /MaxImageDimension|image.*too.*large/i, hint: 'This image is larger than the OCR engine supports. LensLedger normally scales these down automatically — if you see this, the fallback also failed. The photo is fine; OCR just cannot process it.' },
  { pattern: /timed?\s*out|timeout/i, hint: 'OCR took too long on this image and was skipped. This can happen with very large or complex files. The photo is not affected.' },
  { pattern: /access.*denied|permission/i, hint: 'LensLedger could not open this file. It may be locked by another program, or the file permissions may need adjusting.' },
  { pattern: /not.*found|no.*such.*file/i, hint: 'The file was moved or deleted since the last scan. Run a new scan to update the inventory.' },
  { pattern: /corrupt|invalid.*data|bad.*image/i, hint: 'The image file appears to be damaged. The photo may need to be restored from a backup.' },
  { pattern: /out.*of.*memory|insufficient.*memory/i, hint: 'Your computer ran out of memory processing this image. Closing other applications and retrying may help.' },
  { pattern: /face.*detect|mediapipe|onnx/i, hint: 'The face-detection model could not process this image. The photo is fine — this particular file just could not be analyzed for faces.' },
];

function explainError(errorText) {
  for (const { pattern, hint } of ERROR_HINTS) {
    if (pattern.test(errorText)) return hint;
  }
  return null;
}

function errorListNode(errors) {
  const wrapper = document.createElement('div');
  wrapper.className = 'error-list-wrapper';
  const box = document.createElement('div');
  box.className = 'error-list';
  if (!errors.length) {
    const empty = document.createElement('p');
    empty.className = 'error-empty';
    empty.textContent = 'No errors on record.';
    box.append(empty);
    wrapper.append(box);
    return wrapper;
  }
  errors.forEach(({ path, full_path, error }) => {
    const row = document.createElement('div');
    row.className = 'error-row';
    const displayPath = full_path || path;
    const pathEl = document.createElement('a');
    pathEl.className = 'path';
    pathEl.textContent = displayPath;
    pathEl.href = 'file:///' + displayPath.replace(/\\/g, '/');
    pathEl.target = '_blank';
    pathEl.title = 'Open file';
    const msgEl = document.createElement('span');
    msgEl.className = 'msg';
    msgEl.textContent = error;
    const copyPathBtn = document.createElement('button');
    copyPathBtn.type = 'button';
    copyPathBtn.className = 'copy-path-btn';
    copyPathBtn.textContent = 'Copy path';
    copyPathBtn.title = 'Copy full path to clipboard';
    copyPathBtn.onclick = (e) => {
      e.preventDefault();
      navigator.clipboard.writeText(displayPath).then(() => {
        copyPathBtn.textContent = 'Copied!';
        setTimeout(() => { copyPathBtn.textContent = 'Copy path'; }, 1500);
      });
    };
    row.append(pathEl, copyPathBtn, msgEl);
    const hint = explainError(error);
    if (hint) {
      const hintEl = document.createElement('span');
      hintEl.className = 'error-hint';
      hintEl.textContent = hint;
      row.append(hintEl);
    }
    box.append(row);
  });
  wrapper.append(box);

  const footer = document.createElement('div');
  footer.className = 'error-modal-footer';
  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'secondary';
  copyBtn.textContent = 'Copy all to clipboard';
  copyBtn.onclick = () => {
    const text = errors.map(e => `${e.full_path || e.path}\n  ${e.error}`).join('\n\n');
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.textContent = 'Copied!';
      setTimeout(() => { copyBtn.textContent = 'Copy all to clipboard'; }, 2000);
    });
  };
  footer.append(copyBtn);
  wrapper.append(footer);
  return wrapper;
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
    if (!scanAllRunning) $('startScanAll').textContent = 'Run all scans';
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
      metric('Library files', c.assets, null, 'Total photos and videos in your library'),
      metric('Mapped photos', c.mapped, c.mapped ? () => { window.location.href = '/map'; } : null, 'Photos with GPS coordinates — click to view on the map'),
      metric('People to review', c.people_pending, c.people_pending ? () => { window.location.href = '/faces-review'; } : null, 'Groups of faces that may be the same person — click to confirm or separate them'),
      metric('Faces to review', c.unidentified_faces, c.unidentified_faces ? () => { window.location.href = '/faces-review'; } : null, 'Detected faces that haven\'t been given a name yet'),
      metric('OCR complete', c.ocr_complete, null, 'Photos scanned for visible text (signs, documents, screens, etc.)'),
      metric('Meaning indexed', c.semantic_indexed, null, 'Photos indexed for meaning search — lets you search by describing what\'s in the photo'),
      metric('Review Bin', c.review_bin, null, 'Photos you\'ve moved to the review bin for possible removal — not yet permanently deleted'),
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
    const locationErrorCount = Math.max(c.scan_errors || 0, locationStatus.errors || 0);
    $('locationMetrics').replaceChildren(
      metric('Scanned', locationStatus.scanned), metric('Changed', locationStatus.changed),
      metric('Unchanged', locationStatus.unchanged),
      metric('Errors', locationErrorCount, locationErrorCount ? () => showScanErrors('/api/library/errors', 'Photo location scan errors') : null),
    );
    $('startLocation').disabled = locationScanning || scanAllRunning;
    $('pauseLocation').disabled = !locationScanning || scanAllRunning;
    setSpinner('locationSpinner', locationScanning);
    const locationTotal = locationStatus.total_estimate || 0;
    $('locationElapsed').textContent = locationScanning
      ? elapsedText(locationStatus.started_at) + progressSuffix(locationStatus.scanned, locationTotal, locationStatus.started_at)
      : '';
    if (locationTotal) setBar('locationBar', locationStatus.scanned, locationScanning ? locationTotal : 0);
    else { $('locationBarWrap').classList.toggle('indeterminate', locationScanning); $('locationBarWrap').hidden = !locationScanning; }

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
      const semanticErrorCount = Math.max(semantic.failed || 0, semantic.errors || 0);
      $('semanticMetrics').replaceChildren(
        metric('Indexed', semantic.indexed), metric('Remaining', semantic.remaining),
        metric('This pass', semantic.indexed_this_pass),
        metric('Errors', semanticErrorCount, semanticErrorCount ? () => showScanErrors('/api/semantic/errors', 'Meaning search errors') : null),
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
  if (e.key === 'Escape') { $('scanModalBackdrop').classList.remove('open'); closeHelp(); closeMenu(); }
});

function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};
$('menuToggle').onclick = e => { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', e => { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });

refresh();

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
