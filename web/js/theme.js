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
