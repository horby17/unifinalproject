(() => {
  const dialog = document.getElementById('user-dialog');
  if (!dialog) return;
  const form = document.getElementById('user-form');
  const action = document.getElementById('user-action');
  const password = document.getElementById('user-password');
  const configureDialog = () => {
    const editing = action.value === 'update';
    document.getElementById('user-dialog-title').textContent = editing ? 'Editar usuario' : 'Crear usuario';
    document.getElementById('user-save').textContent = editing ? 'Guardar cambios' : 'Crear usuario';
    document.getElementById('user-password-label').textContent = editing ? 'Nueva contraseña (opcional)' : 'Contraseña';
    document.getElementById('user-password-help').textContent = editing ? 'Deja la contraseña vacía para conservar la actual.' : 'Usa al menos 8 caracteres.';
    password.required = !editing;
    dialog.showModal();
    document.getElementById('user-name').focus();
  };
  document.getElementById('create-user').addEventListener('click', () => {
    form.reset();
    action.value = 'create';
    document.getElementById('user-id').value = '';
    document.getElementById('user-name').value = '';
    document.getElementById('user-role').value = 'user';
    password.value = '';
    configureDialog();
  });
  document.querySelectorAll('[data-edit-user]').forEach(button => button.addEventListener('click', () => {
    action.value = 'update';
    document.getElementById('user-id').value = button.dataset.editUser;
    document.getElementById('user-name').value = button.dataset.username;
    document.getElementById('user-role').value = button.dataset.role;
    password.value = '';
    configureDialog();
  }));
  document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => dialog.close()));
  if (dialog.dataset.reopen) configureDialog();
  const rows = [...document.querySelectorAll('[data-user-row]')];
  const search = document.getElementById('user-search');
  let status = 'all', page = 1;
  const perPage = 10;
  const render = () => {
    const query = search.value.trim().toLocaleLowerCase();
    const matches = rows.filter(row => (status === 'all' || row.dataset.state === status) && row.dataset.name.toLocaleLowerCase().includes(query));
    const pages = Math.max(1, Math.ceil(matches.length / perPage));
    page = Math.min(page, pages);
    rows.forEach(row => { row.hidden = true; });
    matches.slice((page - 1) * perPage, page * perPage).forEach(row => { row.hidden = false; });
    document.getElementById('users-empty').hidden = matches.length !== 0;
    document.getElementById('user-count').textContent = matches.length ? `${(page - 1) * perPage + 1}–${Math.min(page * perPage, matches.length)} de ${matches.length} usuarios` : '0 usuarios';
    document.getElementById('users-page').textContent = `Página ${page} de ${pages}`;
    document.getElementById('users-prev').disabled = page === 1;
    document.getElementById('users-next').disabled = page === pages;
  };
  document.querySelectorAll('[data-status]').forEach(button => button.addEventListener('click', () => {
    status = button.dataset.status;
    page = 1;
    document.querySelectorAll('[data-status]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === button)));
    render();
  }));
  search.addEventListener('input', () => { page = 1; render(); });
  document.getElementById('users-prev').addEventListener('click', () => { page--; render(); });
  document.getElementById('users-next').addEventListener('click', () => { page++; render(); });
  render();
})();
