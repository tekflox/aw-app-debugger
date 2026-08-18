# aw-app-debugger

Remote Python debugging over DAP (Debug Adapter Protocol) for every agent in
this workspace — breakpoints, stepping, stack/scope/variable inspection,
exception interception.

**This is a preservation port, not a feature.** Frederico's own framing: "I
don't want to lose the code, but I also don't want to work on it right now."
Ported AS-IS from agentic-workspace's `src/mcp/debugger.py` — same
`DAPClient`, same 17 tools, same behavior, no refactor, no fixes to things
that look broken, no live-session testing (none was available, and
provisioning one was explicitly out of scope for this port).

17 tools, gateway-prefixed `aw__debugger__*`.

| Monolith | This app |
|---|---|
| `agentic-workspace/src/mcp/debugger.py` (stdio MCP, hand-rolled JSON-RPC, 2146 lines) | `debugger_app/mcp_server.py` — same file, relocated into the app's own package, unchanged behavior |
| `src/config/mcp.json`'s `aw-debugger` entry | this app's own `mcp.json`, regenerated on activate/config-save (like aw-app-google-maps / aw-app-mcp-tools) |
| Hardcoded `127.0.0.1:15050` (the DAP server) in 3 places | `config_schema`'s `default_host`/`default_port`, same default values, threaded through as `AW_DEBUGGER_DAP_HOST`/`AW_DEBUGGER_DAP_PORT` env on the stdio subprocess |
| *(no dedicated skill in the monolith)* | `skills/aw-debugger/SKILL.md` — new, teaches the 17 tools, the minimal flow, and the "not verified live" caveat |

## What was NOT ported

Only `src/mcp/debugger.py` — the MCP client. Its counterpart,
agentic-workspace's `dap-server` (a debugpy multiplexer, historically on
`:15050`, the actual bridge to real `debugpy` processes), was **not**
ported — out of scope for this card. Without it reachable at the configured
host/port, every tool degrades to a plain "DAP server is not available"
string rather than hanging or raising. See `skills/aw-debugger/SKILL.md` for
the full list of monolith-only integrations (awserv notify, presentation
window, `aw start dap-server` auto-launch) that are still in the file,
unmodified, and simply no-op here.

## Known quirks preserved, not fixed

Found while reading the source, deliberately left as-is per the card's
instructions ("anote, não conserte"):

- **`tool_list_sessions` / `tool_disconnect` exist but are not registered as
  MCP tools** — there is no `debug_list_sessions` or `debug_disconnect`
  among the 17. Already true in the monolith.
- **The direct-`DAPClient` connection path looks unreachable.** Every tool
  branches on "routed via DAP server" vs "direct `DAPClient` instance", but
  nothing in the file ever constructs a `DAPClient` and registers it —
  `_sessions` only ever holds the string `"dap-server"`. The `DAPClient`
  class (raw DAP framing over a socket) is intact, just currently dead code
  from every tool's perspective.

## Config

`config_schema` (UI-hidden — `config_visible: false`, set via
`POST /api/apps/debugger/config` directly, no Settings gear):

| Key | Default | Meaning |
|---|---|---|
| `default_host` | `127.0.0.1` | Host of the DAP server this MCP talks to |
| `default_port` | `15050` | Port of the DAP server this MCP talks to |

Same defaults the monolith hardcoded — a fresh install behaves identically
to before the port.

## Install

```bash
aw-workspace-cli marketplace install debugger
```

Then **restart mcp-gateway** — this is a Tier-1 in-process app, so
`aw-workspace-cli restart` does not apply to it; the gateway only picks up a
newly-installed app's `mcp.json` on its own restart.

## Tests

```bash
python3 tests/validate_manifest.py aw-app.json --schema <path-to-aw-marketplace>/schemas/aw-app.schema.json
python3 -m pytest tests -q
```

No network calls, no live DAP server / debugpy session required — see
`tests/test_mcp_server.py` for exactly what is and isn't covered.
