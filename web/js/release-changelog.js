// ============================================================
//  release-changelog.js — shared "what's new since your version" box
//
//  ONE FILE, IDENTICAL IN EVERY PRODUCT. The canonical copy is
//  WaxFrame-Professional/js/release-changelog.js; LensLedger and
//  WD-Wireless-Tools carry byte-for-byte copies (plus the matching
//  release-changelog.css). Change it in one place, then copy it to the
//  others — never edit a copy in isolation.
//
//  What it does: turns GitHub release notes into a collapsible list of every
//  release newer than the version the user is running, drawn directly above an
//  update button. It has no product knowledge and no network code; each
//  product fetches the releases its own way and hands them over.
//
//  API (window.ReleaseChangelog):
//    parseApiList(list)           GitHub /releases JSON -> [{version, title, notes, url}]
//                                 (newest first; drafts and prereleases dropped;
//                                 leading "# Title" lifted into `title`; everything
//                                 from a "## Files changed", "## Verified" or
//                                 "## Verification" heading onward removed)
//    newerThan(releases, current) only the entries with version > current
//    render(box, {releases, truncated})
//                                 fills `box` (an empty element). Release text
//                                 is only ever written with textContent / DOM
//                                 nodes, never innerHTML.
//    cmpVer(a, b)                 numeric dotted-version compare, -1 / 0 / 1
//
//  `releases` entries from a server-side parser must have the same
//  {version, title, notes, url} shape.
//
//  The stylesheet is release-changelog.css, loaded from the same folder as this
//  script (a <link>, so it works under a strict style-src CSP).
// ============================================================
(function () {
  'use strict';

  function cmpVer(a, b) {
    var pa = String(a || '').replace(/^v/i, '').split('.').map(function (n) { return parseInt(n, 10) || 0; });
    var pb = String(b || '').replace(/^v/i, '').split('.').map(function (n) { return parseInt(n, 10) || 0; });
    var len = Math.max(pa.length, pb.length);
    for (var i = 0; i < len; i++) {
      var av = pa[i] || 0, bv = pb[i] || 0;
      if (av > bv) return 1;
      if (av < bv) return -1;
    }
    return 0;
  }

  function trimNotes(body) {
    var text = String(body || '').replace(/\r\n?/g, '\n');
    var cut = text.search(/^##\s+(Files changed|Verified|Verification)\b/mi);
    return (cut >= 0 ? text.slice(0, cut) : text).trim();
  }

  function parseApiList(list) {
    if (!Array.isArray(list)) return [];
    return list
      .filter(function (r) { return r && !r.draft && !r.prerelease && r.tag_name; })
      .map(function (r) {
        var body = trimNotes(r.body);
        var m = body.match(/^#\s+(.+)/);
        return {
          version: String(r.tag_name).replace(/^v/i, ''),
          title: m ? m[1].trim() : '',
          notes: body.replace(/^#\s+.+\n?/, '').trim(),
          url: r.html_url || ''
        };
      })
      .sort(function (a, b) { return cmpVer(b.version, a.version); });
  }

  function newerThan(releases, current) {
    return (Array.isArray(releases) ? releases : []).filter(function (r) {
      return r && cmpVer(r.version, current) > 0;
    });
  }

  // Inline **bold** and `code` only, built with DOM nodes.
  function appendInline(parent, text) {
    text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).forEach(function (part) {
      if (!part) return;
      var node;
      if (part.length > 4 && part.indexOf('**') === 0 && part.slice(-2) === '**') {
        node = document.createElement('strong');
        node.textContent = part.slice(2, -2);
      } else if (part.length > 2 && part.charAt(0) === '`' && part.slice(-1) === '`') {
        node = document.createElement('code');
        node.textContent = part.slice(1, -1);
      } else {
        node = document.createTextNode(part);
      }
      parent.appendChild(node);
    });
  }

  function renderNotes(container, notes) {
    var list = null;
    String(notes || '').split('\n').forEach(function (raw) {
      var line = raw.trim();
      if (!line) { list = null; return; }
      var h = line.match(/^#{2,}\s+(.+)$/);
      if (h) {
        list = null;
        var area = document.createElement('div');
        area.className = 'rc-area';
        area.textContent = h[1];
        container.appendChild(area);
        return;
      }
      var b = line.match(/^[-*]\s+(.+)$/);
      if (b) {
        if (!list) {
          list = document.createElement('ul');
          container.appendChild(list);
        }
        var li = document.createElement('li');
        appendInline(li, b[1]);
        list.appendChild(li);
        return;
      }
      list = null;
      var p = document.createElement('p');
      appendInline(p, line);
      container.appendChild(p);
    });
  }

  // ── stylesheet, next to this script ──
  var cssHref = null;
  try {
    var src = document.currentScript && document.currentScript.src;
    // Keep the script's own ?v= so a changed stylesheet is not served stale.
    var parts = src && src.match(/^([^?#]*\/)[^\/?#]*(\?[^#]*)?/);
    if (parts) cssHref = parts[1] + 'release-changelog.css' + (parts[2] || '');
  } catch (e) { cssHref = null; }

  function ensureStyles() {
    if (!cssHref || document.getElementById('rcStyles')) return;
    var link = document.createElement('link');
    link.id = 'rcStyles';
    link.rel = 'stylesheet';
    link.href = cssHref;
    document.head.appendChild(link);
  }

  function render(box, info) {
    box.textContent = '';
    var releases = info && info.releases;
    if (!Array.isArray(releases) || !releases.length) return;
    ensureStyles();
    box.classList.add('rc-box');

    var head = document.createElement('div');
    head.className = 'rc-head';
    head.textContent = releases.length === 1
      ? 'What’s new in this update'
      : 'What’s new since your version (' + releases.length + (info.truncated ? '+' : '') + ' releases)';
    box.appendChild(head);

    var scroller = document.createElement('div');
    scroller.className = 'rc-list';
    releases.forEach(function (rel, i) {
      var d = document.createElement('details');
      d.className = 'rc-item';
      if (i === 0) d.open = true;
      var sum = document.createElement('summary');
      var v = document.createElement('span');
      v.className = 'rc-ver';
      v.textContent = 'v' + rel.version;
      sum.appendChild(v);
      if (rel.title) sum.appendChild(document.createTextNode(' — ' + rel.title));
      d.appendChild(sum);
      var body = document.createElement('div');
      body.className = 'rc-body';
      renderNotes(body, rel.notes);
      d.appendChild(body);
      scroller.appendChild(d);
    });
    box.appendChild(scroller);

    if (info.truncated) {
      var more = document.createElement('p');
      more.className = 'rc-more';
      more.textContent = 'Older changes are listed on the release page.';
      box.appendChild(more);
    }
  }

  window.ReleaseChangelog = {
    cmpVer: cmpVer,
    parseApiList: parseApiList,
    newerThan: newerThan,
    render: render
  };
})();
