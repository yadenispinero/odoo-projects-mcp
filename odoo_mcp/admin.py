from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .auth import UserDirectory, bearer_token
from .config import InstanceDirectory
from .odoo_api import OdooApiError, OdooJson2


ROLE_GROUPS = [
    {"module": "Proyectos", "roles": ["project_manager"]},
    {"module": "Tareas", "roles": ["task_editor"]},
    {"module": "Sitio web", "roles": ["website_editor", "publisher"]},
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
    #app.admin-layout { display: grid; grid-template-columns: minmax(250px, 300px) minmax(0, 1fr); grid-template-areas: "toolbar toolbar" "instances instances" "add users"; gap: 0 20px; align-items: start; }
    #app.admin-layout.hidden { display: none; }
    #toolbar { grid-area: toolbar; }
    #instances-panel { grid-area: instances; }
    #instance-layout { display: grid; grid-template-columns: minmax(250px, 340px) minmax(0, 1fr); gap: 20px; align-items: start; }
    .instance-card { border: 1px solid #d8dee8; border-radius: 8px; padding: 14px; margin: 12px 0; background: #fbfcfe; }
    .instance-card h3 { margin: 0 0 6px; }
    .instance-meta { overflow-wrap: anywhere; margin: 5px 0; }
    #create-panel { grid-area: add; position: sticky; top: 16px; }
    #users-panel { grid-area: users; min-width: 0; }
    .revoked-list { margin: 18px 0; padding: 14px 18px; background: white; border: 1px solid #d8dee8; border-radius: 8px; }
    .revoked-list summary { cursor: pointer; font-weight: 600; }
    label { display: block; margin: 12px 0 6px; font-weight: 600; }
    input[type=text], input[type=password] { box-sizing: border-box; width: min(100%, 520px); padding: 10px; border: 1px solid #9aa6b7; border-radius: 6px; }
    .role-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(245px, 1fr)); gap: 12px; margin: 12px 0; }
    .role-group { border: 1px solid #d8dee8; border-radius: 8px; padding: 12px; background: #fbfcfe; }
    .role-group h4 { margin: 0 0 8px; color: #26364b; }
    .role-options { display: flex; flex-direction: column; gap: 8px; }
    .role-options label { display: flex; align-items: flex-start; gap: 7px; font-weight: 400; margin: 0; }
    .role-options input { margin-top: 4px; }
    button { border: 0; border-radius: 6px; background: #185abd; color: white; padding: 10px 14px; cursor: pointer; margin: 8px 8px 0 0; }
    button.danger { background: #a32626; } button.secondary { background: #566273; }
    .hidden { display: none; } .token { overflow-wrap: anywhere; padding: 12px; background: #edf7ed; border: 1px solid #9bc99b; border-radius: 6px; }
    .error { color: #a32626; white-space: pre-wrap; } .user h3 { margin: 0; }
    @media (max-width: 760px) {
      body { padding: 18px 12px 40px; }
      #app.admin-layout { grid-template-columns: minmax(0, 1fr); grid-template-areas: "toolbar" "instances" "users" "add"; }
      #instance-layout { grid-template-columns: minmax(0, 1fr); }
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
    <div id="toolbar"><button id="sign-out" class="secondary">Sign out</button></div>
    <section id="instances-panel">
      <h2>Odoo instances</h2>
      <p class="muted">Instance connections are stored outside the project source, in the private MCP data directory. API keys are never displayed after saving.</p>
      <p id="wildcard-warning" class="muted"></p>
      <div id="instance-layout">
        <div>
          <h3 id="instance-form-title">Add an instance</h3>
          <label for="instance-name">Instance name</label><input id="instance-name" type="text" maxlength="64" autocomplete="off">
          <label for="instance-url">Odoo base URL (HTTPS)</label><input id="instance-url" type="text" placeholder="https://odoo.example.com" autocomplete="url">
          <label for="instance-database">Database (optional)</label><input id="instance-database" type="text" autocomplete="off">
          <label for="instance-api-key">Odoo API key</label><input id="instance-api-key" type="password" autocomplete="new-password">
          <p class="muted">When editing, leave the key blank to keep the saved key. Remove a saved key only after deactivating that instance.</p>
          <label class="muted"><input id="clear-instance-key" type="checkbox"> Remove the saved API key</label>
          <p class="muted">Test the exact connection settings before saving. Saving repeats the test on the server.</p>
          <div><button id="test-instance" class="secondary">Test connection</button><button id="save-instance" disabled>Save instance</button><button id="cancel-instance" class="secondary hidden">Cancel edit</button></div>
        </div>
        <div><h3>Configured instances</h3><div id="instances-list"></div></div>
      </div>
    </section>
    <aside id="create-panel" class="user">
      <h2>Add a user</h2>
      <label for="new-username">Username</label><input id="new-username" type="text" maxlength="64" autocomplete="off">
      <label for="new-instances">Allowed Odoo instances</label><input id="new-instances" type="text" autocomplete="off">
      <p class="muted" id="instance-help">Enter configured names separated by commas. Use * only when access to every instance is needed.</p>
      <div id="new-roles" class="role-groups"></div>
      <button id="create-user">Create user and generate token</button>
      <div id="created-token" class="hidden"></div>
    </aside>
    <section id="users-panel"><h2>Enabled users</h2><div id="users"></div></section>
  </main>
  <p id="message" class="error"></p>
<script>
(() => {
  const tokenKey = 'odoo-mcp-admin-token';
  const login = document.querySelector('#login');
  const app = document.querySelector('#app');
  const message = document.querySelector('#message');
  const labels = {
    viewer: 'Lectura y búsqueda de proyectos, tareas y páginas',
    task_editor: 'Crear y actualizar tareas',
    website_editor: 'Editar contenido de páginas',
    publisher: 'Publicar y despublicar páginas',
    project_manager: 'Consultar, crear y actualizar proyectos',
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
  const parseInstances = input => {
    const value = input.value.trim();
    return value === '*' ? ['*'] : value.split(',').map(name => name.trim()).filter(Boolean);
  };
  const instancePayload = () => ({
    name: document.querySelector('#instance-name').value.trim(),
    base_url: document.querySelector('#instance-url').value.trim(),
    database: document.querySelector('#instance-database').value.trim(),
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
    document.querySelector('#instance-database').value = '';
    document.querySelector('#instance-api-key').value = '';
    document.querySelector('#clear-instance-key').checked = false;
    document.querySelector('#instance-api-key').disabled = false;
    document.querySelector('#test-instance').disabled = false;
    document.querySelector('#cancel-instance').classList.add('hidden');
  };
  const loadInstances = async () => {
    const data = await api('/instances');
    instanceNames = data.instances;
    instanceRecords = data.records;
    wildcardUserCount = data.wildcard_users;
    document.querySelector('#instance-help').textContent = 'Active instances: ' + (instanceNames.join(', ') || 'none') + '. Use * only when access to every current and future instance is intended.';
    document.querySelector('#wildcard-warning').textContent = data.wildcard_users
      ? data.wildcard_users + ' active user(s) have * access and will also gain access to any new instance. Review their scopes after adding one.'
      : 'No active users currently have wildcard (*) access.';
    const list = document.querySelector('#instances-list'); list.replaceChildren();
    for (const item of instanceRecords) {
      const card = document.createElement('article'); card.className = 'instance-card';
      const heading = document.createElement('h3'); heading.textContent = item.name + (item.active ? ' · active' : ' · inactive');
      const url = document.createElement('p'); url.className = 'instance-meta'; url.textContent = item.base_url;
      const database = document.createElement('p'); database.className = 'instance-meta'; database.textContent = 'Database: ' + (item.database || 'not specified');
      const credential = document.createElement('p'); credential.className = 'instance-meta'; credential.textContent = 'API key: ' + (item.api_key_configured ? 'configured' : 'not configured');
      const edit = document.createElement('button'); edit.textContent = 'Edit'; edit.className = 'secondary';
      edit.addEventListener('click', () => {
        editingInstance = item.name;
        editingInstanceActive = item.active;
        document.querySelector('#instance-form-title').textContent = 'Edit instance';
        const name = document.querySelector('#instance-name'); name.value = item.name; name.disabled = true;
        document.querySelector('#instance-url').value = item.base_url;
        document.querySelector('#instance-database').value = item.database || '';
        document.querySelector('#instance-api-key').value = '';
        document.querySelector('#clear-instance-key').checked = false;
        document.querySelector('#instance-api-key').disabled = false;
        document.querySelector('#test-instance').disabled = false;
        document.querySelector('#cancel-instance').classList.remove('hidden');
        invalidateInstanceTest();
      });
      const toggle = document.createElement('button'); toggle.textContent = item.active ? 'Deactivate' : 'Reactivate'; toggle.className = item.active ? 'danger' : '';
      toggle.addEventListener('click', async () => {
        if (item.active && !confirm('Deactivate ' + item.name + '? MCP requests to this instance will stop. Existing user access assignments are retained.')) return;
        try {
          await api('/instances/' + encodeURIComponent(item.name) + '/status', {method:'PUT', body:JSON.stringify({active:!item.active})});
          await loadInstances(); await loadUsers(); message.textContent = item.active ? 'Instance deactivated.' : 'Instance reactivated.';
        } catch (error) { message.textContent = error.message; }
      });
      card.append(heading,url,database,credential,edit,toggle); list.append(card);
    }
  };
  document.querySelector('#cancel-instance').addEventListener('click', resetInstanceForm);
  document.querySelector('#clear-instance-key').addEventListener('change', event => {
    document.querySelector('#instance-api-key').disabled = event.target.checked;
    invalidateInstanceTest();
    const testButton = document.querySelector('#test-instance');
    testButton.disabled = event.target.checked && !editingInstanceActive;
    if (event.target.checked && !editingInstanceActive) {
      testedInstancePayload = instancePayloadKey();
      document.querySelector('#save-instance').disabled = false;
      message.textContent = 'This inactive instance can have its saved key removed without a connection test.';
    }
  });
  for (const selector of ['#instance-name', '#instance-url', '#instance-database', '#instance-api-key']) {
    document.querySelector(selector).addEventListener('input', invalidateInstanceTest);
  }
  document.querySelector('#test-instance').addEventListener('click', async () => {
    invalidateInstanceTest();
    const testedPayload = instancePayloadKey();
    try {
      const result = await api('/instances/test', {method:'POST', body:JSON.stringify(instancePayload())});
      if (testedPayload !== instancePayloadKey()) throw new Error('Settings changed during the test. Test again before saving.');
      testedInstancePayload = testedPayload;
      document.querySelector('#save-instance').disabled = false;
      message.textContent = result.message;
    } catch (error) { message.textContent = error.message; }
  });
  document.querySelector('#save-instance').addEventListener('click', async () => {
    const payload = instancePayload();
    if (testedInstancePayload !== JSON.stringify(payload)) {
      invalidateInstanceTest();
      message.textContent = 'Test the current connection settings successfully before saving.';
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
      resetInstanceForm(); await loadInstances(); await loadUsers(); message.textContent = 'Instance saved.';
    } catch (error) { message.textContent = error.message; }
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
        try { await api('/users/' + encodeURIComponent(user.username), { method: 'PATCH', body: JSON.stringify({roles: selectedRoles(roleBox)}) }); await loadUsers(); message.textContent = 'Roles saved.'; }
        catch (error) { message.textContent = error.message; }
      });
      card.append(heading, roleBox, save);
      const instanceLabel = document.createElement('label'); instanceLabel.textContent = 'Allowed Odoo instances';
      const instanceInput = document.createElement('input'); instanceInput.type = 'text';
      instanceInput.value = (user.instances || ['*']).join(', '); instanceInput.autocomplete = 'off';
      const instanceSave = document.createElement('button'); instanceSave.textContent = 'Save instance access'; instanceSave.className = 'secondary';
      instanceSave.addEventListener('click', async () => {
        try { await api('/users/' + encodeURIComponent(user.username) + '/instances', { method: 'PUT', body: JSON.stringify({instances: parseInstances(instanceInput)}) }); await loadUsers(); message.textContent = 'Instance access saved.'; }
        catch (error) { message.textContent = error.message; }
      });
      card.append(instanceLabel, instanceInput, instanceSave);
      const rotate = document.createElement('button'); rotate.textContent = 'Rotate token'; rotate.className = 'secondary';
      rotate.addEventListener('click', async () => {
        if (!confirm('Replace the token for ' + user.username + '? Their old token will stop working immediately.')) return;
        try {
          const result = await api('/users/' + encodeURIComponent(user.username) + '/rotate-token', { method: 'POST' });
          const tokenPanel = document.createElement('p'); tokenPanel.className = 'token'; tokenPanel.textContent = result.token + ' — copy now; it will not be shown again.';
          card.append(tokenPanel); message.textContent = 'Token rotated.';
        } catch (error) { message.textContent = error.message; }
      });
      const revoke = document.createElement('button'); revoke.textContent = 'Revoke access'; revoke.className = 'danger';
      revoke.addEventListener('click', async () => {
        if (!confirm('Revoke access for ' + user.username + '? Their token will stop working immediately.')) return;
        try { await api('/users/' + encodeURIComponent(user.username), { method: 'DELETE' }); await loadUsers(); message.textContent = 'Access revoked.'; }
        catch (error) { message.textContent = error.message; }
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
      const instances = parseInstances(document.querySelector('#new-instances'));
      const data = await api('/users', { method: 'POST', body: JSON.stringify({username, roles: chosen, instances}) });
      const note = document.createElement('p'); note.textContent = 'Copy this token now. It will not be shown again.';
      const value = document.createElement('p'); value.className = 'token'; value.textContent = data.token;
      const copy = document.createElement('button'); copy.textContent = 'Copy token'; copy.addEventListener('click', async () => { await navigator.clipboard.writeText(data.token); copy.textContent = 'Copied'; });
      output.append(note, value, copy); output.classList.remove('hidden');
      document.querySelector('#new-username').value = ''; await loadUsers(); message.textContent = 'User created.';
    } catch (error) { message.textContent = error.message; }
  });
  if (token()) enter();
})();
</script>
</body>
</html>"""


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
                clear_api_key,
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
                body.get("name"), body.get("base_url"), body.get("database"), body.get("api_key")
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
                clear_api_key,
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
