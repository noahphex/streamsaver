#!/usr/bin/env bash
# run_streamsaver.sh — Launch the Streamsaver GUI
#
# Usage:
#   ./run_streamsaver.sh              # launch GUI (prompts for passphrase)
#   ./run_streamsaver.sh --setup      # run credential setup wizard instead
#   ./run_streamsaver.sh --scheduler  # run scheduler (once, then exit)
#   ./run_streamsaver.sh --help       # show this help
#
# Set STREAMSAVER_PASSPHRASE in your environment to skip the passphrase prompt.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PYTHON="$VENV/bin/python"

# ── Helpers ───────────────────────────────────────────────────────────────────

die() { echo "❌  $*" >&2; exit 1; }

show_help() {
    grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
    exit 0
}

check_venv() {
    if [[ ! -x "$PYTHON" ]]; then
        echo "⚙️   Virtual environment not found. Creating it now..."
        python3 -m venv "$VENV" || die "Failed to create virtual environment. Is python3 installed?"
        echo "📦  Installing dependencies..."
        "$PYTHON" -m pip install --quiet --upgrade pip
        "$PYTHON" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt" \
            || die "pip install failed. Check requirements.txt."
        echo "✅  Dependencies installed."
    fi
}

check_tkinter() {
    if ! "$PYTHON" -c "import _tkinter" 2>/dev/null; then
        PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        echo "❌  Tkinter is not available in your Python $PY_VER installation." >&2
        echo "" >&2
        echo "    Fix it by running:" >&2
        echo "      brew install python-tk@${PY_VER}" >&2
        echo "" >&2
        echo "    Then delete the venv and re-run this script:" >&2
        echo "      rm -rf .venv && ./run_streamsaver.sh" >&2
        exit 1
    fi
}

# ── Argument parsing ──────────────────────────────────────────────────────────

MODE="gui"
for arg in "$@"; do
    case "$arg" in
        --setup)     MODE="setup" ;;
        --scheduler) MODE="scheduler" ;;
        --help|-h)   show_help ;;
        *) die "Unknown argument: $arg  (use --help for usage)" ;;
    esac
done

# ── Pre-flight ────────────────────────────────────────────────────────────────

cd "$SCRIPT_DIR"

# Load .env if present (sets STREAMSAVER_PASSPHRASE etc. without committing secrets)
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -o allexport
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env"
    set +o allexport
fi

check_venv
check_tkinter

# ── Launch ────────────────────────────────────────────────────────────────────

case "$MODE" in
    gui)
        echo "🎬  Starting Streamsaver..."
        exec "$PYTHON" main.py
        ;;
    setup)
        echo "🔑  Running credential setup wizard..."
        exec "$PYTHON" setup_credentials.py
        ;;
    scheduler)
        echo "🕐  Running scheduler (once)..."
        exec "$PYTHON" scheduler.py --run-once
        ;;
esac
