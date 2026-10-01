"""Fetching and parsing comic feeds (RSS and Atom)."""

from __future__ import annotations

import asyncio
import calendar
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp
import feedparser

from . import __version__

USER_AGENT = f"DiscordComicsBot/{__version__} (+https://github.com/dxcufgb/DiscordComicsBot)"
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)


class FeedError(Exception):
    """A feed could not be fetched or parsed. The message is safe to show users."""


@dataclass(frozen=True)
class FeedEntry:
    id: str
    title: str
    link: str

    @property
    def text(self) -> str:
        return f"{self.title} - {self.link}" if self.title else self.link


def legacy_last_id(last_url: str) -> str:
    """Pre-2.0 versions stored "<title> - <link>"; the entry id is now the link."""
    return last_url.rsplit(" - ", 1)[-1].strip()


def parse_feed(data: bytes) -> list[FeedEntry]:
    """Parse RSS/Atom bytes into entries, newest first."""
    parsed = feedparser.parse(data)
    dated = []
    for index, raw in enumerate(parsed.entries):
        link = (raw.get("link") or "").strip()
        entry_id = link or (raw.get("id") or "").strip()
        if not entry_id:
            continue
        title = " ".join((raw.get("title") or "").split())
        stamp = raw.get("published_parsed") or raw.get("updated_parsed")
        timestamp = calendar.timegm(stamp) if stamp else None
        dated.append((timestamp, index, FeedEntry(entry_id, title, link or entry_id)))

    if not dated:
        if parsed.bozo and parsed.get("bozo_exception"):
            raise FeedError(f"not a valid RSS/Atom feed ({parsed.bozo_exception})")
        raise FeedError("the feed has no entries")

    # Sort by date when every entry has one; otherwise trust the feed's order,
    # which is newest first for practically every comic feed.
    if all(ts is not None for ts, _, _ in dated):
        dated.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _, _, entry in dated]


def new_entries(entries: list[FeedEntry], last_id: str | None, limit: int = 5) -> list[FeedEntry]:
    """Entries newer than ``last_id``, oldest first.

    For a fresh subscription (no ``last_id``), or when ``last_id`` has dropped
    out of the feed, only the newest entry is returned to avoid flooding.
    """
    if not entries:
        return []
    ids = [e.id for e in entries]
    if last_id is None or last_id not in ids:
        return [entries[0]]
    newer = entries[: ids.index(last_id)]
    return list(reversed(newer[:limit]))


async def ensure_public_url(url: str, allow_private: bool = False) -> None:
    """Reject URLs that are not http(s) or that point into a private network."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FeedError("the URL must start with http:// or https://")
    if allow_private:
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname,
            parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError:
        raise FeedError(f"could not resolve host {parts.hostname!r}") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise FeedError("the URL points to a private or local network address")


async def fetch_feed(session: aiohttp.ClientSession, url: str, allow_private: bool = False) -> list[FeedEntry]:
    """Download and parse a feed, checking every redirect hop."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        await ensure_public_url(current, allow_private)
        try:
            async with session.get(current, allow_redirects=False, timeout=REQUEST_TIMEOUT) as resp:
                if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                    current = urljoin(current, resp.headers["Location"])
                    continue
                if resp.status >= 400:
                    raise FeedError(f"the server answered HTTP {resp.status}")
                data = await resp.content.read(MAX_FEED_BYTES + 1)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise FeedError(f"download failed ({type(exc).__name__}: {exc})") from None
        if len(data) > MAX_FEED_BYTES:
            raise FeedError("the feed is larger than 5 MB")
        # feedparser is CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(parse_feed, data)
    raise FeedError("too many redirects")


def new_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
