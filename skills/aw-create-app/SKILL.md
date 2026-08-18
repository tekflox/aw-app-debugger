---
name: aw-create-app
description: >-
  Author a decoupled aw-workspace app from this template — manifest, tiers
  (Tier-1 in-process vs Tier-2 container), the capability/permission catalog,
  what an app can contribute (windows, nav, routes, system CLIs, DB tables,
  frontend bundles), the declarative window widget vocabulary, install +
  marketplace release, and how it shows up in the Apps grid. Use whenever you
  are asked to build a new aw-workspace app, add an app to the marketplace, or
  extend/port a monolith feature into a decoupled app.
---

# Creating a decoupled aw-workspace app

This skill ships **inside `aw-app-template`** and teaches how to turn this
repo into a real app. The template is itself a marketplace app (a minimal,
fully-wired "hello" example). Copy it, rename everything marked `TEMPLATE`/
`hello`, and follow the contract below.

> **Migration mindset:** most new apps are *ports* of an existing
> `agentic-workspace` monolith feature — base the app on the working monolith
> code (cite `file:line`) and port faithfully. Greenfield only when there is
> no monolith equivalent.

## 1. What an app is

A decoupled app is a self-contained repo (`aw-app-<name>`) with a manifest
(`aw-app.json`) at its root. The aw-workspace runtime fetches installed apps
into `/opt/aw-workspace/apps/<slug>/` (`AW_APPS_ROOT`, see `src/apps/fetch.py`)
and serves them under `/api/apps/<id>`.
Two tiers:

- **Tier-1 (`"tier": "inprocess"`)** — a Python plugin loaded into the
  workspace process. Cheapest; use for routes, DB tables, CLIs, windows,
  background tasks. Entrypoint: `runtime.entrypoint = "pkg.plugin:ClassName"`.
- **Tier-2 (`"tier": "container"`)** — a sidecar container from a prebuilt
  image, spawned over the host podman socket and reverse-proxied. Use when the
  app needs its own runtime/binaries (e.g. a browser). Needs
  `permissions: ["containers:manage"]` and `runtime.image/port/run_flags_needed`.

## 2. The manifest (`aw-app.json`)

```jsonc
{
  "manifest_version": 1,
  "id": "myapp",                       // unique slug; becomes /api/apps/myapp
  "name": "My App",
  "version": "0.1.0",
  "description": "...",
  "tier": "inprocess",                 // or "container"
  "publisher": "TekFlox",
  "requires_ui_refresh": true,          // SPA re-fetches contributions after install/config-save
  "resource_estimate": { "cpu": "low", "memory": "low", "disk": "low" },

  "runtime": {                          // Tier-1:
    "python": ">=3.11",
    "entrypoint": "myapp_app.plugin:MyAppPlugin",
    "pip_requires": []
  },
  // Tier-2 instead: "runtime": { "image": "ghcr.io/tekflox/aw-myapp:latest",
  //                              "port": 7900, "resources": {"cpus":0.5,"mem_mb":1024},
  //                              "run_flags_needed": ["--shm-size=1g"] }

  "permissions": [ "routes:register" ],  // capability grants — see §3
  "contributes": { /* see §4 */ },
  "dependencies": {},
  "migrations": {}
}
```

### `description` is product copy, not architecture notes

`description` is the only sentence most people ever read about your app —
it's the marketplace card and the Apps grid tile. **An app is a product.**
Write it for the person deciding whether to install: what it does for them,
benefit first, in plain language. Same for `name` — a product name, not a
module name.

Keep internal architecture out of it. No ADR or decision numbers, no
capability slugs (`commands:install`), no route shapes, no
"component-mode frontend bundle", no porting/migration history. A builder
looking for those reads README.md and this skill; on the card they're noise
that answers none of the reader's actual question.

This template's own description used to fail that test:

> ~~TEMPLATE — a minimal, fully working decoupled app: installs one trivial
> CLI (`hello`, …) through the gated commands:install facade, contributes a
> backend sub-app (GET /hello + app-local WS /ws/echo under the app
> namespace) and a component-mode frontend bundle, and runs standalone too
> (ADR Decision 4/6).~~

Every clause is true and none of it is for the reader. What it says now
leads with what you get:

> TEMPLATE — the fastest way to start a new aw-workspace app. Install it and
> you have a working app on day one: a `hello` CLI that prints a
> configurable greeting, its own window, and an HTTP + WebSocket backend …

Rule of thumb: if a sentence would only make sense to someone who has read
this skill, it belongs in README.md instead.

`config_schema` is **optional** — omit it entirely if your app has no config
at all. Add it when there's a real knob to expose:

```jsonc
"config_schema": {
  "type": "object",
  "properties": {
    "some_field": { "type": "string", "default": "x", "description": "..." }
  },
  "required": []
},
"config_visible": false   // optional, defaults true — see below
```

