"""
Entry point: serves the Chesa Auto webapp and runs the Telegram bot.
Designed for Heroku: one web process, PORT env var, long-running bot.
"""

import os
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from .config import config
from .logger import get_logger
from .bot import run as run_bot
from . import database as db

log = get_logger("Main")

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEBAPP_DIR, **kwargs)

    def log_message(self, *a):
        pass


def _start_server(port: int) -> None:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("Webapp serving on :%d (dir=%s)", port, WEBAPP_DIR)
    httpd.serve_forever()


def main() -> None:
    port = config.WEBAPP_PORT
    server_thread = threading.Thread(target=_start_server, args=(port,), daemon=True)
    server_thread.start()

    import asyncio
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
