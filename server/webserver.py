"""
PitWall - HTTP + WebSocket server.

Stdlib only. No Flask, no websockets package, no pip install, nothing to
compile. That matters because the person running this on race night is a
broadcaster, not a developer.

Serves three things off one port:

  /broadcast/*   OBS browser sources
  /timing        the second-screen timing page (open it on a laptop, tablet
                 or phone anywhere on the LAN)
  /hud/*         the in-game HUD widgets, loaded by the Electron shell
  /register      the driver submission form
  /control       the operator control panel

The wire protocol is two channels so the payload stays small:

  meta    identity and slow-changing data (track, drivers, classes, roster).
          Sent on connect and whenever iRacing's session string changes.
  tick    the fast numbers. Sent at the client's requested rate.
  inputs  60 Hz pedal/steering samples, only to clients that ask.
  events  discrete things worth reacting to: pit stops, fastest laps,
          driver changes.

Clients subscribe on connect:  {"sub":["meta","tick"],"rate":10}
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import socket
import struct
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")


# ---------------------------------------------------------------------------
# WebSocket framing
# ---------------------------------------------------------------------------


class WSClient:
    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr
        self.alive = True
        self.lock = threading.Lock()
        self.subs: Set[str] = {"meta", "tick"}
        self.min_interval = 0.0
        self.last_sent = 0.0
        self.meta_serial = -1
        self.name = ""

    def send(self, payload: str) -> bool:
        if not self.alive:
            return False
        data = payload.encode("utf-8")
        frame = _encode(data, 0x1)
        try:
            with self.lock:
                self.sock.sendall(frame)
            return True
        except OSError:
            self.alive = False
            return False

    def ping(self) -> None:
        try:
            with self.lock:
                self.sock.sendall(_encode(b"", 0x9))
        except OSError:
            self.alive = False

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


def _encode(payload: bytes, opcode: int) -> bytes:
    head = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head += struct.pack(">H", n)
    else:
        head.append(127)
        head += struct.pack(">Q", n)
    return bytes(head) + payload


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_frame(sock: socket.socket):
    """Returns (opcode, payload) or None on close/error."""
    hdr = _recv_exact(sock, 2)
    if not hdr:
        return None
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exact(sock, 2)
        if not ext:
            return None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exact(sock, 8)
        if not ext:
            return None
        length = struct.unpack(">Q", ext)[0]
    if length > 4 * 1024 * 1024:
        return None
    mask = _recv_exact(sock, 4) if masked else b""
    if masked and mask is None:
        return None
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


class Hub:
    def __init__(self) -> None:
        self.clients: List[WSClient] = []
        self.lock = threading.Lock()
        self.meta_json: Optional[str] = None
        self.meta_serial = -1
        self.hello: Dict[str, Any] = {}

    def add(self, c: WSClient) -> None:
        with self.lock:
            self.clients.append(c)

    def remove(self, c: WSClient) -> None:
        with self.lock:
            if c in self.clients:
                self.clients.remove(c)
        c.close()

    def count(self) -> int:
        with self.lock:
            return len(self.clients)

    def set_meta(self, meta: Dict[str, Any]) -> None:
        self.meta_json = json.dumps(meta, separators=(",", ":"))
        self.meta_serial = meta.get("serial", 0)

    def broadcast(self, channel: str, payload: Dict[str, Any]) -> None:
        blob = json.dumps(payload, separators=(",", ":"))
        now = time.time()
        dead = []
        with self.lock:
            clients = list(self.clients)
        for c in clients:
            if not c.alive:
                dead.append(c)
                continue
            if channel not in c.subs:
                continue
            if channel == "tick" and c.min_interval > 0:
                if now - c.last_sent < c.min_interval:
                    continue
                c.last_sent = now
            # Any client behind on meta gets it before the next tick.
            if self.meta_json and c.meta_serial != self.meta_serial and "meta" in c.subs:
                if c.send(self.meta_json):
                    c.meta_serial = self.meta_serial
            if not c.send(blob):
                dead.append(c)
        for c in dead:
            self.remove(c)

    def shutdown(self) -> None:
        with self.lock:
            clients = list(self.clients)
            self.clients = []
        for c in clients:
            c.close()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Router:
    """Maps a path to a handler. Set by main.py."""

    def __init__(self) -> None:
        self.routes: Dict[str, Callable] = {}

    def add(self, path: str, fn: Callable) -> None:
        self.routes[path] = fn

    def get(self, path: str) -> Optional[Callable]:
        return self.routes.get(path)


class Handler(BaseHTTPRequestHandler):
    server_version = "PitWall"
    protocol_version = "HTTP/1.1"

    hub: Hub
    router: Router
    web_root: str
    data_dir: str
    quiet: bool = True
    # Set by main.py once the profile store exists. Left as None so the web
    # server stays usable on its own, which is how its tests run.
    profiles = None

    # -- plumbing --------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003
        if not self.quiet:
            super().log_message(fmt, *args)

    def _cors(self) -> None:
        # Overlays are frequently loaded from a file:// scene collection, a
        # GitHub Pages copy, or another device on the LAN. None of this is
        # sensitive and it all lives behind the user's own firewall.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _text(self, text: str, status: int = 200, ctype: str = "text/plain") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- routing ---------------------------------------------------------

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/ws":
            return self._websocket()
        fn = self.router.get(("GET", path))
        if fn:
            try:
                return fn(self, parse_qs(parsed.query))
            except Exception as exc:
                traceback.print_exc()
                return self._json({"error": str(exc)}, 500)
        return self._static(path)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        fn = self.router.get(("POST", parsed.path))
        if not fn:
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            return self._json({"error": f"bad request body: {exc}"}, 400)
        try:
            return fn(self, body)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)

    def do_DELETE(self):  # noqa: N802
        parsed = urlparse(self.path)
        fn = self.router.get(("DELETE", parsed.path))
        if not fn:
            return self._json({"error": "not found"}, 404)
        try:
            return fn(self, parse_qs(parsed.query))
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)

    # -- static ----------------------------------------------------------

    # Content types are stated here rather than asked of mimetypes.
    #
    # On Windows, Python's mimetypes module reads HKEY_CLASSES_ROOT, and plenty
    # of ordinary software rewrites the entries for .js and .css to text/plain.
    # A stylesheet served as text/plain is rejected outright by every browser in
    # standards mode, so the page still arrives, still has all its content, and
    # renders as an unstyled wall of text that looks exactly like something is
    # badly broken. The registry on the machine is not something this app can
    # fix, and it is not something a user should have to know about, so the
    # eight types PitWall actually serves are pinned here and the registry is
    # never consulted for them.
    TYPES = {
        ".html": "text/html; charset=utf-8",
        ".htm":  "text/html; charset=utf-8",
        ".css":  "text/css; charset=utf-8",
        ".js":   "text/javascript; charset=utf-8",
        ".mjs":  "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg":  "image/svg+xml",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif":  "image/gif",
        ".webp": "image/webp",
        ".ico":  "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf":  "font/ttf",
        ".txt":  "text/plain; charset=utf-8",
        ".md":   "text/plain; charset=utf-8",
        ".csv":  "text/csv; charset=utf-8",
    }

    ALIASES = {
        "/favicon.ico": "/favicon.svg",
        "/": "/control/index.html",
        "/control": "/control/index.html",
        "/timing": "/timing/index.html",
        "/register": "/register/index.html",
        "/hud": "/hud/index.html",
    }

    # The short names are directories. Serving the index straight from the bare
    # name leaves the browser with a base URL that has no trailing slash, so a
    # relative link inside the page resolves one level too high: /hud asking
    # for relative.html gets /relative.html, which is nothing. Redirecting to
    # the slash form costs one round trip on a local server and makes every
    # relative link in every page behave the way its author expected.
    DIR_ALIASES = ("/control", "/timing", "/register", "/hud")

    def _static(self, path: str) -> None:
        if path in self.DIR_ALIASES:
            target = path + "/"
            q = urlparse(self.path).query
            if q:
                target += "?" + q
            self.send_response(301)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
            return
        if path in self.ALIASES:
            path = self.ALIASES[path]
        if path.startswith("/uploads/"):
            root = self.data_dir
            rel = path[len("/uploads/"):]
            full = os.path.normpath(os.path.join(root, "uploads", rel))
            base = os.path.normpath(os.path.join(root, "uploads"))
        else:
            root = self.web_root
            rel = path.lstrip("/")
            full = os.path.normpath(os.path.join(root, rel))
            base = os.path.normpath(root)
        if not full.startswith(base):
            return self._text("forbidden", 403)
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            return self._text("Not found: " + path, 404)
        ext = os.path.splitext(full)[1].lower()
        ctype = self.TYPES.get(ext)
        if ctype is None:
            ctype, _ = mimetypes.guess_type(full)
        try:
            with open(full, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._text("Not found", 404)
        body = self._inject_profile(full, body)
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        # Overlays get reloaded constantly while you are building a scene.
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    # A widget asked for ?profile=name, so the saved options are inlined into
    # the page before any of its own script runs. Doing it here rather than in
    # the browser avoids the one alternative, a synchronous request at load
    # time, which blocks the page and is exactly the sort of thing that turns
    # into a black browser source ten seconds before a green flag.
    def _inject_profile(self, full: str, body: bytes) -> bytes:
        if self.profiles is None or not full.endswith(".html"):
            return body
        q = urlparse(self.path).query
        name = (parse_qs(q).get("profile") or [""])[0]
        if not name:
            return body
        try:
            prof = self.profiles.get(name)
        except Exception:
            return body
        if not prof or not prof.get("name"):
            return body
        try:
            blob = json.dumps(prof).replace("</", "<\\/")
            tag = ("<script>window.PW_PROFILE=" + blob + ";</script>").encode("utf-8")
            marker = b"</head>"
            i = body.find(marker)
            if i < 0:
                return body
            return body[:i] + tag + body[i:]
        except Exception:
            return body

    # -- websocket -------------------------------------------------------

    def _websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self._text("expected websocket upgrade", 400)
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        try:
            self.wfile.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode("ascii")
            )
            self.wfile.flush()
        except OSError:
            return

        self.close_connection = True
        sock = self.connection
        try:
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        client = WSClient(sock, self.client_address)
        self.hub.add(client)
        client.send(json.dumps({"type": "hello", **self.hub.hello}))
        if self.hub.meta_json:
            client.send(self.hub.meta_json)
            client.meta_serial = self.hub.meta_serial

        try:
            while client.alive:
                frame = _read_frame(sock)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    with client.lock:
                        try:
                            sock.sendall(_encode(payload, 0xA))
                        except OSError:
                            break
                    continue
                if opcode != 0x1:
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                self._ws_message(client, msg)
        finally:
            self.hub.remove(client)

    def _ws_message(self, client: WSClient, msg: Dict[str, Any]) -> None:
        if "sub" in msg and isinstance(msg["sub"], list):
            client.subs = {str(s) for s in msg["sub"]}
            client.meta_serial = -1  # force a meta resend
        if "rate" in msg:
            try:
                r = float(msg["rate"])
                client.min_interval = 1.0 / r if r > 0 else 0.0
            except (TypeError, ValueError):
                pass
        if "name" in msg:
            client.name = str(msg["name"])[:40]
        fn = self.router.get(("WS", msg.get("cmd", "")))
        if fn:
            try:
                result = fn(msg)
                if result is not None:
                    client.send(json.dumps({"type": "ack", "cmd": msg.get("cmd"), "result": result}))
            except Exception as exc:
                client.send(json.dumps({"type": "error", "error": str(exc)}))


# ---------------------------------------------------------------------------


class Server:
    def __init__(self, host: str, port: int, web_root: str, data_dir: str) -> None:
        self.hub = Hub()
        self.router = Router()
        handler = type(
            "BoundHandler",
            (Handler,),
            {
                "hub": self.hub,
                "router": self.router,
                "web_root": web_root,
                "data_dir": data_dir,
                "profiles": None,
            },
        )
        self.handler_class = handler
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.thread: Optional[threading.Thread] = None
        self.host, self.port = host, port

    def start(self) -> None:
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.hub.shutdown()
        self.httpd.shutdown()
        self.httpd.server_close()


def lan_addresses(port: int) -> List[str]:
    """Best-effort list of URLs another device on the network can open."""
    urls = [f"http://127.0.0.1:{port}"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            urls.append(f"http://{ip}:{port}")
    except OSError:
        pass
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            url = f"http://{ip}:{port}"
            if not ip.startswith("127.") and url not in urls:
                urls.append(url)
    except OSError:
        pass
    return urls
