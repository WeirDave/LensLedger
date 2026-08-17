(function(){
  var saved = localStorage.getItem('ll-theme');
  if (saved === 'light' || saved === 'dark') {
    document.documentElement.setAttribute('data-theme', saved);
  }

  function current() {
    var attr = document.documentElement.getAttribute('data-theme');
    if (attr === 'light' || attr === 'dark') return attr;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function toggle() {
    var next = current() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('ll-theme', next);
    updateButtons();
  }

  function updateButtons() {
    var isDark = current() === 'dark';
    document.querySelectorAll('.theme-toggle').forEach(function(btn) {
      btn.textContent = isDark ? '☀' : '🌙';
      btn.setAttribute('aria-label', isDark ? 'Switch to light mode' : 'Switch to dark mode');
      btn.title = isDark ? 'Light mode' : 'Dark mode';
    });
  }

  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.theme-toggle').forEach(function(btn) {
      btn.addEventListener('click', toggle);
    });
    updateButtons();
  });

  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function() {
    if (!localStorage.getItem('ll-theme')) updateButtons();
  });
})();

function copyDiagnostics() {
  fetch('/api/diagnostics')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var lines = [];
      lines.push('LensLedger v' + d.app_version);
      lines.push('Browser    : ' + navigator.userAgent);
      lines.push('Platform   : ' + (navigator.platform || 'unknown'));
      lines.push('Database   : schema v' + d.schema_version + ' (current: v' + d.current_schema + ')');
      lines.push('Integrity  : ' + d.integrity);
      lines.push('DB size    : ' + (d.database_bytes / 1048576).toFixed(1) + ' MB');
      var c = d.counts;
      lines.push('Assets     : ' + c.assets + ' (' + c.metadata_ready + ' scanned, ' + c.cloud_only + ' cloud-only)');
      lines.push('OCR        : ' + c.ocr_complete + ' scanned, ' + c.ocr_with_text + ' with text, ' + c.ocr_errors + ' errors');
      lines.push('Faces      : ' + c.unidentified_faces + ' unidentified, ' + c.face_scan_errors + ' scan errors');
      lines.push('Semantic   : ' + c.semantic_indexed + ' indexed, ' + c.semantic_remaining + ' remaining');
      if (d.last_scan) {
        lines.push('Last scan  : ' + d.last_scan.finished_at + ' (' + d.last_scan.scanned + ' scanned, ' + d.last_scan.errors + ' errors)');
      }
      var text = lines.join('\n');
      navigator.clipboard.writeText(text).then(function() {
        var t = document.getElementById('toast');
        if (t) { t.textContent = 'Diagnostics copied to clipboard'; t.style.display = 'block'; setTimeout(function() { t.style.display = 'none'; }, 3000); }
      });
    })
    .catch(function() {
      var t = document.getElementById('toast');
      if (t) { t.textContent = 'Could not fetch diagnostics'; t.style.display = 'block'; setTimeout(function() { t.style.display = 'none'; }, 3000); }
    });
}
