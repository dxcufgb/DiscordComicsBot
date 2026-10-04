# DiscordComicsBot

A Discord bot that watches RSS/Atom feeds of web comics and posts new strips to a channel.

## Setup

1. Create an application at <https://discord.com/developers/applications>. Under **Bot**, reset and copy the token.
   No privileged intents are needed.
2. Invite the bot with this URL, replacing `CLIENT_ID` with your application ID:
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=19456`
   (View Channel, Send Messages, Embed Links).
3. Install it from a release (below), or run it from source.

### Install from a release (recommended)

Download the archive for your system from the [Releases](../../releases) page. It contains only what is needed to run
the bot. The installer installs Python 3.10+ if it is missing, asks for the bot token, installs the dependencies and
starts the bot as a service that starts with the computer.

**Linux** (any distribution with systemd):

```sh
tar xzf ComicsBot-X.Y.Z-linux.tar.gz
cd ComicsBot-X.Y.Z
sudo ./install.sh
```

The bot is installed in `/opt/comicsbot` and runs as the system user `comicsbot`.
Manage it with `systemctl status|restart|stop comicsbot`; logs are in `journalctl -u comicsbot` and
`/opt/comicsbot/discord.log`.

**Windows**: extract `ComicsBot-X.Y.Z-windows.zip` and double-click `install.bat`.

The bot is installed in `%LOCALAPPDATA%\ComicsBot` and starts when you log in. To start it when Windows starts,
even if nobody logs in, run `install.bat -AtStartup` from an administrator command prompt.
Manage it with `install.bat -Restart`, `-Stop` or `-Uninstall`.

| | Linux | Windows |
|---|---|---|
| Upgrade | Run the newer release's `install.sh` | Run the newer release's `install.bat` |
| Bring settings and data from an older install or git checkout | `--migrate-from /path/to/old` | `-MigrateFrom C:\path\to\old` |
| Install somewhere else | `--dir /srv/comicsbot` | `-InstallDir D:\ComicsBot` |
| Give the token without a prompt | `--token TOKEN` | `-Token TOKEN` |
| Uninstall (keeps `.env` and data) | `--uninstall` | `-Uninstall` |
| Uninstall everything | `--uninstall --purge` | `-Uninstall -Purge` |

Upgrades keep `.env`, `state.json` and the log.

### Run from source

Copy `.env.example` to `.env`, set `DISCORD_TOKEN`, then:

| OS | Run once | Start automatically |
|---|---|---|
| Linux | `./start-bot.sh` | `./start-bot.sh install-service` (systemd), or `@reboot /path/start-bot.sh` in `crontab -e` |
| Windows | `start-bot.bat` | `start-bot.bat install` (Task Scheduler, at logon) |

The scripts need Python 3.10 or newer. They create a `.venv`, install `requirements.txt`, and restart the bot if it crashes.
Only one copy of the bot can run from a folder at a time.

## Commands

| Command | Who | What |
|---|---|---|
| `/comics available` | everyone | All comics the bot knows about |
| `/comics subscriptions` | everyone | Comics this server is subscribed to |
| `/comics subscribe <name>` | everyone | Subscribe this server (autocompletes) |
| `/comics unsubscribe <name>` | everyone | Unsubscribe |
| `/comics help` | everyone | Help |
| `/comics-admin channel-set [channel]` | Manage Server | Post comics in this or the given channel |
| `/comics-admin channel-show` / `channel-clear` | Manage Server | Show or remove the comics channel |
| `/comics-admin add <name> <url>` | Manage Server | Add a feed to the bot and subscribe to it |
| `/comics-admin delete <name>` | bot owner | Remove a comic for all servers |
| `/comics-admin check-now` | bot owner | Check the feeds immediately |
| `/comics-admin restart` | bot owner | Restart the bot |

Server admins can change who may use each command under **Server Settings → Integrations → ComicsBot**.

## Upgrading from 1.x

The first start of 2.0 does this automatically:

- It repairs a `.env` that the old bot had corrupted. The original is kept as `.env.broken-backup`.
- It moves `comics.json`, `<server>-Comics.json` and the `COMICS-CHANNEL-*` settings into `state.json`. The old files are left in place and are no longer used.

After that, the `!` commands are gone; use the slash commands above. If they don't show up, re-invite the bot with the URL above (it needs the `applications.commands` scope).

## Development

```sh
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && pytest
```

### Making a release

1. Bump the version in `comicsbot/__init__.py` and `pyproject.toml` (a test checks that they match) and merge to `main`.
2. Tag the merge commit and push the tag:

   ```sh
   git tag v2.1.0 && git push origin v2.1.0
   ```

The Release workflow runs the tests, builds the archives with `scripts/build_release.py` and publishes a GitHub
release with them, a `SHA256SUMS` file and install instructions. Creating the release in the GitHub UI with a new
`vX.Y.Z` tag works too; the workflow then attaches the archives to it. Build the archives locally with
`python scripts/build_release.py` (output in `dist/`).

### Dependencies

Renovate (`renovate.json`) opens pull requests when dependencies have new releases. Enable it at <https://github.com/apps/renovate>.
