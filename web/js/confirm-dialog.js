// In-page confirmation for pages that cannot rely on the browser's own
// confirmation dialog: a browser that has blocked a page's dialogs answers it
// with Cancel and shows nothing, so the button it guards looks dead.
function askConfirm(title, paragraphs, actionLabel) {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'confirm-dialog';
    dialog.setAttribute('aria-labelledby', 'confirmDialogTitle');
    const heading = document.createElement('h2');
    heading.id = 'confirmDialogTitle';
    heading.textContent = title;
    dialog.append(heading);
    paragraphs.forEach(text => {
      const p = document.createElement('p');
      p.textContent = text;
      dialog.append(p);
    });
    const bar = document.createElement('div');
    bar.className = 'confirm-dialog-bar';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = 'Cancel';
    const ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'confirm-dialog-danger';
    ok.textContent = actionLabel;
    bar.append(cancel, ok);
    dialog.append(bar);
    let answer = false;
    cancel.onclick = () => dialog.close();
    ok.onclick = () => { answer = true; dialog.close(); };
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(answer); });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}
