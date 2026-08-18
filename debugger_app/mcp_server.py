"""MCP server for remote Python debugging via DAP (Debug Adapter Protocol).

Connects to debugpy sessions running on aw components and exposes
debugging operations (breakpoints, stepping, inspection) as MCP tools.

This is a stdio-based MCP server.

Ported AS-IS from agentic-workspace's ``src/mcp/debugger.py`` (2026-08-18) —
preservation port, not a rewrite. Behavior, including known quirks and dead
code, is unchanged; see this repo's README for the full list of what that
means concretely. The only functional change from the original: the DAP
server host/port were hardcoded to ``127.0.0.1:15050`` in three places
(``DAP_SERVER_URL``, ``_dap_server_available``, ``_EventListener.
_connect_and_listen``) — those now read ``AW_DEBUGGER_DAP_HOST`` /
``AW_DEBUGGER_DAP_PORT``, set by this app's ``debugger_app/mcp_config.py``
from ``config_schema``'s ``default_host``/``default_port``, with the exact
same values as defaults. Everything else — the awserv notify/presentation
integration, the "aw start dap-server" auto-launch, the API key file lookup —
is monolith-only plumbing that does not exist in this app; it fails closed
(caught exceptions, silent no-ops) exactly as it would in agentic-workspace
if awserv/dap-server were not running, so it was left untouched.
"""

import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.request


