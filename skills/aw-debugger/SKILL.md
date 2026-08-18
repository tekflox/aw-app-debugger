---
name: aw-debugger
description: Remote Python debugging over DAP (Debug Adapter Protocol) — breakpoints, stepping, stack/scope/variable inspection, exception interception — against debugpy sessions exposed by agentic-workspace's DAP server, through the 17 debug_* tools contributed by aw-app-debugger. Use when asked to set a breakpoint, step through code, inspect a stack trace or variables, or evaluate an expression in a running debugpy session. NOT exercised against a live session since this app was ported — read "Ported as-is, not verified live" before trusting any tool's output blindly.
---

# aw-debugger — remote Python debugging over DAP

17 tools, gateway-prefixed `aw__debugger__*`. Ported AS-IS from
agentic-workspace's `src/mcp/debugger.py` (2026-08-18) — same `DAPClient`,
same tool set, same behavior, no refactor.

## Ported as-is, not verified live

This app was moved out of the monolith to avoid losing the code, not because
anything currently depends on it. **No live debugpy/DAP-server session was
available at port time**, and provisioning one was explicitly out of scope —
so beyond unit tests of the pure/no-network parts (JSON-RPC framing, the
17-tool surface, source-context reading), nothing here has been exercised
against a real target. Treat a confusing or empty result as "this needs a
live target to work at all" before assuming your breakpoint/expression is
wrong.

## What this app does NOT include

This app is the **MCP client only** — it talks to a DAP server over HTTP/TCP,
it does not run one. Nothing here starts, manages, or bridges to actual
`debugpy` sessions; that's agentic-workspace's separate `dap-server`
component (a debugpy multiplexer, historically on `:15050`), which was **not**
ported. Without a DAP server reachable at the configured host/port, every
tool below degrades to a plain "not available" string — it will not hang or
raise.

- **Config**: `default_host` / `default_port` in this app's `config_schema`
  (no Settings UI — `config_visible` is `false`; set via
  `POST /api/apps/debugger/config` directly). Same defaults the monolith
  hardcoded: `127.0.0.1:15050`.

## Known quirks preserved from the monolith (not bugs to fix here)

- **`tool_list_sessions` and `tool_disconnect` exist in the source but are
  not wired to any MCP tool** — no `debug_list_sessions` or
  `debug_disconnect` in the 17. This was already true in agentic-workspace;
  ported unchanged.
- **The direct-`DAPClient` code paths are effectively dead.** Every tool
  branches on whether a session is "routed via DAP server" vs a raw
  `DAPClient` instance, but nothing in this file ever constructs a
  `DAPClient` and stores it in `_sessions` — sessions only ever get
  registered as the string `"dap-server"`. The `DAPClient` class itself
  (connect/attach/DAP-message framing) is intact and correct-looking code,
  just currently unreachable from any tool call.
- **Presentation/awserv integration is monolith-only plumbing.** Functions
  that notify `awserv` (`:9123`), open a debugger presentation window, or
  read an API key file under `.tmp/awserv_api_key` all fail closed (caught
  exceptions, silent no-ops) here, same as they would in agentic-workspace
  with `awserv` not running. Nothing to configure — they're just inert.

## The 17 tools

| Tool | Does |
|---|---|
| `debug_status` | Full state of one session, or an overview of all connected sessions if no `session` given. Also lists all breakpoints. |
| `debug_threads` | List threads in the debugged process. `session="all"` spans every connected service. |
| `debug_pause` | Pause a running process. |
| `debug_stack_trace` | Current call stack (function, file, line per frame). `session="all"` for every stopped service. |
| `debug_scopes` | Locals/Globals for a stack frame — returns `variablesReference` ids for `debug_variables`. |
| `debug_variables` | Expand a scope or a nested variable by `variablesReference`. |
| `debug_set_variable` | Set a variable's value (evaluated as a Python expression). |
| `debug_evaluate` | Evaluate a Python expression in the current frame — reads, calls, runs arbitrary code. |
| `debug_set_breakpoints` | Set breakpoints in a file, replacing existing ones there. Supports per-line conditions. |
| `debug_remove_breakpoints` | Remove breakpoints — one file, or all if no `path`. |
| `debug_continue` | Resume until the next breakpoint or exit. |
| `debug_next` | Step over. |
| `debug_step_in` | Step into a call. |
| `debug_step_out` | Run until the current function returns. |
| `debug_exception_breakpoints` | Break on `raised` / `uncaught` / `userUnhandled` exceptions. |
| `debug_events` | Drain recent events (stopped/continued/output) from the real-time stream. |
| `debug_open_file` | Open a source file in VSCode, optionally at a line. |

## Minimal flow

1. **`debug_status`** with no `session` — see what's connected, what's
   stopped, existing breakpoints. If nothing is connected, every subsequent
   call will say so; that means no DAP server is reachable, not that the
   tools are broken.
2. **`debug_set_breakpoints(session, path, lines)`** — `path` must be an
   absolute path that exists on the machine the DAP server itself runs on
   (checked with `os.path.isfile`, evaluated in this MCP process, not the
   debugged one).
3. **`debug_continue`** (or let the process run into the breakpoint on its
   own).
4. Once stopped: **`debug_stack_trace`** → pick a `frame_id` → **`debug_scopes`**
   → pick a `variablesReference` → **`debug_variables`**. `debug_evaluate` is
   the shortcut for "just tell me the value of X" without walking scopes.
5. **`debug_next`** / **`debug_step_in`** / **`debug_step_out`** to move;
   **`debug_events`** to see what happened since your last call.

Most tools take an optional `session` — omit it when exactly one session is
connected (auto-resolved); pass it explicitly with more than one.

## Failure modes

| Symptom | Cause |
|---|---|
| "DAP server is not available. Start it with: aw start dap-server" | No DAP server reachable at `default_host:default_port` — expected in this app, since the DAP server itself was not ported. |
| "No active debug sessions. Use connect first." | No session name resolvable and nothing connected. |
| "Multiple sessions active. Specify 'session': [...]" | More than one session connected — pass `session` explicitly. |
| A tool call just hangs briefly then returns a timeout-shaped error | The DAP server answered the initial probe but the underlying debugpy connection is gone — same ambiguity the monolith had. |