Its presence is normally what turns on `has_config: true` (and the Settings
gear / config form) for your app. But not every app has *user-facing*
settings worth putting in front of a human (most Runnables-style apps
don't) — set the sibling field **`config_visible: false`** to keep a real
`config_schema` (still readable via `ctx.config`, still editable through
`POST /api/apps/<id>/config` directly) WITHOUT surfacing the gear/Settings
entry for it. This template ships exactly that: a real `greeting` config
knob, hidden by `config_visible: false`. Flip it to `true` (or delete the
field, since that's the default) once your app has settings worth exposing
in the UI — see aw-app-git for a real example with the gear on.

Validate it: `python tests/validate_manifest.py` — the schema is not in this
repo, it lives in aw-marketplace and the validator picks it up from a sibling
checkout (or `--schema <path>`). Do not copy it in.

### Calling another route on this workspace (or from an external MCP)

If your app needs to call a workspace API route from an outbound request
(in-process `httpx` call, or a standalone MCP server that talks to this
workspace over HTTP), authenticate with the workspace's shared API key
instead of building your own auth — see `docs/app-workspace-api-auth.md`
for the full pattern and a worked example
(`tekflox/aw-app-whiteboard`'s `mcp_server/`).

### Reacting to a settings save (`on_config_saved`)

`POST /api/apps/<id>/config` updates `ctx.config` and, if your plugin
defines it, awaits `plugin.on_config_saved(ctx)` right after — optional,
duck-typed (no base class required), a no-op if you don't define it. Use it
when a config change needs to do something beyond being read lazily next
time — e.g. `aw-app-mcp-tools` regenerates its own root `mcp.json` on disk
here from a per-tool enable/disable toggle, which is also why it sets
`contributes.mcp.reload_on_save: true` (§4): that flag makes aw-workspace
call the MCP Gateway's `POST /reload` right after your hook returns, so
whatever file you just wrote takes effect immediately.

```python
class MyAppPlugin:
    async def activate(self, ctx):
        ...
    async def on_config_saved(self, ctx):
        # ctx.config already reflects the just-saved values here.
        ...
    async def deactivate(self):
        ...
```

## 3. Capability catalog (`permissions`)

Only request what you use — each is enforced by the runtime's `AppContext`.
Authoritative source: aw-workspace `src/apps/capabilities.py` (mirrored in
aw-backend `src/api/app_capabilities.py`). aw-marketplace's canonical schema
generates its permissions pattern from that catalog, so a new capability
needs no schema edit here — and no app repo should carry a copy to keep in
sync.

| Capability | Risk | Grants |
|---|---|---|
| `routes:register` | low | mount `/api/apps/<id>/*` routes |
| `db:own-tables` | low | create/use app-owned workspace tables |
| `commands:install` | low | install CLIs/commands that survive restart |
| `service:manage` | low | register a start/stop background service |
| `watchdog:tasks` | low | register in-process periodic tasks |
| `net:outbound` | low | outbound HTTP from Tier-1 code |
| `fs:workspace-data` | low | read/write the app's own data dir |
| `secrets:own` | low | request the app's own secrets |
| `notifications:send` | low | fire a workspace notification |
| `tasks:contribute` | low | seed scheduled tasks on install (created once, never updated) |
| `agents:contribute` | low | seed Agents Platform models/configs/groups/agents on install (created once, never updated) |
| `containers:manage` | **high** | run/manage sidecar containers (Tier-2) |
| `host:device-kvm` | **high** | `/dev/kvm` in the app's container — a KVM guest |
| `host:device-tun` | **high** | `/dev/net/tun` + `NET_ADMIN` — a guest's own NIC |
| `host:device-fuse` | **high** | `/dev/fuse` + `SYS_ADMIN` — FUSE mounts |
| `host:device-binder` | **high** | the Android binder devices — a redroid guest |
| `host:privileged` | **high** | `--privileged` — every device and capability, no isolation |
| `ui:code` | **high** | load the app's JS bundle into the SPA |
| `ui:slots:<slot>` | low | render into a named SPA slot (e.g. `core.nav.workspace`) |
| `config:extend:<app>` | high | write config into another app's extension point |

> **`routes:local` (proposed, NOT yet live)** — ADR Decision 2
> (`docs/knowledge_base/docs/architecture/adr-app-front-back-routes-dual-mode.md`)
> proposes a `routes:local` capability that lets specific sub-paths of your
> app skip the JWT check when called from `127.0.0.1`/`::1` (agent-driven
> endpoints, e.g. a future `aw-app-devctl` `/eval`). As of 2026-07-28 this
> capability does not exist in `capabilities.py` and the `local_paths`
> manifest field has no runtime effect — do not request it yet. See §4 for
> the documented (forward-looking) shape.

## 4. What an app contributes (`contributes`)

- **`windows`** — declarative windows opened from the app's card. Each:
  `{ "id": "myapp.main", "title": "...", "body": { "type": "declarative", "spec": "windows/main.json" } }`.
- **`nav`** — OPTIONAL top-bar buttons. `section: "workspace"` → Workspace menu.
  **Omit nav for normal apps** — every installed app already appears as a card
  in the **Apps grid** (the launcher reads `GET /api/apps`); nav is only for
  apps that also want a persistent menu entry.
- **`routes`** — `[{ "prefix": "/api/apps/myapp" }]` (needs `routes:register`).
  See §5 for the full backend contract, and the not-yet-live `local_paths`
  escape:
  ```jsonc
  "contributes": {
    "routes": [
      {
        "prefix": "/api/apps/myapp",
        // Forward-looking (ADR Decision 2) — needs "routes:local", not live yet.
        "local_paths": ["/eval", "/tabs"]
      }
    ]
  }
  ```
- **`system_clis`** — `[{ "name": "hello", "installer": "scripts/install_hello.sh",
  "verify": "hello --version" }]` (needs `commands:install`).
  See §6b — **installers have a contract, and getting it wrong fails silently.**
- **`db`** — app-owned tables (needs `db:own-tables`).
- **`frontend`** — a JS bundle mounted into granted slots (needs `ui:code`;
  unsigned apps are downgraded to iframe mode). See §6 for the source layout:
  ```jsonc
  "contributes": {
    "frontend": {
      "mode": "component",       // or "iframe" / "declarative" — see loadPlugin.js
      "bundle": "ui/dist/myapp.js"
    }
  }
  ```
- **`mcp`** — this app's MCP-related declarations. Two independent, optional
  sibling keys on the same object:
  ```jsonc
  "contributes": {
    "mcp": {
      // Marketplace "What you get" detail-view list (Manifest.what_you_get) —
      // purely informational, doesn't affect what actually gets exposed.
      "provides": [{ "name": "my_tool" }, "another_tool"],
      // True if this app ALSO ships its own root mcp.json — aw-mcp-gateway's
      // app-scan reads that file directly (see aw-app-mcp-tools for the
      // reference implementation: a Playwright + echo MCP tool bundle with
      // per-tool enable/disable toggles). Setting this tells aw-workspace's
      // POST /api/apps/<id>/config to call the installed mcp-gateway app's
      // POST /reload right after your on_config_saved hook returns (see
      // below) — so a settings change that affects your mcp.json takes
      // effect immediately, no gateway restart.
      "reload_on_save": true
    }
  }
  ```

  **If your upstream needs a credential, ship `mcp.template.json`, not
  `mcp.json`.** The gateway scans `apps/<slug>/mcp.json` in the installed
  *package* dir, and an update overwrites that dir wholesale — so a token
  written into it survives exactly until the next version bump, after which
  the upstream stays listed and serves **zero tools** with nothing reporting
  it. A Tier-1 app can dodge this by rewriting its own `mcp.json` on
  `activate` (aw-app-notion does, from its secret store); **a Tier-2 container
  app runs no workspace-side code and has no such escape.**

  So the runtime renders the template for you, on every activation *and*
  every config save:

  ```jsonc
  // mcp.template.json  — versioned in your repo
  {
    "mcpServers": {
      "my-service": {
        "enabled": true,
        "type": "http",
        "url": "http://aw-app-myapp:8123/api/mcp",
        "headers": { "Authorization": "Bearer ${config.mcp_token}" }
      }
    }
  }
  ```

  Pair it with an `mcp_token` field in `config_schema`. The value lands in
  `<AW_WORKSPACE_HOME>/app-config/<app_id>.json` (`config_store`), which lives
  outside the package dir and which **uninstall deliberately keeps** — so the
  credential survives an update, an uninstall/install and a workspace
  redeploy with nobody re-pasting anything.

  Three things to know:

  - Same `${config.x}` / `${env.X}` / `${app.url}` grammar as `runtime.env`,
    including `|` fallback — but here placeholders **interpolate inside a
    larger string**, because `"Bearer ${config.mcp_token}"` is the shape
    every credentialed upstream needs. In `runtime.env` they stay
    whole-value.
  - An **unresolved placeholder disables that one server** rather than
    shipping it. A literal `${config.mcp_token}` in an auth header doesn't
    fail loudly — it connects, 401s, and serves nothing.
  - The rendered `mcp.json` is **generated**. Don't commit it, don't edit the
    installed copy, and add it to `.gitignore`.

  Implementation: `src/apps/mcp_template.py` in aw-workspace. Apps that ship
  `mcp.json` directly (aw-app-browser, aw-app-code-server) are untouched — no
  template, no-op.
- **`skills`** — teach an agent how to use/build with this app. Each entry:
  `{ "id": "my-skill", "path": "skills/my-skill/SKILL.md", "description": "..." }`
  — no extra permission needed, every app gets this for free. On install, the
  runtime **copies** (never symlinks) the **whole directory** the `SKILL.md`
  lives in (so any reference assets next to it come along too) into the
  shared skills index at `<AW_WORKSPACE_HOME>/skills/<app_id>__<skill_id>/`
  (`src/apps/skills.py`) — an installed app's own package dir is immutable
  (overwritten wholesale on update), so the workspace's copy is what the user
  is meant to edit in place; re-registering (every boot re-activates every
  installed app) never overwrites a copy that already exists. Removed on
  uninstall. `GET /api/apps/-/skills` lists every installed app's registered
  skills (pointer only, for an agent runtime to read). This file is a live
  example of the pattern — see this repo's own `aw-app.json` `contributes.skills`.
  (Known gap: `<AW_WORKSPACE_HOME>/skills` isn't committed/backed up anywhere
  today, so an edit doesn't survive a full workspace recreation — unsolved.)
  ```jsonc
  "contributes": {
    "skills": [
      { "id": "my-skill", "path": "skills/my-skill/SKILL.md",
        "description": "One line — when an agent should reach for this." }
    ]
  }
  ```

### Seeded surfaces: `tasks` and `agents`

Two contributions behave unlike every other entry above. They don't *mount*
something owned by the app — they **seed** an object into a store the user
also edits by hand, so both follow one rule:

> **Create-if-absent, matched by identity, never updated, never removed on
> uninstall.**

An existing object is left exactly as it is. Nothing you change in a later
app version reaches an installation that already seeded — ship it under a
new identity, or the user edits theirs. That is deliberate: a schedule and a
system prompt are precisely the things people tune, and an app re-asserting
its own copy on every boot (activation re-runs on *every* boot) would undo
that silently. It also means an object the user already made by hand is
recognised rather than duplicated.

Neither can fail an install. Core has no storage for either — it dispatches
to whichever installed app provides the surface (`aw-app-tasks`,
`aw-app-agents-platform-runners`) and swallows every error to a log. If no
provider is loaded yet the declaration is **held** and replayed when one
appears, and the provider sweeps every already-loaded app when it activates
— so activation order doesn't matter, and declare the provider as a
`required: false` dependency, not a hard one.

Full docs, with the field tables and the failure matrix:
[`docs/contributing-tasks.md`](../../docs/contributing-tasks.md) ·
[`docs/contributing-agents.md`](../../docs/contributing-agents.md).
Complete, validating example manifests: [`examples/`](../../examples/).

- **`tasks`** — scheduled work the app depends on (needs `tasks:contribute`).
  Identity is the **`name`**. `enabled` defaults to **false** — a task that
  starts firing the moment an app is installed is a surprise.
  ```jsonc
  "contributes": {
    "tasks": [
      { "name": "Indexer — nightly rebuild",
        "type": "agentic_output",              // or "terminal" (fires a prompt)
        "command": "indexer-cli rebuild --quiet",
        "notify_exit_codes": [1, 2],           // agentic_output only
        "schedules": [{ "kind": "daily", "time": "03:00" }] }
    ]
  }
  ```
  Schedule kinds: `once` (`at`), `daily` (`time`), `weekly` (`days` 0=Mon,
  `time`), `monthly` (`day_of_month`, `time`), `cron` (`expr`). Several are
  allowed; it fires on whichever comes first. An empty list = manual-only.

- **`agents`** — the Agents Platform objects the app's features need (needs
  `agents:contribute`). Identity is the **`slug`**, which is the platform's
  own natural key. An object of four lists, and **the key order is the
  creation order**: an Agent references a model, a config and a group by
  slug, and the platform stores those as plain strings — so a group that
  doesn't exist yet doesn't error, it produces an agent pointing at nothing.
  The provider always creates models → configs → groups → agents; your
  manifest never has to sequence it. Every key is optional.
  ```jsonc
  "contributes": {
    "agents": {
      "models": [
        { "slug": "secreview-sonnet", "provider": "anthropic",
          "model_id": "claude-sonnet-5" }
      ],
      "agent_configs": [
        { "slug": "secreview-config", "name": "Security Review Config",
          "extra_volumes": ["/opt/aw-workspace/repos:/repos:ro"] }
      ],
      "groups": [
        { "slug": "secreview-reviewers", "name": "Security Reviewers",
          "instructions_file": "prompts/reviewers-group.md" }
      ],
      "agents": [
        { "slug": "secreview-sonnet-agent", "name": "Security Reviewer",
          "system_prompt_file": "prompts/security-reviewer.md",
          "model_slug": "secreview-sonnet",
          "agent_config_slug": "secreview-config",
          "group_slug": "secreview-reviewers",
          "skill_slugs": ["aw-agent-qa"] }
      ]
    }
  }
  ```
  **Long prompts go in files, not JSON.** `system_prompt_file` (agents) and
  `instructions_file` (groups) take a path inside your package, inlined by
  the workspace before the declaration reaches the provider; paths are
  confined to the package. A missing one is the quietest failure here — the
  agent is still created, with an empty prompt — so `validate_manifest.py`
  fails the build on it. Pair this with `contributes.skills`: put the
  durable contract in a SKILL.md and keep the prompt to the lines that point
  at it.

### Window widget vocabulary (`windows/*.json`)

`layout: "stack"`, `regions: [{ id, widgets: [...] }]`. Widget `type`:
`markdown`, `list`, `button`, `collapsible`, `form`, `auth_status`,
`iframe` (`{ src }`, an `/api/*` path — rewritten to the workspace API host),
`app_iframe` (`{ app_id, path }` — resolves to the app's **external
subdomain** `https://<app_id>.app.<slug>.workspace.<apex>`, honoring the LAN
fast-path; use this to surface a Tier-2 container's own web UI).

> **An `iframe` panel must supply its own `body { padding: 12px }`.** The host
> renders it in a **cross-origin** document, so no stylesheet of its can reach
> inside, and padding on the `<iframe>` element only shifts the origin while
> the document keeps its full layout width — clipping the right-hand side and
> adding an inner horizontal scrollbar (tried in aw-workspace-ui and reverted,
> 2026-08-13). Without your own padding the panel sits flush against the frame.

## 5. Backend routes (HTTP + WS)

Every app that has a server side ships exactly ONE mode-agnostic factory,
`build_routes() -> FastAPI`, with **relative** paths (no `/api/apps/<id>`
prefix inside the app — see `template_app/routes.py`):

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

def build_routes() -> FastAPI:
    app = FastAPI(title="myapp")

    @app.get("/hello")
    async def hello():
        return {"message": "hi"}

    @app.websocket("/ws/echo")           # app-local WS; externally stays app-namespaced
    async def ws_echo(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_text(await ws.receive_text())
        except WebSocketDisconnect:
            pass

    return app
```

Register it in `plugin.py`'s `activate(ctx)` via the gated facade:
`ctx.routes.register(build_routes())` — the runtime mounts it at
`/api/apps/<id>` behind `IdentityGuard` (bearer/cookie for HTTP, `?token=`
then cookie for WS). **Apps never implement their own auth in integrated
mode.** Full canonical path shapes:

```
/api/apps/<id>/...           HTTP routes
/api/apps/<id>/ws/<name>     in-process app WebSocket routes
/ws/apps/<id>/<name>         reserved edge namespace for app-owned WebSockets
/ws/*                        core/control-plane only
```

`local_paths` (agent-callable, no-JWT-from-localhost endpoints) is a
**proposed, not-yet-live** manifest extension — see §3/§4, don't request the
`routes:local` capability until it lands in `capabilities.py`.

## 6. Frontend code (`register(host)`, headless services, host helpers)

A frontend contribution ships `ui/src/plugin.js` with ONE required export:

```js
export function register(host) {
  // host.React / host.h / host.sdk are the SHARED instances — never import
  // your own React (breaks the "exactly one React instance" invariant).
  function MyPill() {
    const [msg, setMsg] = host.React.useState('…');
    host.React.useEffect(() => {
      host.sdk.api.fetch('/api/apps/myapp/hello')
        .then((r) => r.json()).then((d) => setMsg(d.message));
    }, []);
    return host.h('span', {}, msg);
  }
  host.registerSlot('core.nav', MyPill);   // needs "ui:slots:core.nav" grant

  // Headless pattern — background work with NO slot, teardown via onDispose:
  const ws = new WebSocket(host.sdk.api.wsUrl('/api/apps/myapp/ws/echo'));
  host.onDispose(() => ws.close());
}
export default register;
```

`register(host)` **may be headless** — start background work (open a WS,
install listeners) without registering any slot, as long as every teardown
goes through `host.onDispose(fn)`. This is the sanctioned pattern for a
client that has no UI of its own to show (e.g. a future devctl client).

`host.app.{apiUrl,fetch,wsUrl}` scoped helpers (`app.apiUrl('/hello')` →
`/api/apps/<id>/hello`, no hand-built prefix) are **proposed in ADR Decision
3 but not yet landed** in aw-frontend's `pluginHost.js` — until then, build
the `/api/apps/<id>/...` prefix yourself (see `ui/src/plugin.js` in this
repo) and use `host.sdk.api.fetch`/`host.sdk.api.wsUrl` for the actual
network call (those two already exist and are BYOD-correct — never
hand-build a raw `fetch()`/`new WebSocket()` URL yourself).

Only `component` mode (needs `ui:code`, **signed/marketplace-only**) runs
`register(host)` at all — an unsigned/side-loaded install is silently
downgraded to `iframe` mode (`loadPlugin.js`'s `effectiveMode()`).

## 6b. The installer contract (`system_clis`)

Read this before writing `scripts/install_*.sh`. Every rule here comes from a
bug that shipped and then stayed invisible for months, because a failing
installer is retried on a timer and only ever logged.

**1. Run privileged steps under `sudo`.** The container's default user is
`ubuntu` (uid 1001) with `NOPASSWD: ALL` baked into the image. A bare
`apt-get` dies with `Could not open lock file /var/lib/apt/lists/lock -
open (13: Permission denied)`, on every boot, forever. Note that
`sudo cmd > /root/file` still opens the file as the *calling* user — pipe
through `sudo tee` instead.

**2. Verify, don't detect.** An early-exit guard like

```bash
command -v git >/dev/null 2>&1 && exit 0   # WRONG
```

asks "is there a binary with this name", which is not what you need to know.
A `git` shipped without its remote helpers prints a version quite happily and
fails every `https://` fetch; that guard saw it, exited 0 on every reconcile
pass, and the repair never ran. Check the capability you actually depend on,
and pass `--reinstall` (or equivalent) so a half-present install is repaired
rather than reported as "already the newest version" and skipped.

**3. Declare `verify` in the manifest.** This is the framework's own health
check — what makes `missing_system_clis` (and `aw-workspace-cli doctor`) able
to see a CLI that is present but broken:

```jsonc
{ "name": "git", "installer": "scripts/install_git.sh",
  "verify": "test -x \"$(git --exec-path)/git-remote-https\"" }
```

Defaults to `<name> --version`, which is right for most CLIs. Use an explicit
command when that would lie (as for `git`) or when the CLI has no version flag
(`ping`, `nc`, `go`, `gofmt`). An explicit `verify` is the SOLE authority — no
PATH check runs first — which is how `nvm`, a shell function that is never on
PATH, can be verified at all. Use `verify: false` only for a CLI with no
meaningful check, so the weakening is explicit in your manifest rather than
the silent default. Then thread it through:

```python
ctx.commands.install_system_cli(
    cli["name"], cli["installer"], uninstall="scripts/uninstall.sh",
    verify=cli.get("verify"))
```

**4. Don't assume anything beyond the base image.** It ships `curl`, `git`,
`sudo`, `unzip`, `build-essential` and Python. `unzip` arrived late — apps
that needed it were aborting with "unsupported base image" — so prefer
`python3 -m zipfile -e` (stdlib, always there) and keep `unzip` as the
preferred-when-present path. Note `python3 -m zipfile` drops mode bits, so
`chmod +x` what you extract.

**5. Check your work on a real workspace**, not just in tests:

```bash
aw-workspace-cli doctor          # anything silently degraded?
```

## 7. Standalone mode

Ship `<pkg>/__main__.py` so your app runs with `python -m <pkg>` completely
outside the workspace runtime — useful for developing/demoing the UI, or as
a piloted service on its own (ADR Decision 4):

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .routes import build_routes

def build_standalone_app() -> FastAPI:
    app = FastAPI()
    app.mount("/api/apps/myapp", build_routes())     # SAME prefix as integrated
    app.mount("/", StaticFiles(directory="ui/dist", html=True), name="ui")
    return app
```

Declare it (optional, tolerated forward-compatibly by the v1 validator):

```jsonc
"runtime": {
  "entrypoint": "myapp_app.plugin:MyAppPlugin",
  "standalone": { "module": "myapp_app", "default_port": 9400 }
}
```

**No `IdentityGuard` in standalone** — that's runtime machinery, not app
code. Default posture: bind `127.0.0.1`. A shared-token/`AW_APP_TOKEN` gate
is the app's own responsibility if you deploy it more openly. See
`template_app/__main__.py` for the full pattern, and `ui/vite.config.js` /
`ui/src/standalone.js` for the matching frontend half (same `client.js`
core as the plugin bundle, just same-origin relative URLs instead of
`host.sdk.api.*`).

## 8. What stays in core (nothing of yours)

The workspace runtime — not your app — owns: the mount point
(`/api/apps/<id>`), the `IdentityGuard` auth check on every integrated HTTP
and WS request, serving your built bundle from `<pkg>/ui/dist/` at
`/api/apps/<id>/ui/<file>`, the contributions/slot-registry feed the SPA
reads, and the single shared React/SDK instance every `component`-mode
bundle resolves through `window.__AW_PLUGIN_HOST__`. **Never**: add a new
root-level route or WS path for an app feature (`/ws/devctl` was the
mistake this ADR undoes; use `/ws/apps/<id>/...` for top-level app-owned
WS namespaces), implement your own auth on an integrated route,
or bundle your own copy of React into a plugin bundle.

## 8b. Apps that need a real device (`runtime.host_power`)

An app that runs a **guest** rather than a process cannot be built out of
userspace: without `/dev/kvm` a Windows VM falls back to software emulation
and is unusably slow, without `/dev/net/tun` it has no NIC, an Android guest
needs the binder devices. A Tier-2 container gets none of those by default,
and that stays the default.

`runtime.host_power` is the exception, gated three independent ways — and note
that **you only control the first two**:

1. **the app asks** — `runtime.host_power: ["kvm", "tun"]`;
2. **the app may ask** — the matching `host:*` permission from §3, all high
   risk, so marketplace-signed apps only;
3. **the machine offers it** — whoever owns the BYOD host runs
   `aw-remote-host bootstrap-workspace --host-power=kvm,tun`.

```jsonc
"tier": "container",                    // Tier-1 apps cannot be elevated
"runtime": {
  "image": "dockurr/windows",
  "port": 8006,
  "host_power": ["kvm", "tun"]
},
"permissions": ["containers:manage", "host:device-kvm", "host:device-tun"]
```

Grants: `kvm`, `tun`, `fuse`, `binder`, `privileged`, and `all` — which means
every grant **except** `privileged`, because "every device this host offers"
and "dissolve the container boundary" are different decisions and a keyword
must not make the second one for you.

Rules worth knowing before you write the manifest:

- **A host granted `privileged` satisfies any narrower request**, and your app
  still gets only what it declared (`kvm`+`tun` stays `kvm`+`tun`, not
  `--privileged`). One-way: `all` never satisfies a request for `privileged`.
- **A missing leg fails the install**, naming the leg and the command that
  fixes it. It does not start a container without the grant: a VM that comes up
  in software emulation reads as "the app is broken", with the real cause
  (a host that never opted in) nowhere in sight.
- **Ask for what you need, not `all`.** `all` in an app manifest requests four
  device grants and makes the app uninstallable on any host that cannot supply
  all four — including hosts that would have run it fine with two.
- **`tier: container` only.** A Tier-1 app already has the workspace's own
  access; the key there would read as a privilege and change nothing, so it is
  a manifest error.
- **Sidecars are refused, not ignored** (`runtime.sidecars[].host_power`) —
  tolerating it would start a companion container without its device while the
  manifest read as correct.
- **`--privileged` as a `run_flags_needed` entry is still rejected**, and
  always will be: that channel carries none of these checks.

Verifying it took effect: `aw-remote-host status` prints requested vs
effective with a reason per refusal (a request is not a grant — there is no
`/dev/kvm` on macOS), `aw-workspace-cli doctor` lists the grants and which apps
use them, and aw-console's remote-host panel shows a **Host power** badge plus
the delta.

Full reference, including the host-side commands and where each piece of code
lives: [`docs/host-power.md`](../../docs/host-power.md).

## 9. Depending on another app (`dependencies.apps`)

If your app needs another app's routes/tools to already be loaded — e.g. an
app that contributes an `mcp.json` needs the **MCP Gateway** app running so
its tools get picked up — declare it in the manifest's `dependencies.apps`
list:

```jsonc
"dependencies": {
  "apps": [
    {
      "id": "mcp-gateway",              // the dependency's app id/slug
      "version": ">=0.1.0",             // optional semver constraint (informational)
      "required": true,                 // default true; set false/omit for an optional dep
      "reason": "Contributes mcp.json definitions the gateway discovers and merges."
    }
  ]
}
```

Enforced by `AppReconciler._install_dependencies` (aw-workspace
`src/apps/reconciler.py`) — **not** a `depends_on` key; the field is
`dependencies.apps`, kept as a loose/forward-compatible object so unrelated
metadata can live alongside it. Behavior:

- `POST /api/apps/install` (and `aw-workspace marketplace install <id>` in
  the CLI, `bin/aw-workspace`) resolves every `required` entry in
  `dependencies.apps` **before** installing your app — pulling each
  dependency from the local mirror, the marketplace catalog, or an explicit
  `repo`/`package_dir` on the dependency entry, in that order. Already-loaded
  dependencies are skipped.
- A short string entry (`"apps": ["mcp-gateway"]`) is equivalent to
  `{"id": "mcp-gateway"}` — required by default.
- Set `"required": false` or `"optional": true` on an entry to make it a soft
  dependency (documented, but not auto-installed or blocking).
- Cyclic dependency chains raise instead of hanging (`cyclic app dependency
  chain: a -> b -> a`).
- The reconciler's removal pass also protects a loaded required dependency
  from being uninstalled just because only the *dependent* app was named in
  the desired set (`_loaded_dependency_closure`) — so installing `mcp-tools`
  keeps `mcp-gateway` around even if only `mcp-tools` is in the cloud
  registry's desired-apps row.

Real example: `aw-app-mcp-tools/aw-app.json` declares `mcp-gateway` as a
required dependency for exactly this reason — see that repo's manifest.

## 10. How it shows up + install

- Installed apps live in `/opt/aw-workspace/apps/<slug>/` (`AW_APPS_ROOT`);
  the runtime loads their manifests and serves `GET /api/apps` (list) +
  `GET /api/apps/-/contributions` (live-refetched nav/windows) +
  `GET /api/apps/-/catalog` (marketplace) +
  `GET /api/apps/-/skills` (every installed app's `contributes.skills`, §4).
- The SPA "Apps" launcher lists them as cards; clicking opens the default window.
- Install paths: `POST /api/apps/install` (fetch repo + reconcile), the
  reconciler's "Install My Apps", or a hand-sync of the app dir + workspace reload.
- **Marketplace:** the app must be listed in the marketplace catalog source
  (public, tokenless raw-GET). Ship `.github/workflows/release.yml` (see this
  repo) to cut versioned releases; the catalog references the repo.

### Test through the marketplace, not through a sideload

`POST /api/apps/install {package_dir}` exists and works, and it is the wrong
way to validate an app that is already in the catalog. **The cloud registry is
the source of truth and the reconciler converges to it**, so a sideload wins
for a few minutes and then loses — without logging anything a caller sees:

- Sideload v0.3.0 while the catalog says v0.2.1 → `version_changed` → the
  reconciler uninstalls yours and reinstalls the catalog copy. Pinning
  `version` to match only defers the problem to the next drift.
- Pin a version the catalog does not have *yet* — the raw catalog lags the
  sync PR merge by ~5 min — and the reconciler can remove the app outright.
  Observed 2026-08-13: every route 404, every MCP tool gone from the gateway,
  `install-status` reporting "not installed", while `apps/<slug>/` still sat
  on disk looking perfectly installed.
- The gateway's app-scan globs `apps/<slug>/mcp.json`. A `package_dir`
  pointing at `repos/aw-app-<name>` activates the app and serves its routes
  while contributing **zero** MCP tools, because the scan never looks there.
  If you must sideload, stage into `apps/<slug>/` first.
- Most of what actually breaks lives on the marketplace path anyway: release
  CI, the catalog sync PR, tag resolution, and grant derivation from catalog
  membership (`signed`, which gates `ui:code` — a denied `ui:code` silently
  removes your entire frontend). A sideload exercises none of it.

So the loop is: commit → push → release CI tags → merge the `chore(sync)` PR
in `tekflox/aw-marketplace` → wait until `GET /api/apps/-/catalog` (the
workspace's own view, not GitHub's) serves the new version → then
`aw-workspace-cli marketplace install <slug> --update`.

Two things to check after **any** install, marketplace included:

- **Did `ctx.config` survive?** A reinstall restored an app's secret-store
  token but silently dropped two of its four config keys, and every route kept
  answering 200. `doctor` did not flag it. Re-POST `/config` and verify.
- **Reload the MCP gateway.** An app that was down while the gateway scanned
  keeps serving zero tools until the gateway restarts — the upstream entry in
  the merged `mcp.json` is present and correct, it is just a dead connection.
  `aw-workspace-cli restart mcp-gateway`, then count the tools.

### First push after `gh repo create tekflox/aw-app-<name> --private --source=. --push`

The release CI will fail on the very first push with `Secret
MARKETPLACE_SYNC_TOKEN is required, but not provided` — that org secret has a
per-repo allowlist (`visibility: selected`) and a brand-new repo isn't on it
yet. Fix it immediately (don't make the user click through the GitHub
org-admin UI) using `GH_RUNNERS_ADMIN_TOKEN` (root `.env`, `admin:org` scope):

```bash
GH_ADMIN_TOKEN=$(grep ^GH_RUNNERS_ADMIN_TOKEN= /opt/aw-workspace/.env | cut -d= -f2)
REPO_ID=$(curl -s -H "Authorization: token $GH_ADMIN_TOKEN" \
  https://api.github.com/repos/tekflox/aw-app-<name> | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -s -o /dev/null -w "http_code=%{http_code}\n" -X PUT \
  -H "Authorization: token $GH_ADMIN_TOKEN" \
  "https://api.github.com/orgs/tekflox/actions/secrets/MARKETPLACE_SYNC_TOKEN/repositories/$REPO_ID"
```

204 = added. Then `gh workflow run release.yml --repo tekflox/aw-app-<name>`
to confirm CI actually goes green before telling the user it's done — org
secrets are write-only (never readable back via the API), so this allowlist
PUT is the only automatable step; nothing else about `MARKETPLACE_SYNC_TOKEN`
needs touching.

## 11. Reference apps (read these before building)

- `aw-app-template` (this repo) — Tier-1 + `commands:install` (the `hello`
  CLI) + `routes:register`/`ui:code` (a `/hello` + `/ws/echo` sub-app, a
  `core.nav` slot component — shipped commented out, so the template itself
  renders nothing in the nav; uncomment it in your app — and standalone
  mode — §5–§7 above).
- `aw-app-whiteboard` — Tier-1 + `routes:register` + `db:own-tables` + a window.
- `aw-app-devctl` — Tier-1 + `routes:register` (talks CDP to another app's container).
- `aw-app-browser` — Tier-2 container + `app_iframe` window → external subdomain (noVNC).

## 12. Checklist

1. Copy this repo → `aw-app-<name>`; rename `template_app/`, `id`, `name`, entrypoint.
2. Pick the tier; declare only the capabilities you use.
3. Add your `contributes` (managed window for a full UI, declarative window
   for a small settings/control panel, routes for a backend, frontend bundle,
   etc. — §4–§7 and `docs/window-contract.md`).
4. `python tests/validate_manifest.py` green; `pytest tests/` green (routes
   TestClient + standalone boot smoke); `npm run build` in `ui/` if you have
   a frontend; `tests/standalone_test.sh` green.
5. Push to `github.com/tekflox/aw-app-<name>`; wire `release.yml`; add to the
   marketplace catalog.
6. Install **via the marketplace** (`aw-workspace-cli marketplace install
   <slug>`, once the catalog serves your version — §10) and confirm the card
   shows in the Apps grid and opens. Sideloading is for an app that is not in
   the catalog yet; anything else the reconciler quietly reverts.
7. Re-check `ctx.config` and reload `mcp-gateway` after the install — both
   have been silently lost across a reinstall (§10).
