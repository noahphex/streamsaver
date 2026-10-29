#!/usr/bin/env bash
set -euo pipefail

case "${1:-web}" in
    --help|-h)
        echo "Usage: ./run_streamsaver.sh [web|--scheduler|--help]"
        echo "web starts the local Flask app; --scheduler runs one scheduled job."
        exit 0
        ;;
    web|--scheduler) mode="${1:-web}" ;;
    *) echo "Unknown mode: $1 (use --help)" >&2; exit 1 ;;
esac
if [[ $# -gt 1 ]]; then
    echo "Expected one mode (use --help)." >&2
    exit 1
fi

cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet -r requirements.txt
if [[ -f .env ]]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi
if [[ "$mode" == --scheduler ]]; then
    echo "Running scheduled job once..."
    exec .venv/bin/python scheduler.py --run-once
fi
echo "Starting Streamsaver..."
exec .venv/bin/python main.py
