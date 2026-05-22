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

# Ensure the project root is on sys.path when launched from another directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def main() -> None:
    init_db()
    from gui.app import run
    run()


if __name__ == "__main__":
    main()
