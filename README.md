# DiscordComicsBot

A Discord bot that watches RSS/Atom feeds of web comics and posts new strips to a channel.

## Setup

1. Create an application at <https://discord.com/developers/applications>. Under **Bot**, reset and copy the token.
   **Under Bot → Privileged Gateway Intents, turn on "Server Members Intent" and save.** The bot needs it to find
   the server admins it sends update notices to; without it Discord refuses the login and the bot stops with an
   error in `discord.log` saying so (it is not restarted over and over). It is not requested when `UPDATE_NOTIFY`
   is `owner` or `off`.
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

Upgrades keep `.env`, `state.json` and the log. **Upgrading from 2.2.0 or older:** first turn on
"Server Members Intent" for the bot in the Developer Portal (see Setup), or the new version cannot log in.

### Automatic updates

Off by default. When turned on, the bot checks once a day for a newer release on GitHub and installs it the same way
a manual upgrade does (the release's own installer), so `.env`, `state.json`, the logs and their permissions are kept.

How it works: the updater (`comicsbot/updater.py`, Python standard library only) asks
`https://api.github.com/repos/dxcufgb/DiscordComicsBot/releases/latest` for the latest published release (drafts and
pre-releases are ignored) and does nothing unless its version is newer than the installed one. Otherwise it downloads
this system's archive (`ComicsBot-X.Y.Z-linux.tar.gz` or `-windows.zip`) and checks it against the release's
`SHA256SUMS`, extracts it into a new temporary folder and runs its installer. If the installer fails (for example the
new version does not log in to Discord within 30-45 seconds), the previous program files are put back and the old
version is restarted. Only one update runs at a time; downloads are HTTPS only, with timeouts and size limits.

**GitHub token.** The repository is private, so the updater needs a token that can read it:

1. Open <https://github.com/settings/personal-access-tokens/new> (Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token).
2. Resource owner: `dxcufgb`; Repository access: *Only select repositories* → `dxcufgb/DiscordComicsBot`.
3. Permissions → Repository permissions → **Contents: Read-only** (Metadata: Read-only is added automatically).
   Nothing else.
4. Pick an expiration date and generate it. When it expires, updates fail with "GitHub refused the request"; enable
   automatic updates again with a new token (it replaces the stored one).

The installer asks for the token, or reads it from the `COMICSBOT_GITHUB_TOKEN` environment variable; it is never
passed on the command line. It is stored apart from the bot's `.env` and sent only to `api.github.com` (the actual
download is a redirect to GitHub's storage, which is fetched without it). It never appears in a log.

| | Linux | Windows |
|---|---|---|
| Turn on while installing | `sudo ./install.sh --auto-update` (an interactive first install also asks) | Check *Install new ComicsBot releases automatically* in the setup, or `install.bat -EnableAutoUpdate` |
| Turn on later | `sudo /opt/comicsbot/install.sh --enable-auto-update` | `install.bat -EnableAutoUpdate` in the install folder |
| Turn off (deletes the token) | `sudo /opt/comicsbot/install.sh --disable-auto-update` | `install.bat -DisableAutoUpdate` |
| Check only (tests the token) | `sudo /opt/comicsbot/install.sh --check-update` | `install.bat -CheckUpdate` |
| Update now | `sudo /opt/comicsbot/install.sh --update-now` | `install.bat -UpdateNow` |
| Schedule | systemd timer `comicsbot-update.timer`, daily with up to 6 h random delay, runs missed checks after boot | Scheduled task `ComicsBot-Update`, daily between 04:00 and 08:00, or as soon as possible after that |
| Runs as | root (it runs `install.sh`), in a hardened systemd service | the same Windows account as the bot (at logon, or without logon when the bot starts at boot) |
| Token stored in | `/etc/comicsbot/github-token` (root, mode 600) | `%LOCALAPPDATA%\ComicsBot\updater\github-token` (readable only by your account, like `.env`) |
| Log | `journalctl -u comicsbot-update` | `%LOCALAPPDATA%\ComicsBot\updater\update.log` |

Upgrading (manually, or by the updater) keeps the choice; uninstalling removes the timer or task, the updater and
the token. On Linux the updater runs from a root-owned copy in `/usr/local/lib/comicsbot-updater`, so the bot's own
user cannot change what root runs. A setup run as administrator with *start at boot* sets up the update task to run
without a logon too; that needs administrator rights, also for `install.bat -EnableAutoUpdate` later.
Unattended Windows setup: set `COMICSBOT_GITHUB_TOKEN` and add `autoupdate` to `/TASKS`.

Automatic updates work from the first release that includes the updater; install that one by hand.

### Update notices to server admins

When the bot starts on a newer version than it ran before (after an automatic or a manual upgrade), it sends one DM
per person to the admins of the servers it is in: "ComicsBot updated to vX.Y.Z" with the release's changes and the
servers it applies to. The changes come from the GitHub release after an automatic update; the release's "Install"
section is left out. After a manual upgrade only the bundled `RELEASE_NOTES.md` is available, which holds just the
install steps, so the DM shows the version and a link to the release.
Each version is announced once; a fresh install only records its version. Finding the Administrator members uses
the **Server Members Intent**, which must be enabled in the Developer Portal (see Setup); `owner` and `off` do not request it. Set in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `UPDATE_NOTIFY` | `admins` | `admins`: server owners and members with the Administrator permission; `owner`: owners only; `off`: no DMs |

Admins who have DMs from server members turned off are skipped (logged, not retried).

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
