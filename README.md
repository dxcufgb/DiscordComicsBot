# DiscordComicsBot

[![Latest release](https://img.shields.io/github/v/release/dxcufgb/DiscordComicsBot?label=release&color=ff6400)](https://github.com/dxcufgb/DiscordComicsBot/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/dxcufgb/DiscordComicsBot/total?label=downloads&color=blue)](https://github.com/dxcufgb/DiscordComicsBot/releases)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/dxcufgb)\
[![CI](https://img.shields.io/github/actions/workflow/status/dxcufgb/DiscordComicsBot/ci.yml?branch=main&label=CI)](https://github.com/dxcufgb/DiscordComicsBot/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/dxcufgb/DiscordComicsBot)](LICENSE)

A Discord bot that watches RSS/Atom feeds of web comics and posts new strips to a channel.

## Installation

You need a Discord application with a bot token first: see [Discord Setup](https://github.com/dxcufgb/DiscordComicsBot/wiki/Discord-Setup).

Linux (any distribution with systemd):

```sh
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s comicsbot
```

Windows: run `comicsbot-setup.exe` from the [Releases](../../releases) page.

Installer options, upgrades and running from source: see [Installation](https://github.com/dxcufgb/DiscordComicsBot/wiki/Installation) in the wiki.

## Documentation

The full guide is in the [wiki](https://github.com/dxcufgb/DiscordComicsBot/wiki):

- [Discord Setup](https://github.com/dxcufgb/DiscordComicsBot/wiki/Discord-Setup): the application, the bot token and the invite URL.
- [Installation](https://github.com/dxcufgb/DiscordComicsBot/wiki/Installation): Linux and Windows installers and their options.
- [Commands](https://github.com/dxcufgb/DiscordComicsBot/wiki/Commands): every slash command and who may use it.
- [Automatic Updates](https://github.com/dxcufgb/DiscordComicsBot/wiki/Automatic-Updates): the opt-in daily updater, rollback and the optional GitHub token.
- [Update Notifications](https://github.com/dxcufgb/DiscordComicsBot/wiki/Update-Notifications): the DM server admins get after an update.
- [Files and Configuration](https://github.com/dxcufgb/DiscordComicsBot/wiki/Files-and-Configuration): the Linux layout, `.env`, the data folder and running from source.
- [Upgrading](https://github.com/dxcufgb/DiscordComicsBot/wiki/Upgrading): from 1.x, from before 2.3 and from 2.2.0 or older.
- [Troubleshooting](https://github.com/dxcufgb/DiscordComicsBot/wiki/Troubleshooting): missing commands, update problems.

## License

MIT, see [LICENSE](LICENSE).
