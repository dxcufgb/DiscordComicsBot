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
Distributions older than Debian 12 / Ubuntu 22.04 ship a Python older than 3.10; upgrade the system, or install a
newer Python yourself and run `sudo PYTHON=/path/to/python3.x ./install.sh`.
Manage it with `systemctl status|restart|stop comicsbot`; logs are in `journalctl -u comicsbot` and
`/opt/comicsbot/discord.log`.

**Windows**: run `ComicsBot-X.Y.Z-setup.exe`. The wizard asks for the bot token and installs everything. Run it as
administrator to get the option to start the bot when Windows starts, even if nobody logs in. The Start menu gets
*Restart ComicsBot*, *ComicsBot log* and *ComicsBot settings (.env)* shortcuts, and the bot can be removed from
*Settings → Apps*, which asks whether to keep the token and data. The setup is not code-signed, so SmartScreen may
ask you to confirm (*More info → Run anyway*). Unattended: set `DISCORD_TOKEN` and run
`ComicsBot-X.Y.Z-setup.exe /VERYSILENT [/TASKS=startatboot]`.

Prefer no installer? Extract `ComicsBot-X.Y.Z-windows.zip` and double-click `install.bat` instead; it installs the
same way (`install.bat -AtStartup` as administrator to start at boot) and is managed with `install.bat -Restart`,
`-Stop` or `-Uninstall`. Both install into `%LOCALAPPDATA%\ComicsBot` and can upgrade each other.

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

The version lives in `comicsbot/__init__.py` (`__version__`); `pyproject.toml` must match it (a test checks).

1. Bump `__version__` in `comicsbot/__init__.py` and `version` in `pyproject.toml`, and merge to `main`.
2. Publish, any of these ways:
   - **Actions → Release → Run workflow** on `main`. This creates the tag `v<version>` and the release. It
     refuses to run if that tag already exists, so a forgotten version bump can't overwrite a release.
   - Push a tag yourself: `git tag v2.2.0 && git push origin v2.2.0`.
   - Publish a release with a new `v<version>` tag on GitHub's Releases page.

The Release workflow runs the tests and the real-install CI jobs, builds the archives with `scripts/build_release.py`
and the Windows `setup.exe` with Inno Setup (`scripts/build_setup.ps1`, `packaging/windows/ComicsBot.iss`), and
publishes the release with all three, a `SHA256SUMS` file and install instructions above the generated changelog (it adds them to a
release created on GitHub's website too). Versions with a hyphen (`2.2.0-rc1`) become pre-releases. To inspect the
archives locally, run `python scripts/build_release.py`; they're written to `dist/`, and the same commit always
builds byte-identical archives.

### Dependencies

Renovate (`renovate.json`) opens pull requests when dependencies have new releases. Enable it at <https://github.com/apps/renovate>.
