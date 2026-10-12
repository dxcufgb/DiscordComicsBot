# DiscordComicsBot

A Discord bot that watches RSS/Atom feeds of web comics and posts new strips to a channel.

## Setup

1. Create an application at <https://discord.com/developers/applications>. Under **Bot**, reset and copy the token.
   Under Bot → Privileged Gateway Intents, also turn on **"Server Members Intent"** and save. The bot uses it to find
   the server admins it sends update notices to. Without it the bot still runs: Discord refuses the intent, the bot
   logs one warning and starts again without it, and update notices then go to server owners only. It is not
   requested when `UPDATE_NOTIFY` is `owner` or `off` (set `owner` to silence the warning).
2. Invite the bot with this URL, replacing `CLIENT_ID` with your application ID:
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=19456`
   (View Channel, Send Messages, Embed Links).
3. Install it from a release (below), or run it from source.

### Install from a release (recommended)

The [Releases](../../releases) page has the files for each system. They contain only what is needed to run the bot.
The installer installs Python 3.10+ if it is missing, asks for the bot token, installs the dependencies and starts the
bot as a service that starts with the computer.

**Install on Linux** (any distribution with systemd):

```sh
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s comicsbot
```

This repository is private, so it asks for a fine-grained token with read-only *Contents* access to this repository.
Or download `comicsbot-linux.tar.gz` from the latest release, then:

```sh
tar xzf comicsbot-linux.tar.gz
cd comicsbot-X.Y.Z
sudo ./install.sh
```

| Option | Meaning |
|---|---|
| (none) | install or upgrade in place, interactive (asks for what it needs) |
| `--yes`, `-y` | non-interactive: no questions; missing required settings → clear error |
| `--auto-update` / `--no-auto-update` | turn automatic updates on / off as part of this install |
| `--enable-auto-update` / `--disable-auto-update` | turn them on / off on an existing install, nothing else |
| `--check-update` | only check: exit 0 = up to date, 10 = newer release available, other = error |
| `--update-now` | install the latest release now (same path as the daily updater) |
| `--uninstall` [`--purge`] | remove (keep / also delete settings and data) |
| `--help` | usage listing exactly these first, then the ComicsBot options below |

ComicsBot options: `--dir DIR` (install location, default `/opt/comicsbot`), `--token TOKEN` (or `DISCORD_TOKEN`),
`--migrate-from DIR` and `PYTHON=/path/to/python3.x` (see the table further down). The GitHub token for automatic
updates is never an option: it is asked for, or read from `COMICSBOT_GITHUB_TOKEN`, or from `INSTALL_GITHUB_TOKEN`
(the one-line installer sets it; an interactive install asks before reusing it for automatic updates).

The bot is installed in `/opt/comicsbot` and runs as the system user `comicsbot`.
Distributions older than Debian 12 / Ubuntu 22.04 ship a Python older than 3.10; upgrade the system, or install a
newer Python yourself and run `sudo PYTHON=/path/to/python3.x ./install.sh`.
Manage it with `systemctl status|restart|stop comicsbot`; logs are in `journalctl -u comicsbot` and
`/opt/comicsbot/data/discord.log`.

**Linux layout.** Root runs the program files (when installing, and the automatic updater), so the bot's own account
must not be able to change them:

| Path | Owner, mode | What |
|---|---|---|
| `/opt/comicsbot/` | `root:root 755` | Program files: `ComicsBot.py`, `comicsbot/`, `start-bot.sh`, `install.sh`, `requirements.txt`, `README.md`, `VERSION`, `RELEASE_NOTES.md`, `.env.example` (all root's, not writable by group or others) |
| `/opt/comicsbot/.venv/` | `root:root` | The virtual environment, built by the installer as root; the bot only runs it |
| `/opt/comicsbot/.env` | `comicsbot:comicsbot 600` | Settings and the bot token |
| `/opt/comicsbot/data/` | `comicsbot:comicsbot 750` | Everything the bot writes: `state.json`, `discord.log` (+ rotations), `bot.lock`, `update-notice.json`, temporary files of atomic writes |

The bot uses `<program folder>/data` when that folder exists, and otherwise writes next to its program files (as
before 2.3; this is what Windows installs and runs from source do). Set `DATA_DIR` in `.env` to use another folder
(relative paths are relative to the program folder); the Linux installer and updater always use `data/`.

**Upgrading an install from before 2.3** (where the `comicsbot` account owned the whole folder) switches to this
layout: the installer gives the folder and the program files to root, moves everything else that the bot kept in the
folder (`state.json`, `discord.log*`, `bot.lock`, `update-notice.json`, `.state-*.tmp`, old `comics.json` /
`*-Comics.json`, any other file that is not a program file, `.env` or `.venv`) into `data/` with its owner and
mode unchanged (a name that is already taken in `data/` gets `.old`, `.old2`, ... appended), deletes the bot
account's pip cache (`.cache`) and rebuilds `.venv` as root. Running the installer again does nothing more. The
automatic updater runs the new release's `install.sh`, so automatic updates migrate the same way; if such an update
fails, the old updater puts the old program files back but not the old layout, so install the new release by hand
(`sudo ./install.sh`) in that case.

**Windows**: run `comicsbot-setup.exe` (or `ComicsBot-X.Y.Z-setup.exe`, the same file). The wizard asks for the bot token and installs everything. Run it as
administrator to get the option to start the bot when Windows starts, even if nobody logs in. The Start menu gets
*Restart ComicsBot*, *ComicsBot log* and *ComicsBot settings (.env)* shortcuts, and the bot can be removed from
*Settings → Apps*, which asks whether to keep the token and data. The setup is not code-signed, so SmartScreen may
ask you to confirm (*More info → Run anyway*). Unattended: set `DISCORD_TOKEN` and run
`comicsbot-setup.exe /VERYSILENT [/TASKS=startatboot]`.

Prefer no installer? Extract `comicsbot-windows.zip` and double-click `install.bat` instead; it installs the
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

Upgrades keep `.env` and the data (`state.json`, the log). When upgrading from 2.2.0 or older, turn on
"Server Members Intent" for the bot in the Developer Portal (see Setup) so update notices reach all server admins;
without it they go to the server owners only.

### Automatic updates

Off by default. When turned on, the bot checks once a day for a newer release on GitHub and installs it the same way
a manual upgrade does (the release's own installer), so `.env`, `state.json`, the logs and their permissions are kept.

How it works: the updater (`comicsbot/updater.py`, Python standard library only) asks
`https://api.github.com/repos/dxcufgb/DiscordComicsBot/releases/latest` for the latest published release (drafts and
pre-releases are ignored) and does nothing unless its version is newer than the installed one. Otherwise it downloads
this system's archive (`comicsbot-X.Y.Z-linux.tar.gz` or `-windows.zip`, name compared ignoring case; for now it is
`ComicsBot-X.Y.Z-...`, see below) and checks it against the release's
`SHA256SUMS.txt`, extracts it into a new temporary folder and runs its installer. A latest release without these
files (published by hand and its files not attached yet, or one that only has the old names) is reported in the log
and left alone; the next run looks again. If the installer fails (for example the
new version does not log in to Discord within 30-45 seconds), the previous program files are put back and, if the
bot was running before, the old version is restarted and checked again 15 seconds later. If that rollback fails too
(the files cannot be put back, or the old version does not start), the updater logs `ROLLBACK FAILED: ...` with the
steps to recover by hand, keeps the backup of the previous version (`/var/backups/comicsbot-update/ComicsBot-<old
version>-*` on Linux, `%LOCALAPPDATA%\ComicsBot\updater\backups\...` on Windows; the message names it) and exits
with code 3. Only one update runs at a time; downloads are HTTPS only, with timeouts and size limits.