class DAPClient:
    """Minimal DAP client that connects to a debugpy server."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock = None
        self._seq = 1
        self._responses = {}
        self._events = []
        self._lock = threading.Lock()
        self._reader_thread = None
        self._running = False
        self._buffer = b""
        self._thread_id = None
        self.session_name = None
        self._on_stopped = None

    def connect(self, timeout=5):
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(0.2)
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def disconnect(self):
        self._running = False
        try:
            self._send_request("disconnect", {"restart": False, "terminateDebuggee": False})
        except Exception:
            pass
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    def _send_request(self, command, arguments=None):
        seq = self._seq
        self._seq += 1
        msg = {
            "seq": seq,
            "type": "request",
            "command": command,
        }
        if arguments is not None:
            msg["arguments"] = arguments
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._sock.sendall(header + body)
        return seq

    def _read_loop(self):
        while self._running:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                self._buffer += chunk
                self._process_buffer()
            except socket.timeout:
                continue
            except Exception:
                break

    def _process_buffer(self):
        while True:
            header_end = self._buffer.find(b"\r\n\r\n")
            if header_end == -1:
                break
            header = self._buffer[:header_end].decode("ascii")
            content_length = 0
            for line in header.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":")[1].strip())
            body_start = header_end + 4
            if len(self._buffer) < body_start + content_length:
                break
            body = self._buffer[body_start:body_start + content_length]
            self._buffer = self._buffer[body_start + content_length:]
            try:
                msg = json.loads(body.decode("utf-8"))
                with self._lock:
                    if msg.get("type") == "response":
                        self._responses[msg.get("request_seq")] = msg
                    elif msg.get("type") == "event":
                        self._events.append(msg)
                        if msg.get("event") == "stopped":
                            self._thread_id = msg.get("body", {}).get("threadId")
                            if self._on_stopped:
                                threading.Thread(
                                    target=self._on_stopped,
                                    args=(self,),
                                    daemon=True,
                                ).start()
            except Exception:
                pass

    def _wait_response(self, seq, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if seq in self._responses:
                    return self._responses.pop(seq)
            time.sleep(0.05)
        return {"success": False, "message": "timeout waiting for response"}

    def request(self, command, arguments=None, timeout=10):
        seq = self._send_request(command, arguments)
        return self._wait_response(seq, timeout)

    def initialize(self):
        resp = self.request("initialize", {
            "clientID": "aw-debugger",
            "clientName": "AW MCP Debugger",
            "adapterID": "debugpy",
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "supportsVariableType": True,
            "supportsVariablePaging": True,
            "supportsRunInTerminalRequest": True,
            "supportsStartDebuggingRequest": True,
        }, timeout=15)
        if not resp or resp.get("success") is not True:
            return resp
        attach_seq = self._send_request("attach", {
            "justMyCode": False,
            "subProcess": True,
        })
        deadline = time.time() + 10
        while time.time() < deadline:
            with self._lock:
                if any(e.get("event") == "initialized" for e in self._events):
                    break
            time.sleep(0.1)
        self._send_request("configurationDone")
        self._wait_response(attach_seq, timeout=10)
        time.sleep(0.5)
        self.drain_events()
        return resp

    def get_threads(self):
        return self.request("threads")

    def get_stack_trace(self, thread_id, start_frame=0, levels=20):
        return self.request("stackTrace", {
            "threadId": thread_id,
            "startFrame": start_frame,
            "levels": levels,
        })

    def get_scopes(self, frame_id):
        return self.request("scopes", {"frameId": frame_id})

    def get_variables(self, variables_reference, start=0, count=100):
        return self.request("variables", {
            "variablesReference": variables_reference,
            "start": start,
            "count": count,
        })

    def set_variable(self, variables_reference, name, value):
        return self.request("setVariable", {
            "variablesReference": variables_reference,
            "name": name,
            "value": value,
        })

    def evaluate(self, expression, frame_id=None, context="repl"):
        args = {"expression": expression, "context": context}
        if frame_id is not None:
            args["frameId"] = frame_id
        return self.request("evaluate", args)

    def set_breakpoints(self, source_path, lines):
        breakpoints = [{"line": l} for l in lines]
        return self.request("setBreakpoints", {
            "source": {"path": source_path},
            "breakpoints": breakpoints,
        })

    def continue_execution(self, thread_id=None):
        tid = thread_id or self._thread_id or 1
        return self.request("continue", {"threadId": tid})

    def next_step(self, thread_id=None):
        tid = thread_id or self._thread_id or 1
        return self.request("next", {"threadId": tid})

    def step_in(self, thread_id=None):
        tid = thread_id or self._thread_id or 1
        return self.request("stepIn", {"threadId": tid})

    def step_out(self, thread_id=None):
        tid = thread_id or self._thread_id or 1
        return self.request("stepOut", {"threadId": tid})

    def pause(self, thread_id=None):
        tid = thread_id or self._thread_id or 1
        return self.request("pause", {"threadId": tid})

    def set_exception_breakpoints(self, filters):
        return self.request("setExceptionBreakpoints", {"filters": filters})

    def drain_events(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events




_sessions = {}


# Same defaults the monolith hardcoded; overridable via this app's
# config_schema (default_host/default_port) -> mcp_config.py -> these env
# vars, set on the stdio subprocess by aw-mcp-gateway.
DAP_HOST = os.environ.get("AW_DEBUGGER_DAP_HOST", "127.0.0.1")
DAP_PORT = int(os.environ.get("AW_DEBUGGER_DAP_PORT", "15050"))
DAP_SERVER_URL = f"http://{DAP_HOST}:{DAP_PORT}"
_dap_server_started = False


def _dap_server_available():
    """Check if the DAP server is listening on DAP_PORT."""
    try:
        s = socket.create_connection((DAP_HOST, DAP_PORT), timeout=1)
        s.close()
        return True
    except (OSError, socket.error):
        return False


def _ensure_dap_server():
    """Start the DAP server if not already running. Returns True if available."""
    global _dap_server_started
    if os.environ.get("AW_NO_DAP_SERVER") == "1":
        return False
    if _dap_server_available():
        return True
    if _dap_server_started:
        return False
    _dap_server_started = True
    try:
        import subprocess
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        subprocess.Popen(
            [os.path.join(project_root, "aw"), "start", "dap-server", "--bg"],
            cwd=project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if _dap_server_available():
                return True
            time.sleep(0.5)
    except Exception:
        pass
    return False


def _dap_request(method, path, data=None, timeout=15):
    """HTTP request to the DAP server. Returns parsed JSON response."""
    url = f"{DAP_SERVER_URL}{path}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _is_routed(name):
    """Check if the session is managed by the DAP server."""
    return _sessions.get(name) == "dap-server"



class _EventListener:
    """Background WS listener to /ws/events. Tracks live state from all services."""

    def __init__(self):
        self._lock = threading.Lock()
        self._recent_events: dict[str, list[dict]] = {}
        self._stopped: dict[str, set[int]] = {}
        self._last_stop: dict[str, dict] = {}
        self._connected = False
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """Connect to WS and process events. Auto-reconnects."""
        while True:
            try:
                self._connect_and_listen()
            except Exception:
                pass
            self._connected = False
            time.sleep(5)

    def _connect_and_listen(self):
        """Connect using raw WebSocket over TCP (no external deps)."""
        import hashlib
        import base64

        host, port = DAP_HOST, DAP_PORT
        path = "/ws/events"

        sock = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(handshake.encode())

        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WS handshake failed")
            resp += chunk
        if b"101" not in resp.split(b"\r\n")[0]:
            raise ConnectionError(f"WS handshake rejected: {resp[:100]}")

        self._connected = True
        sock.settimeout(35)
        last_ping = time.time()

        while True:
            try:
                msg = self._ws_recv(sock)
                if msg is None:
                    break
                data = json.loads(msg)
                self._handle(data)
            except socket.timeout:
                if time.time() - last_ping > 25:
                    try:
                        self._ws_send(sock, json.dumps({"type": "ping"}))
                        last_ping = time.time()
                    except Exception:
                        break
            except Exception:
                break
        sock.close()

    @staticmethod
    def _ws_recv(sock):
        """Read one WebSocket text frame."""
        header = b""
        while len(header) < 2:
            chunk = sock.recv(2 - len(header))
            if not chunk:
                return None
            header += chunk
        b0, b1 = header[0], header[1]
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        length = b1 & 0x7F
        if length == 126:
            raw = sock.recv(2)
            length = struct.unpack("!H", raw)[0]
        elif length == 127:
            raw = sock.recv(8)
            length = struct.unpack("!Q", raw)[0]
        if masked:
            mask = sock.recv(4)
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            sock.sendall(bytes([0x8A, len(payload)]) + payload)
            return ""
        return payload.decode("utf-8", errors="replace")

    @staticmethod
    def _ws_send(sock, text: str):
        """Send a masked WebSocket text frame."""
        payload = text.encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytearray()
        header.append(0x81)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        sock.sendall(bytes(header) + masked)

    def _handle(self, data):
        evt_type = data.get("type", "")
        service = data.get("service", "")

        if evt_type == "init":
            with self._lock:
                self._stopped.clear()
                self._last_stop.clear()
                for svc in data.get("services", []):
                    name = svc.get("name", "")
                    if svc.get("stopped"):
                        tid = svc.get("thread_id")
                        if tid:
                            self._stopped.setdefault(name, set()).add(tid)
            return

        if not service:
            return

        if evt_type == "stopped":
            stack = data.get("stack", [])
            if stack and isinstance(stack, list):
                top = stack[0]
                data["function"] = top.get("name", "")
                data["file"] = top.get("path", "") or top.get("file", "")
                data["line"] = top.get("line", "")

        with self._lock:
            events = self._recent_events.setdefault(service, [])
            events.append(data)
            if len(events) > 50:
                self._recent_events[service] = events[-50:]

        if evt_type == "stopped":
            tid = data.get("thread_id")
            with self._lock:
                if tid:
                    self._stopped.setdefault(service, set()).add(tid)
                self._last_stop[service] = {
                    "thread_id": tid,
                    "reason": data.get("reason", "breakpoint"),
                    "stack": data.get("stack"),
                    "source": data.get("source"),
                }

        elif evt_type == "continued":
            tid = data.get("body", {}).get("threadId")
            all_continued = data.get("body", {}).get("allThreadsContinued", False)
            with self._lock:
                if all_continued:
                    self._stopped.pop(service, None)
                    self._last_stop.pop(service, None)
                elif tid and service in self._stopped:
                    self._stopped[service].discard(tid)
                    if not self._stopped[service]:
                        self._stopped.pop(service, None)
                        self._last_stop.pop(service, None)

        elif evt_type == "disconnected":
            with self._lock:
                self._stopped.pop(service, None)
                self._last_stop.pop(service, None)

    def get_stopped_services(self) -> dict[str, dict]:
        """Return {service: {thread_ids, last_stop_info}} for all stopped services."""
        with self._lock:
            result = {}
            for svc, tids in self._stopped.items():
                if tids:
                    result[svc] = {
                        "thread_ids": sorted(tids),
                        "last_stop": self._last_stop.get(svc),
                    }
            return result

    def get_recent_events(self, service: str, count: int = 10) -> list[dict]:
        with self._lock:
            return list(self._recent_events.get(service, [])[-count:])

    @property
    def is_connected(self) -> bool:
        return self._connected


_event_listener = _EventListener()



AWSERV_URL = "http://127.0.0.1:9123"


_API_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".tmp", "awserv_api_key")


def _get_api_key():
    """Read the API key fresh every time — survives awserv restarts."""
    try:
        with open(_API_KEY_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def _notify(endpoint, data):
    """POST JSON to awserv (fire-and-forget, non-blocking)."""
    def _do():
        try:
            body = json.dumps(data).encode()
            headers = {"Content-Type": "application/json"}
            api_key = _get_api_key()
            if api_key:
                headers["x-api-key"] = api_key
            req = urllib.request.Request(f"{AWSERV_URL}{endpoint}", data=body, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def _notify_session(name, port, status, extra=None):
    """Notify awserv of session state change."""
    data = {"name": name, "port": port, "status": status}
    if extra:
        data.update(extra)
    _notify("/api/debug/sessions", data)


def _notify_event(session_name, event_type, **kwargs):
    """Notify awserv of a debug event."""
    data = {"session": session_name, "event": event_type, **kwargs}
    _notify("/api/debug/events", data)


def _auto_notify_stopped(client):
    """Called automatically by the DAP reader thread when a stopped event arrives."""
    time.sleep(0.3)
    _notify_stopped(client.session_name, client)


def _notify_stopped(session_name, client, reason="breakpoint"):
    """Capture stack + locals + source context and push a stopped event to awserv."""
    try:
        tid = client._thread_id or 1
        st = client.get_stack_trace(tid)
        frames = st.get("body", {}).get("stackFrames", [])
        stack = []
        for f in frames[:15]:
            stack.append({
                "id": f["id"],
                "name": f["name"],
                "path": f.get("source", {}).get("path", ""),
                "line": f.get("line"),
            })

        local_vars = []
        source_context = None
        if frames:
            scopes = client.get_scopes(frames[0]["id"])
            for s in scopes.get("body", {}).get("scopes", []):
                if s.get("name") == "Locals":
                    vresp = client.get_variables(s["variablesReference"], count=30)
                    for v in vresp.get("body", {}).get("variables", []):
                        local_vars.append({
                            "name": v["name"],
                            "value": v.get("value", "")[:200],
                            "type": v.get("type", ""),
                        })
                    break

            top = frames[0]
            src_path = top.get("source", {}).get("path", "")
            src_line = top.get("line", 0)
            if src_path and src_line:
                source_context = _read_source_context(src_path, src_line, 1000)

        _notify_event(session_name, "stopped",
                      reason=reason, thread_id=tid,
                      stack=stack, locals=local_vars,
                      source=source_context)
    except Exception:
        _notify_event(session_name, "stopped", reason=reason)


def _read_source_context(path, line, context=5):
    """Read source lines around a line number."""
    try:
        with open(path) as f:
            lines = f.readlines()
        start = max(0, line - context - 1)
        end = min(len(lines), line + context)
        return {
            "path": path,
            "current_line": line,
            "start_line": start + 1,
            "lines": [{"num": start + 1 + i, "text": l.rstrip(), "current": (start + 1 + i) == line}
                       for i, l in enumerate(lines[start:end])],
        }
    except Exception:
        return None


_command_ws_running = False


def _start_command_listener():
    """Start a background thread that listens for commands via WebSocket."""
    global _command_ws_running
    if _command_ws_running:
        return
    _command_ws_running = True
    threading.Thread(target=_command_ws_loop, daemon=True).start()


def _command_ws_loop():
    """Connect to /ws/debug via WebSocket and listen for commands from the presentation UI."""
    global _command_ws_running
    import websocket

    while _command_ws_running and _sessions:
        try:
            api_key = _get_api_key()
            ws_url = AWSERV_URL.replace("http://", "ws://").replace("https://", "wss://")
            ws_url += f"/ws/debug?api-key={api_key}" if api_key else "/ws/debug"

            _command_ws_raw(ws_url)
        except Exception:
            pass
        if _command_ws_running and _sessions:
            time.sleep(2)

    _command_ws_running = False


def _command_ws_raw(ws_url):
    """Minimal WebSocket client using raw sockets — no external deps."""
    import hashlib, base64
    from urllib.parse import urlparse

    parsed = urlparse(ws_url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path + ("?" + parsed.query if parsed.query else "")

    sock = socket.create_connection((host, port), timeout=5)
    sock.settimeout(2.0)

    ws_key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(handshake.encode())

    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            return
        resp += chunk

    if b"101" not in resp.split(b"\r\n")[0]:
        sock.close()
        return

    while _command_ws_running and _sessions:
        try:
            frame_data = _ws_read_frame(sock)
            if frame_data is None:
                break
            if not frame_data:
                continue
            msg = json.loads(frame_data)
            if msg.get("type") == "command":
                _execute_presentation_command(msg)
        except socket.timeout:
            continue
        except Exception:
            break

    sock.close()


def _ws_read_frame(sock):
    """Read a single WebSocket frame. Returns decoded text or None on close."""
    try:
        b0 = sock.recv(1)
        if not b0:
            return None
        b1 = sock.recv(1)
        if not b1:
            return None

        opcode = b0[0] & 0x0F
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            return b""

        masked = (b1[0] & 0x80) != 0
        length = b1[0] & 0x7F

        if length == 126:
            raw = sock.recv(2)
            length = int.from_bytes(raw, "big")
        elif length == 127:
            raw = sock.recv(8)
            length = int.from_bytes(raw, "big")

        if masked:
            mask = sock.recv(4)

        data = b""
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                return None
            data += chunk

        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

        if opcode == 0x1:
            return data.decode("utf-8")
        return data
    except socket.timeout:
        raise
    except Exception:
        return None


def _execute_presentation_command(cmd):
    """Execute a debug command received from the presentation UI."""
    command = cmd.get("command", "")
    session_name = cmd.get("session", "")

    client = _sessions.get(session_name)
    if not client and len(_sessions) == 1:
        session_name = next(iter(_sessions))
        client = _sessions[session_name]
    if not client:
        return

    if client == "dap-server":
        try:
            action_map = {"continue": "continue", "next": "next",
                          "step_in": "step_in", "step_out": "step_out", "pause": "pause"}
            action = action_map.get(command)
            if action:
                _dap_request("POST", "/step", {"service": session_name, "action": action})
        except Exception:
            pass
        return

    if command == "continue":
        client.continue_execution()
        _notify_event(session_name, "continued")
    elif command == "next":
        client.next_step()
    elif command == "step_in":
        client.step_in()
    elif command == "step_out":
        client.step_out()
    elif command == "pause":
        client.pause()
        time.sleep(0.3)
        client.drain_events()
        _notify_stopped(session_name, client, reason="pause")


def _open_debug_presentation(session_name):
    """Signal awserv to open the debugger window.

    The debugger UI is now served directly by the DAP server at :15050/debugger
    and embedded as an iframe in the aw UI. The DAP server's WebSocket events
    automatically trigger the Debugger button to pulse and auto-open the window
    when a breakpoint is hit. This function is kept for backward compatibility
    but is now a no-op since the UI reacts to WS events directly.
    """
    pass


def _build_debug_presentation_html(session_name, api_key_param):
    """Fetch debugger.html from awserv and return it as presentation HTML.

    The UI source lives at src/app/public/debugger.html. We fetch it at
    connect time so changes to the file don't require MCP restart.
    The presentation renders it via doc.write() which shares the same origin,
    so WebSocket connections to /ws/debug work.
    """
    try:
        api_key = _get_api_key()
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        req = urllib.request.Request(f"{AWSERV_URL}/debugger.html", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        return (
            '<!DOCTYPE html><html><body style="background:#1e1e1e;color:#ccc;font-family:sans-serif;'
            'display:flex;align-items:center;justify-content:center;height:100vh">'
            '<p>Could not load debugger UI from awserv. Is it running?</p>'
            '</body></html>'
        )


def _get_debugpy_version(events):
    """Extract debugpy version from initialization events."""
    for e in events:
        if e.get("event") == "output":
            data = e.get("body", {}).get("data", {})
            ver = data.get("packageVersion")
            if ver:
                return ver
    return "unknown"


def _probe_port(host, port, timeout=0.5):
    """Check if a port is listening without consuming the connection.

    Uses a non-connecting socket check to avoid consuming debugpy's
    single-client connection slot.
    """
    import subprocess
    result = subprocess.run(
        f"lsof -i :{port} -sTCP:LISTEN 2>/dev/null",
        shell=True, capture_output=True, text=True,
    )
    return "LISTEN" in result.stdout


def _check_existing_client(port):
    """Check if another debug client is already connected to this port."""
    import subprocess
    result = subprocess.run(
        f"lsof -i :{port} 2>/dev/null", shell=True, capture_output=True, text=True,
    )
    established = [l for l in result.stdout.splitlines() if "ESTABLISHED" in l]
    return len(established) > 0



def _fmt_response(resp):
    """Format a DAP response body for display."""
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return f"Error: {resp.get('message', 'unknown error')}"
        return resp.get("body", resp)
    return resp


def tool_list_sessions(args):
    """List available debugpy sessions and their connection status."""
    results = []

    if _dap_server_available():
        try:
            svc_resp = _dap_request("GET", "/services")
            dap_services = svc_resp if isinstance(svc_resp, list) else svc_resp.get("services", [])
            for svc in dap_services:
                name = svc.get("name", "")
                if not name:
                    continue
                connected_via_mcp = name in _sessions
                entry = {
                    "name": name,
                    "port": svc.get("port", 0),
                    "listening": True,
                    "connected": svc.get("connected", False) or connected_via_mcp,
                }
                if connected_via_mcp:
                    entry["via"] = "dap-server"
                if svc.get("stopped"):
                    entry["stopped"] = True
                if not entry["connected"] and svc.get("port"):
                    has_client = _check_existing_client(svc["port"])
                    if has_client:
                        entry["busy"] = True
                results.append(entry)
        except Exception:
            pass

    if not results:
        return json.dumps([{"error": "DAP server not available. Start with: aw start dap-server"}])

    return json.dumps(results, indent=2)


def _ensure_session(name: str):
    """Ensure a session is tracked. Returns error string or None on success.

    With self-registration, services auto-connect to the DAP server.
    This lazily registers them in _sessions on first use.
    """
    if name in _sessions:
        return None
    if not _dap_server_available():
        return "DAP server is not available. Start it with: aw start dap-server"
    try:
        services = _dap_request("GET", "/services")
        svc_list = services if isinstance(services, list) else []
        found = any(s.get("name") == name and s.get("connected") for s in svc_list)
        if not found:
            return f"Service '{name}' is not connected. Is it running?"
        _sessions[name] = "dap-server"
        return None
    except Exception as e:
        return f"DAP server error: {e}"


def tool_disconnect(args):
    """Disconnect from a debugpy session."""
    name = args.get("session", "")
    if name not in _sessions:
        return f"Not connected to {name}. Connected: {list(_sessions.keys())}"
    if _is_routed(name):
        try:
            resp = _dap_request("POST", "/disconnect", {"service": name})
            _sessions.pop(name, None)
            _notify_event(name, "disconnected")
            return f"Disconnected from {name} (via DAP server)"
        except Exception as e:
            _sessions.pop(name, None)
            return f"Disconnected from {name} (DAP server error: {e})"
    _sessions[name].disconnect()
    del _sessions[name]
    _notify_event(name, "disconnected")
    return f"Disconnected from {name}"


def _get_client(args):
    name = args.get("session", "")
    if name in _sessions:
        val = _sessions[name]
        if val == "dap-server":
            return None, f"Session {name} is routed via DAP server (not a direct client)."
        return val, None
    direct = {k: v for k, v in _sessions.items() if v != "dap-server"}
    if len(direct) == 1:
        return next(iter(direct.values())), None
    if not direct:
        if _sessions:
            return None, "All sessions are routed via DAP server. Specify session name."
        return None, "No active debug sessions. Use connect first."
    return None, f"Multiple sessions active. Specify 'session': {list(_sessions.keys())}"


def tool_threads(args):
    """List all threads in the debugged process."""
    name = _resolve_session(args)
    if name == "all":
        try:
            result = _dap_request("GET", "/threads/all")
            lines = []
            for svc, threads in result.items():
                lines.append(f"\n{svc} ({len(threads)} threads):")
                for t in threads:
                    state = "STOPPED" if t.get("stopped") else "running"
                    lines.append(f"  [{t['id']}] {t['name']} — {state}")
            return "\n".join(lines) if lines else "No connected services."
        except Exception as e:
            return f"DAP server error: {e}"
    if _is_routed(name):
        try:
            resp = _dap_request("GET", f"/threads/{name}")
            return json.dumps(resp, indent=2)
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    resp = client.get_threads()
    body = _fmt_response(resp)
    if isinstance(body, dict):
        threads = body.get("threads", [])
        lines = [f"Threads ({len(threads)}):"]
        for t in threads:
            lines.append(f"  [{t['id']}] {t['name']}")
        return "\n".join(lines)
    return str(body)


def tool_pause(args):
    """Pause execution of the debugged process."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            data = {"service": name, "action": "pause"}
            thread_id = args.get("thread_id")
            if thread_id:
                data["thread_id"] = thread_id
            resp = _dap_request("POST", "/step", data)
            return json.dumps(resp, indent=2)
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    thread_id = args.get("thread_id")
    resp = client.pause(thread_id)
    time.sleep(0.5)
    client.drain_events()
    name = args.get("session", next(iter(_sessions), ""))
    _notify_stopped(name, client, reason="pause")
    return f"Paused. {_fmt_response(resp)}"


