#!/usr/bin/env bash
# ComicsBot installer for Linux (systemd). Run it from an extracted release archive:
#
#   sudo ./install.sh                        Install or upgrade, then start the service
#   sudo ./install.sh --migrate-from DIR     Also copy .env and bot data from an older install
#                                            (for example the git checkout you ran it from before)
#   sudo ./install.sh --uninstall            Remove the service and program files, keep .env and data
#   sudo ./install.sh --uninstall --purge    Remove everything, including data and the service user
#
# Layout: the program files (and .venv) belong to root and only root can change them; the bot runs as the
# system user comicsbot and writes only to DIR/data (state.json, discord.log, bot.lock, ...). DIR/.env is
# comicsbot:comicsbot mode 600. Upgrading an older install moves its data files into DIR/data.
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
#   --dir DIR       Install location (default /opt/comicsbot; a folder only root can change)
#   --token TOKEN   Discord bot token (or set DISCORD_TOKEN); asked for when missing
#   --yes           Do not ask before installing system packages
#   PYTHON=/path    Use this Python 3.10+ instead of searching PATH (environment variable)
set -euo pipefail
# Program files must not be writable by group or others.
umask 022

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
# The previous version is backed up here during an update; a backup is only left behind when the rollback
# of a failed update fails too (the updater then logs "ROLLBACK FAILED" with the path and what to do).
UPDATE_BACKUPS="/var/backups/comicsbot-update"

APP_FILES=(ComicsBot.py comicsbot requirements.txt start-bot.sh install.sh .env.example README.md VERSION RELEASE_NOTES.md)
# Bot data that --migrate-from copies (.env goes next to the program, the rest into the data folder).
DATA_FILES=(.env state.json comics.json)
# The bot's data folder in the install folder; the bot uses it when it exists (comicsbot/config.py).
DATA_DIR_NAME="data"

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
DATA_DIR="$INSTALL_DIR/$DATA_DIR_NAME"
[ "$INSTALL_DIR" != / ] || die "--dir / is not a valid install location"

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

# -- root-only program files -------------------------------------------------
# Root runs the program files (start-bot.sh install-service, .venv's Python, the updater runs install.sh), so
# no other account may be able to change them, their folders or the Python that root runs.

# Prints PATH if an account other than root could change it (a link counts by its owner only).
changeable() {
    find "$1" -maxdepth 0 \( \( -type l ! -user root \) -o \( ! -type l \( ! -user root -o -perm -o+w \
        -o \( -perm -g+w ! -group root \) \) \) \) -print 2> /dev/null
}

# Prints the first of PATH and its parent folders that an account other than root could change.
changeable_chain() {
    local p="$1"
    while :; do
        if [ -n "$(changeable "$p")" ]; then
            echo "$p"
            return 0
        fi
        if [ "$p" = / ]; then
            return 0
        fi
        p="$(dirname "$p")"
    done
}

# Prints the first path of the install folder (resolved through links; everything but the data folder and
# .env) or of its parent folders that an account other than root could change.
unsafe_path() {
    local dir bad
    dir="$(readlink -e "$1")" || { echo "$1"; return 0; }
    bad="$(find "$dir" -mindepth 1 \( -path "$dir/$DATA_DIR_NAME" -o -path "$dir/.env" \) -prune -o \
        \( \( -type l ! -user root \) -o \( ! -type l \( ! -user root -o -perm -o+w \
        -o \( -perm -g+w ! -group root \) \) \) \) -print -quit 2> /dev/null)"
    if [ -n "$bad" ]; then
        echo "$bad"
        return 0
    fi
    changeable_chain "$dir"
}

# Prints the first path an account other than root could change on the way to the Python root runs:
# the path as given (and its folders), then the real file it leads to (and its folders).
unsafe_python() {
    local path real bad
    path="$(command -v "$1")" || { echo "$1"; return 0; }
    real="$(readlink -e "$path")" || { echo "$path"; return 0; }
    bad="$(changeable_chain "$path")"
    if [ -z "$bad" ]; then
        bad="$(changeable_chain "$real")"
    fi
    echo "$bad"
}

# Automatic updates run as root: refuse an install (or a Python) another account could use to get root.
require_root_only() {
    local bad
    bad="$(unsafe_path "$INSTALL_DIR")"
    if [ -n "$bad" ]; then
        die "$bad can be changed by an account other than root, and automatic updates run as root.
       Install into a folder that only root can change (the default /opt/comicsbot) with this install.sh,
       which makes the program files root-owned, then turn automatic updates on again."
    fi
    bad="$(unsafe_python "$PYTHON")"
    if [ -n "$bad" ]; then
        die "$bad can be changed by an account other than root, and the updater runs $PYTHON as root.
       Use a Python installed system-wide (for example PYTHON=/usr/bin/python3) and run this again."
    fi
}

# True when root could not be sure .venv was built by root alone (a pre-2.3 install built it as the bot's user).
venv_untrusted() {
    local venv="$INSTALL_DIR/.venv"
    if [ -L "$venv" ] || { [ -e "$venv" ] && [ ! -d "$venv" ]; }; then
        return 0
    fi
    [ -d "$venv" ] || return 1
    [ -n "$(find "$venv" \( \( -type l ! -user root \) -o \( ! -type l \( ! -user root -o -perm -o+w \
        -o \( -perm -g+w ! -group root \) \) \) \) -print -quit 2> /dev/null)" ]
}

