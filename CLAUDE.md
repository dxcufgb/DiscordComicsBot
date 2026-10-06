# DiscordComicsBot

A Discord bot that polls RSS/Atom feeds and posts new comic strips to each server's chosen
channel, managed with `/comics` and `/comics-admin` slash commands. Ships with Linux (systemd)
and Windows (scheduled task + Inno Setup wizard) installers.

## Layout
- `ComicsBot.py` – entry point: loads config, sets up logging (`discord.log`, rotating), takes the
  `bot.lock` instance lock, runs the bot. Exit codes matter to the start scripts:
  75 = restart, 73 = already running, `EXIT_CONFIG_ERROR` = bad config.
- `comicsbot/bot.py` – `ComicsBot(discord.Client)`: shared aiohttp session (created in
  `setup_hook`, closed in `close()`), `poll_feeds` task loop, lock around checks, Semaphore(4)
  for fetches.
- `comicsbot/commands.py` – slash commands; owner-only ones use `@app_commands.check(is_bot_owner)`.
- `comicsbot/feeds.py` – fetching/parsing feeds and SSRF protection.
- `comicsbot/storage.py` – `State` in `state.json`, atomic writes, migration from old versions.
- `comicsbot/config.py` – `.env` loading (environment variables override the file; old variable
  names still accepted). `comicsbot/lock.py` – `InstanceLock`.
- `packaging/`, `install.sh`, `install.ps1`, `scripts/build_release.py`, `scripts/build_setup.ps1`.
- `AUDIT.md` – 28 issues fixed in the 2.0 rewrite. Do not let any of them come back.

## Commands
- `pip install -r requirements-dev.txt`
- `ruff check .` and `ruff format --check .` (line length 120; rules E, F, W, I, B, UP, ASYNC, S)
- `pytest -q`
- CI tests Python 3.10–3.14 and really installs, upgrades and uninstalls the bot on Linux and
  Windows. `python scripts/build_release.py --out dist` builds the release archives.

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
- **Installers**: `.env` stays `comicsbot:comicsbot 600` on Linux and owner-only ACL on Windows;
  the token goes to `install.ps1` through an environment variable, never the command line;
  upgrades must keep the existing `.env`. Keep shell scripts shellcheck-clean and PowerShell
  scripts PSScriptAnalyzer-clean.
- **Versions**: `comicsbot/__init__.py` `__version__` and `pyproject.toml` `version` must match
  (the release build and `tests/test_release.py` enforce it).
