from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from typing import Any, Dict, Optional

from . import paths

_lock = threading.Lock()
_commands: Queue = Queue()
_snapshot: Optional[Dict[str, Any]] = None
_last_result: Optional[Dict[str, Any]] = None
_ea_seen = 0.0


def ea_connected() -> bool:
    return (time.time() - _ea_seen) < 2.0


class _HttpHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _token_ok(self) -> bool:
        sent = self.headers.get("X-Hook-Token", "")
        return sent == paths.token()

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._token_ok():
            self._send(403, {"ok": False, "message": "bad token"})
            return
        if self.path.split("?", 1)[0] in ("/snapshot", "/status", "/health"):
            with _lock:
                snap = _snapshot
                result = _last_result
                seen = _ea_seen
            self._send(
                200,
                {
                    "ok": True,
                    "ea_connected": ea_connected(),
                    "ea_seen_seconds_ago": None if seen == 0 else round(time.time() - seen, 2),
                    "snapshot": snap,
                    "last_result": result,
                },
            )
            return
        self._send(404, {"ok": False, "message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._token_ok():
            self._send(403, {"ok": False, "message": "bad token"})
            return
        if self.path.split("?", 1)[0] != "/cmd":
            self._send(404, {"ok": False, "message": "not found"})
            return
        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"ok": False, "message": "invalid json"})
            return
        payload.setdefault("token", paths.token())
        _commands.put(payload)
        deadline = time.time() + 8.0
        cmd_id = payload.get("id")
        while time.time() < deadline:
            with _lock:
                result = _last_result
            if result and result.get("id") == cmd_id:
                self._send(200, result)
                return
            time.sleep(0.05)
        self._send(504, {"ok": False, "message": "EA did not answer in time", "id": cmd_id})


def _tcp_loop(host: str, port: int, stop: threading.Event) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(1)
    sock.settimeout(0.5)
    print(f"EA TCP hook on {host}:{port}")
    while not stop.is_set():
        try:
            conn, addr = sock.accept()
        except socket.timeout:
            continue
        if addr[0] not in ("127.0.0.1", "::1"):
            conn.close()
            continue
        conn.settimeout(1.0)
        try:
            _handle_ea(conn)
        except OSError:
            pass
        finally:
            conn.close()
    sock.close()


def _recv_line(conn: socket.socket) -> str:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return ""
        buf += chunk
        if len(buf) > 1_000_000:
            return ""
    line, _rest = buf.split(b"\n", 1)
    return line.decode("utf-8", errors="replace").strip()


def _handle_ea(conn: socket.socket) -> None:
    global _snapshot, _last_result, _ea_seen
    while True:
        line = _recv_line(conn)
        if not line:
            return
        try:
            poll = json.loads(line)
        except json.JSONDecodeError:
            continue
        with _lock:
            _ea_seen = time.time()
            if isinstance(poll, dict) and isinstance(poll.get("snapshot"), dict):
                _snapshot = poll["snapshot"]
        try:
            cmd = _commands.get_nowait()
        except Empty:
            cmd = {"cmd": "idle"}
        conn.sendall((json.dumps(cmd, separators=(",", ":")) + "\n").encode("utf-8"))
        result_line = _recv_line(conn)
        if not result_line:
            return
        try:
            result = json.loads(result_line)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and result.get("cmd") != "idle":
            with _lock:
                _last_result = result
                if isinstance(result.get("snapshot"), dict):
                    _snapshot = result["snapshot"]


def serve(http_port: int = paths.HTTP_PORT, tcp_port: int = paths.TCP_PORT) -> None:
    stop = threading.Event()
    tcp_thread = threading.Thread(
        target=_tcp_loop, args=("127.0.0.1", tcp_port, stop), daemon=True
    )
    tcp_thread.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", http_port), _HttpHandler)
    print(f"Control HTTP on http://127.0.0.1:{http_port}")
    print("Waiting for PythonBridgeEA. Keep this process and MT5 running.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping hook server.")
    finally:
        stop.set()
        httpd.server_close()
