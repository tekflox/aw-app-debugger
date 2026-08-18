"""Builds this app's own root ``mcp.json`` — the file aw-mcp-gateway's
app-scan reads directly (same contract as aw-app-weather/aw-app-mcp-tools).

One server: ``debugger``, a stdio subprocess (``debugger_app.mcp_server``,
ported unchanged from agentic-workspace's ``src/mcp/debugger.py``). The two
host/port literals that file used to hardcode (the DAP server it talks to,
``127.0.0.1:15050``) are threaded through here as env vars instead, sourced
from ``config_schema``'s ``default_host``/``default_port`` — same default
values, so a fresh install behaves identically to the monolith.

Regenerated on every ``activate()`` and on every ``on_config_saved()`` (see
plugin.py), mirroring aw-app-mcp-tools/aw-app-google-maps — NOT a static
committed file, because the whole point of the config knobs is that editing
them (via ``POST /api/apps/debugger/config`` — there is no Settings UI,
``config_visible`` is false) has to reach the subprocess env on the next
gateway reload.
"""
from __future__ import annotations

import json
from pathlib import Path

SERVER_NAME = "debugger"

# Same defaults agentic-workspace/src/mcp/debugger.py hardcoded.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 15050


def build_mcp_servers(config: dict | None = None) -> dict:
    config = config or {}
    host = str(config.get("default_host") or DEFAULT_HOST)
    port = int(config.get("default_port") or DEFAULT_PORT)
    return {
        SERVER_NAME: {
            "enabled": True,
            "type": "stdio",
            "command": "python3",
            "args": ["-m", "debugger_app.mcp_server"],
            "env": {
                "PYTHONUNBUFFERED": "1",
                "AW_DEBUGGER_DAP_HOST": host,
                "AW_DEBUGGER_DAP_PORT": str(port),
            },
        },
    }


def write_mcp_json(package_dir: str, config: dict | None = None) -> dict:
    """Regenerate ``<package_dir>/mcp.json``, skipping the write when nothing
    changed — aw-mcp-gateway reloads on mtime, and a no-op rewrite on every
    activate would be a reload loop (see aw-app-google-maps' write_mcp_json)."""
    doc = {"mcpServers": build_mcp_servers(config)}
    body = json.dumps(doc, indent=2) + "\n"
    path = Path(package_dir) / "mcp.json"
    try:
        if path.read_text(encoding="utf-8") == body:
            return doc
    except FileNotFoundError:
        pass
    path.write_text(body, encoding="utf-8")
    return doc
