# ComicsBot audit (2026-10-01)

Line numbers refer to `ComicsBot.py` at commit `d852eca`. Findings are ordered by severity.

## Critical: security

1. **`!show-env` posts the bot token to Discord** (L237-239). `os.environ` contains `TOKEN`, so anyone who can trigger the command gets full control of the bot. Remove the command, or at least filter out secrets and reply only by DM.
2. **Owner check uses `display_name`** (L132, L237). Any member can change their server nickname to `dxcufgb` and then run `!restart-bot` or `!show-env`. Compare `message.author.id` with a fixed `OWNER_ID` (or use `await self.application_info()`).
3. **TLS verification is turned off for the whole process** (L71, `ssl._create_default_https_context = ssl._create_unverified_context`). Remove it. If one feed has a bad certificate, handle that feed on its own.
4. **Any user can add any URL to the global comic list** (`!add-comic`, L142). The bot then fetches that URL from your server every interval (SSRF into your LAN, for example `http://192.168.1.1/`). Reusing a name also overwrites that comic's URL for **every** server. Restrict the command to admins, allow only `http(s)`, reject private IPs, and refuse to overwrite names.

## High: bugs that lose data or stop the bot

5. **`updateEnv()` corrupts `.env`** (L58-64). It writes `KEY:value` with **no newlines** and keeps only `TOKEN` and `COMICS-CHANNEL-*`. python-dotenv cannot parse that. I checked: `TOKEN:abcCOMICS-CHANNEL-1:2` loads as a single key with no value. After any `!sbc`, `!dbc` or Christmas toggle, the next restart has no `TOKEN`, `INTERVAL`, `LOG-LEVEL` or `CHRISTMAS_WISH`, so the bot crashes on L12. This is very likely why the Christmas greeting kept breaking. Fix: write `f"{key}={value}\n"`, or better, move runtime state (channel IDs, Christmas flag) out of `.env` into a `state.json`.
6. **Guild data is wiped when the working directory is not the bot folder** (L36). `os.path.exists` checks a *relative* path, but `open` uses `dir`. If you start the bot from anywhere else (a systemd unit, a cron job, a shortcut), every `<guild>-Comics.json` is overwritten with `{}`. Use `os.path.join(dir, ...)` in the `exists` check too. The new start scripts `cd` into the bot folder to avoid this.
7. **Commands fail in any server without a comics channel** (L97). `int(os.getenv(...))` raises `TypeError` when the variable is missing, so `!sbc` can never be run in a new server, and every `== None` check after it is dead code. Read the value first and convert only when it is set.
8. **One bad server stops the whole posting loop** (L274, L286). For a server without a channel, `os.getenv(...)` is `None` and `.strip()` raises. A deleted channel makes `get_channel()` return `None`. A server joined after startup is not in `guildComics` (`KeyError`). A subscribed comic missing from `comics.json` also raises `KeyError`. Each of these kills `checkForDailyUpdates`, and no comics are posted to anyone until the bot restarts. Skip and log per server and per comic, inside `try/except`.
9. **Duplicate posting loops** (L45). `on_ready` runs again after every reconnect, and each run starts another `checkForDailyUpdates` loop, so comics get posted several times. Start the loop once in `setup_hook` (or use `discord.ext.tasks.loop`).
10. **`sendToAllGuilds()` and `updateComicsFile()` run inside the per-comic loop** (L257-258). With N comics, every server is scanned N times and the file is written N times per cycle. Move both after the `for` loop.
11. **Removing your last comic does not persist** (L53). `if guildComics[guildId]:` is false for `{}`, so the file is never written and the comic comes back after a restart.
12. **Typo `'lastURL'` vs `'LastURL'`** (L253). The check is always true. The fallback on L251 also raises `KeyError` for a comic that has never been fetched.
13. **Feed fetches block the event loop** (L75, L79). `feedparser.parse` and `requests.get` are synchronous and have **no timeout**, so one slow feed freezes the bot and can drop the gateway heartbeat. Use `await asyncio.to_thread(...)` and `requests.get(url, timeout=15)` (or `aiohttp`).

