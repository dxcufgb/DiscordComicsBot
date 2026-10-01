"""The Discord client: feed polling and delivery."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

from .config import DEFAULT_INTERVAL, Config
from .feeds import FeedEntry, FeedError, fetch_feed, new_entries, new_session
from .storage import State

log = logging.getLogger(__name__)

CHRISTMAS_GIF = "https://tenor.com/view/merry-christmas-gif-church-gif-10346745427738870662"
MAX_CONCURRENT_FETCHES = 4


class ComicsBot(discord.Client):
    def __init__(self, config: Config, state: State) -> None:
        # The bot has no voice features; skip the "PyNaCl/davey not installed" warnings.
        discord.VoiceClient.warn_nacl = False
        discord.VoiceClient.warn_dave = False
        # Slash commands need no privileged intents (no message content).
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Activity(type=discord.ActivityType.watching, name="for new comics"),
        )
        self.config = config
        self.state = state
        self.tree = app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None
        self.owner_ids: set[int] = set(config.owner_ids)
        self.restart_requested = False
        self._check_lock = asyncio.Lock()
        self._background: set[asyncio.Task[None]] = set()

    # -- lifecycle --------------------------------------------------------

    async def setup_hook(self) -> None:
        from .commands import register_commands

        self.session = new_session()
        if not self.owner_ids:
            info = await self.application_info()
            if info.team:
                self.owner_ids = {member.id for member in info.team.members}
            else:
                self.owner_ids = {info.owner.id}

        register_commands(self)
        synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

        self.poll_feeds.change_interval(seconds=self.config.interval)
        self.poll_feeds.start()

    async def close(self) -> None:
        self.poll_feeds.cancel()
        await super().close()
        if self.session:
            await self.session.close()

    async def on_ready(self) -> None:
        log.info(
            "Logged in as %s in %d servers, checking feeds every %d seconds",
            self.user,
            len(self.guilds),
            self.config.interval,
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined server %s (%d)", guild.name, guild.id)
        channel = guild.system_channel
        if channel and channel.permissions_for(guild.me).send_messages:
            await channel.send(
                "Hello! I'm ComicsBot.\n"
                "An admin can pick the channel for comics with `/comics-admin channel-set`, "
                "then anyone can use `/comics available` and `/comics subscribe`.\n"
                "Type `/comics help` for all commands."
            )

    def run_in_background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background_done)

    def _background_done(self, task: asyncio.Task[None]) -> None:
        self._background.discard(task)
        if not task.cancelled() and task.exception():
            log.error("Background task failed", exc_info=task.exception())

    # -- feed polling -----------------------------------------------------

    @tasks.loop(seconds=DEFAULT_INTERVAL)
    async def poll_feeds(self) -> None:
        try:
            await self.run_check()
        except Exception:
            log.exception("Feed check failed")

    @poll_feeds.before_loop
    async def _before_poll(self) -> None:
        await self.wait_until_ready()

    async def run_check(self, *, guild_id: int | None = None, comic: str | None = None) -> None:
        """Fetch feeds and post new entries.

        Without arguments, checks every subscribed comic for every server.
        ``guild_id`` / ``comic`` narrow the check, e.g. right after subscribing.
        """
        async with self._check_lock:
            names = {comic} if comic else self.state.subscribed_comics()
            names &= set(self.state.comics)
            log.debug("Checking %d feeds", len(names))
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

            async def fetch(name: str) -> tuple[str, list[FeedEntry] | None]:
                url = self.state.comics[name]["url"]
                async with semaphore:
                    try:
                        entries = await fetch_feed(self.session, url, self.config.allow_private_feed_urls)
                    except FeedError as exc:
                        log.warning("Could not fetch %s (%s): %s", name, url, exc)
                        return name, None
                    except Exception:
                        log.exception("Unexpected error fetching %s (%s)", name, url)
                        return name, None
                return name, entries

            results = dict(await asyncio.gather(*(fetch(n) for n in sorted(names))))

            for guild in self.guilds:
                if guild_id is None or guild.id == guild_id:
                    await self._deliver(guild, results)

            if guild_id is None and comic is None:
                await self._maybe_send_christmas_greeting()

    def comics_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        guild_state = self.state.guilds.get(str(guild.id))
        channel_id = guild_state and guild_state.get("channel_id")
        if not channel_id:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("Comics channel %s in %s no longer exists", channel_id, guild.name)
            return None
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages):
            log.warning("Missing permission to post in #%s in %s", channel.name, guild.name)
            return None
        return channel

    async def _deliver(self, guild: discord.Guild, results: dict[str, list[FeedEntry] | None]) -> None:
        guild_state = self.state.guilds.get(str(guild.id))
        if not guild_state or not guild_state["subscriptions"]:
            return
        channel = self.comics_channel(guild)
        if channel is None:
            return
        for name, sub in list(guild_state["subscriptions"].items()):
            entries = results.get(name)
            if not entries:
                continue
            for entry in new_entries(entries, sub.get("last_id")):
                try:
                    await channel.send(entry.text)
                except discord.Forbidden:
                    log.warning("Forbidden to post in #%s in %s", channel.name, guild.name)
                    return
                except discord.HTTPException:
                    log.exception("Failed to post %s in %s", name, guild.name)
                    break
                log.info("Posted %s to %s: %s", name, guild.name, entry.link)
                sub["last_id"] = entry.id
                self.state.save()

    async def _maybe_send_christmas_greeting(self) -> None:
        if not self.config.christmas_greeting:
            return
        now = datetime.now()
        in_window = now.month == 12 and (now.day in (25, 26) or (now.day == 24 and now.hour >= 12))
        if not in_window or self.state.christmas_year == now.year:
            return
        self.state.christmas_year = now.year
        self.state.save()
        for guild in self.guilds:
            channel = self.comics_channel(guild)
            if channel:
                try:
                    await channel.send(CHRISTMAS_GIF)
                except discord.HTTPException:
                    log.exception("Failed to send Christmas greeting to %s", guild.name)
