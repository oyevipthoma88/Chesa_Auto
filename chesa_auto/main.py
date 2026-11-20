"""
Entry point: serves the Chesa Auto webapp and runs the Telegram bot.
Designed for Heroku: one web process, binds $PORT, long-running bot.

Routes:
  /           → "OK" health check (Heroku router / uptime pingers)
  /webapp/... → static files from chesa_auto/webapp/
"""

import asyncio
import os
import posixpath
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .config import config
from .logger import get_logger

log = get_logger("Main")

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
PREFIX = "/webapp"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEBAPP_DIR, **kwargs)

    def _route(self) -> bool:
        path = urlsplit(self.path).path
        if path in ("/", "/health", "/healthz"):
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return True
        if path == PREFIX:
            self.send_response(301)
            self.send_header("Location", PREFIX + "/")
            self.end_headers()
            return True
        if not path.startswith(PREFIX + "/"):
            self.send_error(404)
            return True
        return False

    def translate_path(self, path: str) -> str:
        # Map /webapp/<file> → WEBAPP_DIR/<file> (the original server looked for
        # WEBAPP_DIR/webapp/<file>, so every /webapp/ URL returned 404).
        path = unquote(urlsplit(path).path)[len(PREFIX):]
        path = posixpath.normpath(path)
        parts = [p for p in path.split("/") if p and p not in (".", "..")]
        return os.path.join(WEBAPP_DIR, *parts)

    def do_GET(self):
        if not self._route():
            super().do_GET()

    def do_HEAD(self):
        if not self._route():
            super().do_HEAD()

    def log_message(self, *a):
        pass


def _start_server(port: int) -> None:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("HTTP server on :%d (webapp at %s/)", port, PREFIX)
    httpd.serve_forever()


def main() -> None:
    threading.Thread(target=_start_server, args=(config.WEBAPP_PORT,), daemon=True).start()
    # Import after the HTTP server is up so Heroku sees the port bound quickly.
    from .bot import run as run_bot

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
