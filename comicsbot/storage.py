"""Persistent bot state (comic catalogue, per-server settings) in state.json."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from .feeds import legacy_last_id

log = logging.getLogger(__name__)

STATE_VERSION = 1


class State:
    """All mutable bot state. Every change is followed by ``save()``.

    Layout of state.json::

        {
          "version": 1,
          "comics": {"<name>": {"url": "<feed url>"}},
          "guilds": {"<guild id>": {"channel_id": <int|null>,
                                    "subscriptions": {"<name>": {"last_id": <str|null>}}}},
          "christmas_year": <int|null>,
          "bot_version": <str|null>   # version that last ran, to notice upgrades (added in 2.3)
        }
    """

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        data = data or {}
        self.comics: dict[str, dict[str, Any]] = data.get("comics", {})
        self.guilds: dict[str, dict[str, Any]] = data.get("guilds", {})
        self.christmas_year: int | None = data.get("christmas_year")
        version = data.get("bot_version")
        self.bot_version: str | None = version if isinstance(version, str) else None

    # -- loading / saving -------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> State:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != STATE_VERSION:
            raise ValueError(f"{path}: unsupported state version {data.get('version')!r}")
        return cls(path, data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "comics": self.comics,
            "guilds": self.guilds,
            "christmas_year": self.christmas_year,
            "bot_version": self.bot_version,
        }

    def save(self) -> None:
        """Write atomically so a crash never leaves a half-written file."""
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- helpers ----------------------------------------------------------

    def guild(self, guild_id: int) -> dict[str, Any]:
        return self.guilds.setdefault(str(guild_id), {"channel_id": None, "subscriptions": {}})

    def find_comic(self, name: str) -> str | None:
        """Return the catalogue name matching ``name`` case-insensitively."""
        if name in self.comics:
            return name
        lowered = name.casefold()
        return next((n for n in self.comics if n.casefold() == lowered), None)

    def find_comic_by_url(self, url: str) -> str | None:
        normalized = url.rstrip("/")
        return next((n for n, c in self.comics.items() if c["url"].rstrip("/") == normalized), None)

    def subscribed_comics(self) -> set[str]:
        return {name for g in self.guilds.values() for name in g["subscriptions"] if name in self.comics}


def load_or_migrate(path: Path, legacy_channels: dict[int, int]) -> State:
    """Load state.json, or build it from the files used before version 2.0."""
    if path.exists():
        return State.load(path)

    base = path.parent
    state = State(path)

    legacy_catalogue = base / "comics.json"
    if legacy_catalogue.is_file():
        for name, comic in _read_json(legacy_catalogue).items():
            if isinstance(comic, dict) and comic.get("URL"):
                state.comics[name] = {"url": comic["URL"]}

    for guild_file in sorted(base.glob("*-Comics.json")):
        guild_id = guild_file.name.removesuffix("-Comics.json")
        if not guild_id.isdigit():
            continue
        guild = state.guild(int(guild_id))
        for name, sub in _read_json(guild_file).items():
            if name in state.comics:
                last = sub.get("LastURL") if isinstance(sub, dict) else None
                guild["subscriptions"][name] = {"last_id": legacy_last_id(last) if last else None}

    for guild_id, channel_id in legacy_channels.items():
        state.guild(guild_id)["channel_id"] = channel_id

    state.save()
    if state.comics or state.guilds:
        log.warning(
            "Migrated %d comics and %d servers from the old data files into %s. "
            "The old comics.json / *-Comics.json files are no longer used.",
            len(state.comics),
            len(state.guilds),
            path.name,
        )
    return state


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read %s, skipping it", path)
        return {}
    return data if isinstance(data, dict) else {}
