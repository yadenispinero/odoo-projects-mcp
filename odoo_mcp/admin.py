from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .auth import UserDirectory, bearer_token
from .config import InstanceDirectory
from .odoo_api import OdooApiError, OdooJson2


ROLE_GROUPS = [
    {"module": "Proyectos", "roles": ["project_manager"]},
    {"module": "Tareas", "roles": ["task_editor", "task_archiver"]},
    {"module": "Soporte", "roles": ["support_ticket_editor"]},
    {"module": "CRM", "roles": ["crm_editor"]},
    {"module": "Contactos", "roles": ["partner_viewer", "partner_editor"]},
    {"module": "Compras y facturación", "roles": ["purchase_viewer", "purchase_manager", "purchase_approver", "vendor_bill_creator"]},
    {"module": "Sitio web", "roles": ["website_editor", "publisher"]},
    {"module": "Blog", "roles": ["blog_editor", "blog_publisher", "blog_scheduler"]},
    {"module": "Acceso general", "roles": ["viewer", "administrator"]},
]


ADMIN_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Odoo MCP · Administration</title>
  <style>
    :root { color-scheme: light; font: 16px system-ui, sans-serif; color: #182230; background: #f3f5f8; }
    body { margin: 0 auto; max-width: 1160px; padding: 32px 18px 64px; }
    h1 { margin-bottom: 4px; } .muted { color: #596579; }
    section, .user { background: white; border: 1px solid #d8dee8; border-radius: 10px; padding: 20px; margin: 18px 0; }
    #app.admin-layout { display: block; }
    #app.admin-layout.hidden { display: none; }
    #toolbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
    .admin-tabs { display: flex; gap: 8px; }
    .admin-tabs button { background: transparent; color: #26364b; border: 1px solid #aab5c4; }
    .admin-tabs button.active { background: #185abd; color: white; border-color: #185abd; }
    .admin-view { margin-top: 20px; }
    #instance-layout { display: grid; grid-template-columns: minmax(250px, 340px) minmax(0, 1fr); gap: 20px; align-items: start; }
    .instance-card { border: 1px solid #d8dee8; border-radius: 8px; padding: 14px; margin: 12px 0; background: #fbfcfe; }
    .instance-card h3 { margin: 0 0 6px; }
    .instance-meta { overflow-wrap: anywhere; margin: 5px 0; }
    #users-layout { display: block; }
    #create-panel { position: static; }
    #create-panel > summary { cursor: pointer; font-size: 1.15rem; font-weight: 650; }
    #create-panel[open] > summary { margin-bottom: 16px; }
    .create-user-form { display: grid; grid-template-columns: minmax(220px, .7fr) minmax(260px, 1fr); gap: 0 24px; align-items: start; }
    .create-user-form > label, .create-user-form > input, .create-user-form > .instance-picker,
    .create-user-form > .field-help { grid-column: 1; }
    .create-user-form > #new-roles { grid-column: 2; grid-row: 1 / span 5; }
    .create-user-form > #create-user, .create-user-form > #created-token { grid-column: 1 / -1; }
    #enabled-users-panel { min-width: 0; }
    .revoked-list { margin: 18px 0; padding: 14px 18px; background: white; border: 1px solid #d8dee8; border-radius: 8px; }
    .revoked-list summary { cursor: pointer; font-weight: 600; }
    label { display: block; margin: 12px 0 6px; font-weight: 600; }
    input[type=text], input[type=password] { box-sizing: border-box; width: min(100%, 520px); padding: 10px; border: 1px solid #9aa6b7; border-radius: 6px; }
    select { box-sizing: border-box; max-width: 100%; padding: 9px; border: 1px solid #9aa6b7; border-radius: 6px; background: white; }
    .instance-picker { display: grid; gap: 8px; margin: 8px 0 14px; }
    .instance-choice { display: flex; gap: 9px; align-items: flex-start; padding: 10px; margin: 0; border: 1px solid #d8dee8; border-radius: 7px; background: #fbfcfe; font-weight: 400; }
    .instance-choice input { margin-top: 4px; }
    .instance-choice span { min-width: 0; overflow-wrap: anywhere; }
    .instance-choice small { display: block; color: #596579; font-weight: 400; }
    .role-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(245px, 1fr)); gap: 12px; margin: 12px 0; }
    .role-group { border: 1px solid #d8dee8; border-radius: 8px; padding: 12px; background: #fbfcfe; }
    .role-group h4 { margin: 0 0 8px; color: #26364b; }
    .role-options { display: flex; flex-direction: column; gap: 8px; }
    .role-options label { display: flex; align-items: flex-start; gap: 7px; font-weight: 400; margin: 0; }
    .role-options input { margin-top: 4px; }
    button { border: 0; border-radius: 6px; background: #185abd; color: white; padding: 10px 14px; cursor: pointer; margin: 8px 8px 0 0; }
    button.danger { background: #a32626; } button.secondary { background: #566273; }
    .hidden { display: none; } .token { overflow-wrap: anywhere; padding: 12px; background: #edf7ed; border: 1px solid #9bc99b; border-radius: 6px; }
    .error { color: #a32626; white-space: pre-wrap; }
    .notice { margin: 12px 0; padding: 10px 12px; border: 1px solid #9bc99b; border-radius: 6px; color: #185b28; background: #edf7ed; }
    .notice.error { border-color: #d99; color: #a32626; background: #fff1f1; }
    .field-help { margin: 5px 0 10px; font-size: .9rem; }
    .user h3 { margin: 0; }
    @media (max-width: 760px) {
      body { padding: 18px 12px 40px; }
      #instance-layout { grid-template-columns: minmax(0, 1fr); }
      .create-user-form { grid-template-columns: minmax(0, 1fr); }
      .create-user-form > #new-roles, .create-user-form > #create-user,
      .create-user-form > #created-token { grid-column: 1; grid-row: auto; }
      #create-panel { position: static; }
      section, .user { padding: 15px; }
    }
  </style>
</head>
<body>
  <h1>Odoo MCP administration</h1>
  <p class="muted">Manage Odoo connections, named MCP users, and the tools each role can use. Odoo’s own permissions still apply.</p>
  <section id="login">
    <h2>Administrator sign in</h2>
    <p class="muted">Enter the existing owner Bearer token. It is kept in this browser tab only.</p>
    <label for="token">Owner token</label><input id="token" type="password" autocomplete="current-password">
    <div><button id="sign-in">Sign in</button></div><p id="login-error" class="error"></p>
  </section>
  <main id="app" class="hidden admin-layout">
    <div id="toolbar">
      <nav class="admin-tabs" role="tablist" aria-label="Administration sections">
        <button id="tab-connections" class="active" type="button" role="tab" aria-controls="instances-panel" aria-selected="true">Odoo connections</button>
        <button id="tab-users" type="button" role="tab" aria-controls="users-panel" aria-selected="false">Users and access</button>
      </nav>
      <button id="sign-out" class="secondary">Sign out</button>
    </div>
    <section id="instances-panel" class="admin-view">
      <h2>Odoo instances</h2>
      <p id="instance-message" class="notice hidden" role="status" aria-live="polite"></p>
      <p class="muted">Instance connections are stored outside the project source, in the private MCP data directory. API keys are never displayed after saving.</p>
      <p id="wildcard-warning" class="muted"></p>
      <div id="instance-layout">
        <div>
          <h3 id="instance-form-title">Add an instance</h3>
          <label for="instance-name">Instance key</label><input id="instance-name" type="text" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" placeholder="old-merchise-15" title="Use 1 to 64 letters, numbers, hyphens, or underscores; no spaces." autocomplete="off" required>
          <p class="muted field-help">This key is used by MCP users to choose the connection. Use letters, numbers, hyphens, or underscores, with no spaces.</p>
          <label for="instance-url">Odoo base URL (HTTPS)</label><input id="instance-url" type="text" placeholder="https://odoo.example.com" autocomplete="url" required>
          <label for="instance-api-type">Connection mode</label><select id="instance-api-type"><option value="json2">JSON-2 (Odoo 19+)</option><option value="xmlrpc">XML-RPC (Odoo 15/18)</option></select>
          <p id="api-mode-help" class="muted field-help"></p>
          <label id="instance-database-label" for="instance-database">Database (optional)</label><input id="instance-database" type="text" autocomplete="off">
          <div id="xmlrpc-fields"><label for="instance-username">Odoo username/login</label><input id="instance-username" type="text" autocomplete="username"></div>
          <label for="instance-api-key">Odoo API key</label><input id="instance-api-key" type="password" autocomplete="new-password">
          <p class="muted">When editing, leave the key blank to keep the saved key. Remove a saved key only after deactivating that instance.</p>
          <label class="muted"><input id="clear-instance-key" type="checkbox"> Remove the saved API key</label>
          <p class="muted">Test the exact connection settings before saving. Saving repeats the test on the server.</p>
          <div><button id="test-instance" class="secondary">Test connection</button><button id="save-instance" disabled>Save instance</button><button id="cancel-instance" class="secondary hidden">Cancel edit</button></div>
        </div>
        <div><h3>Configured instances</h3><div id="instances-list"></div></div>
      </div>
    </section>
    <section id="users-panel" class="admin-view hidden">
      <h2>Users and access</h2>
      <p class="muted field-help">Ticket access: <strong>viewer</strong> grants search/read; add <strong>support_ticket_editor</strong> to allow changes.</p>
      <p id="user-message" class="notice hidden" role="status" aria-live="polite"></p>
      <div id="users-layout">
        <section id="enabled-users-panel"><h3>Enabled users</h3><div id="users"></div></section>
        <details id="create-panel" class="user">
          <summary>Add a user</summary>
          <div class="create-user-form">
          <label for="new-username">Username</label><input id="new-username" type="text" maxlength="64" autocomplete="off" required>
          <label>Allowed Odoo instances</label><div id="new-instances" class="instance-picker"></div>
          <p class="muted field-help">Choose one or more active connections. “All instances” also grants access to future connections.</p>
          <div id="new-roles" class="role-groups"></div>
          <button id="create-user">Create user and generate token</button>
          <div id="created-token" class="hidden"></div>
          </div>
        </details>
      </div>
    </section>
  </main>
<script>
(() => {
  const tokenKey = 'odoo-mcp-admin-token';
  const login = document.querySelector('#login');
  const app = document.querySelector('#app');
  const labels = {
    viewer: 'Consultar proyectos, tareas, tickets, canales de Discuss, oportunidades CRM y páginas',
    crm_editor: 'Crear, editar, cambiar de etapa, archivar y restaurar oportunidades de CRM',
    partner_viewer: 'Consultar contactos y empresas',
    partner_editor: 'Consultar, crear, editar, archivar y reactivar contactos y empresas',
    task_editor: 'Crear y actualizar tareas, cambiar etapas y estados',
    task_archiver: 'Archivar y restaurar tareas',
    support_ticket_editor: 'Editar asunto, descripción, prioridad, equipo, etapa y persona asignada; quitar asignación',
    purchase_viewer: 'Consultar órdenes de compra, proveedores, líneas e importes',
    purchase_manager: 'Crear RFQ, editar órdenes en borrador y administrar sus líneas',
    purchase_approver: 'Confirmar RFQ y convertirlas en órdenes de compra',
    vendor_bill_creator: 'Crear borradores de factura de proveedor desde líneas facturables de una orden',
    website_editor: 'Editar contenido de páginas',
    publisher: 'Publicar y despublicar páginas',
    blog_editor: 'Buscar, leer, crear y editar entradas del blog',
    blog_publisher: 'Publicar y despublicar entradas del blog',
    blog_scheduler: 'Programar entradas del blog para una fecha futura',
    project_manager: 'Consultar, crear, actualizar, archivar y desarchivar proyectos; consultar etapas de tareas',
    administrator: 'Todos los permisos disponibles en el MCP'
  };
  let roleGroups = [];
  let instanceNames = [];
  let instanceRecords = [];
  let wildcardUserCount = 0;
  let editingInstance = null;
  let editingInstanceActive = true;
  let testedInstancePayload = null;
  const token = () => sessionStorage.getItem(tokenKey) || '';
  const notify = (text, isError = false, section = 'users') => {
    const message = document.querySelector(section === 'instances' ? '#instance-message' : '#user-message');
    message.textContent = text;
    message.className = isError ? 'notice error' : 'notice';
  };
  const api = async (path, options = {}) => {
    const response = await fetch('/admin/api' + path, {
      ...options,
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token(), ...(options.headers || {}) }
    });
    const data = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || ('Request failed (' + response.status + ')'));
    return data;
  };
  const selectedRoles = container => [...container.querySelectorAll('input:checked')].map(input => input.value);
  const selectedInstanceNames = container => [...container.querySelectorAll('input:checked')].map(input => input.value);
  const drawInstanceChoices = (container, selected = []) => {
    container.replaceChildren();
    const selectedNames = Array.isArray(selected) ? selected : [];
    const names = [...new Set([...instanceNames, ...selectedNames.filter(name => name !== '*')])];
    const addChoice = (name, title, description) => {
      const label = document.createElement('label'); label.className = 'instance-choice';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = name;
      checkbox.checked = selectedNames.includes(name);
      checkbox.addEventListener('change', () => {
        const checks = [...container.querySelectorAll('input[type=checkbox]')];
        if (name === '*' && checkbox.checked) checks.filter(item => item !== checkbox).forEach(item => { item.checked = false; });
        if (name !== '*' && checkbox.checked) {
          const all = checks.find(item => item.value === '*'); if (all) all.checked = false;
        }
      });
      const text = document.createElement('span'); text.append(document.createTextNode(title));
      const detail = document.createElement('small'); detail.textContent = description; text.append(detail);
      label.append(checkbox, text); container.append(label);
    };
    addChoice('*', 'All instances', 'Includes current and future connections');
    if (!names.length) {
      const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'No configured Odoo instances yet.';
      container.append(empty);
    }
    for (const name of names) {
      const record = instanceRecords.find(item => item.name === name);
      const availability = record ? (record.active ? record.base_url : 'Inactive · ' + record.base_url) : 'Not currently configured';
      addChoice(name, name, availability);
    }
  };
  const showAdminView = view => {
    const connections = view === 'connections';
    document.querySelector('#instances-panel').classList.toggle('hidden', !connections);
    document.querySelector('#users-panel').classList.toggle('hidden', connections);
    document.querySelector('#tab-connections').classList.toggle('active', connections);
    document.querySelector('#tab-connections').setAttribute('aria-selected', String(connections));
    document.querySelector('#tab-users').classList.toggle('active', !connections);
    document.querySelector('#tab-users').setAttribute('aria-selected', String(!connections));
  };
  const updateConnectionFields = () => {
    const xmlrpc = document.querySelector('#instance-api-type').value === 'xmlrpc';
    document.querySelector('#xmlrpc-fields').classList.toggle('hidden', !xmlrpc);
    const database = document.querySelector('#instance-database');
    database.required = xmlrpc;
    document.querySelector('#instance-database-label').textContent = xmlrpc ? 'Database (required for XML-RPC)' : 'Database (optional for JSON-2)';
    document.querySelector('#instance-username').required = xmlrpc;
    document.querySelector('#instance-api-key').required = !editingInstance;
    document.querySelector('#api-mode-help').textContent = xmlrpc
      ? 'Use XML-RPC for Odoo 15/18. Database and login are required; the API key is used as the password.'
      : 'Use JSON-2 for Odoo 19+. The API key is required; specify a database only when the Odoo host needs it.';
  };
  const instancePayload = () => ({
    name: document.querySelector('#instance-name').value.trim(),
    base_url: document.querySelector('#instance-url').value.trim(),
    api_type: document.querySelector('#instance-api-type').value,
    database: document.querySelector('#instance-database').value.trim(),
    username: document.querySelector('#instance-username').value.trim(),
    api_key: document.querySelector('#instance-api-key').value,
    clear_api_key: document.querySelector('#clear-instance-key').checked,
    existing_name: editingInstance
  });
  const instancePayloadKey = () => JSON.stringify(instancePayload());
  const invalidateInstanceTest = () => {
    testedInstancePayload = null;
    document.querySelector('#save-instance').disabled = true;
  };
  const resetInstanceForm = () => {
    editingInstance = null;
    editingInstanceActive = true;
    invalidateInstanceTest();
    document.querySelector('#instance-form-title').textContent = 'Add an instance';
    document.querySelector('#instance-name').disabled = false;
    document.querySelector('#instance-name').value = '';
    document.querySelector('#instance-url').value = '';
    document.querySelector('#instance-api-type').value = 'json2';
    document.querySelector('#instance-database').value = '';
    document.querySelector('#instance-username').value = '';
    document.querySelector('#instance-api-key').value = '';
    document.querySelector('#clear-instance-key').checked = false;
    document.querySelector('#instance-api-key').disabled = false;
    document.querySelector('#test-instance').disabled = false;
    document.querySelector('#cancel-instance').classList.add('hidden');
    updateConnectionFields();
    const instanceMessage = document.querySelector('#instance-message');
    instanceMessage.textContent = ''; instanceMessage.className = 'notice hidden';
  };
  const loadInstances = async () => {
    const data = await api('/instances');
    instanceNames = data.instances;
    instanceRecords = data.records;
    wildcardUserCount = data.wildcard_users;
    const newPicker = document.querySelector('#new-instances');
    drawInstanceChoices(newPicker, selectedInstanceNames(newPicker));
    document.querySelector('#wildcard-warning').textContent = data.wildcard_users
      ? data.wildcard_users + ' active user(s) have * access and will also gain access to any new instance. Review their scopes after adding one.'
      : 'No active users currently have wildcard (*) access.';
    const list = document.querySelector('#instances-list'); list.replaceChildren();
    for (const item of instanceRecords) {
      const card = document.createElement('article'); card.className = 'instance-card';
      const heading = document.createElement('h3'); heading.textContent = item.name + (item.active ? ' · active' : ' · inactive');
      const url = document.createElement('p'); url.className = 'instance-meta'; url.textContent = item.base_url;
      const database = document.createElement('p'); database.className = 'instance-meta'; database.textContent = 'Database: ' + (item.database || 'not specified');
      const apiType = document.createElement('p'); apiType.className = 'instance-meta'; apiType.textContent = 'Connection mode: ' + item.api_type;
      const credential = document.createElement('p'); credential.className = 'instance-meta'; credential.textContent = 'API key: ' + (item.api_key_configured ? 'configured' : 'not configured');
      const edit = document.createElement('button'); edit.textContent = 'Edit'; edit.className = 'secondary';
      edit.addEventListener('click', () => {
        editingInstance = item.name;
        editingInstanceActive = item.active;
        document.querySelector('#instance-form-title').textContent = 'Edit instance';
        const name = document.querySelector('#instance-name'); name.value = item.name; name.disabled = true;
        document.querySelector('#instance-url').value = item.base_url;
        document.querySelector('#instance-api-type').value = item.api_type || 'json2';
        document.querySelector('#instance-database').value = item.database || '';
        document.querySelector('#instance-username').value = item.username || '';
        document.querySelector('#instance-api-key').value = '';
        document.querySelector('#clear-instance-key').checked = false;
        document.querySelector('#instance-api-key').disabled = false;
        document.querySelector('#test-instance').disabled = false;
        document.querySelector('#cancel-instance').classList.remove('hidden');
        updateConnectionFields();
        invalidateInstanceTest();
      });
      const toggle = document.createElement('button'); toggle.textContent = item.active ? 'Deactivate' : 'Reactivate'; toggle.className = item.active ? 'danger' : '';
      toggle.addEventListener('click', async () => {
        if (item.active && !confirm('Deactivate ' + item.name + '? MCP requests to this instance will stop. Existing user access assignments are retained.')) return;
        try {
          await api('/instances/' + encodeURIComponent(item.name) + '/status', {method:'PUT', body:JSON.stringify({active:!item.active})});
          await loadInstances(); await loadUsers(); notify(item.active ? 'Instance deactivated.' : 'Instance reactivated.', false, 'instances');
        } catch (error) { notify(error.message, true, 'instances'); }
      });
      card.append(heading,url,database,apiType,credential,edit,toggle); list.append(card);
    }
  };
  document.querySelector('#cancel-instance').addEventListener('click', resetInstanceForm);
  document.querySelector('#tab-connections').addEventListener('click', () => showAdminView('connections'));
  document.querySelector('#tab-users').addEventListener('click', () => showAdminView('users'));
  document.querySelector('#instance-api-type').addEventListener('change', () => { updateConnectionFields(); invalidateInstanceTest(); });
  document.querySelector('#clear-instance-key').addEventListener('change', event => {
    document.querySelector('#instance-api-key').disabled = event.target.checked;
    invalidateInstanceTest();
    const testButton = document.querySelector('#test-instance');
    testButton.disabled = event.target.checked && !editingInstanceActive;
    if (event.target.checked && !editingInstanceActive) {
      testedInstancePayload = instancePayloadKey();
      document.querySelector('#save-instance').disabled = false;
      notify('This inactive instance can have its saved key removed without a connection test.', false, 'instances');
    }
  });
  for (const selector of ['#instance-name', '#instance-url', '#instance-api-type', '#instance-database', '#instance-username', '#instance-api-key']) {
    document.querySelector(selector).addEventListener('input', invalidateInstanceTest);
  }
  document.querySelector('#test-instance').addEventListener('click', async () => {
    invalidateInstanceTest();
    const requiredFields = ['#instance-name', '#instance-url', '#instance-database', '#instance-username', '#instance-api-key'];
    for (const selector of requiredFields) {
      const field = document.querySelector(selector);
      if (field.required && !field.reportValidity()) return;
    }
    const testedPayload = instancePayloadKey();
    try {
      const result = await api('/instances/test', {method:'POST', body:JSON.stringify(instancePayload())});
      if (testedPayload !== instancePayloadKey()) throw new Error('Settings changed during the test. Test again before saving.');
      testedInstancePayload = testedPayload;
      document.querySelector('#save-instance').disabled = false;
      notify(result.message, false, 'instances');
    } catch (error) { notify(error.message, true, 'instances'); }
  });
  document.querySelector('#save-instance').addEventListener('click', async () => {
    const payload = instancePayload();
    if (testedInstancePayload !== JSON.stringify(payload)) {
      invalidateInstanceTest();
      notify('Test the current connection settings successfully before saving.', true, 'instances');
      return;
    }
    if (payload.clear_api_key && !confirm('Remove the saved API key from this inactive instance?')) return;
    if (!editingInstance && wildcardUserCount > 0 &&
        !confirm('Users with * access will also gain access to this new Odoo instance. Add it anyway?')) return;
    try {
      if (editingInstance) {
        await api('/instances/' + encodeURIComponent(editingInstance), {method:'PUT', body:JSON.stringify(payload)});
      } else {
        await api('/instances', {method:'POST', body:JSON.stringify(payload)});
      }
      resetInstanceForm(); await loadInstances(); await loadUsers(); notify('Instance saved.', false, 'instances');
    } catch (error) { notify(error.message, true, 'instances'); }
  });
  const drawRoleBoxes = (container, chosen = []) => {
    container.replaceChildren();
    container.classList.add('role-groups');
    for (const group of roleGroups) {
      const groupBox = document.createElement('div'); groupBox.className = 'role-group';
      const heading = document.createElement('h4'); heading.textContent = group.module;
      const options = document.createElement('div'); options.className = 'role-options';
      for (const role of group.roles) {
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox'; checkbox.value = role; checkbox.checked = chosen.includes(role);
        label.append(checkbox, document.createTextNode(role + ' — ' + labels[role]));
        options.append(label);
      }
      groupBox.append(heading, options); container.append(groupBox);
    }
  };
  const loadUsers = async () => {
    const data = await api('/users');
    const list = document.querySelector('#users'); list.replaceChildren();
    const enabledUsers = data.users.filter(user => user.active);
    for (const user of enabledUsers) {
      const card = document.createElement('article'); card.className = 'user';
      const heading = document.createElement('h3'); heading.textContent = user.username;
      const roleBox = document.createElement('div'); drawRoleBoxes(roleBox, user.roles);
      const save = document.createElement('button'); save.textContent = 'Save roles';
      save.addEventListener('click', async () => {
        try { await api('/users/' + encodeURIComponent(user.username), { method: 'PATCH', body: JSON.stringify({roles: selectedRoles(roleBox)}) }); await loadUsers(); notify('Roles saved.'); }
        catch (error) { notify(error.message, true); }
      });
      card.append(heading, roleBox, save);
      const instanceLabel = document.createElement('label'); instanceLabel.textContent = 'Allowed Odoo instances';
      const instanceInput = document.createElement('div'); instanceInput.className = 'instance-picker';
      drawInstanceChoices(instanceInput, user.instances || ['*']);
      const instanceSave = document.createElement('button'); instanceSave.textContent = 'Save instance access'; instanceSave.className = 'secondary';
      instanceSave.addEventListener('click', async () => {
        try {
          const instances = selectedInstanceNames(instanceInput);
          if (!instances.length) throw new Error('Select at least one Odoo instance or All instances.');
          await api('/users/' + encodeURIComponent(user.username) + '/instances', { method: 'PUT', body: JSON.stringify({instances}) });
          await loadUsers(); notify('Instance access saved.');
        }
        catch (error) { notify(error.message, true); }
      });
      card.append(instanceLabel, instanceInput, instanceSave);
      const rotate = document.createElement('button'); rotate.textContent = 'Rotate token'; rotate.className = 'secondary';
      rotate.addEventListener('click', async () => {
        if (!confirm('Replace the token for ' + user.username + '? Their old token will stop working immediately.')) return;
        try {
          const result = await api('/users/' + encodeURIComponent(user.username) + '/rotate-token', { method: 'POST' });
          const tokenPanel = document.createElement('p'); tokenPanel.className = 'token'; tokenPanel.textContent = result.token + ' — copy now; it will not be shown again.';
          card.append(tokenPanel); notify('Token rotated.');
        } catch (error) { notify(error.message, true); }
      });
      const revoke = document.createElement('button'); revoke.textContent = 'Revoke access'; revoke.className = 'danger';
      revoke.addEventListener('click', async () => {
        if (!confirm('Revoke access for ' + user.username + '? Their token will stop working immediately.')) return;
        try { await api('/users/' + encodeURIComponent(user.username), { method: 'DELETE' }); await loadUsers(); notify('Access revoked.'); }
        catch (error) { notify(error.message, true); }
      });
      card.append(rotate, revoke);
      list.append(card);
    }
    const revokedUsers = data.users.filter(user => !user.active);
    if (revokedUsers.length) {
      const details = document.createElement('details'); details.className = 'revoked-list';
      const summary = document.createElement('summary'); summary.textContent = 'Revoked users (' + revokedUsers.length + ')';
      details.append(summary);
      for (const user of revokedUsers) {
        const row = document.createElement('p'); row.textContent = user.username; details.append(row);
      }
      list.append(details);
    }
  };
  const enter = async () => {
    try {
      const [data] = await Promise.all([api('/roles'), loadInstances()]);
      roleGroups = data.groups;
      updateConnectionFields();
      showAdminView('connections');
      drawRoleBoxes(document.querySelector('#new-roles'), ['viewer']);
      await loadUsers(); login.classList.add('hidden'); app.classList.remove('hidden');
      document.querySelector('#login-error').textContent = '';
    } catch (error) {
      sessionStorage.removeItem(tokenKey); document.querySelector('#login-error').textContent = error.message;
      login.classList.remove('hidden'); app.classList.add('hidden');
    }
  };
  document.querySelector('#sign-in').addEventListener('click', async () => {
    sessionStorage.setItem(tokenKey, document.querySelector('#token').value.trim());
    await enter();
  });
  document.querySelector('#token').addEventListener('keydown', event => { if (event.key === 'Enter') document.querySelector('#sign-in').click(); });
  document.querySelector('#sign-out').addEventListener('click', () => { sessionStorage.removeItem(tokenKey); location.reload(); });
  document.querySelector('#create-user').addEventListener('click', async () => {
    const username = document.querySelector('#new-username').value.trim();
    const chosen = selectedRoles(document.querySelector('#new-roles'));
    const output = document.querySelector('#created-token'); output.replaceChildren(); output.classList.add('hidden');
    try {
      const instances = selectedInstanceNames(document.querySelector('#new-instances'));
      if (!instances.length) throw new Error('Select at least one Odoo instance or All instances.');
      const data = await api('/users', { method: 'POST', body: JSON.stringify({username, roles: chosen, instances}) });
      const note = document.createElement('p'); note.textContent = 'Copy this token now. It will not be shown again.';
      const value = document.createElement('p'); value.className = 'token'; value.textContent = data.token;
      const copy = document.createElement('button'); copy.textContent = 'Copy token'; copy.addEventListener('click', async () => { await navigator.clipboard.writeText(data.token); copy.textContent = 'Copied'; });
      output.append(note, value, copy); output.classList.remove('hidden');
      document.querySelector('#new-username').value = ''; await loadUsers(); notify('User created.');
    } catch (error) { notify(error.message, true); }
  });
  if (token()) enter();
})();
</script>
</body>
</html>"""


class AdminPortMiddleware:
    """Serve the admin page only on its own listening port and the MCP only on the others."""

    def __init__(self, app, admin_port: int | None):
        self.app = app
        self.admin_port = admin_port

    async def __call__(self, scope, receive, send):
        if self.admin_port is not None and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            admin_path = path == "/admin" or path.startswith("/admin/")
            server_port = (scope.get("server") or (None, None))[1]
            if admin_path != (server_port == self.admin_port):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = b"Not Found"
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                        (b"content-length", str(len(body)).encode("ascii"))]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


async def _authorized_admin(request: Request, directory: UserDirectory):
    token = bearer_token(request.headers)
    user = directory.authenticate(token) if token else None
    if user is None:
        return None, JSONResponse({"detail": "Authentication required"}, status_code=401,
                                  headers={"WWW-Authenticate": "Bearer"})
    if "administrator" not in user.roles:
        return None, JSONResponse({"detail": "Administrator role required"}, status_code=403)
    return user, None


def create_admin_routes(directory: UserDirectory, instance_names=(),
                        instance_directory: InstanceDirectory | None = None, on_instances_changed=None) -> list[Route]:
    configured_instances = sorted(set(instance_names))

    def refresh_instances() -> None:
        if instance_directory is None:
            return
        active = instance_directory.active_instances()
        directory.instance_names = frozenset(instance_directory.names(active_only=False))
        if on_instances_changed:
            on_instances_changed(active)

    async def page(request: Request) -> Response:
        return HTMLResponse(ADMIN_PAGE)

    async def list_roles(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        return JSONResponse({"groups": ROLE_GROUPS})

    async def list_users(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        return JSONResponse({"users": directory.list_users()})

    async def list_instances(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        if instance_directory:
            records = instance_directory.public_records()
            active_names = instance_directory.names(active_only=True)
        else:
            records = [{"name": name, "base_url": "", "database": None,
                        "active": True, "api_key_configured": False} for name in configured_instances]
            active_names = configured_instances
        wildcard_users = sum(
            1 for user in directory.list_users()
            if user["active"] and "*" in user.get("instances", [])
        )
        return JSONResponse({"instances": active_names, "records": records, "wildcard_users": wildcard_users})

    async def test_instance(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        if instance_directory is None:
            return JSONResponse({"detail": "Instance management is not enabled"}, status_code=501)
        try:
            body = await request.json()
            clear_api_key = body.get("clear_api_key", False)
            if not isinstance(clear_api_key, bool):
                raise ValueError("clear_api_key must be true or false")
            instance = instance_directory.candidate(
                body.get("name"), body.get("base_url"), body.get("database"), body.get("api_key"),
                clear_api_key, body.get("api_type", "json2"), body.get("username"),
            )
            await verify_credentials(instance)
        except (ValueError, TypeError, OdooApiError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return JSONResponse({"message": "Connection and authenticated read succeeded."})

    async def verify_credentials(instance) -> None:
        api = OdooJson2(instance)
        try:
            await api.test_credentials()
        finally:
            await api.close()

    async def create_instance(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        if instance_directory is None:
            return JSONResponse({"detail": "Instance management is not enabled"}, status_code=501)
        try:
            body = await request.json()
            instance = instance_directory.candidate(
                body.get("name"), body.get("base_url"), body.get("database"), body.get("api_key"),
                False, body.get("api_type", "json2"), body.get("username")
            )
            if instance.name in instance_directory.names(active_only=False):
                return JSONResponse({"detail": "That instance name already exists"}, status_code=409)
            await verify_credentials(instance)
            instance_directory.save(instance)
            refresh_instances()
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OdooApiError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"detail": "Could not securely save the instance configuration"}, status_code=500)
        return JSONResponse({"created": True}, status_code=201)

    async def update_instance(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        if instance_directory is None:
            return JSONResponse({"detail": "Instance management is not enabled"}, status_code=501)
        try:
            body = await request.json()
            clear_api_key = body.get("clear_api_key", False)
            if not isinstance(clear_api_key, bool):
                raise ValueError("clear_api_key must be true or false")
            name = request.path_params["name"]
            if name not in instance_directory.names(active_only=False):
                return JSONResponse({"detail": "Instance not found"}, status_code=404)
            current = next(record for record in instance_directory.public_records() if record["name"] == name)
            if clear_api_key and current["active"]:
                raise ValueError("Deactivate an instance before removing its API key")
            if body.get("name", name) != name:
                raise ValueError("Instance names cannot be changed; add a new name and deactivate the old instance")
            instance = instance_directory.candidate(
                name, body.get("base_url"), body.get("database"), body.get("api_key"),
                clear_api_key, body.get("api_type", current.get("api_type", "json2")),
                body.get("username", current.get("username")),
            )
            if not clear_api_key:
                await verify_credentials(instance)
            instance_directory.save(instance, clear_api_key=clear_api_key)
            refresh_instances()
        except KeyError:
            return JSONResponse({"detail": "Instance not found"}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OdooApiError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"detail": "Could not securely save the instance configuration"}, status_code=500)
        return JSONResponse({"updated": True})

    async def set_instance_status(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        if instance_directory is None:
            return JSONResponse({"detail": "Instance management is not enabled"}, status_code=501)
        try:
            body = await request.json()
            active = body.get("active")
            if not isinstance(active, bool):
                raise ValueError("active must be true or false")
            if active:
                record = next((
                    item for item in instance_directory.public_records()
                    if item["name"] == request.path_params["name"]
                ), None)
                if record is None:
                    raise KeyError(request.path_params["name"])
                if not record["api_key_configured"]:
                    raise ValueError("Configure and test an API key before reactivating this instance")
            instance_directory.set_active(request.path_params["name"], active)
            refresh_instances()
        except KeyError:
            return JSONResponse({"detail": "Instance not found"}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"detail": "Could not securely save the instance configuration"}, status_code=500)
        return JSONResponse({"updated": True})

    async def create_user(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        try:
            body = await request.json()
            requested_instances = body.get("instances")
            if (instance_directory and isinstance(requested_instances, list)
                    and "*" not in requested_instances):
                inactive = set(requested_instances) - set(instance_directory.names(active_only=True))
                if inactive:
                    raise ValueError("New users can only be assigned to active Odoo instances")
            token = directory.add_user(body.get("username"), body.get("roles"), body.get("instances"))
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return JSONResponse({"detail": "That username already exists"}, status_code=409)
            raise
        return JSONResponse({"username": body["username"].strip(), "token": token}, status_code=201)

    async def update_user(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        try:
            body = await request.json()
            directory.update_roles(request.path_params["username"], body.get("roles"))
        except KeyError:
            return JSONResponse({"detail": "User not found"}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return JSONResponse({"updated": True})

    async def update_instances(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        try:
            body = await request.json()
            directory.update_instances(request.path_params["username"], body.get("instances"))
        except KeyError:
            return JSONResponse({"detail": "User not found"}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return JSONResponse({"updated": True})

    async def revoke_user(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        try:
            directory.revoke_user(request.path_params["username"])
        except KeyError:
            return JSONResponse({"detail": "User not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return Response(status_code=204)

    async def rotate_token(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        try:
            token = directory.rotate_token(request.path_params["username"])
        except KeyError:
            return JSONResponse({"detail": "User not found"}, status_code=404)
        return JSONResponse({"token": token})

    return [
        Route("/admin", page, methods=["GET"]),
        Route("/admin/", page, methods=["GET"]),
        Route("/admin/api/roles", list_roles, methods=["GET"]),
        Route("/admin/api/instances/test", test_instance, methods=["POST"]),
        Route("/admin/api/instances", list_instances, methods=["GET"]),
        Route("/admin/api/instances", create_instance, methods=["POST"]),
        Route("/admin/api/instances/{name:str}/status", set_instance_status, methods=["PUT"]),
        Route("/admin/api/instances/{name:str}", update_instance, methods=["PUT"]),
        Route("/admin/api/users", list_users, methods=["GET"]),
        Route("/admin/api/users", create_user, methods=["POST"]),
        Route("/admin/api/users/{username:str}/instances", update_instances, methods=["PUT"]),
        Route("/admin/api/users/{username:str}", update_user, methods=["PATCH"]),
        Route("/admin/api/users/{username:str}", revoke_user, methods=["DELETE"]),
        Route("/admin/api/users/{username:str}/rotate-token", rotate_token, methods=["POST"]),
    ]
