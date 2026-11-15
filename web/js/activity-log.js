(() => {
  const body = document.getElementById('activityLog');
  if (!body) return;
  const state = document.getElementById('activityState');
  const MAX_LINES = 500;
  let after = 0;
  let inFlight = false;

  function lineElement(item) {
    const row = document.createElement('span');
    row.className = 'code-line';
    const time = document.createElement('span');
    time.className = 'code-time';
    time.textContent = item.time + ' ';
    const text = String(item.text || '');
    const split = text.indexOf(': ');
    const action = document.createElement('span');
    action.className = 'code-action';
    const rest = document.createElement('span');
    rest.className = 'code-rest';
    if (split > 0 && split < 60) {
      action.textContent = text.slice(0, split + 1) + ' ';
      rest.textContent = text.slice(split + 2);
    } else {
      rest.textContent = text;
    }
    if (/could not|did not go ahead|failed|error/i.test(text)) row.classList.add('is-problem');
    else if (/finished|done|complete/i.test(text)) row.classList.add('is-done');
    row.append(time, action, rest, '\n');
    return row;
  }

  function append(items) {
    if (!items.length) return;
    const nearEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
    const fragment = document.createDocumentFragment();
    items.forEach(item => fragment.append(lineElement(item)));
    body.append(fragment);
    while (body.childElementCount > MAX_LINES) body.firstElementChild.remove();
    if (nearEnd) body.scrollTop = body.scrollHeight;
  }

  async function refresh() {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const response = await fetch('/api/log/live?after=' + after, { cache: 'no-store' });
      const data = await response.json();
      if (data.last < after) { body.replaceChildren(); after = 0; }
      append(data.lines || []);
      after = data.last;
      state.textContent = 'Live';
      state.classList.remove('is-off');
    } catch (_) {
      state.textContent = 'Not connected';
      state.classList.add('is-off');
    } finally {
      inFlight = false;
    }
  }

  document.getElementById('clearActivity').onclick = () => body.replaceChildren();

  document.getElementById('copyActivity').onclick = async event => {
    const button = event.currentTarget;
    const ok = await copyText(body.textContent);
    button.textContent = ok ? 'Copied' : 'Select the text instead';
    setTimeout(() => { button.textContent = 'Copy'; }, 2500);
  };

  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  refresh();
  setInterval(refresh, 700);
})();
