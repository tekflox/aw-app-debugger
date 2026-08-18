---
repo: architecture
path: docs/architecture/aw-app-hello.md
source: generated
edited: false
checksum: sha256:fa837bf06eca8658c316febfadd4662b0f75f1555291a2e60d699c17406602f8
---
# Hello World (App Template)

- **repo**: aw-app-template
- **layer**: app
- **technologies**: python, react
- **health** (derived): planned

TEMPLATE — the fastest way to start a new aw-workspace app. Install it and you have a working app on day one: a `hello` CLI that prints a configurable greeting, its own window in the Apps grid, and an HTTP + WebSocket backend — with tests and marketplace release already wired. Rename everything marked TEMPLATE/hello to make it yours; see README.md.

## Connections
- `http` → **aw-workspace** — routes mounted at /api/apps/hello

## MCP tools
_none exposed_

## Requirements
_none documented_
