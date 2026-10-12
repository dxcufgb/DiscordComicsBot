# DiscordComicsBot

A Discord bot that polls RSS/Atom feeds and posts new comic strips to each server's chosen
channel, managed with `/comics` and `/comics-admin` slash commands. Ships with Linux (systemd)
and Windows (scheduled task + Inno Setup wizard) installers.

## Layout
- `ComicsBot.py` – entry point: loads config, sets up logging (`discord.log`, rotating), takes the
  `bot.lock` instance lock, runs the bot. If Discord refuses the Server Members intent with
  `UPDATE_NOTIFY=admins`, it logs how to enable it and runs again in `owner` mode. Exit codes matter to the start scripts:
  75 = restart, 73 = already running, `EXIT_CONFIG_ERROR` = bad config.
- `comicsbot/bot.py` – `ComicsBot(discord.Client)`: shared aiohttp session (created in
  `setup_hook`, closed in `close()`), `poll_feeds` task loop, lock around checks, Semaphore(4)
  for fetches.
- `comicsbot/commands.py` – slash commands; owner-only ones use `@app_commands.check(is_bot_owner)`.
- `comicsbot/feeds.py` – fetching/parsing feeds and SSRF protection.
- `comicsbot/updater.py` – opt-in auto-updater (stdlib only, self-contained: the installers run a copy of it
  outside the package; the GitHub token goes only to api.github.com and never to logs). Checks
  `releases/latest`, downloads `comicsbot-X.Y.Z-linux.tar.gz` / `-windows.zip` (name matched ignoring case; top
  folder `comicsbot-X.Y.Z/` or `ComicsBot-X.Y.Z/`, exactly one),
  requires `SHA256SUMS.txt` (a release without them is logged and left alone), `--check` exits 10 when a newer
  release exists. Extracts by hand, backs up the program files, runs the release's
  installer and rolls back on failure; a rollback that fails too logs "ROLLBACK FAILED" and keeps the backup.