def tool_stack_trace(args):
    """Get the current stack trace."""
    name = _resolve_session(args)
    if name == "all":
        try:
            result = _dap_request("GET", "/stack/all")
            lines = []
            for svc, threads in result.items():
                stopped = [t for t in threads if t.get("stopped")]
                running = [t for t in threads if not t.get("stopped")]
                lines.append(f"\n{'='*60}")
                lines.append(f"{svc} ({len(threads)} threads, {len(stopped)} stopped)")
                lines.append(f"{'='*60}")
                for t in stopped:
                    lines.append(f"\n  [{t['id']}] {t['name']} — STOPPED")
                    frames = t.get("stackTrace", [])
                    for fr in frames[:10]:
                        src = fr.get("source", {}).get("path", "?")
                        hint = fr.get("presentationHint", "")
                        if hint == "subtle":
                            continue
                        lines.append(f"    {fr['name']}() at {src}:{fr.get('line', '?')}")
                if running:
                    names = ", ".join(f"[{t['id']}] {t['name']}" for t in running)
                    lines.append(f"\n  running: {names}")
            return "\n".join(lines) if lines else "No connected services."
        except Exception as e:
            return f"DAP server error: {e}"
    if _is_routed(name):
        try:
            path = f"/stack/{name}"
            thread_id = args.get("thread_id")
            if thread_id:
                path += f"?thread_id={thread_id}"
            resp = _dap_request("GET", path)
            return json.dumps(resp, indent=2)
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    thread_id = args.get("thread_id") or client._thread_id or 1
    resp = client.get_stack_trace(thread_id)
    body = _fmt_response(resp)
    if isinstance(body, dict):
        frames = body.get("stackFrames", [])
        lines = [f"Stack trace (thread {thread_id}, {len(frames)} frames):"]
        for f in frames:
            src = f.get("source", {}).get("path", "?")
            lines.append(f"  #{f['id']} {f['name']} at {src}:{f.get('line', '?')}")
        return "\n".join(lines)
    return str(body)


