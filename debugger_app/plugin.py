"""Entrypoint referenced by aw-app.json's ``runtime.entrypoint``
("debugger_app.plugin:DebuggerAppPlugin").

Nothing for this Tier-1 plugin to register through the framework's ``ctx``
besides its own ``mcp.json`` — no routes, no CLIs, no window (compare
aw-app-weather's plugin.py, the closest sibling: also a single stdio MCP
server with no UI). The one thing this app's manifest adds over weather's is
a (UI-hidden) ``config_schema`` for the DAP server host/port, so activate()
and on_config_saved() both regenerate mcp.json from ``ctx.config`` — same
pattern as aw-app-mcp-tools/aw-app-google-maps.

``ctx.config`` at activate() time is the raw stored config, NOT defaulted
from config_schema (that JSON-Schema default is only applied by
aw-workspace's API-response layer) — mcp_config.build_mcp_servers() falls
back to the same literal defaults the monolith hardcoded, so a fresh install
with no config saved yet behaves identically to before the port.
"""
from __future__ import annotations

import logging

from . import mcp_config

log = logging.getLogger("aw_apps.debugger")


class DebuggerAppPlugin:
    async def activate(self, ctx) -> None:
        self.ctx = ctx
        config = getattr(ctx, "config", {}) or {}
        doc = mcp_config.write_mcp_json(ctx.package_dir, config)
        log.info(
            "aw-app-debugger activated: mcp server=%s (stdio, dap host=%s port=%s)",
            sorted(doc["mcpServers"]),
            config.get("default_host") or mcp_config.DEFAULT_HOST,
            config.get("default_port") or mcp_config.DEFAULT_PORT,
        )

    async def on_config_saved(self, ctx) -> None:
        """Regenerate mcp.json from the newly-saved default_host/default_port
        before aw-workspace tells the MCP Gateway to /reload (contributes.mcp.
        reload_on_save) — same ordering as aw-app-mcp-tools."""
        config = getattr(ctx, "config", {}) or {}
        doc = mcp_config.write_mcp_json(ctx.package_dir, config)
        log.info("aw-app-debugger config saved: mcp.json servers=%s", sorted(doc["mcpServers"]))

    async def deactivate(self) -> None:
        log.info("aw-app-debugger deactivated")