- `comicsbot/announce.py` – after an upgrade, DMs the release notes to server admins once (version in
  `state.json`, notes from the updater's `update-notice.json` or the bundled `RELEASE_NOTES.md`).
- `comicsbot/storage.py` – `State` in `state.json`, atomic writes, migration from old versions.
- `comicsbot/config.py` – `.env` loading (environment variables override the file; old variable
  names still accepted). `Config.data_dir` holds `state.json`, `discord.log*` and `bot.lock`: `data/` when
  that folder exists (Linux installs), else the install folder (Windows, git checkouts). `comicsbot/lock.py` – `InstanceLock`.
- `packaging/`, `install.sh`, `install.ps1`, `scripts/build_release.py`, `scripts/build_setup.ps1`.
  `install.sh` migrates pre-2.3 installs to the root-owned layout (`migrate_layout`);
  `.github/check-linux-layout.sh` is the CI check of that layout.
- `AUDIT.md` – 28 issues fixed in the 2.0 rewrite. Do not let any of them come back.

## Commands
- `pip install -r requirements-dev.txt`
- `ruff check .` and `ruff format --check .` (line length 120; rules E, F, W, I, B, UP, ASYNC, S)
- `pytest -q`
- CI tests Python 3.10–3.14 and really installs, upgrades and uninstalls the bot on Linux and
  Windows. `python scripts/build_release.py --version 9.9.9 --out dist` builds the release archives
  (`--finish` adds the setup.exe copies and checksums once `scripts/build_setup.ps1` has built the setup.exe).
- Security (`.github/workflows/security.yml`, required checks `Secrets (gitleaks)`, `Code (Semgrep)`, `Dependencies`; push to main, PRs, weekly): locally `gitleaks git --redact .`, `semgrep scan --metrics=off --error --severity WARNING --severity ERROR --config p/python --config p/javascript --config p/security-audit --config p/secrets --config p/github-actions --config r/bash`, and `pip-audit --strict -r requirements.txt` and `pip-audit --strict -r requirements-dev.txt`. False positives: `# nosemgrep: <rule-id>` (Python, shell) on or above the line, with a reason; gitleaks: `.gitleaks.toml`.

## Install on Linux and releasing
- **Install on Linux**:
  `curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s comicsbot`
  (private repo: it asks for a fine-grained token with read-only *Contents* access to this repository), or
  download `comicsbot-linux.tar.gz`, `tar xzf`, `cd comicsbot-X.Y.Z`, `sudo ./install.sh`. Common options:
  `--yes`/`-y`, `--auto-update`/`--no-auto-update`, `--enable-auto-update`/`--disable-auto-update`,
  `--check-update` (0 = up to date, 10 = newer release, other = error), `--update-now`, `--uninstall [--purge]`,
  `--help`; `INSTALL_GITHUB_TOKEN` from the one-liner may become the updater token (asked first when interactive).
- **Releasing**: Actions → Release → Run workflow → type the version. Fallback: publish a release `vX.Y.Z` on
  GitHub, files are attached afterwards. No version bump in the code.
- Release files: versioned `ComicsBot-X.Y.Z-linux.tar.gz` / `-windows.zip` / `-setup.exe` (top folder
  `ComicsBot-X.Y.Z/`) and `SHA256SUMS`, as up to 2.3.2, because 2.3.2's updater needs exactly that; fixed names
  `comicsbot-linux.tar.gz` / `comicsbot-windows.zip` / `comicsbot-setup.exe` (top folder `comicsbot-X.Y.Z/`); and
  `SHA256SUMS.txt`. GitHub file names are unique ignoring case, so `comicsbot-X.Y.Z-*` cannot be attached too; the
  build fails on any such clash. Dropping `LEGACY_ASSETS` (scripts/build_release.py) means switching the versioned
  names and top folder to lowercase. `tests/test_legacy_update.py` runs the real 2.3.2 updater
  (`tests/updater_2_3_2.py`) and the current one against the published files.

## Conventions
- `from __future__ import annotations` in every module; modern type hints; frozen dataclasses.
- `log = logging.getLogger(__name__)`, %-style arguments, `log.exception` in catch-all blocks.
- Tests are plain pytest functions; async tests use `asyncio.run(...)` (no pytest-asyncio).
- Runtime dependencies are pinned in `requirements.txt` and updated by Renovate.

## Review focus
- **No blocking calls on the event loop.** `feedparser` stays in `asyncio.to_thread`. Flag new
  sync network or file I/O in coroutines (`State.save()` is small sync I/O and accepted as is).
- **HTTP**: every request needs a timeout (`REQUEST_TIMEOUT`); bodies stay capped at
  `MAX_FEED_BYTES`; redirects are followed by hand and **every hop** is re-checked with
  `ensure_public_url`. Flag any new fetch path (images, previews) that skips this – it is the
  SSRF protection.
- **Untrusted feed content** is posted to Discord: keep `AllowedMentions.none()` and the
  2000-character splitting (`split_message`).
- **Secrets**: the token must never reach logs or Discord messages (AUDIT.md item 1). Keep
  `discord.http` at WARNING unless `LOG_LEVEL` is DEBUG.
- **Linux layout**: `/opt/comicsbot`, every program file and the root-built `.venv` are `root:root` and not
  group/other-writable; the bot writes only to `data/` (`comicsbot:comicsbot 750`). Never give program files
  back to the bot's account. The installer refuses a non-empty folder that is not a ComicsBot install.
- **Auto-update runs as root on Linux** (`comicsbot-update.service` + daily timer; updater copy in
  `/usr/local/lib/comicsbot-updater/`, token in `/etc/comicsbot/github-token` root 600, backups in
  `/var/backups/comicsbot-update`). It must refuse an install folder, program file, parent folder or Python
  interpreter that a non-root account can change, never follow symlinks when backing up or restoring, and
  install the updater from the release being installed, not from the install folder. On Windows the update
  task runs as the bot's own account (per-user install), never SYSTEM. Auto-update stays opt-in.
- **Installers**: `.env` stays `comicsbot:comicsbot 600` on Linux and owner-only ACL on Windows;
  the token goes to `install.ps1` through an environment variable, never the command line;
  upgrades must keep the existing `.env`. Keep shell scripts shellcheck-clean and PowerShell
  scripts PSScriptAnalyzer-clean.
- **Versions**: the release version comes from the tag; `build_release.py --version` stamps it into the built
  `comicsbot/__init__.py` and `VERSION` only. Never bump `__version__` / `pyproject.toml` for a release (they stay
  the last release's version and must match each other, which `tests/test_release.py` checks).