def tool_scopes(args):
    """Get scopes (locals, globals, etc.) for a stack frame."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            frame_id = args.get("frame_id")
            if frame_id is None:
                return "Error: 'frame_id' required. Get it from stack_trace."
            resp = _dap_request("GET", f"/scopes/{name}/{frame_id}")
            return json.dumps(resp, indent=2)
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    frame_id = args.get("frame_id")
    if frame_id is None:
        return "Error: 'frame_id' required. Get it from stack_trace."
    resp = client.get_scopes(frame_id)
    body = _fmt_response(resp)
    if isinstance(body, dict):
        scopes = body.get("scopes", [])
        lines = [f"Scopes for frame {frame_id}:"]
        for s in scopes:
            lines.append(f"  [{s['variablesReference']}] {s['name']} ({s.get('presentationHint', '')})"
                         f" — {s.get('namedVariables', '?')} vars")
        return "\n".join(lines)
    return str(body)


def tool_variables(args):
    """Get variables from a scope or container."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            ref = args.get("variables_reference")
            if ref is None:
                return "Error: 'variables_reference' required. Get it from scopes."
            resp = _dap_request("GET", f"/variables/{name}/{ref}")
            return json.dumps(resp, indent=2)
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    ref = args.get("variables_reference")
    if ref is None:
        return "Error: 'variables_reference' required. Get it from scopes."
    resp = client.get_variables(int(ref), count=args.get("count", 100))
    body = _fmt_response(resp)
    if isinstance(body, dict):
        variables = body.get("variables", [])
        lines = [f"Variables ({len(variables)}):"]
        for v in variables:
            vref = f" [ref:{v['variablesReference']}]" if v.get("variablesReference", 0) > 0 else ""
            typ = f" ({v['type']})" if v.get("type") else ""
            val = v.get("value", "")
            if len(val) > 200:
                val = val[:200] + "..."
            lines.append(f"  {v['name']}{typ}: {val}{vref}")
        return "\n".join(lines)
    return str(body)


