#!/usr/bin/env bash
# Start script for ComicsBot on Linux/macOS.
#
#   ./start-bot.sh                   Set up the virtualenv (first run) and start the bot
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

# The bot reads comics.json and <guild>-Comics.json relative to the working
# directory in places, so always run it from its own folder.
cd "$BOT_DIR"

setup_venv() {
    if [ ! -x "$VENV_DIR/bin/python" ]; then
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

check_files() {
    local missing=0
    for f in .env comics.json; do
        if [ ! -f "$f" ]; then
            echo "Missing $BOT_DIR/$f" >&2
            missing=1
        fi
    done
    [ "$missing" -eq 0 ] || { echo "See .env.example for the required settings." >&2; exit 1; }
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
Restart=always
RestartSec=10

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
    run)
        check_files
        setup_venv
        exec "$VENV_DIR/bin/python" ComicsBot.py
        ;;
    install-service) install_service ;;
    remove-service)  remove_service ;;
    *)
        echo "Usage: $0 [run|install-service|remove-service]" >&2
        exit 2
        ;;
esac
