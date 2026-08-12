// A "who is this" combobox built from a <button> trigger, never a native
// text <input> for the visible field -- buttons are never autofill targets,
// which sidesteps the browser address-autofill popups a plain
// autocomplete="off" input didn't reliably suppress (Firefox's Form
// Autofill in particular mostly ignores autocomplete="off"). Shared by
// faces-review.js, people-review.js, and viewer.js.
//
// createPersonPicker({container, getNames, placeholder, onChoose}):
//   container   -- an empty element to fill with the picker's DOM
//   getNames()  -- returns the current array of known person names; called
//                  fresh on every open/filter so newly-added names (from
//                  this picker or another one on the page) show up without
//                  a page reload
//   placeholder -- trigger button text before anything is chosen
//   onChoose(name) -- called with the chosen (existing or newly typed) name
function createPersonPicker({ container, getNames, placeholder, onChoose }) {
  container.classList.add('person-picker');
  container.innerHTML = '<button type="button" class="person-trigger"></button>'
    + '<div class="person-popover">'
    + '<input type="text" class="person-search" autocomplete="off" placeholder="Search people">'
    + '<div class="person-list"></div>'
    + '<div class="person-new-row" hidden>'
    + '<input type="text" class="person-new-input" autocomplete="off" placeholder="New person’s name">'
    + '<button type="button" class="person-new-add">Add</button>'
    + '</div>'
    + '</div>';
  const trigger = container.querySelector('.person-trigger');
  const popover = container.querySelector('.person-popover');
  const search = container.querySelector('.person-search');
  const list = container.querySelector('.person-list');
  const newRow = container.querySelector('.person-new-row');
  const newInput = container.querySelector('.person-new-input');
  const newAdd = container.querySelector('.person-new-add');
  trigger.textContent = placeholder;

  function showNewEntry(prefill) {
    search.hidden = true;
    list.hidden = true;
    newRow.hidden = false;
    newInput.value = prefill || '';
    newInput.focus();
  }

  function showSearch() {
    newRow.hidden = true;
    search.hidden = false;
    list.hidden = false;
    search.focus();
  }

  function renderList() {
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

  function open() {
    showSearch();
    search.value = '';
    renderList();
    popover.classList.add('open');
  }

  function close() {
    popover.classList.remove('open');
  }

  trigger.onclick = event => {
    event.stopPropagation();
    if (popover.classList.contains('open')) close(); else open();
  };
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
  document.addEventListener('click', event => { if (!container.contains(event.target)) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });

  return { close, focus: open, reset: () => { trigger.textContent = placeholder; } };
}
