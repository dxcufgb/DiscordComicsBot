"""Configuration loaded from the environment / .env file."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

log = logging.getLogger(__name__)

# Exit code used when the configuration is unusable; the start scripts do not
# restart the bot when they see it (it would only fail again).
EXIT_CONFIG_ERROR = 78

DEFAULT_INTERVAL = 3600
MIN_INTERVAL = 60
# Folder for everything the bot writes, inside the program folder. The Linux installer creates it (owned by
# the bot's account, while the program folder belongs to root); without it the bot writes next to its program
# files, as before 2.3 (Windows installs and runs from source).
DATA_DIR_NAME = "data"

# Versions before 2.0 rewrote .env as "KEY:valueKEY:value" on one line, which
# python-dotenv cannot parse. This pattern recovers the values from such a file.
_LEGACY_ENV_PATTERN = re.compile(
    r"(TOKEN|COMICS-CHANNEL-\d+|INTERVAL|LOG-LEVEL|CHRISTMAS_WISH):"
    r"(.*?)(?=(?:TOKEN|COMICS-CHANNEL-\d+|INTERVAL|LOG-LEVEL|CHRISTMAS_WISH):|\s*$)",
    re.S,
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    token: str
    base_dir: Path
    interval: int = DEFAULT_INTERVAL
    log_level: str = "INFO"
    owner_ids: frozenset[int] = frozenset()
    christmas_greeting: bool = True
    allow_private_feed_urls: bool = False
    # Who gets a DM after an update: "admins" (owner + Administrator members), "owner" or "off".
    update_notify: str = "admins"
    # COMICS-CHANNEL-<guild id> values from a pre-2.0 .env, used once to migrate.
    legacy_channels: dict[int, int] = field(default_factory=dict)
    # Where the bot writes state.json, discord.log, bot.lock and reads update-notice.json
    # (None: see default_data_dir).
    data_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.data_dir is None:
            object.__setattr__(self, "data_dir", default_data_dir(self.base_dir))

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "discord.log"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "bot.lock"


def default_data_dir(base_dir: Path) -> Path:
    """``<base_dir>/data`` when that folder exists (Linux installs), else ``base_dir`` itself."""
    data_dir = base_dir / DATA_DIR_NAME
    return data_dir if data_dir.is_dir() else base_dir


def parse_legacy_env(text: str) -> dict[str, str]:
    """Recover key/value pairs from a .env file written by the old updateEnv()."""
    return {m.group(1): m.group(2).strip() for m in _LEGACY_ENV_PATTERN.finditer(text)}


def repair_env_file(env_file: Path) -> None:
    """Rewrite a .env corrupted by pre-2.0 versions into normal KEY=value lines."""
    if not env_file.is_file():
        return
    text = env_file.read_text(encoding="utf-8")
    if any("=" in line for line in text.splitlines() if not line.lstrip().startswith("#")):
        return  # already a normal dotenv file
    recovered = parse_legacy_env(text)
    if "TOKEN" not in recovered:
        return
    backup = env_file.with_name(env_file.name + ".broken-backup")
    shutil.copy2(env_file, backup)  # keeps the restrictive permissions of the original
    lines = [f"DISCORD_TOKEN={recovered.pop('TOKEN')}"]
    lines += [f"{key}={value}" for key, value in recovered.items() if key != "CHRISTMAS_WISH"]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.warning("Repaired corrupted %s (original saved as %s)", env_file, backup.name)


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(base_dir: Path) -> Config:
    env_file = base_dir / ".env"
    repair_env_file(env_file)
    load_dotenv(env_file)
    env = {**dotenv_values(env_file), **os.environ}

    token = (env.get("DISCORD_TOKEN") or env.get("TOKEN") or "").strip()
    if not token:
        raise ConfigError(f"DISCORD_TOKEN is not set (add it to {env_file}, see .env.example)")

    raw_interval = (env.get("INTERVAL") or str(DEFAULT_INTERVAL)).strip()
    try:
        interval = int(raw_interval)
    except ValueError:
        raise ConfigError(f"INTERVAL must be a number of seconds, got {raw_interval!r}") from None
    interval = max(interval, MIN_INTERVAL)

    log_level = (env.get("LOG_LEVEL") or env.get("LOG-LEVEL") or "INFO").strip().upper()
    if not isinstance(logging.getLevelName(log_level), int):
        raise ConfigError(f"LOG_LEVEL {log_level!r} is not a valid level")

    owner_ids = set()
    for part in (env.get("OWNER_IDS") or env.get("OWNER_ID") or "").replace(",", " ").split():
        if not part.isdigit():
            raise ConfigError(f"OWNER_IDS must be Discord user IDs, got {part!r}")
        owner_ids.add(int(part))

    update_notify = (env.get("UPDATE_NOTIFY") or "admins").strip().lower()
    if update_notify not in ("admins", "owner", "off"):
        raise ConfigError(f"UPDATE_NOTIFY must be admins, owner or off, got {update_notify!r}")

    raw_data_dir = (env.get("DATA_DIR") or "").strip()
    data_dir = base_dir / Path(raw_data_dir).expanduser() if raw_data_dir else default_data_dir(base_dir)

    legacy_channels = {}
    for key, value in env.items():
        if key.startswith("COMICS-CHANNEL-") and value and value.strip().isdigit():
            guild_id = key.removeprefix("COMICS-CHANNEL-")
            if guild_id.isdigit():
                legacy_channels[int(guild_id)] = int(value.strip())

    return Config(
        token=token,
        base_dir=base_dir,
        interval=interval,
        log_level=log_level,
        owner_ids=frozenset(owner_ids),
        christmas_greeting=_bool(env.get("CHRISTMAS_GREETING"), True),
        allow_private_feed_urls=_bool(env.get("ALLOW_PRIVATE_FEED_URLS"), False),
        update_notify=update_notify,
        legacy_channels=legacy_channels,
        data_dir=data_dir,
    )
