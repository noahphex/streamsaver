#!/bin/bash

set -e

LABEL="com.streamsaver.scheduler"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
SCHEDULER_SCRIPT="$PROJECT_DIR/scheduler.py"

LOG_DIR="$PROJECT_DIR/logs"
STDOUT_LOG="$LOG_DIR/streamsaver.out.log"
STDERR_LOG="$LOG_DIR/streamsaver.err.log"

DEFAULT_HOUR=9
DEFAULT_MINUTE=0

mkdir -p "$LOG_DIR"

get_schedule() {
    if [ -f "$PLIST" ]; then
        HOUR=$(defaults read "${PLIST%.plist}" StartCalendarInterval Hour 2>/dev/null || echo "$DEFAULT_HOUR")
        MINUTE=$(defaults read "${PLIST%.plist}" StartCalendarInterval Minute 2>/dev/null || echo "$DEFAULT_MINUTE")
    else
        HOUR="$DEFAULT_HOUR"
        MINUTE="$DEFAULT_MINUTE"
    fi
}

create_plist() {
    local HOUR="$1"
    local MINUTE="$2"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>

    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${SCHEDULER_SCRIPT}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${HOUR}</integer>

        <key>Minute</key>
        <integer>${MINUTE}</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${STDOUT_LOG}</string>

    <key>StandardErrorPath</key>
    <string>${STDERR_LOG}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

</dict>
</plist>
PLIST
}

install_agent() {
    if [ ! -f "$PYTHON_BIN" ]; then
        echo "ERROR: Python virtualenv not found:"
        echo "$PYTHON_BIN"
        exit 1
    fi

    if [ ! -f "$SCHEDULER_SCRIPT" ]; then
        echo "ERROR: scheduler.py not found:"
        echo "$SCHEDULER_SCRIPT"
        exit 1
    fi

    create_plist "$DEFAULT_HOUR" "$DEFAULT_MINUTE"

    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    launchctl load "$PLIST"

    echo ""
    echo "Installed LaunchAgent:"
    echo "$LABEL"
    echo ""
    echo "Schedule:"
    printf "%02d:%02d daily\n" "$DEFAULT_HOUR" "$DEFAULT_MINUTE"
    echo ""
}

set_schedule() {
    local HOUR="$1"
    local MINUTE="$2"

    if [[ ! "$HOUR" =~ ^[0-9]+$ ]] || [[ ! "$MINUTE" =~ ^[0-9]+$ ]]; then
        echo "Usage:"
        echo "./streamsaver-launchagent.sh schedule <hour> <minute>"
        exit 1
    fi

    if [ "$HOUR" -lt 0 ] || [ "$HOUR" -gt 23 ]; then
        echo "Hour must be 0-23"
        exit 1
    fi

    if [ "$MINUTE" -lt 0 ] || [ "$MINUTE" -gt 59 ]; then
        echo "Minute must be 0-59"
        exit 1
    fi

    create_plist "$HOUR" "$MINUTE"

    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    launchctl load "$PLIST"

    echo ""
    echo "Updated schedule:"
    printf "%02d:%02d daily\n" "$HOUR" "$MINUTE"
    echo ""
}

run_now() {
    "$PYTHON_BIN" "$SCHEDULER_SCRIPT"
}

start_agent() {
    launchctl start "$LABEL"
    echo "Started:"
    echo "$LABEL"
}

stop_agent() {
    launchctl stop "$LABEL"
    echo "Stopped:"
    echo "$LABEL"
}

restart_agent() {
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    launchctl load "$PLIST"

    echo "Restarted:"
    echo "$LABEL"
}

remove_agent() {
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    rm -f "$PLIST"

    echo "Removed LaunchAgent:"
    echo "$LABEL"
}

status_agent() {
    get_schedule

    echo ""
    echo "Launchctl status:"
    echo ""

    launchctl list | grep "$LABEL" || echo "Not loaded"

    echo ""
    echo "Current schedule:"
    printf "%02d:%02d daily\n" "$HOUR" "$MINUTE"

    echo ""
    echo "Plist:"
    echo "$PLIST"

    echo ""
    echo "Logs:"
    echo "$STDOUT_LOG"
    echo "$STDERR_LOG"
    echo ""
}

tail_logs() {
    touch "$STDOUT_LOG" "$STDERR_LOG"
    tail -f "$STDOUT_LOG" "$STDERR_LOG"
}

validate_agent() {
    plutil -lint "$PLIST"
}

case "$1" in
    install)
        install_agent
        ;;
    schedule)
        set_schedule "$2" "$3"
        ;;
    run)
        run_now
        ;;
    start)
        start_agent
        ;;
    stop)
        stop_agent
        ;;
    restart)
        restart_agent
        ;;
    remove|uninstall)
        remove_agent
        ;;
    status)
        status_agent
        ;;
    logs)
        tail_logs
        ;;
    validate)
        validate_agent
        ;;
    *)
        echo "Usage:"
        echo ""
        echo "./streamsaver-launchagent.sh install"
        echo "./streamsaver-launchagent.sh schedule <hour> <minute>"
        echo "./streamsaver-launchagent.sh run"
        echo "./streamsaver-launchagent.sh start"
        echo "./streamsaver-launchagent.sh stop"
        echo "./streamsaver-launchagent.sh restart"
        echo "./streamsaver-launchagent.sh remove"
        echo "./streamsaver-launchagent.sh status"
        echo "./streamsaver-launchagent.sh logs"
        echo "./streamsaver-launchagent.sh validate"
        echo ""
        echo "Examples:"
        echo "./streamsaver-launchagent.sh schedule 9 30"
        echo "./streamsaver-launchagent.sh schedule 22 15"
        echo ""
        exit 1
        ;;
esac
