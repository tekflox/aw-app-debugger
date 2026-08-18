"""Unit tests for debugger_app/mcp_server.py — no network, no live debugpy/
DAP server, no running workspace. Covers what the AS-IS port promises:
JSON-RPC framing, the 17-tool surface, host/port parameterization, and a
couple of pure helper functions. Deliberately does NOT try to exercise the
DAPClient/DAP-server code paths end-to-end — that needs a live session,
which is explicitly out of scope for this port (see README)."""
from pathlib import Path
import importlib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload_with_env(monkeypatch, host=None, port=None):
    if host is not None:
        monkeypatch.setenv("AW_DEBUGGER_DAP_HOST", host)
    if port is not None:
        monkeypatch.setenv("AW_DEBUGGER_DAP_PORT", port)
    from debugger_app import mcp_server
    return importlib.reload(mcp_server)


def test_default_dap_host_port_match_the_monolith(monkeypatch):
    monkeypatch.delenv("AW_DEBUGGER_DAP_HOST", raising=False)
    monkeypatch.delenv("AW_DEBUGGER_DAP_PORT", raising=False)
    mcp_server = _reload_with_env(monkeypatch)
    assert mcp_server.DAP_HOST == "127.0.0.1"
    assert mcp_server.DAP_PORT == 15050
    assert mcp_server.DAP_SERVER_URL == "http://127.0.0.1:15050"


def test_dap_host_port_overridable_via_env(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch, host="10.1.2.3", port="9999")
    assert mcp_server.DAP_HOST == "10.1.2.3"
    assert mcp_server.DAP_PORT == 9999
    assert mcp_server.DAP_SERVER_URL == "http://10.1.2.3:9999"


def test_exposes_exactly_17_tools(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    assert len(mcp_server.TOOLS) == 17
    assert len(mcp_server.TOOL_HANDLERS) == 17
    names_in_schema = {t["name"] for t in mcp_server.TOOLS}
    assert names_in_schema == set(mcp_server.TOOL_HANDLERS)


def test_tool_names_match_the_kanban_card_list(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    expected = {
        "debug_status", "debug_open_file", "debug_set_breakpoints",
        "debug_remove_breakpoints", "debug_exception_breakpoints", "debug_continue",
        "debug_next", "debug_step_in", "debug_step_out", "debug_pause",
        "debug_stack_trace", "debug_scopes", "debug_variables", "debug_set_variable",
        "debug_evaluate", "debug_threads", "debug_events",
    }
    assert set(mcp_server.TOOL_HANDLERS) == expected


def test_list_sessions_and_disconnect_are_not_wired_to_any_mcp_tool(monkeypatch):
    """Known AS-IS quirk, not a bug to fix here: tool_list_sessions and
    tool_disconnect exist as functions in the source but were never
    registered in TOOLS/TOOL_HANDLERS in the monolith either — so there is
    no debug_list_sessions or debug_disconnect tool. Documented in README;
    this test just pins the observation so nobody "fixes" it unnoticed."""
    mcp_server = _reload_with_env(monkeypatch)
    assert hasattr(mcp_server, "tool_list_sessions")
    assert hasattr(mcp_server, "tool_disconnect")
    assert "debug_list_sessions" not in mcp_server.TOOL_HANDLERS
    assert "debug_disconnect" not in mcp_server.TOOL_HANDLERS


def test_initialize_reports_server_name(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    result = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert result["result"]["serverInfo"]["name"] == "aw-debugger"


def test_tools_list_returns_all_17(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    result = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert len(result["result"]["tools"]) == 17


def test_ping(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    result = mcp_server.handle_request({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert result == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_unknown_method(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    result = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "nope"})
    assert result["error"]["code"] == -32601


def test_unknown_tool_call_is_an_error(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    result = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    assert result["result"]["isError"] is True


def test_status_with_no_dap_server_and_no_sessions_does_not_crash(monkeypatch):
    """No live DAP server / debugpy session in CI — the tool must degrade to
    a plain string, not raise. This is the one behavior explicitly promised
    by the Kanban card ('não precisa que executem contra um alvo real')."""
    mcp_server = _reload_with_env(monkeypatch)
    mcp_server._sessions.clear()
    result = mcp_server.tool_status({})
    assert isinstance(result, str)
    assert "No active debug sessions" in result


def test_fmt_response_surfaces_dap_error_message(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    assert mcp_server._fmt_response({"success": False, "message": "boom"}) == "Error: boom"


def test_fmt_response_passes_through_non_dict(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    assert mcp_server._fmt_response("already a string") == "already a string"


def test_read_source_context(monkeypatch, tmp_path):
    mcp_server = _reload_with_env(monkeypatch)
    f = tmp_path / "sample.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")
    ctx = mcp_server._read_source_context(str(f), 5, context=2)
    assert ctx["current_line"] == 5
    lines = {l["num"]: l["text"] for l in ctx["lines"]}
    assert lines[5] == "line 5"
    assert any(l["current"] for l in ctx["lines"] if l["num"] == 5)


def test_read_source_context_missing_file_returns_none(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    assert mcp_server._read_source_context("/no/such/file.py", 1) is None


def test_get_debugpy_version_extracts_from_output_event(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    events = [{"event": "output", "body": {"data": {"packageVersion": "1.8.0"}}}]
    assert mcp_server._get_debugpy_version(events) == "1.8.0"


def test_get_debugpy_version_unknown_when_absent(monkeypatch):
    mcp_server = _reload_with_env(monkeypatch)
    assert mcp_server._get_debugpy_version([]) == "unknown"