def tool_set_variable(args):
    """Set a variable's value."""
    client, err = _get_client(args)
    if err:
        return err
    ref = args.get("variables_reference")
    name = args.get("name")
    value = args.get("value")
    if not all([ref, name, value is not None]):
        return "Error: 'variables_reference', 'name', and 'value' required."
    resp = client.set_variable(int(ref), name, str(value))
    body = _fmt_response(resp)
    if isinstance(body, dict):
        return f"Set {name} = {body.get('value', value)}"
    return str(body)


def tool_evaluate(args):
    """Evaluate an expression in the current frame."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            expr = args.get("expression", "")
            if not expr:
                return "Error: 'expression' required."
            _event_listener.start()
            events_before = len(_event_listener.get_recent_events(name, 50))

            data = {"service": name, "expression": expr, "context": "repl"}
            frame_id = args.get("frame_id")
            if frame_id is not None:
                data["frame_id"] = frame_id
            resp = _dap_request("POST", "/evaluate", data)

            body = resp.get("body", {}) if isinstance(resp, dict) else {}
            if not body and isinstance(resp, dict):
                body = resp
            val = body.get("result", "")
            typ = body.get("type", "")
            vref = body.get("variablesReference", 0)
            success = resp.get("success", True) if isinstance(resp, dict) else True
            error_msg = resp.get("message", "") if isinstance(resp, dict) else ""

            if not success:
                return f"Error: {error_msg or val or 'evaluation failed'}"

            parts = []
            if val:
                parts.append(val)
            if typ and val != "None":
                parts.append(f"(type: {typ})")
            if vref and vref > 0:
                parts.append(f"[ref:{vref} — use debug_variables to expand]")

            time.sleep(0.3)
            events_after = _event_listener.get_recent_events(name, 50)
            new_events = events_after[events_before:]
            output_lines = []
            for e in new_events:
                if e.get("type") == "output":
                    out = e.get("body", {}).get("output", "").rstrip("\n")
                    if out:
                        output_lines.append(out)

            result = " ".join(parts) if parts else "None"
            if output_lines:
                result += "\n\n[stdout]\n" + "\n".join(output_lines)
            return result
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    expr = args.get("expression", "")
    frame_id = args.get("frame_id")
    if not expr:
        return "Error: 'expression' required."
    resp = client.evaluate(expr, frame_id=frame_id)
    body = _fmt_response(resp)
    if isinstance(body, dict):
        val = body.get("result", "")
        typ = body.get("type", "")
        vref = body.get("variablesReference", 0)
        name = _resolve_session(args)
        _notify_event(name, "evaluate", expression=expr, result=val[:300])
        result = f"{val}"
        if typ:
            result += f"  (type: {typ})"
        if vref > 0:
            result += f"  [ref:{vref} — use variables to expand]"
        return result
    return str(body)


def _session_name_from_args(args):
    name = args.get("session", "")
    if not name and len(_sessions) == 1:
        name = next(iter(_sessions))
    if name:
        _ensure_session(name)
    return name


def _resolve_session(args):
    """Resolve session name from args and auto-connect. Use this in all tool functions."""
    name = args.get("session", "")
    if not name and len(_sessions) == 1:
        name = next(iter(_sessions))
    if name:
        _ensure_session(name)
    return name


def tool_set_breakpoints(args):
    """Set breakpoints in a source file, optionally with conditions."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            path = args.get("path", "")
            lines = args.get("lines", [])
            conditions = args.get("conditions")
            if not path or not lines:
                return "Error: 'path' (file path) and 'lines' (list of line numbers) required."
            if not os.path.isfile(path):
                return f"Error: file does not exist: {path}"
            data = {
                "file": path,
                "lines": lines,
                "agent_id": f"mcp-{os.getpid()}",
            }
            if name:
                data["service"] = name
            if conditions:
                data["conditions"] = {str(k): v for k, v in conditions.items()}
            resp = _dap_request("POST", "/breakpoints", data)
            return json.dumps(resp, indent=2)
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    path = args.get("path", "")
    lines = args.get("lines", [])
    if not path or not lines:
        return "Error: 'path' (file path) and 'lines' (list of line numbers) required."
    if not os.path.isfile(path):
        return f"Error: file does not exist: {path}"
    resp = client.set_breakpoints(path, lines)
    body = _fmt_response(resp)
    name = _resolve_session(args)
    _notify_event(name, "breakpoint_set", path=path, lines=lines)
    if isinstance(body, dict):
        bps = body.get("breakpoints", [])
        results = []
        for bp in bps:
            status = "verified" if bp.get("verified") else "pending"
            results.append(f"  Line {bp.get('line', '?')}: {status}")
        return f"Breakpoints in {path}:\n" + "\n".join(results)
    return str(body)


def tool_remove_breakpoints(args):
    """Remove breakpoints. If path given, clear that file. If no path, clear ALL from all agents."""
    name = _resolve_session(args)
    path = args.get("path", "")

    if _is_routed(name):
        try:
            if path:
                agent_id = f"mcp-{os.getpid()}"
                data = {"file": path, "agent_id": agent_id}
                if name:
                    data["service"] = name
                resp = _dap_request("DELETE", "/breakpoints", data)
                return json.dumps(resp, indent=2)
            else:
                resp = _dap_request("DELETE", f"/breakpoints/all?service={name}")
                files = resp.get("files_cleared", 0)
                return f"Cleared all breakpoints on {name} ({files} files cleared from debugpy)"
        except Exception as e:
            return f"DAP server error: {e}"

    client, err = _get_client(args)
    if err:
        return err
    if not path:
        return "Error: 'path' required for direct connections."
    resp = client.set_breakpoints(path, [])
    _notify_event(name, "breakpoint_removed", path=path)
    return f"All breakpoints removed from {path}"