**GitHub token.** The repository is private, so the updater needs a token that can read it:

1. Open <https://github.com/settings/personal-access-tokens/new> (Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token).
2. Resource owner: `dxcufgb`; Repository access: *Only select repositories* → `dxcufgb/DiscordComicsBot`.
3. Permissions → Repository permissions → **Contents: Read-only** (Metadata: Read-only is added automatically).
   Nothing else.
4. Pick an expiration date and generate it. When it expires, updates fail with "GitHub refused the request"; enable
   automatic updates again with a new token (it replaces the stored one).

The installer asks for the token, or reads it from the `COMICSBOT_GITHUB_TOKEN` environment variable (on Linux also
from `INSTALL_GITHUB_TOKEN`, which the one-line installer sets: used as is with `--yes`, after asking otherwise); it
is never passed on the command line. It is stored apart from the bot's `.env` and sent only to `api.github.com` (the actual
download is a redirect to GitHub's storage, which is fetched without it). It never appears in a log.

| | Linux | Windows |
|---|---|---|
| Turn on (off) while installing | `sudo ./install.sh --auto-update` (`--no-auto-update`; an interactive first install also asks) | Check *Install new ComicsBot releases automatically* in the setup, or `install.bat -EnableAutoUpdate` |
| Turn on later | `sudo /opt/comicsbot/install.sh --enable-auto-update` | `install.bat -EnableAutoUpdate` in the install folder |
| Turn off (deletes the token) | `sudo /opt/comicsbot/install.sh --disable-auto-update` | `install.bat -DisableAutoUpdate` |
| Check only (tests the token; exit 0 = up to date, 10 = newer release available) | `sudo /opt/comicsbot/install.sh --check-update` | `install.bat -CheckUpdate` |
| Update now | `sudo /opt/comicsbot/install.sh --update-now` | `install.bat -UpdateNow` |
| Schedule | systemd timer `comicsbot-update.timer`, daily with up to 6 h random delay, runs missed checks after boot | Scheduled task `ComicsBot-Update`, daily between 04:00 and 08:00, or as soon as possible after that |
| Runs as | root (it runs `install.sh`), in a hardened systemd service | the same Windows account as the bot (at logon, or without logon when the bot starts at boot) |
| Token stored in | `/etc/comicsbot/github-token` (root, mode 600) | `%LOCALAPPDATA%\ComicsBot\updater\github-token` (readable only by your account, like `.env`) |
| Log | `journalctl -u comicsbot-update` | `%LOCALAPPDATA%\ComicsBot\updater\update.log` |

