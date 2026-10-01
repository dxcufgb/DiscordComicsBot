# DiscordComicsBot

A Discord bot that watches RSS/Atom feeds of web comics and posts new strips to a channel.

## Setup

1. Create an application at <https://discord.com/developers/applications>. Under **Bot**, reset and copy the token.
   No privileged intents are needed.
2. Invite the bot with this URL, replacing `CLIENT_ID` with your application ID:
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=19456`
   (View Channel, Send Messages, Embed Links).
3. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
4. Start the bot:

| OS | Run once | Start automatically |
|---|---|---|
| Linux | `./start-bot.sh` | `./start-bot.sh install-service` (systemd), or `@reboot /path/start-bot.sh` in `crontab -e` |
| Windows | `start-bot.bat` | `start-bot.bat install` (Task Scheduler, at logon) |

The scripts need Python 3.10 or newer. They create a `.venv`, install `requirements.txt`, and restart the bot if it crashes.

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

Renovate (`renovate.json`) opens pull requests when dependencies have new releases. Enable it at <https://github.com/apps/renovate>.
