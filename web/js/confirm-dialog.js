// In-page dialogs for every page. A browser that has blocked a page's own
// dialogs answers them with Cancel (or nothing) and shows no message, so the
// button they guard looks dead. These open inside LensLedger instead.
//
//   askConfirm(title, paragraphs, actionLabel, danger = true) -> Promise<boolean>
//   askNotice(title, paragraphs)                              -> Promise<true>
//   askChoice(title, paragraphs, choices)                     -> Promise<value|null>
//     choices: [{ label, detail, value }]
let lensDialogCount = 0;

function lensDialog(title, paragraphs, buildBar) {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'confirm-dialog';
    const headingId = 'confirmDialogTitle' + (++lensDialogCount);
    dialog.setAttribute('aria-labelledby', headingId);
    const heading = document.createElement('h2');
    heading.id = headingId;
    heading.textContent = title;
    dialog.append(heading);
    (paragraphs || []).forEach(text => {
      const p = document.createElement('p');
      p.textContent = text;
      dialog.append(p);
    });
    let answer = null;
    const bar = document.createElement('div');
    const finish = value => { answer = value; dialog.close(); };
    const focusTarget = buildBar(bar, finish);
    dialog.append(bar);
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(answer); });
    document.body.append(dialog);
    dialog.showModal();
    if (focusTarget) focusTarget.focus();
  });
}

function lensDialogButton(label, className, onclick) {
  const button = document.createElement('button');
  button.type = 'button';
  if (className) button.className = className;
  button.textContent = label;
  button.onclick = onclick;
  return button;
}

function askConfirm(title, paragraphs, actionLabel, danger = true) {
  return lensDialog(title, paragraphs, (bar, finish) => {
    bar.className = 'confirm-dialog-bar';
    const cancel = lensDialogButton('Cancel', 'secondary', () => finish(false));
    const ok = lensDialogButton(actionLabel, danger ? 'confirm-dialog-danger' : '', () => finish(true));
    bar.append(cancel, ok);
    return danger ? cancel : ok;
  }).then(answer => answer === true);
}

function askNotice(title, paragraphs) {
  return lensDialog(title, paragraphs, (bar, finish) => {
    bar.className = 'confirm-dialog-bar';
    const ok = lensDialogButton('OK', '', () => finish(true));
    bar.append(ok);
    return ok;
  }).then(() => true);
}

function askChoice(title, paragraphs, choices) {
  return lensDialog(title, paragraphs, (bar, finish) => {
    bar.className = 'confirm-dialog-choices';
    let first = null;
    choices.forEach(choice => {
      const button = lensDialogButton('', 'confirm-dialog-choice', () => finish(choice.value));
      const name = document.createElement('strong');
      name.textContent = choice.label;
      button.append(name);
      if (choice.detail) {
        const detail = document.createElement('span');
        detail.textContent = choice.detail;
        button.append(detail);
      }
      bar.append(button);
      if (!first) first = button;
    });
    bar.append(lensDialogButton('Cancel', 'secondary', () => finish(null)));
    return first;
  });
}
