#!/usr/bin/env bash
# CI: checks the Linux install layout. Root owns the install folder, the program files and .venv, and nothing
# but .env and data/ can be changed by another account; the root updater relies on that.
set -euo pipefail
dir=/opt/comicsbot
test "$(stat -c '%U:%G %a' "$dir")" = "root:root 755"
test "$(sudo stat -c '%U:%G %a' "$dir/.env")" = "comicsbot:comicsbot 600"
test "$(sudo stat -c '%U:%G %a' "$dir/data")" = "comicsbot:comicsbot 750"
test "$(stat -c '%U:%G' "$dir/.venv/bin/python")" = "root:root"
test "$(stat -c '%U:%G %a' "$dir/start-bot.sh")" = "root:root 755"
bad="$(sudo find "$dir" \( -path "$dir/data" -o -path "$dir/.env" \) -prune -o ! -type l \( ! -user root -o -perm /022 \) -print)"
if [ -n "$bad" ]; then
    echo "changeable by an account other than root:" >&2
    echo "$bad" >&2
    exit 1
fi
for f in state.json bot.lock discord.log; do
    test "$(sudo stat -c '%U' "$dir/data/$f")" = comicsbot
done
echo "layout OK"
