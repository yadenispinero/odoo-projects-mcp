from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .auth import UserDirectory, bearer_token


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
  <title>Odoo MCP · User access</title>
  <style>
    :root { color-scheme: light; font: 16px system-ui, sans-serif; color: #182230; background: #f3f5f8; }
    body { margin: 0 auto; max-width: 920px; padding: 32px 18px 64px; }
    h1 { margin-bottom: 4px; } .muted { color: #596579; }
    section, .user { background: white; border: 1px solid #d8dee8; border-radius: 10px; padding: 20px; margin: 18px 0; }
    #app.admin-layout { display: grid; grid-template-columns: minmax(250px, 300px) minmax(0, 1fr); grid-template-areas: "toolbar toolbar" "add users"; gap: 0 20px; align-items: start; }
    #app.admin-layout.hidden { display: none; }
    #toolbar { grid-area: toolbar; }
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
      #app.admin-layout { grid-template-columns: minmax(0, 1fr); grid-template-areas: "toolbar" "users" "add"; }
      #create-panel { position: static; }
      section, .user { padding: 15px; }
    }
  </style>
</head>
<body>
  <h1>Odoo MCP user access</h1>
  <p class="muted">Manage named MCP users and the tools each role can use. Odoo’s own permissions still apply.</p>
  <section id="login">
    <h2>Administrator sign in</h2>
    <p class="muted">Enter the existing owner Bearer token. It is kept in this browser tab only.</p>
    <label for="token">Owner token</label><input id="token" type="password" autocomplete="current-password">
    <div><button id="sign-in">Sign in</button></div><p id="login-error" class="error"></p>
  </section>
  <main id="app" class="hidden admin-layout">
    <div id="toolbar"><button id="sign-out" class="secondary">Sign out</button></div>
    <aside id="create-panel" class="user">
      <h2>Add a user</h2>
      <label for="new-username">Username</label><input id="new-username" type="text" maxlength="64" autocomplete="off">
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
      const data = await api('/roles'); roleGroups = data.groups;
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
      const data = await api('/users', { method: 'POST', body: JSON.stringify({username, roles: chosen}) });
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


def create_admin_routes(directory: UserDirectory) -> list[Route]:
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

    async def create_user(request: Request) -> Response:
        _, error = await _authorized_admin(request, directory)
        if error:
            return error
        try:
            body = await request.json()
            token = directory.add_user(body.get("username"), body.get("roles"))
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
        Route("/admin/api/users", list_users, methods=["GET"]),
        Route("/admin/api/users", create_user, methods=["POST"]),
        Route("/admin/api/users/{username:str}", update_user, methods=["PATCH"]),
        Route("/admin/api/users/{username:str}", revoke_user, methods=["DELETE"]),
        Route("/admin/api/users/{username:str}/rotate-token", rotate_token, methods=["POST"]),
    ]
