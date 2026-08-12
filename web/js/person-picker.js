// A "who is this" combobox built from a <button> trigger, never a native
// text <input> for the visible field -- buttons are never autofill targets,
// which sidesteps the browser address-autofill popups a plain
// autocomplete="off" input didn't reliably suppress (Firefox's Form
// Autofill in particular mostly ignores autocomplete="off"). Shared by
// faces-review.js, people-review.js, and viewer.js.
//
// The dropdown itself is appended straight to <body> and positioned with
// JS against the trigger's own screen coordinates, rather than nested
// inside the trigger's container with position:absolute -- narrow cards
// (see faces-review.css's .face-card) use overflow:hidden for their
// rounded photo corners, which would otherwise silently clip the dropdown
// to nothing the instant it extended past the card's edge. It only exists
// in the DOM while open, so there's nothing to clean up when a card is
// later removed.
//
// createPersonPicker({container, getNames, placeholder, onChoose}):
//   container   -- an empty element to fill with the picker's trigger
//   getNames()  -- returns the current array of known person names; called
//                  fresh on every open/filter so newly-added names (from
//                  this picker or another one on the page) show up without
//                  a page reload
//   placeholder -- trigger button text before anything is chosen
//   onChoose(name) -- called with the chosen (existing or newly typed) name
function createPersonPicker({ container, getNames, placeholder, onChoose }) {
  container.classList.add('person-picker');
  container.innerHTML = '<button type="button" class="person-trigger"></button>';
  const trigger = container.querySelector('.person-trigger');
  trigger.textContent = placeholder;
  let popover = null;

  function position() {
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(260, window.innerWidth * 0.8);
    const left = Math.min(Math.max(rect.left, 8), window.innerWidth - width - 8);
    popover.style.width = width + 'px';
    popover.style.left = left + 'px';
    const spaceBelow = window.innerHeight - rect.bottom;
    const opensAbove = spaceBelow < 300 && rect.top > spaceBelow;
    popover.classList.toggle('above', opensAbove);
    if (opensAbove) {
      popover.style.bottom = (window.innerHeight - rect.top + 6) + 'px';
      popover.style.top = '';
    } else {
      popover.style.top = (rect.bottom + 6) + 'px';
      popover.style.bottom = '';
    }
  }

  function showNewEntry(prefill) {
    if (!popover) return;
    popover.querySelector('.person-search').hidden = true;
    popover.querySelector('.person-list').hidden = true;
    const newRow = popover.querySelector('.person-new-row');
    newRow.hidden = false;
    const newInput = popover.querySelector('.person-new-input');
    newInput.value = prefill || '';
    newInput.focus();
  }

  function showSearch() {
    if (!popover) return;
    popover.querySelector('.person-new-row').hidden = true;
    popover.querySelector('.person-search').hidden = false;
    popover.querySelector('.person-list').hidden = false;
    popover.querySelector('.person-search').focus();
  }

  function renderList() {
    if (!popover) return;
    const search = popover.querySelector('.person-search');
    const list = popover.querySelector('.person-list');
    const query = search.value.trim().toLowerCase();
    const names = getNames();
    const matches = query ? names.filter(name => name.toLowerCase().includes(query)) : names;
    const newItem = document.createElement('button');
    newItem.type = 'button';
    newItem.className = 'person-item person-item-new';
    newItem.textContent = '+ New person';
    newItem.onclick = () => showNewEntry(search.value.trim());
    const items = matches.map(name => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'person-item';
      item.textContent = name;
      item.onclick = () => choose(name);
      return item;
    });
    list.replaceChildren(newItem, ...items);
  }

  function choose(name) {
    trigger.textContent = name;
    close();
    onChoose(name);
  }

  function onDocClick(event) {
    if (popover && !popover.contains(event.target) && event.target !== trigger) close();
  }
  function onDocKeydown(event) {
    if (event.key === 'Escape') close();
  }
  function onReposition() {
    if (popover) position();
  }

  function close() {
    if (!popover) return;
    popover.remove();
    popover = null;
    document.removeEventListener('click', onDocClick);
    document.removeEventListener('keydown', onDocKeydown);
    window.removeEventListener('resize', onReposition);
    window.removeEventListener('scroll', onReposition, true);
  }

  function open() {
    if (popover) return;
    popover = document.createElement('div');
    popover.className = 'person-popover';
    popover.innerHTML = '<input type="text" class="person-search" autocomplete="off" placeholder="Search people">'
      + '<div class="person-list"></div>'
      + '<div class="person-new-row" hidden>'
      + '<input type="text" class="person-new-input" autocomplete="off" placeholder="New person’s name">'
      + '<button type="button" class="person-new-add">Add</button>'
      + '</div>';
    document.body.append(popover);
    const search = popover.querySelector('.person-search');
    const newInput = popover.querySelector('.person-new-input');
    const newAdd = popover.querySelector('.person-new-add');
    search.oninput = renderList;
    search.onkeydown = event => {
      if (event.key === 'Escape') { close(); return; }
      if (event.key !== 'Enter') return;
      event.preventDefault();
      const typed = search.value.trim();
      if (!typed) return;
      const exact = getNames().find(name => name.toLowerCase() === typed.toLowerCase());
      if (exact) choose(exact); else showNewEntry(typed);
    };
    const addNew = () => {
      const name = newInput.value.trim();
      if (!name) { newInput.focus(); return; }
      choose(name);
    };
    newAdd.onclick = addNew;
    newInput.onkeydown = event => {
      if (event.key === 'Enter') { event.preventDefault(); addNew(); }
      else if (event.key === 'Escape') { event.preventDefault(); showSearch(); }
    };
    renderList();
    position();
    search.focus();
    document.addEventListener('click', onDocClick);
    document.addEventListener('keydown', onDocKeydown);
    window.addEventListener('resize', onReposition);
    window.addEventListener('scroll', onReposition, true);
  }

  trigger.onclick = event => {
    event.stopPropagation();
    if (popover) close(); else open();
  };

  return { close, focus: open, reset: () => { trigger.textContent = placeholder; } };
}