is_app_file() {
    local item
    for item in "${APP_FILES[@]}"; do
        if [ "$1" = "$item" ]; then
            return 0
        fi
    done
    return 1
}

# Moves PATH into the data folder under its own name (or NAME.old, NAME.old2, ... when that is taken).
# A rename keeps the owner and mode, and never follows a link.
move_into_data_dir() {
    local path="$1" name target n=1
    name="$(basename "$path")"
    target="$DATA_DIR/$name"
    while [ -e "$target" ] || [ -L "$target" ]; do
        target="$DATA_DIR/$name.old"
        if [ "$n" -gt 1 ]; then
            target="$target$n"
        fi
        n=$((n + 1))
    done
    mv -T "$path" "$target"
    echo "    $name -> ${target#"$INSTALL_DIR/"}"
}

# Before 2.3 the bot's account owned the whole install folder and wrote its data (state.json, discord.log*,
# bot.lock, update-notice.json, ...) next to the program files. Give the folder to root and move everything
# in it that is not a program file, .env or .venv into the data folder. Safe to run again: a new layout has
# nothing left to move.
migrate_layout() {
    local path name moved=0
    chown root:root "$INSTALL_DIR"
    chmod 755 "$INSTALL_DIR"
    # Something the bot's account left where the data folder goes (a link, a file) is moved in with the rest.
    if [ -L "$DATA_DIR" ] || { [ -e "$DATA_DIR" ] && [ ! -d "$DATA_DIR" ]; }; then
        mv -T "$DATA_DIR" "$INSTALL_DIR/.data-before-2.3"
    fi
    if [ ! -d "$DATA_DIR" ]; then
        install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
    fi
    chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
    chmod 750 "$DATA_DIR"
    for path in "$INSTALL_DIR"/* "$INSTALL_DIR"/.[!.]* "$INSTALL_DIR"/..?*; do
        if [ ! -e "$path" ] && [ ! -L "$path" ]; then
            continue  # an unmatched pattern
        fi
        name="$(basename "$path")"
        case "$name" in
            "$DATA_DIR_NAME" | .env | .venv) continue ;;
            .cache) rm -rf "$path"; continue ;;  # pip's cache from when the bot's account built .venv
        esac
        if is_app_file "$name"; then
            continue
        fi
        if [ "$moved" -eq 0 ]; then
            info "Moving the bot's data into $DATA_DIR"
            moved=1
        fi
        move_into_data_dir "$path"
    done
}

# Copies a file from --migrate-from into the data folder: copied as root into the root-owned install folder,
# given to the bot's account, then renamed into place (a rename never follows a link the account made).
copy_into_data_dir() {
    local tmp
    tmp="$(mktemp "$INSTALL_DIR/.migrate.XXXXXX")"
    cp --preserve=mode,timestamps "$1" "$tmp"
    chown "$SERVICE_USER:$SERVICE_USER" "$tmp"
    mv -T "$tmp" "$DATA_DIR/$2"
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
# A root systemd timer runs comicsbot/updater.py (copied to $UPDATER_DIR) once a day. It downloads the latest release with the GitHub token in
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
    require_root_only
    # From the release being installed (root's extracted copy during automatic updates).
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
ExecStart="$PYTHON" -I "$UPDATER_DIR/updater.py" --install-dir "$INSTALL_DIR" --token-file "$UPDATE_TOKEN_FILE" --lock-file "$UPDATE_LOCK" --backup-dir "$UPDATE_BACKUPS"
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
    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$UPDATE_BACKUPS"
    else
        rmdir "$UPDATE_BACKUPS" 2> /dev/null || true
    fi
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
        --lock-file "$UPDATE_LOCK" --backup-dir "$UPDATE_BACKUPS" --check
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
    if [ -e "$INSTALL_DIR/.git" ]; then
        die "$INSTALL_DIR is a git checkout; install into another folder (the default /opt/comicsbot), with --migrate-from $INSTALL_DIR to bring its settings and data"
    fi
    # Upgrades move everything that is not a program file into data/: never do that to someone else's folder.
    # A ComicsBot folder has the program files, or .env and data/ left by --uninstall without --purge.
    if [ -d "$INSTALL_DIR" ] && [ ! -f "$INSTALL_DIR/ComicsBot.py" ] && [ ! -f "$INSTALL_DIR/VERSION" ] \
        && { [ ! -f "$INSTALL_DIR/.env" ] || [ ! -d "$DATA_DIR" ]; } && [ -n "$(ls -A "$INSTALL_DIR")" ]; then
        die "$INSTALL_DIR is not empty and is not a ComicsBot install; choose an empty or new folder (the default /opt/comicsbot)"
    fi
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
    if [ "$AUTO_UPDATE" -eq 1 ] || auto_update_enabled; then
        # Checked again in full once the program files are in place; fail before changing anything.
        local bad
        bad="$(changeable_chain "$(dirname "$INSTALL_DIR")")"
        if [ -n "$bad" ]; then
            die "$bad can be changed by an account other than root, and automatic updates run as root.
       Install into a folder that only root can change (the default /opt/comicsbot)."
        fi
        bad="$(unsafe_python "$PYTHON")"
        if [ -n "$bad" ]; then
            die "$bad can be changed by an account other than root, and the updater runs $PYTHON as root.
       Use a Python installed system-wide (for example PYTHON=/usr/bin/python3) and run this again."
        fi
    fi
    stop_service

    if ! id "$SERVICE_USER" > /dev/null 2>&1; then
        info "Creating system user '$SERVICE_USER'"
        useradd --system --home-dir "$INSTALL_DIR" --no-create-home \
            --shell "$(command -v nologin || echo /bin/false)" "$SERVICE_USER"
    fi

    mkdir -p "$INSTALL_DIR"
    migrate_layout

    local item
    if [ "$SRC_DIR" != "$INSTALL_DIR" ]; then
        info "Copying program files to $INSTALL_DIR"
        for item in "${APP_FILES[@]}"; do
            # Remove first: never copy into (or through) whatever is there now.
            rm -rf "${INSTALL_DIR:?}/$item"
            if [ -e "$SRC_DIR/$item" ]; then
                cp -R --preserve=mode,timestamps "$SRC_DIR/$item" "$INSTALL_DIR/"
            fi
        done
    fi

    if [ -n "$MIGRATE_FROM" ]; then
        [ -d "$MIGRATE_FROM" ] || die "--migrate-from: $MIGRATE_FROM is not a folder"
        info "Copying settings and data from $MIGRATE_FROM"
        local path name dest
        for path in "${DATA_FILES[@]/#/$MIGRATE_FROM/}" "$MIGRATE_FROM"/*-Comics.json; do
            [ -f "$path" ] || continue
            name="$(basename "$path")"
            dest="$DATA_DIR/$name"
            if [ "$name" = .env ]; then
                dest="$INSTALL_DIR/.env"
            fi
            if [ -e "$dest" ] || [ -L "$dest" ]; then
                echo "    $name already exists in $(dirname "$dest"), kept the existing one"
            elif [ "$name" = .env ]; then
                (umask 077 && cp "$path" "$dest")
                echo "    $name"
            else
                copy_into_data_dir "$path" "$name"
                echo "    $name -> $DATA_DIR_NAME/$name"
            fi
        done
    fi

    local env_file="$INSTALL_DIR/.env"
    # Root writes and chowns .env: refuse a link (or hard link) the bot's account may have left there before 2.3.
    if [ -L "$env_file" ] || { [ -e "$env_file" ] && { [ ! -f "$env_file" ] || [ "$(stat -c %h "$env_file")" -ne 1 ]; }; }; then
        die "$env_file is not a plain file (a link?); check it, move it away and run this again"
    fi
    if [ ! -f "$env_file" ]; then
        (umask 077 && cp "$INSTALL_DIR/.env.example" "$env_file")
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
    chown "$SERVICE_USER:$SERVICE_USER" "$env_file"
    chmod 600 "$env_file"

    if venv_untrusted; then
        info "Rebuilding the virtual environment as root (it was not root's alone)"
        rm -rf "$INSTALL_DIR/.venv"
    fi
    info "Installing Python dependencies"
    PYTHON="$PYTHON" PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1 "$INSTALL_DIR/start-bot.sh" setup
    # The bot's account cannot write here, so compile the bot's modules now instead of on every start.
    (cd / && "$INSTALL_DIR/.venv/bin/python" -m compileall -q "$INSTALL_DIR/comicsbot" > /dev/null) || true

    # Everything but .env and the data folder belongs to root, and only root can change it.
    for item in "${APP_FILES[@]}" .venv; do
        if [ -e "$INSTALL_DIR/$item" ] || [ -L "$INSTALL_DIR/$item" ]; then
            chown -R root:root "$INSTALL_DIR/$item"
            chmod -R go-w "$INSTALL_DIR/$item"
        fi
    done
    chmod 755 "$INSTALL_DIR/start-bot.sh" "$INSTALL_DIR/install.sh"

    run_as_service_user test -x "$INSTALL_DIR/start-bot.sh" \
        || die "user '$SERVICE_USER' cannot access $INSTALL_DIR (is a parent folder private?); use a location like /opt/comicsbot"
    run_as_service_user test -w "$DATA_DIR" -a -r "$env_file" \
        || die "user '$SERVICE_USER' cannot write $DATA_DIR or read $env_file"
    local bad
    bad="$(unsafe_path "$INSTALL_DIR")"
    if [ -n "$bad" ]; then
        echo "Warning: $bad can be changed by an account other than root. Root runs the program files" >&2
        echo "         (when installing and updating), so only root should be able to change them and their folders." >&2
    fi

    info "Registering and starting the systemd service"
    local log="$DATA_DIR/discord.log" before=0
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
            echo "  Data:     $DATA_DIR"
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
        info "Removing program files (keeping $INSTALL_DIR/.env and $DATA_DIR)"
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
