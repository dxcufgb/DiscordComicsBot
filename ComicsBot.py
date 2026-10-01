"""Entry point: python ComicsBot.py (or use start-bot.sh / start-bot.bat)."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

if sys.version_info < (3, 10):  # noqa: UP036 - friendly message for old Pythons
    sys.exit("ComicsBot needs Python 3.10 or newer.")

import discord  # noqa: E402

from comicsbot.bot import ComicsBot  # noqa: E402
from comicsbot.config import EXIT_CONFIG_ERROR, ConfigError, load_config  # noqa: E402
from comicsbot.storage import load_or_migrate  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
# Exit code that tells the start scripts to restart the bot immediately.
EXIT_RESTART = 75


def setup_logging(level: str, log_file: Path) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    console_handler = logging.StreamHandler()
    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.setLevel(level)
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # discord.http at DEBUG logs every request payload; keep it quieter unless asked.
    if level != "DEBUG":
        logging.getLogger("discord.http").setLevel(logging.WARNING)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(BASE_DIR)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    setup_logging(config.log_level, config.log_file)
    log = logging.getLogger("comicsbot")
    try:
        state = load_or_migrate(config.state_file, config.legacy_channels)
    except (OSError, ValueError):
        log.exception("Could not load %s", config.state_file)
        return EXIT_CONFIG_ERROR

    bot = ComicsBot(config, state)
    try:
        bot.run(config.token, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord rejected the token; check DISCORD_TOKEN in .env")
        return EXIT_CONFIG_ERROR
    except discord.PrivilegedIntentsRequired:
        log.error("The bot requested an intent that is not enabled in the Developer Portal")
        return EXIT_CONFIG_ERROR

    if bot.restart_requested:
        log.info("Exiting for restart")
        return EXIT_RESTART
    return 0


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    sys.exit(main())
