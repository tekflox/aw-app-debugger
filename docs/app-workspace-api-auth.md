# Calling the Workspace API From an App or MCP

Every aw-workspace has one shared secret — the **workspace API key** — that
lets an app, an MCP server, or any other process authenticate into that
workspace's HTTP API without a browser-issued identity JWT (`aw_id_jwt`).
This document covers what it is, where it lives, and how to use it from
both an in-process app and a standalone external process.

## What It Is

- A random 64-character hex string, generated automatically the first time
  a workspace boots — no manual setup step.
- Stored in the workspace's own `settings` key-value table (Postgres),
  scoped to that one workspace like everything else in its schema.
- Visible and regenerable from the workspace UI at **Settings →
  Integrations → Workspace API Key** (reveal / copy / regenerate).
- A single shared secret, not per-app and not per-caller. Anyone who has it
  can call any route that accepts it. Treat it exactly like a password —
  don't commit it, don't log it, don't put it in a URL query string.

Regenerating invalidates the previous key immediately — every caller still
using the old value gets a `401` on its next request.

## How Authentication Works

Send the key as a header on any HTTP request to the workspace's API:

```
X-Api-Key: <the key>
```

The workspace's identity layer checks this header as an alternative to the
usual identity JWT, on both kinds of routes:

- **App routes** (`/api/apps/<slug>/...`, whatever your app's own
  `contributes.routes` mounts) — checked by the framework's `IdentityGuard`
  before your app's code ever runs. A valid key is treated as an
  authenticated caller (`{"sub": "workspace-api-key"}`); your app doesn't
  need to check it itself.
- **Framework routes** (`/api/settings/...`, `/api/apps`, ...) — checked by
  the same `require_identity` dependency the identity JWT goes through.

A missing or wrong key behaves exactly like a missing or wrong JWT: `401`.

## Two Ways an App Reads the Key

Which one applies depends on whether your code runs **inside** the
aw-workspace process (a Tier-1 in-process app) or **outside** it (a
standalone MCP server, a script, another service).

### 1. In-process Tier-1 app — read `os.environ`

Your app's Python code runs in the SAME process as aw-workspace. Every time
the workspace generates or regenerates its key, it publishes the value
directly into `os.environ["AW_WORKSPACE_API_KEY"]` for that process — no
restart needed, no core-module import needed (apps must not import
`src.api.*` directly; a plain environment variable is the sanctioned
surface for this one case):

```python
import os
import httpx

def call_some_workspace_route():
    api_key = os.environ.get("AW_WORKSPACE_API_KEY")
    headers = {"X-Api-Key": api_key} if api_key else {}
    return httpx.get("http://127.0.0.1:9030/api/apps/other-app/status", headers=headers)
```

This is what `aw-app-whiteboard`'s own outbound presentation-API calls do
(`whiteboard_app/routes.py`) — no `config_schema` field for the key, it
just reads the environment.

### 2. Standalone external process — read the `.env` file

Your process is separate — a stdio MCP server, a script on another
machine, a browser extension's companion service. It has no shared process
memory with aw-workspace, so it can't rely on step 1's `os.environ` publish.

aw-workspace ALSO writes the key to `<AW_WORKSPACE_HOME>/.env` (default
`~/.aw-workspace/.env`) on every generate/regenerate:

```
AW_WORKSPACE_API_KEY=6045f44044391d20e1fa8fc75c88a619588ada22a055e95fb6f5128f15a1e378
```

Read it fresh on every call (not once at process start) so a regenerated
key takes effect without restarting your process:

```python
import os
from pathlib import Path

ENV_VAR_NAME = "AW_WORKSPACE_API_KEY"


def _read_key_from_env_file(path: str | None = None) -> str | None:
    path = path or os.environ.get("AW_WORKSPACE_ENV_FILE") or str(Path.home() / ".aw-workspace" / ".env")
    prefix = f"{ENV_VAR_NAME}="
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(prefix):
                    return line[len(prefix):].strip() or None
    except FileNotFoundError:
        return None
    return None


def get_api_key() -> str | None:
    # An explicit env var always wins (e.g. set by the process that spawned
    # this one, or a developer testing locally) — otherwise fall back to
    # the file aw-workspace itself writes to.
    return os.environ.get(ENV_VAR_NAME) or _read_key_from_env_file()
```

If your process runs on a DIFFERENT machine than the workspace (not the
common case, but supported), there's no local `.env` file to read — pass
the key in explicitly via your own process's config/env instead, and skip
the file-fallback step entirely.

## Full Worked Example

`tekflox/aw-app-whiteboard`'s [`mcp_server/`](https://github.com/tekflox/aw-app-whiteboard/tree/master/mcp_server)
is a complete, tested, real-world example of pattern 2 — a standalone
stdio MCP server that authenticates into a running aw-workspace with
`X-Api-Key` and drives the Whiteboard app's own HTTP routes. Read
`mcp_server/server.py`'s `_get_api_key()` / `_api()` helpers for the exact
request-building code, and `mcp_server/README.md` for how to configure and
run it.

That MCP was verified end-to-end against a real, freshly-booted
aw-workspace instance before release: a request with no key or the wrong
key gets `401`; a request with the real key succeeds; regenerating the key
immediately invalidates the old one and the MCP picks up the new one via
the `.env` fallback with no restart.

## Security Notes

- The key is a bearer credential — anyone who has it can call anything it
  authorizes. Don't commit it to a repo, don't put it in client-side
  JavaScript, don't log the raw value.
- Regenerate it if you suspect it leaked (Settings → Integrations →
  Workspace API Key → Regenerate). This is a hard cutover — every caller
  using the old key needs the new one.
- It authenticates a request as "some trusted caller," not as a specific
  human user — routes that need to know WHICH person is calling (audit
  logs, per-user permissions) should still prefer the identity JWT where a
  real user session exists, and reserve the API key for
  service-to-service / automation callers.
