import logging
import sys
import os

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

_fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_datefmt = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=LOG_LEVEL,
    format=_fmt,
    datefmt=_datefmt,
    handlers=[logging.StreamHandler(sys.stdout)],
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


logger = get_logger("ChesaAuto")
