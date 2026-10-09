#!/usr/bin/env bash
# ComicsBot installer for Linux (systemd). Run it from an extracted release archive:
#
#   sudo ./install.sh                        Install or upgrade, then start the service
#   sudo ./install.sh --migrate-from DIR     Also copy .env and bot data from an older install
#                                            (for example the git checkout you ran it from before)
#   sudo ./install.sh --uninstall            Remove the service and program files, keep .env and data
#   sudo ./install.sh --uninstall --purge    Remove everything, including data and the service user
#
# Automatic updates (off unless enabled; see "Automatic updates" in README.md):
#   sudo ./install.sh --auto-update          Install or upgrade and turn on daily automatic updates
#   sudo ./install.sh --enable-auto-update   Turn them on for the existing install
#   sudo ./install.sh --disable-auto-update  Turn them off and delete the GitHub token
#   sudo ./install.sh --check-update         Only report whether a newer release exists
#   sudo ./install.sh --update-now           Update to the latest release now
#   The GitHub token is asked for, or read from COMICSBOT_GITHUB_TOKEN (never pass it as an option).
#
# Options:
#   --dir DIR       Install location (default /opt/comicsbot)
#   --token TOKEN   Discord bot token (or set DISCORD_TOKEN); asked for when missing
#   --yes           Do not ask before installing system packages
#   PYTHON=/path    Use this Python 3.10+ instead of searching PATH (environment variable)
set -euo pipefail

SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
SRC_DIR="$(dirname "$SCRIPT")"
INSTALL_DIR="/opt/comicsbot"
SERVICE_USER="comicsbot"
SERVICE_NAME="comicsbot"
TOKEN="${DISCORD_TOKEN:-}"
MIGRATE_FROM=""
ASSUME_YES=0
ACTION="install"
PURGE=0
AUTO_UPDATE=0
UPDATE_TOKEN="${COMICSBOT_GITHUB_TOKEN:-}"
GITHUB_REPO="dxcufgb/DiscordComicsBot"
UPDATE_UNIT="comicsbot-update"
UPDATER_DIR="/usr/local/lib/comicsbot-updater"
UPDATER_CONF_DIR="/etc/comicsbot"
UPDATE_TOKEN_FILE="$UPDATER_CONF_DIR/github-token"
UPDATE_LOCK="/run/comicsbot-update.lock"

APP_FILES=(ComicsBot.py comicsbot requirements.txt start-bot.sh install.sh .env.example README.md VERSION RELEASE_NOTES.md)
DATA_FILES=(.env state.json comics.json)

die() { echo "Error: $*" >&2; exit 1; }
info() { echo "==> $*"; }
confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    [ -t 0 ] || return 1
    local answer
    read -r -p "$1 [Y/n] " answer
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}
# Like confirm, but the answer defaults to no and --yes does not mean yes.
ask_no() {
    [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ] || return 1
    local answer
    read -r -p "$1 [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]]
}

if [ "$(id -u)" -ne 0 ]; then
    command -v sudo > /dev/null || die "run this script as root"
    exec sudo --preserve-env=DISCORD_TOKEN,COMICSBOT_GITHUB_TOKEN,PYTHON bash "$SCRIPT" "$@"
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) INSTALL_DIR="${2:?--dir needs a path}"; shift 2 ;;
        --token) TOKEN="${2:?--token needs a value}"; shift 2 ;;
        --migrate-from) MIGRATE_FROM="${2:?--migrate-from needs a path}"; shift 2 ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --uninstall) ACTION="uninstall"; shift ;;
        --purge) PURGE=1; shift ;;
        --auto-update) AUTO_UPDATE=1; shift ;;
        --enable-auto-update) ACTION="enable-update"; shift ;;
        --disable-auto-update) ACTION="disable-update"; shift ;;
        --check-update) ACTION="check-update"; shift ;;
        --update-now) ACTION="update-now"; shift ;;
        -h|--help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$SCRIPT"; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done
INSTALL_DIR="$(readlink -m "$INSTALL_DIR")"

if ! command -v systemctl > /dev/null || [ ! -d /run/systemd/system ]; then
    die "systemd is not running here. Use ./start-bot.sh with a crontab '@reboot' line instead."