Upgrading (manually, or by the updater) keeps the choice; uninstalling removes the timer or task, the updater and
the token. On Linux the updater runs from a root-owned copy in `/usr/local/lib/comicsbot-updater`, runs the new
release's `install.sh` from its own extracted copy and writes `update-notice.json` into `data/`. Because it runs as
root, turning automatic updates on (and every updater run) refuses an install folder (resolved through links),
program file, `.venv` file or parent folder that an account other than root could change, and a Python interpreter
(the given path, the file it leads to, or their folders) that one could change: install into a folder only root can
write, such as the default `/opt/comicsbot`, with a system-wide Python. The updater also backs up and restores `.venv`
on Linux. A setup run as administrator with *start at boot* sets up the update task to run
without a logon too; that needs administrator rights, also for `install.bat -EnableAutoUpdate` later.
Unattended Windows setup: set `COMICSBOT_GITHUB_TOKEN` and add `autoupdate` to `/TASKS`.

Automatic updates work from the first release that includes the updater; install that one by hand.
Releases up to 2.3.2 named their files `ComicsBot-X.Y.Z-linux.tar.gz`, `-windows.zip`, `-setup.exe` and `SHA256SUMS`,
and their updaters need exactly those names and the top folder `ComicsBot-X.Y.Z/`. GitHub treats file names that
differ only in case as the same, so a release cannot carry both `comicsbot-X.Y.Z-*` and `ComicsBot-X.Y.Z-*`: for now
the versioned files keep the old case (see `LEGACY_ASSETS` in `scripts/build_release.py`). An installation on 2.3.2
updates to the next release, whose installer puts in the new updater; that one finds the versioned files ignoring
case and accepts the top folder `comicsbot-X.Y.Z/` or `ComicsBot-X.Y.Z/`, so a later release can switch to lowercase
names without breaking it.

### Update notices to server admins

When the bot starts on a newer version than it ran before (after an automatic or a manual upgrade), it sends one DM
per person to the admins of the servers it is in: "ComicsBot updated to vX.Y.Z" with the release's changes and the
servers it applies to. The changes come from the GitHub release after an automatic update; the release's "Install"
section is left out. After a manual upgrade only the bundled `RELEASE_NOTES.md` is available, which holds just the
install steps, so the DM shows the version and a link to the release.
Each version is announced once; a fresh install only records its version. Finding the Administrator members uses
the **Server Members Intent**, which must be enabled in the Developer Portal (see Setup). If it is not, Discord
refuses it; the bot then logs a warning (`Discord refused the Server Members intent ...`), starts again without it
and sends the notices to the server owners only, as with `owner`. `owner` and `off` do not request it. Set in `.env`:

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