def _wait_for_stop(name, timeout=3.0):
    """Wait for a stopped event on the WS listener. Returns description or None."""
    _event_listener.start()
    deadline = time.time() + timeout
    before = set()
    info = _event_listener.get_stopped_services().get(name)
    if info:
        before = set(info.get("thread_ids", []))
    while time.time() < deadline:
        time.sleep(0.15)
        current = _event_listener.get_stopped_services().get(name)
        if current:
            new_tids = set(current.get("thread_ids", [])) - before
            if new_tids:
                last = current.get("last_stop", {})
                fn = ""
                src = ""
                line_no = ""
                tid = sorted(new_tids)[0]
                try:
                    stack = _dap_request("GET", f"/stack/{name}?thread_id={tid}")
                    frames = stack.get("body", {}).get("stackFrames", [])
                    if frames:
                        top = frames[0]
                        fn = top.get("name", "")
                        src = (top.get("source", {}).get("path", "") or "").split("/")[-1]
                        line_no = top.get("line", "")
                except Exception:
                    fn = last.get("reason", "")
                reason = last.get("reason", "breakpoint")
                if fn:
                    return f"Stopped ({reason}) at {fn}() {src}:{line_no} (thread {tid})"
                return f"Stopped ({reason}) thread {tid}"
    return None


def tool_continue(args):
    """Continue execution."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            data = {"service": name, "action": "continue"}
            thread_id = args.get("thread_id")
            if thread_id:
                data["thread_id"] = thread_id
            _dap_request("POST", "/step", data)
            stop = _wait_for_stop(name, timeout=2.0)
            if stop:
                return f"Continued → {stop}"
            return f"Continued. {name} is running."
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    thread_id = args.get("thread_id")
    resp = client.continue_execution(thread_id)
    name = _resolve_session(args)
    _notify_event(name, "continued")
    return f"Continuing execution."


def _handle_step(client, args, action_name):
    """Common step handler — step, wait for stopped, notify."""
    name = _resolve_session(args)
    time.sleep(0.3)
    events = client.drain_events()
    for e in events:
        if e.get("event") == "stopped":
            body = e.get("body", {})
            _notify_stopped(name, client, reason=body.get("reason", action_name))
            return f"Stopped: {body.get('reason', '?')} (thread {body.get('threadId', '?')})"
    return f"{action_name} executed."


def tool_next(args):
    """Step over to the next line."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            data = {"service": name, "action": "next"}
            thread_id = args.get("thread_id")
            if thread_id:
                data["thread_id"] = thread_id
            _dap_request("POST", "/step", data)
            stop = _wait_for_stop(name, timeout=3.0)
            if stop:
                return f"Step next → {stop}"
            return f"Step next executed. {name} may still be running."
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    client.next_step(args.get("thread_id"))
    return _handle_step(client, args, "Step next")


