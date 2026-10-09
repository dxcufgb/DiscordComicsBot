"""Tell server admins by DM when the bot was updated to a new version, with the release notes."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord

from . import __version__
from .updater import UpdateError, is_newer, parse_version

if TYPE_CHECKING:
    from .storage import State

log = logging.getLogger(__name__)

UPDATE_NOTICE = "update-notice.json"  # written by the updater into the data folder, next to state.json
BUNDLED_NOTES = "RELEASE_NOTES.md"  # shipped in the release archives
RELEASES_URL = "https://github.com/dxcufgb/DiscordComicsBot/releases"
NOTIFY_MODES = ("admins", "owner", "off")

DESCRIPTION_LIMIT = 4096
EMBED_LIMIT = 6000
FIELD_LIMIT = 1024
SEND_PAUSE = 1.0  # seconds between DMs


@dataclass(frozen=True)
class ReleaseNotes:
    version: str
    name: str = ""
    body: str = ""
    html_url: str = ""

    @property
    def url(self) -> str:
        return self.html_url or f"{RELEASES_URL}/tag/v{self.version}"


def detect_update(state: State, running: str = __version__) -> str | None:
    """Record the running version; return the previous one if this start is an upgrade.

    The caller saves the state before announcing, so a version is announced at most once.
    The first start that records a version (fresh install, or an upgrade from a version
    without this field) announces nothing.
    """
    recorded = state.bot_version
    if not recorded:
        state.bot_version = running
        return None
    try:
        newer = is_newer(running, recorded)
    except UpdateError:
        log.warning("Ignoring unreadable recorded version %r", recorded[:40])
        state.bot_version = running
        return None
    if not newer:
        # Same version, or an older one (a rollback): keep the highest, so a version that was
        # already announced is never announced again.
        return None
    state.bot_version = running
    return recorded


def load_release_notes(base_dir: Path, version: str = __version__, data_dir: Path | None = None) -> ReleaseNotes:
    """Release notes for ``version``: the updater's notice (deleted once read), else the bundled file.

    The notice is in the data folder (``data_dir``, default ``base_dir``), the bundled notes next to the program.
    """
    notice = (data_dir or base_dir) / UPDATE_NOTICE
    notes = None
    if notice.is_file():
        try:
            data = json.loads(notice.read_text(encoding="utf-8"))
            if isinstance(data, dict) and _same_version(str(data.get("version", "")), version):
                notes = ReleaseNotes(
                    version=version,
                    name=str(data.get("name") or ""),
                    body=str(data.get("body") or ""),
                    html_url=_github_url(data.get("html_url")),
                )
        except (OSError, ValueError):
            log.warning("Could not read %s; ignoring it", notice.name)
        try:
            notice.unlink()
        except OSError:
            log.warning("Could not delete %s", notice.name)
    if notes is None:
        bundled = base_dir / BUNDLED_NOTES
        body = ""
        if bundled.is_file():
            try:
                body = bundled.read_text(encoding="utf-8")
            except OSError:
                log.warning("Could not read %s", bundled.name)
        notes = ReleaseNotes(version=version, body=body)
    return notes


def strip_install_section(body: str) -> str:
    """Drop the leading "## Install" section of the release notes: admins want the changes, not install steps."""
    text = body.strip()
    if re.match(r"##\s+Install\b", text):
        following = re.search(r"^##\s", text[2:], re.M)
        text = text[2 + following.start() :].strip() if following else ""
    return text


def _same_version(a: str, b: str) -> bool:
    try:
        return parse_version(a) == parse_version(b)
    except UpdateError:
        return False


def _github_url(value: Any) -> str:
    return value if isinstance(value, str) and value.startswith("https://github.com/") else ""


def _truncate(text: str, limit: int, suffix: str = "") -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - len(suffix) - 1, 0)].rstrip() + "…" + suffix


def build_embed(notes: ReleaseNotes, servers: Iterable[str]) -> discord.Embed:
    title = f"ComicsBot updated to v{notes.version}"
    names = sorted(servers, key=str.casefold)
    field_name = "Your server" if len(names) == 1 else "Your servers"
    field_value = _truncate(", ".join(names) or "-", FIELD_LIMIT)
    footer = "You get this because you administer a server that uses ComicsBot."
    used = len(title) + len(field_name) + len(field_value) + len(footer)
    limit = min(DESCRIPTION_LIMIT, EMBED_LIMIT - used)
    body = strip_install_section(notes.body)
    if body:
        description = _truncate(body, limit, f"\n\n[Full release notes]({notes.url})")
    else:
        description = f"[Release on GitHub]({notes.url})"
    embed = discord.Embed(title=title, url=notes.url, description=description, colour=discord.Colour.blurple())
    embed.add_field(name=field_name, value=field_value, inline=False)
    embed.set_footer(text=footer)
    return embed


def collect_recipients(guilds: Iterable[Any], mode: str) -> dict[int, list[str]]:
    """Map user id -> names of the servers they administer (owner, plus Administrator members)."""
    recipients: dict[int, list[str]] = {}

    def add(user_id: int | None, guild: Any) -> None:
        if user_id and guild.name not in recipients.setdefault(user_id, []):
            recipients[user_id].append(guild.name)

    if mode == "off":
        return recipients
    for guild in guilds:
        add(guild.owner_id, guild)
        if mode == "admins":
            for member in guild.members:
                if not member.bot and member.guild_permissions.administrator:
                    add(member.id, guild)
    return recipients


async def send_announcements(
    client: discord.Client,
    notes: ReleaseNotes,
    recipients: dict[int, list[str]],
    pause: float = SEND_PAUSE,
) -> int:
    """DM every recipient one embed; return how many were sent. Failures are logged and skipped."""
    sent = 0
    for user_id, servers in recipients.items():
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
            await user.send(embed=build_embed(notes, servers), allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            log.info("Could not DM user %s about the update (DMs closed or no shared server)", user_id)
        except discord.HTTPException as exc:
            log.warning("Could not DM user %s about the update: HTTP %s", user_id, exc.status)
        except Exception:
            log.exception("Could not DM user %s about the update", user_id)
        else:
            sent += 1
        await asyncio.sleep(pause)
    return sent