The **Security** workflow (`.github/workflows/security.yml`; GitHub's secret scanning and CodeQL are not available for
this private repository) runs on every pull request, on pushes to `main` and weekly: `Secrets (gitleaks)` scans the
whole git history, `Code (Semgrep)` runs the Semgrep registry rules, `Dependencies` runs pip-audit on
`requirements.txt` and `requirements-dev.txt`. The same checks locally (gitleaks 8.30.1, semgrep 1.180.0, pip-audit
2.10.1):

```sh
gitleaks git --redact --log-opts="--full-history --diff-filter=tuxdb HEAD" .
semgrep scan --metrics=off --error --severity WARNING --severity ERROR \
  --config p/python --config p/javascript --config p/security-audit \
  --config p/secrets --config p/github-actions --config r/bash
pip-audit --strict -r requirements.txt
pip-audit --strict -r requirements-dev.txt
```

Mark a Semgrep false positive with `# nosemgrep: <rule-id>` (Python, shell, YAML, PowerShell) on the matched line or
the line immediately before it, with a reason. Mark a gitleaks false positive with a narrow, commented allowlist entry
in `.gitleaks.toml`; the file must have `[extend]` with `useDefault = true` (the workflow checks this), or it would
replace the default rules. Before scanning, the workflow checks that the pinned gitleaks detects a token generated at
run time, and it scans only the checked-out commit's history (`HEAD`), not every branch.

### Releasing

**Actions → Release → Run workflow → type the version** (`1.2.3`; a leading `v` is dropped, `1.2.3-rc.1` makes a
pre-release). Fallback: publish a release `vX.Y.Z` on GitHub; the files are attached afterwards. No version bump in
the code: the version comes from the tag and is stamped into the built files only (`comicsbot/__init__.py` and
`VERSION` in the archives); `__version__` in the repository is just the last release's version.

The Release workflow refuses a version whose tag or release already exists, runs the tests and the real-install CI
jobs (`ci.yml`), builds the archives with `scripts/build_release.py --version X.Y.Z` and the Windows setup.exe with
Inno Setup (`scripts/build_setup.ps1`, `packaging/windows/ComicsBot.iss`), and publishes the release with all files
at once and install instructions above the generated changelog (for a release published by hand it attaches the
files and adds the instructions). The files:

| File | What |
|---|---|
| `ComicsBot-X.Y.Z-linux.tar.gz` | Linux archive; one top folder `ComicsBot-X.Y.Z/` with `install.sh` at its top |
| `ComicsBot-X.Y.Z-windows.zip` | Windows archive (`install.bat`) |
| `ComicsBot-X.Y.Z-setup.exe` | Windows setup wizard |
| `comicsbot-linux.tar.gz`, `comicsbot-windows.zip`, `comicsbot-setup.exe` | the same under fixed names (top folder `comicsbot-X.Y.Z/`), so `releases/latest/download/<name>` links never change |
| `SHA256SUMS` | checksums of the three versioned files, as up to 2.3.2 |
| `SHA256SUMS.txt` | `sha256sum` checksums of every other file |

The versioned names keep the case of 2.3.2 (`ComicsBot-`) while installations on 2.3.2 or older may still update:
their updater needs exactly those names, and GitHub refuses two file names that differ only in case, so
`comicsbot-X.Y.Z-*` cannot be attached as well (the build fails if two names would clash). Once no installation runs
2.3.2 or older, `LEGACY_ASSETS` goes: the versioned names and their top folder become lowercase `comicsbot-X.Y.Z`.

To inspect the files locally, run `python scripts/build_release.py --version 9.9.9`; they're written to `dist/`, and
the same commit and version always build byte-identical archives.

### Dependencies

Renovate (`renovate.json`) opens pull requests when dependencies have new releases. Enable it at <https://github.com/apps/renovate>.
