#!/usr/bin/env bash
# Start script for ComicsBot on Linux/macOS.
#
#   ./start-bot.sh                   Set up the virtualenv (first run) and start the bot.
#                                    The bot is restarted automatically if it crashes.
#   ./start-bot.sh install-service   Register a systemd service so the bot starts on boot
#   ./start-bot.sh remove-service    Remove that systemd service
#
# Alternative without systemd: add this line to `crontab -e`
#   @reboot /path/to/DiscordComicsBot/start-bot.sh >> /path/to/DiscordComicsBot/startup.log 2>&1
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
VENV_DIR="$BOT_DIR/.venv"
SERVICE_NAME="comicsbot"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
PYTHON="${PYTHON:-python3}"
# Exit codes used by ComicsBot.py
EXIT_RESTART=75
EXIT_CONFIG_ERROR=78

cd "$BOT_DIR"

python_ok() {
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null
}

setup_venv() {
    if [ -x "$VENV_DIR/bin/python" ] && ! python_ok "$VENV_DIR/bin/python"; then
        echo "Existing virtual environment uses Python < 3.10, recreating it ..."
        rm -rf "$VENV_DIR"
    fi
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        if ! python_ok "$PYTHON"; then
            echo "ComicsBot needs Python 3.10 or newer ('$PYTHON' is missing or too old)." >&2
            echo "Install a newer Python or point PYTHON=/path/to/python3.x at one." >&2
            exit 1
        fi
        echo "Creating virtual environment in $VENV_DIR ..."
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    # Reinstall dependencies only when requirements.txt has changed.
    if ! cmp -s requirements.txt "$VENV_DIR/requirements.installed"; then
        echo "Installing dependencies ..."
        "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
        "$VENV_DIR/bin/python" -m pip install --quiet -r requirements.txt
        cp requirements.txt "$VENV_DIR/requirements.installed"
    fi
}

run_bot() {
    if [ ! -f .env ]; then
        echo "Missing $BOT_DIR/.env - copy .env.example to .env and fill in DISCORD_TOKEN." >&2
        exit 1
    fi
    setup_venv
    while true; do
        set +e
        "$VENV_DIR/bin/python" ComicsBot.py
        code=$?
        set -e
        case "$code" in
            0) exit 0 ;;
            "$EXIT_CONFIG_ERROR")
                echo "Configuration error, not restarting. Fix .env and start again." >&2
                exit "$code" ;;
            "$EXIT_RESTART") echo "Restarting ComicsBot ..." ;;
            *)
                echo "ComicsBot exited with code $code, restarting in 10 seconds (Ctrl+C to stop) ..." >&2
                sleep 10 ;;
        esac
    done
}

install_service() {
    setup_venv
    local run_user="${SUDO_USER:-$(id -un)}"
    local sudo=""
    [ "$(id -u)" -eq 0 ] || sudo="sudo"
    echo "Writing $SERVICE_FILE (runs as user '$run_user') ..."
    $sudo tee "$SERVICE_FILE" > /dev/null <<UNIT
[Unit]
Description=Discord ComicsBot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$run_user
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/start-bot.sh
Restart=on-failure
RestartSec=10
RestartPreventExitStatus=$EXIT_CONFIG_ERROR

[Install]
WantedBy=multi-user.target
UNIT
    $sudo systemctl daemon-reload
    $sudo systemctl enable --now "$SERVICE_NAME"
    echo "Done. Check status with: systemctl status $SERVICE_NAME"
    echo "Follow logs with:        journalctl -u $SERVICE_NAME -f"
}

remove_service() {
    local sudo=""
    [ "$(id -u)" -eq 0 ] || sudo="sudo"
    $sudo systemctl disable --now "$SERVICE_NAME" || true
    $sudo rm -f "$SERVICE_FILE"
    $sudo systemctl daemon-reload
    echo "Service removed."
}

case "${1:-run}" in
    run) run_bot ;;
    install-service) install_service ;;
    remove-service) remove_service ;;
    *)
        echo "Usage: $0 [run|install-service|remove-service]" >&2
        exit 2
        ;;
esac
