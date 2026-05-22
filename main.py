"""
main.py
Streamsaver — Application entry point
Author: Noah Lee

Initialises the database and launches the Tkinter GUI.
Run with the venv Python:
    .venv/bin/python main.py
"""

import sys
import os
import logging
from logging.handlers import RotatingFileHandler

# Ensure the project root is on sys.path when launched from another directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_LOG_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_LOG_FILE = os.path.join(_LOG_DIR, "streamsaver.log")

def _setup_logging() -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)

    fmt     = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root    = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console: INFO and above (same as before)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    # Rotating file: DEBUG and above — 5 MB × 3 backups
    file_h  = RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(fmt)

    root.addHandler(console)
    root.addHandler(file_h)

    # Quiet noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)


from db import init_db


def main() -> None:
    logger.info("Streamsaver starting — log file: %s", _LOG_FILE)
    init_db()
    from gui.app import run
    run()


if __name__ == "__main__":
    main()