fi

# -- helpers ---------------------------------------------------------------

python_ok() {
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2> /dev/null
}

find_python() {
    local candidate
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" > /dev/null && python_ok "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

# Explain why the package manager's Python is not enough (Debian 11 "bullseye" and
# Ubuntu 20.04 only ship Python 3.9 or older).
too_old_message() {
    local found="" release=""
    if command -v python3 > /dev/null; then
        found=" (python3 is $(python3 --version 2>&1))"
    fi
    if [ -r /etc/os-release ]; then
        # shellcheck source=/dev/null
        release=" $(. /etc/os-release && echo "${PRETTY_NAME:-$ID}")"
    fi
    echo "the Python your distribution${release} provides is older than 3.10${found}."
    echo "       Upgrade to a newer release of your distribution (Debian 12 or Ubuntu 22.04 and later ship Python 3.11+),"
    echo "       or install Python 3.10+ another way and run this script again with PYTHON=/path/to/python3.x"
}

install_packages() {
    if command -v apt-get > /dev/null; then
        # apt-get update fails as a whole when any single source is broken (a stale
        # third-party PPA, for example), even though the distribution's own package
        # lists refreshed fine. Warn and let apt-get install try with what it has.
        if ! apt-get update -qq; then
            echo "Warning: some package sources could not be refreshed (see above); trying to install anyway." >&2
        fi
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
    elif command -v dnf > /dev/null; then
        dnf install -y -q "$@"
    elif command -v pacman > /dev/null; then
        pacman -S --noconfirm --needed "$@"
    elif command -v zypper > /dev/null; then
        zypper --non-interactive install "$@"
    else
        return 1
    fi
}

ensure_python() {
    if [ -n "${PYTHON:-}" ]; then
        python_ok "$PYTHON" || die "PYTHON=$PYTHON is not a Python 3.10+ interpreter"
    elif ! PYTHON="$(find_python)"; then
        local pkg="python3"
        command -v pacman > /dev/null && pkg="python"
        confirm "Python 3.10 or newer is required. Install it with the package manager now?" \
            || die "install Python 3.10+ and run this script again"
        install_packages "$pkg" || die "could not install Python automatically; install Python 3.10+ manually"
        PYTHON="$(find_python)" || die "$(too_old_message)"
    fi
    # Debian/Ubuntu ship venv separately.
    if ! "$PYTHON" -c 'import venv, ensurepip' 2> /dev/null; then
        local version
        version="$("$PYTHON" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
        confirm "The Python venv module is missing. Install it now?" \
            || die "install the venv module for $PYTHON (e.g. apt install python$version-venv)"
        install_packages "python$version-venv" || install_packages python3-venv \
            || die "could not install the venv module; install python$version-venv manually"
    fi
    info "Using $PYTHON ($("$PYTHON" --version 2>&1))"
}

run_as_service_user() {
    if command -v runuser > /dev/null; then
        runuser -u "$SERVICE_USER" -- env HOME="$INSTALL_DIR" "$@"
    else
        sudo -u "$SERVICE_USER" -- env HOME="$INSTALL_DIR" "$@"
    fi
}

stop_service() {
    if systemctl is-active --quiet "$SERVICE_NAME" 2> /dev/null; then
        info "Stopping the running bot"
        systemctl stop "$SERVICE_NAME"
    fi
}

set_token() {
    local env_file="$INSTALL_DIR/.env" tmp
    tmp="$(mktemp)"
    awk -v t="$TOKEN" '/^DISCORD_TOKEN=/ { print "DISCORD_TOKEN=" t; next } { print }' "$env_file" > "$tmp"
    cat "$tmp" > "$env_file"
    rm -f "$tmp"
}

# -- automatic updates -----------------------------------------------------
# A root systemd timer runs comicsbot/updater.py (copied to $UPDATER_DIR, so the bot's user cannot
# change what root runs) once a day. It downloads the latest release with the GitHub token in
# $UPDATE_TOKEN_FILE (root only), checks SHA256SUMS and runs that release's install.sh, which
# keeps .env and the data; if that fails the previous version is restored.

auto_update_enabled() {
    [ -f "/etc/systemd/system/$UPDATE_UNIT.timer" ]
}

# Asks for the GitHub token unless one is stored already or given in COMICSBOT_GITHUB_TOKEN.
need_update_token() {
    [ -n "$UPDATE_TOKEN" ] && return 0
    [ -s "$UPDATE_TOKEN_FILE" ] && return 0
    if [ -t 0 ]; then
        echo "Automatic updates download releases from the private GitHub repository $GITHUB_REPO."
        echo "Create a fine-grained token at https://github.com/settings/personal-access-tokens/new with"
        echo "access to only $GITHUB_REPO and the permission Contents: Read-only, and paste it here."
        read -r -s -p "GitHub token: " UPDATE_TOKEN
        echo
    fi
    [ -n "$UPDATE_TOKEN" ] || die "automatic updates need a GitHub token: set COMICSBOT_GITHUB_TOKEN"
}

write_update_token() {
    [ -n "$UPDATE_TOKEN" ] || return 0
    local tmp
    install -d -m 700 -o root -g root "$UPDATER_CONF_DIR"
    tmp="$(umask 077 && mktemp "$UPDATER_CONF_DIR/.github-token.XXXXXX")"
    printf '%s\n' "$UPDATE_TOKEN" > "$tmp"
    chown root:root "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$UPDATE_TOKEN_FILE"
}

# Installs (or refreshes) the updater and its systemd units. Needs $PYTHON.
install_updater() {
    # From the release being installed (root's extracted copy during automatic updates),
    # not from the install folder, which the bot's account can write.
    local updater="$SRC_DIR/comicsbot/updater.py"
    [ -f "$updater" ] || die "this ComicsBot version has no updater ($updater is missing)"
    install -d -m 755 -o root -g root "$UPDATER_DIR"
    install -m 644 -o root -g root "$updater" "$UPDATER_DIR/updater.py"
    cat > "/etc/systemd/system/$UPDATE_UNIT.service" <<UNIT
[Unit]
Description=Update ComicsBot to the latest release
Documentation=https://github.com/$GITHUB_REPO#automatic-updates
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
# Runs as root because it runs the release's install.sh, like a manual upgrade.
ExecStart="$PYTHON" -I "$UPDATER_DIR/updater.py" --install-dir "$INSTALL_DIR" --token-file "$UPDATE_TOKEN_FILE" --lock-file "$UPDATE_LOCK"
TimeoutStartSec=45min
UMask=0022
PrivateTmp=yes
NoNewPrivileges=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
UNIT
    cat > "/etc/systemd/system/$UPDATE_UNIT.timer" <<UNIT
[Unit]
Description=Daily check for ComicsBot updates

[Timer]
OnCalendar=daily
RandomizedDelaySec=6h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    chmod 644 "/etc/systemd/system/$UPDATE_UNIT.service" "/etc/systemd/system/$UPDATE_UNIT.timer"
    systemctl daemon-reload
    systemctl enable --quiet "$UPDATE_UNIT.timer"
    systemctl start "$UPDATE_UNIT.timer"
}

enable_auto_update() {
    need_update_token
    write_update_token
    [ -s "$UPDATE_TOKEN_FILE" ] || die "no GitHub token stored in $UPDATE_TOKEN_FILE"
    install_updater
    info "Automatic updates are on: checked daily (sudo $INSTALL_DIR/install.sh --check-update to test the token)"
}

remove_updater() {
    local unit
    if [ -f "/etc/systemd/system/$UPDATE_UNIT.timer" ] || [ -f "/etc/systemd/system/$UPDATE_UNIT.service" ]; then
        info "Removing automatic updates"
        systemctl disable --now "$UPDATE_UNIT.timer" 2> /dev/null || true
        for unit in timer service; do
            rm -f "/etc/systemd/system/$UPDATE_UNIT.$unit"
        done
        systemctl daemon-reload
    fi
    rm -rf "$UPDATER_DIR"
    rm -f "$UPDATE_TOKEN_FILE" "$UPDATE_LOCK"
    rmdir "$UPDATER_CONF_DIR" 2> /dev/null || true
}

require_installed() {
    if [ ! -f "$INSTALL_DIR/VERSION" ] || [ ! -f "$INSTALL_DIR/ComicsBot.py" ]; then
        die "ComicsBot is not installed in $INSTALL_DIR (use --dir)"
    fi
}

do_enable_update() {
    require_installed
    ensure_python
    enable_auto_update
}

do_check_update() {
    require_installed
    if [ ! -f "$UPDATER_DIR/updater.py" ] || [ ! -s "$UPDATE_TOKEN_FILE" ]; then
        die "automatic updates are not enabled; run: sudo $SCRIPT --enable-auto-update"
    fi
    ensure_python > /dev/null
    "$PYTHON" -I "$UPDATER_DIR/updater.py" --install-dir "$INSTALL_DIR" --token-file "$UPDATE_TOKEN_FILE" \
        --lock-file "$UPDATE_LOCK" --check
}

do_update_now() {
    auto_update_enabled || die "automatic updates are not enabled; run: sudo $SCRIPT --enable-auto-update"
    local since status=0
    since="$(date +%s)"
    info "Updating (this can take a few minutes)"
    systemctl start "$UPDATE_UNIT.service" || status=$?
    journalctl -u "$UPDATE_UNIT" --since "@$since" --no-pager -o cat || true
    [ "$status" -eq 0 ] || die "the update failed (see above, or: journalctl -u $UPDATE_UNIT)"
}

# -- install ---------------------------------------------------------------

do_install() {
    [ -f "$SRC_DIR/ComicsBot.py" ] || die "run install.sh from the extracted release folder"
    if [ -z "$TOKEN" ] && [ ! -f "$INSTALL_DIR/.env" ] && [ ! -f "$MIGRATE_FROM/.env" ] && [ ! -t 0 ]; then
        die "a bot token is required: pass --token or set DISCORD_TOKEN"
    fi
    # First interactive install: offer automatic updates. Upgrades keep whatever was chosen.
    if [ "$AUTO_UPDATE" -eq 0 ] && ! auto_update_enabled && [ ! -f "$INSTALL_DIR/.env" ] \
        && ask_no "Install new ComicsBot releases automatically (checked daily; needs a GitHub token)?"; then
        AUTO_UPDATE=1
    fi
    [ "$AUTO_UPDATE" -eq 1 ] && need_update_token
    ensure_python
    stop_service

    if ! id "$SERVICE_USER" > /dev/null 2>&1; then
        info "Creating system user '$SERVICE_USER'"
        useradd --system --home-dir "$INSTALL_DIR" --no-create-home \
            --shell "$(command -v nologin || echo /bin/false)" "$SERVICE_USER"
    fi

    mkdir -p "$INSTALL_DIR"
    if [ "$SRC_DIR" != "$INSTALL_DIR" ]; then
        info "Copying program files to $INSTALL_DIR"
        rm -rf "$INSTALL_DIR/comicsbot"
        local item
        for item in "${APP_FILES[@]}"; do
            if [ -e "$SRC_DIR/$item" ]; then
                cp -a "$SRC_DIR/$item" "$INSTALL_DIR/"
            fi
        done
    fi

    if [ -n "$MIGRATE_FROM" ]; then
        [ -d "$MIGRATE_FROM" ] || die "--migrate-from: $MIGRATE_FROM is not a folder"
        info "Copying settings and data from $MIGRATE_FROM"
        local path name
        for path in "${DATA_FILES[@]/#/$MIGRATE_FROM/}" "$MIGRATE_FROM"/*-Comics.json; do
            [ -f "$path" ] || continue
            name="$(basename "$path")"
            if [ -e "$INSTALL_DIR/$name" ]; then
                echo "    $name already exists in $INSTALL_DIR, kept the existing one"
            else
                cp -a "$path" "$INSTALL_DIR/$name"
                echo "    $name"
            fi
        done
    fi

    if [ ! -f "$INSTALL_DIR/.env" ]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        if [ -z "$TOKEN" ] && [ -t 0 ]; then
            echo "Paste the bot token from https://discord.com/developers/applications (Bot -> Reset Token)."
            echo "On that Bot page, also turn on \"Server Members Intent\" (Privileged Gateway Intents) and save;"
            echo "without it update notices go to server owners only, not to all server admins."
            read -r -s -p "Discord bot token: " TOKEN
            echo
        fi
        [ -n "$TOKEN" ] || die "a bot token is required: pass --token or set DISCORD_TOKEN"
        set_token
    elif [ -n "$TOKEN" ]; then
        set_token
    fi

    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    chmod 600 "$INSTALL_DIR/.env"
    chmod 755 "$INSTALL_DIR/start-bot.sh" "$INSTALL_DIR/install.sh"

    run_as_service_user test -x "$INSTALL_DIR/start-bot.sh" \
        || die "user '$SERVICE_USER' cannot access $INSTALL_DIR (is a parent folder private?); use a location like /opt/comicsbot"

    info "Installing Python dependencies"
    run_as_service_user env PYTHON="$PYTHON" "$INSTALL_DIR/start-bot.sh" setup

    info "Registering and starting the systemd service"
    local log="$INSTALL_DIR/discord.log" before=0
    [ -f "$log" ] && before="$(wc -l < "$log")"
    SERVICE_USER="$SERVICE_USER" "$INSTALL_DIR/start-bot.sh" install-service > /dev/null

    if [ "$AUTO_UPDATE" -eq 1 ]; then
        info "Setting up automatic updates"
        enable_auto_update
    elif auto_update_enabled; then
        info "Updating the automatic updater"
        install_updater
    fi

    info "Waiting for the bot to log in"
    local _
    for _ in $(seq 1 30); do
        sleep 1
        if [ -f "$log" ] && tail -n +"$((before + 1))" "$log" | grep -q "Logged in as"; then
            tail -n +"$((before + 1))" "$log" | grep "Logged in as" | tail -n 1
            echo
            echo "ComicsBot $(cat "$INSTALL_DIR/VERSION" 2> /dev/null || true) is installed in $INSTALL_DIR and starts on boot."
            echo "  Status:  systemctl status $SERVICE_NAME"
            echo "  Logs:    journalctl -u $SERVICE_NAME -f   (or $log)"
            echo "  Restart: systemctl restart $SERVICE_NAME"
            echo "  Settings: $INSTALL_DIR/.env"
            if auto_update_enabled; then
                echo "  Updates: automatic, daily (journalctl -u $UPDATE_UNIT; turn off with --disable-auto-update)"
            fi
            return 0
        fi
        if ! systemctl is-active --quiet "$SERVICE_NAME"; then
            break
        fi
    done
    echo "The bot did not report a successful login. Recent errors from $log:" >&2
    if [ -f "$log" ]; then
        tail -n +"$((before + 1))" "$log" | grep -E "ERROR|CRITICAL" | tail -n 5 >&2 || true
    fi
    echo "Full output: journalctl -u $SERVICE_NAME -n 50" >&2
    exit 1
}

# -- uninstall ---------------------------------------------------------------

do_uninstall() {
    remove_updater
    if [ -f "/etc/systemd/system/$SERVICE_NAME.service" ]; then
        info "Removing the systemd service"
        systemctl disable --now "$SERVICE_NAME" 2> /dev/null || true
        rm -f "/etc/systemd/system/$SERVICE_NAME.service"
        systemctl daemon-reload
    fi
    [ -d "$INSTALL_DIR" ] || { info "Nothing installed in $INSTALL_DIR"; return 0; }
    if [ "$PURGE" -eq 1 ]; then
        info "Deleting $INSTALL_DIR"
        rm -rf "$INSTALL_DIR"
        if id "$SERVICE_USER" > /dev/null 2>&1; then
            userdel "$SERVICE_USER" || true
        fi
    else
        info "Removing program files (keeping .env and data in $INSTALL_DIR)"
        local item
        for item in "${APP_FILES[@]}" .venv .cache; do
            [ "$item" = ".env.example" ] && continue
            rm -rf "${INSTALL_DIR:?}/$item"
        done
    fi
    info "ComicsBot has been uninstalled"
}

case "$ACTION" in
    install) do_install ;;
    uninstall) do_uninstall ;;
    enable-update) do_enable_update ;;
    disable-update) remove_updater; info "Automatic updates are off" ;;
    check-update) do_check_update ;;
    update-now) do_update_now ;;
esac
