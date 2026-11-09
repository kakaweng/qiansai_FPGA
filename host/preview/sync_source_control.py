"""Authenticated source RPC. Tk methods always execute on the Tk thread."""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import queue
import threading


class SourceControl:
    def __init__(self, host, port, token):
        if len(token) < 16:
            raise ValueError("Control token must contain at least 16 characters")
        self.token, self.commands = token, queue.Queue(maxsize=16)
        self.lock, self.saved = threading.Lock(), OrderedDict()
        self.snapshot = {"state": "WAITING", "session": 0, "physical_sync": "unverified"}
        control = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, value):
                data = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def authorized(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + control.token):
                    self.respond(403, {"error": "invalid token"})
                    return False
                return True

            def do_GET(self):
                if self.authorized():
                    self.respond(200 if self.path == "/status" else 404,
                                 dict(control.snapshot) if self.path == "/status" else {"error": "unknown route"})

            def do_POST(self):
                if not self.authorized():
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.path != "/command" or not 0 < length <= 1024:
                        raise ValueError("Invalid command request")
                    command = json.loads(self.rfile.read(length))
                    ident, session, action = command["id"], command["session"], command["action"]
                    if (type(ident) is not int or not 0 < ident <= 0xffffffff or
                            type(session) is not int or not 0 < session <= 0xffffffff or
                            action not in ("prepare", "play", "stop")):
                        raise ValueError("Invalid command fields")
                    key = session, ident
                    with control.lock:
                        existing = control.saved.get(key)
                        if existing is not None:
                            previous, future = existing
                            if previous != action:
                                raise ValueError("Command id reused with different action")
                        else:
                            future = Future()
                            control.commands.put_nowait((command, future))
                            control.saved[key] = action, future
                            # Keep retries idempotent; unfinished commands are never evicted.
                            while len(control.saved) > 64:
                                first = next(iter(control.saved))
                                if not control.saved[first][1].done():
                                    break
                                control.saved.pop(first)
                    self.respond(200, future.result(timeout=8))
                except TimeoutError:
                    self.respond(202, {"pending": True})
                except (ValueError, KeyError, TypeError, queue.Full) as error:
                    self.respond(400, {"error": str(error)})

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            def get_request(self):
                sock, addr = super().get_request()
                sock.settimeout(10)
                return sock, addr

        self.server = Server((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