## Medium: robustness

14. **Command parsing crashes on bad input.** `!add-comic` slices `[13:]` and splits on `--`. A missing `--url`, an extra space or a name with spaces raises `IndexError` or `KeyError`. `!remove-comic` on an unknown name raises `KeyError`. The bot gives no feedback in either case. Moving to slash commands (`discord.app_commands`) gives typed arguments and validation for free, and removes the need for the privileged `message_content` intent.
15. **Empty or too-long messages.** `!active-comics` with no subscriptions sends `''`, which Discord rejects (HTTP 400). `!available-comics` stops working once the list goes over 2000 characters. Split long text into chunks, or use an embed.
16. **Writes are not atomic.** A crash or power loss in the middle of a write leaves an empty or half-written JSON file. Write to `file.tmp`, then `os.replace()`.
17. **Startup crashes on missing config.** A missing `LOG-LEVEL`, `INTERVAL` or `comics.json` gives a stack trace. Provide defaults (`os.getenv('INTERVAL', '3600')`) and exit with a clear message instead.
18. **Logging.** `mode='w'` truncates `discord.log` on every start, so the log from a crash is lost. Use a `RotatingFileHandler` in append mode, log the bot's own messages under its own logger (not `discord`), and use `logger.exception` in the bare `except:` blocks so you can see *why* a feed failed.
19. **Feed handling.** Only `entries[0]` is used, and the "new comic" check compares a `title - link` string. A title edit re-posts the comic, and two updates within one interval post only one. Track the entry `id`/`guid` and post every new entry since the last one seen.
20. **Christmas logic.** It depends on the `.env` flag (see #5), only resets in January, and needs the bot to be running between 12:00 and 15:00 on Dec 24. Store `lastChristmasYear` in the state file and send when `today >= Dec 24 and lastChristmasYear != year`.
21. **`!restart-bot` uses `os.execv`.** This is fine in a venv, but now that a supervisor exists (systemd `Restart=always`, or the restart loop in the Windows script), `await self.close()` followed by `sys.exit(0)` is cleaner.

## Low: code quality and maintainability

22. Module-level globals (`availableComics`, `guildComics`), the global `client` used inside the class (L31, L91), and `dir` shadowing a builtin. Move state onto the client instance and the paths into constants.
23. `"% s" % x` is used everywhere for `str(x)`. f-strings are clearer.
24. The 20 `if message.content.startswith(...)` checks run one after another with no `elif`. A dict of command handlers (or slash commands) is shorter and faster.
25. The help text is missing a `\n` after the `LAST CHANGE` banner, and that date has to be edited by hand.
26. `requests` is imported but missing from the install scripts. `pip install --system` is not a standard pip flag. `install-windows.sh` is a shell script on Windows. All of these are replaced by `requirements.txt` and the new start scripts.
27. `.gitignore` ignores `*.json`, so there is no sample `comics.json` for a fresh install. Add `comics.example.json` (`.env.example` has been added).
28. There are no tests and no lint. Adding `ruff` would catch #12, #22 and the bare `except:` blocks automatically.

## Suggested order of work

1. #1 to #3 (one small commit, closes the security holes).
2. #5 and #6 (stop losing configuration and data).
3. #7 to #11 (comics reliably reach every server).
4. #13 and #16 to #18 (stability).
5. Migrate to slash commands plus a `state.json` (fixes #4, #14, #15 and #20, and makes most of the low-severity items easy).

## Startup scripts (added in this change)

| OS | Run once | Start on boot |
|---|---|---|
| Linux | `./start-bot.sh` | `./start-bot.sh install-service` (systemd, `Restart=always`). Without systemd: `@reboot /path/start-bot.sh` in `crontab -e` |
| Windows | `start-bot.bat` (restarts the bot automatically if it crashes) | `start-bot.bat install` (Task Scheduler, at logon, no admin needed) |

On first run, both scripts create a `.venv`. They reinstall `requirements.txt` only when it changes, check that `.env` and `comics.json` exist, and always run the bot from its own folder.