def tool_step_in(args):
    """Step into a function call."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            data = {"service": name, "action": "step_in"}
            thread_id = args.get("thread_id")
            if thread_id:
                data["thread_id"] = thread_id
            _dap_request("POST", "/step", data)
            stop = _wait_for_stop(name, timeout=3.0)
            if stop:
                return f"Step in → {stop}"
            return f"Step in executed. {name} may still be running."
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    client.step_in(args.get("thread_id"))
    return _handle_step(client, args, "Step in")


def tool_step_out(args):
    """Step out of the current function."""
    name = _resolve_session(args)
    if _is_routed(name):
        try:
            data = {"service": name, "action": "step_out"}
            thread_id = args.get("thread_id")
            if thread_id:
                data["thread_id"] = thread_id
            _dap_request("POST", "/step", data)
            stop = _wait_for_stop(name, timeout=3.0)
            if stop:
                return f"Step out → {stop}"
            return f"Step out executed. {name} may still be running."
        except Exception as e:
            return f"DAP server error: {e}"
    client, err = _get_client(args)
    if err:
        return err
    client.step_out(args.get("thread_id"))
    return _handle_step(client, args, "Step out")


def tool_exception_breakpoints(args):
    """Set exception breakpoints."""
    client, err = _get_client(args)
    if err:
        return err
    filters = args.get("filters", ["raised"])
    resp = client.set_exception_breakpoints(filters)
    return f"Exception breakpoints set: {filters}"


def _status_all_sessions():
    """Return an overview of ALL sessions from the DAP server, including offline components."""
    _event_listener.start()
    try:
        services = _dap_request("GET", "/services")
        svc_list = services if isinstance(services, list) else services.get("services", [])
    except Exception as e:
        return f"DAP server error: {e}"

    connected_lines = []
    offline_names = []

    for svc in svc_list:
        sname = svc.get("name", "?")
        connected = svc.get("connected", False)
        stopped = svc.get("stopped", False)
        tid = svc.get("thread_id")
        bps = svc.get("breakpoints", {})
        bp_count = sum(len(v) for v in bps.values())

        if not connected:
            offline_names.append(sname)
            continue

        stopped_threads = svc.get("stopped_threads", [])
        bp_details = svc.get("breakpoint_details", [])
        clients = svc.get("connected_clients", [])
        status = "STOPPED" if stopped else "RUNNING"
        parts = [f"  {sname}: {status}"]
        if clients:
            parts.append(f"clients: {', '.join(clients)}")
        if bp_details:
            bp_strs = []
            for bp in bp_details:
                fname = bp.get("file", "").split("/")[-1]
                line = bp.get("line", 0)
                agent = bp.get("agent_id", "?")
                bp_strs.append(f"{fname}:{line} ({agent})")
            parts.append(f"{len(bp_details)} bp [{', '.join(bp_strs)}]")
        elif bp_count:
            bp_files = ", ".join(f"{f.split('/')[-1]}:{lines}" for f, lines in bps.items())
            parts.append(f"{bp_count} bp [{bp_files}]")
        if stopped_threads:
            for stid in stopped_threads:
                try:
                    stack = _dap_request("GET", f"/stack/{sname}?thread_id={stid}")
                    frames = stack.get("body", {}).get("stackFrames", [])
                    if frames:
                        top = frames[0]
                        src = top.get("source", {}).get("path", "")
                        fn = src.split("/")[-1] if src else "?"
                        parts.append(f"thread {stid} at {top.get('name')}() {fn}:{top.get('line')}")
                    else:
                        parts.append(f"thread {stid}")
                except Exception:
                    parts.append(f"thread {stid}")
        elif stopped and tid:
            parts.append(f"thread={tid}")
        connected_lines.append(" | ".join(parts))

    lines = []
    if connected_lines:
        lines.extend(connected_lines)
    if offline_names:
        lines.append(f"\n  Offline: {', '.join(offline_names)}")
    if not connected_lines and not offline_names:
        lines.append("No services registered with DAP server.")

    ws_status = "connected" if _event_listener.is_connected else "disconnected"
    lines.append(f"\n  [agent: mcp-{os.getpid()}] [event-stream: {ws_status}]")

    return "\n".join(lines)


def _status_single_from_dap(name):
    """Return detailed status for a single session via the DAP server."""
    try:
        services = _dap_request("GET", "/services")
        svc_list = services if isinstance(services, list) else services.get("services", [])
        svc_info = None
        for svc in svc_list:
            if svc.get("name") == name:
                svc_info = svc
                break
        if not svc_info:
            return f"Service {name} not found on DAP server"

        if svc_info.get("stopped"):
            try:
                stack = _dap_request("GET", f"/stack/{name}")
                frames = stack.get("body", {}).get("stackFrames", [])
                lines = [f"Session: {name}", f"Status: STOPPED"]
                if frames:
                    top = frames[0]
                    src = top.get("source", {}).get("path", "")
                    lines.append(f"Location: {src}:{top.get('line')}")
                    lines.append(f"Function: {top.get('name')}")
                    src_line = top.get("line", 0)
                    if src:
                        ctx = _read_source_context(src, src_line, 1000)
                        if ctx:
                            lines.append("\nSource:")
                            for sl in ctx.get("lines", []):
                                marker = ">>>" if sl.get("current") else "   "
                                lines.append(f"  {marker} {sl['num']:4d} | {sl['text']}")
                    lines.append(f"\nStack ({len(frames)} frames):")
                    for f in frames[:10]:
                        fp = f.get("source", {}).get("path", "?").split("/")[-1]
                        lines.append(f"  {f.get('name')} @ {fp}:{f.get('line')}")
                lines.append(f"\nBreakpoints: {json.dumps(svc_info.get('breakpoints', {}))}")
                lines.append(f"Agents: {svc_info.get('agents', [])}")
                return "\n".join(lines)
            except Exception:
                pass

        status = "STOPPED" if svc_info.get("stopped") else "RUNNING"
        lines = [f"Session: {name}", f"Status: {status}"]
        lines.append(f"Breakpoints: {json.dumps(svc_info.get('breakpoints', {}))}")
        lines.append(f"Agents: {svc_info.get('agents', [])}")
        return "\n".join(lines)
    except Exception as e:
        return f"DAP server error: {e}"


def tool_status(args):
    """Get the full current state of debug sessions.

    If a session is specified, returns detailed info for that session.
    If no session is specified, returns an overview of ALL connected sessions.
    """
    name = args.get("session", "")
    if name:
        _ensure_session(name)

    if not name:
        if not _dap_server_available():
            if not _sessions:
                return "No active debug sessions."
            name = next(iter(_sessions))
        else:
            return _status_all_sessions()

    if not name:
        return "No active debug sessions."

    if name not in _sessions:
        if _dap_server_available():
            return _status_single_from_dap(name)
        return f"Session '{name}' not connected."

    if _is_routed(name):
        return _status_single_from_dap(name)

    client = _sessions[name]
    events = client.drain_events()

    stopped = False
    stop_reason = ""
    for e in events:
        if e.get("event") == "stopped":
            stopped = True
            stop_reason = e.get("body", {}).get("reason", "breakpoint")
        elif e.get("event") == "continued":
            stopped = False

    if client._thread_id and not stopped:
        try:
            st = client.get_stack_trace(client._thread_id)
            if st.get("body", {}).get("stackFrames"):
                stopped = True
                stop_reason = "breakpoint"
        except Exception:
            pass

    lines = [f"Session: {name} (port {client.port})"]

    if not stopped:
        lines.append("Status: RUNNING")
        if events:
            lines.append(f"Recent events: {len(events)}")
        return "\n".join(lines)

    lines.append(f"Status: STOPPED ({stop_reason})")

    tid = client._thread_id or 1
    st = client.get_stack_trace(tid)
    frames = st.get("body", {}).get("stackFrames", [])
    if frames:
        top = frames[0]
        src_path = top.get("source", {}).get("path", "")
        src_file = src_path.split("/")[-1] if src_path else "?"
        lines.append(f"Stopped at: {top['name']} ({src_file}:{top.get('line')})")
        lines.append(f"Thread: {tid}")
        lines.append("")
        lines.append("Call Stack:")
        for i, f in enumerate(frames[:10]):
            p = f.get("source", {}).get("path", "")
            fn = p.split("/")[-1] if p else "?"
            marker = " >>>" if i == 0 else "    "
            lines.append(f"{marker} #{f['id']} {f['name']} at {fn}:{f.get('line')}")

        scopes = client.get_scopes(top["id"])
        for s in scopes.get("body", {}).get("scopes", []):
            if s.get("name") == "Locals":
                vresp = client.get_variables(s["variablesReference"], count=20)
                local_vars = vresp.get("body", {}).get("variables", [])
                if local_vars:
                    lines.append("")
                    lines.append(f"Locals ({len(local_vars)}):")
                    for v in local_vars:
                        val = v.get("value", "")[:120]
                        lines.append(f"  {v['name']}: {val}")
                break

        if src_path and top.get("line"):
            ctx = _read_source_context(src_path, top["line"], 1000)
            if ctx:
                lines.append("")
                lines.append(f"Source ({src_file}):")
                for sl in ctx["lines"]:
                    marker = ">>>" if sl["current"] else "   "
                    lines.append(f"  {marker} {sl['num']:4d} | {sl['text']}")

    _notify_stopped(name, client, reason=stop_reason)

    return "\n".join(lines)


def tool_events(args):
    """Drain and show pending debug events (stopped, output, etc.)."""
    name = args.get("session", "")
    count = args.get("count", 10)
    if name == "all" or not name or _dap_server_available():
        _event_listener.start()
        if name == "all" or not name:
            all_events = {}
            stopped = _event_listener.get_stopped_services()
            try:
                services = _dap_request("GET", "/services")
                svc_list = services if isinstance(services, list) else []
            except Exception:
                svc_list = []
            for svc in svc_list:
                sname = svc.get("name", "")
                if sname:
                    evts = _event_listener.get_recent_events(sname, count)
                    if evts:
                        all_events[sname] = evts
            if not all_events:
                return "No recent events." + (f" [event-stream: {'connected' if _event_listener.is_connected else 'disconnected'}]")
            lines = []
            for svc, evts in all_events.items():
                lines.append(f"\n{svc} ({len(evts)} events):")
                for e in evts:
                    etype = e.get("type", "?")
                    tid = e.get("thread_id", "")
                    reason = e.get("reason", "")
                    extra = ""
                    if etype == "stopped":
                        fn = e.get("function", "")
                        src = (e.get("file") or "").split("/")[-1]
                        line_no = e.get("line", "")
                        extra = f" — {fn}() {src}:{line_no}" if fn else ""
                        if reason:
                            extra = f" ({reason}){extra}"
                    elif reason:
                        extra = f" ({reason})"
                    tid_str = f" thread {tid}" if tid else ""
                    lines.append(f"  {etype}{tid_str}{extra}")
            return "\n".join(lines)
        else:
            events = _event_listener.get_recent_events(name, count)
            if not events:
                return f"No recent events for {name}."
            lines = [f"{name} — last {len(events)} events:"]
            for e in events:
                etype = e.get("type", "?")
                tid = e.get("thread_id", "")
                reason = e.get("reason", "")
                extra = ""
                if etype == "stopped":
                    fn = e.get("function", "")
                    src = (e.get("file") or "").split("/")[-1]
                    line_no = e.get("line", "")
                    extra = f" — {fn}() {src}:{line_no}" if fn else ""
                    if reason:
                        extra = f" ({reason}){extra}"
                elif reason:
                    extra = f" ({reason})"
                tid_str = f" thread {tid}" if tid else ""
                lines.append(f"  {etype}{tid_str}{extra}")
            return "\n".join(lines)
    client, err = _get_client(args)
    if err:
        return err
    events = client.drain_events()
    if not events:
        return "No pending events."
    name = _resolve_session(args)
    for e in events:
        if e.get("event") == "stopped":
            reason = e.get("body", {}).get("reason", "breakpoint")
            _notify_stopped(name, client, reason=reason)
    lines = [f"Events ({len(events)}):"]
    for e in events:
        body = e.get("body", {})
        lines.append(f"  {e.get('event', '?')}: {json.dumps(body)[:200]}")
    return "\n".join(lines)


def tool_open_file(args):
    """Open a file in VSCode, optionally at a specific line."""
    path = args.get("path", "")
    line = args.get("line")
    if not path:
        return "Error: 'path' required."
    if not os.path.isfile(path):
        return f"Error: file does not exist: {path}"
    try:
        data = {"path": path}
        if line:
            data["line"] = line
        resp = _dap_request("POST", "/open-in-editor", data)
        if isinstance(resp, dict) and resp.get("ok"):
            loc = f"{path}:{line}" if line else path
            return f"Opened {loc} in VSCode."
        return f"Failed to open: {resp}"
    except Exception as e:
        return f"Error: {e}"



TOOLS = [
    {
        "name": "debug_threads",
        "description": "List all threads in the debugged process. Use session='all' to list threads across ALL services at once.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name, or 'all' for every connected service"},
            },
        },
    },
    {
        "name": "debug_pause",
        "description": "Pause execution of the debugged process. The process must be running (not already stopped at a breakpoint).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "thread_id": {"type": "integer", "description": "Thread to pause (default: main thread)"},
            },
        },
    },
    {
        "name": "debug_stack_trace",
        "description": "Get the current call stack. Shows function names, file paths, and line numbers for each frame. Use session='all' to get stack traces for all stopped threads across ALL services (skips library frames). Use frame IDs from the result to inspect scopes and variables.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name, or 'all' for every connected service"},
                "thread_id": {"type": "integer"},
            },
        },
    },
    {
        "name": "debug_scopes",
        "description": "Get scopes (Locals, Globals, etc.) for a stack frame. Returns variablesReference IDs to use with debug_variables.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "frame_id": {"type": "integer", "description": "Frame ID from stack_trace"},
            },
            "required": ["frame_id"],
        },
    },
    {
        "name": "debug_variables",
        "description": "Get variables from a scope or expandable container. Use variablesReference from scopes or from a variable with children.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "variables_reference": {"type": "integer", "description": "variablesReference from scopes or a parent variable"},
                "count": {"type": "integer", "description": "Max variables to return (default 100)"},
            },
            "required": ["variables_reference"],
        },
    },
    {
        "name": "debug_set_variable",
        "description": "Set a variable's value in the current scope. The value is evaluated as a Python expression.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "variables_reference": {"type": "integer", "description": "Scope's variablesReference"},
                "name": {"type": "string", "description": "Variable name"},
                "value": {"type": "string", "description": "New value (Python expression)"},
            },
            "required": ["variables_reference", "name", "value"],
        },
    },
    {
        "name": "debug_evaluate",
        "description": "Evaluate a Python expression in the context of the current stack frame. Can read variables, call functions, or run arbitrary code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "expression": {"type": "string", "description": "Python expression to evaluate"},
                "frame_id": {"type": "integer", "description": "Stack frame context (optional, uses topmost if omitted)"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "debug_set_breakpoints",
        "description": "Set breakpoints in a source file. Replaces all breakpoints in that file with the given lines. Supports conditional breakpoints.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "path": {"type": "string", "description": "Absolute path to the source file"},
                "lines": {"type": "array", "items": {"type": "integer"}, "description": "Line numbers to set breakpoints on"},
                "conditions": {"type": "object", "description": "Optional conditions: {line_number: \"python_expression\"}. Breakpoint only fires when expression is truthy."},
            },
            "required": ["path", "lines"],
        },
    },
    {
        "name": "debug_remove_breakpoints",
        "description": "Remove breakpoints. If path is given, removes breakpoints from that file. If no path, removes ALL breakpoints from the session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "path": {"type": "string", "description": "Absolute path to the source file (omit to clear all breakpoints)"},
            },
        },
    },
    {
        "name": "debug_continue",
        "description": "Continue execution until the next breakpoint or program end.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "thread_id": {"type": "integer"},
            },
        },
    },
    {
        "name": "debug_next",
        "description": "Step over — execute the current line and stop at the next line in the same function.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "thread_id": {"type": "integer"},
            },
        },
    },
    {
        "name": "debug_step_in",
        "description": "Step into — if the current line has a function call, step into that function.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "thread_id": {"type": "integer"},
            },
        },
    },
    {
        "name": "debug_step_out",
        "description": "Step out — run until the current function returns, then stop.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "thread_id": {"type": "integer"},
            },
        },
    },
    {
        "name": "debug_exception_breakpoints",
        "description": "Configure exception breakpoints. Break when exceptions are raised or uncaught.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "filters": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["raised", "uncaught", "userUnhandled"]},
                    "description": "Exception filters: 'raised' (all), 'uncaught' (unhandled only), 'userUnhandled' (user code only)",
                },
            },
            "required": ["filters"],
        },
    },
    {
        "name": "debug_events",
        "description": "Show recent debug events (stopped, continued, output, etc.) from the real-time event stream. Use session='all' or omit for events across all services. Use session='<name>' for one service. Shows last N events (default 10).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name, 'all', or omit for all services"},
                "count": {"type": "integer", "description": "Number of recent events to show (default 10)"},
            },
        },
    },
    {
        "name": "debug_open_file",
        "description": "Open a source file in VSCode. Optionally jump to a specific line.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the source file"},
                "line": {"type": "integer", "description": "Line number to jump to (optional)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "debug_status",
        "description": "Get the full current state of all debug sessions in one call. When called without a session name, returns an overview of ALL connected services: running/stopped, active breakpoints (file and line numbers), and stop location. When called with a session name, returns detailed info: stack trace, local variables, and source context. Also use this to list all breakpoints across services.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name (optional if only one connected)"},
            },
        },
    },
]

TOOL_HANDLERS = {
    "debug_status": tool_status,
    "debug_threads": tool_threads,
    "debug_pause": tool_pause,
    "debug_stack_trace": tool_stack_trace,
    "debug_scopes": tool_scopes,
    "debug_variables": tool_variables,
    "debug_set_variable": tool_set_variable,
    "debug_evaluate": tool_evaluate,
    "debug_set_breakpoints": tool_set_breakpoints,
    "debug_remove_breakpoints": tool_remove_breakpoints,
    "debug_continue": tool_continue,
    "debug_next": tool_next,
    "debug_step_in": tool_step_in,
    "debug_step_out": tool_step_out,
    "debug_exception_breakpoints": tool_exception_breakpoints,
    "debug_events": tool_events,
    "debug_open_file": tool_open_file,
}


def _tool_result(req_id, text, is_error=False):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            **({"isError": True} if is_error else {}),
        },
    }


def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aw-debugger", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name", "")
        args = request.get("params", {}).get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return _tool_result(req_id, f"Unknown tool: {tool_name}", is_error=True)
        try:
            result = handler(args)
            return _tool_result(req_id, result)
        except Exception as e:
            return _tool_result(req_id, f"Error: {e}", is_error=True)

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    """Stdio MCP server loop."""
    if _dap_server_available():
        _event_listener.start()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request)
        if response is not None:
            out = json.dumps(response)
            sys.stdout.write(out + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
