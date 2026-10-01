"""Slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from .feeds import FeedError, fetch_feed

if TYPE_CHECKING:
    from .bot import ComicsBot

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 2000
NAME_MAX_LENGTH = 100


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split text on line breaks into chunks Discord accepts."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or ["(nothing to show)"]


async def reply(interaction: discord.Interaction, text: str, *, ephemeral: bool = False) -> None:
    for chunk in split_message(text):
        if interaction.response.is_done():
            await interaction.followup.send(chunk, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(chunk, ephemeral=ephemeral)


def is_bot_owner(interaction: discord.Interaction) -> bool:
    if interaction.user.id not in interaction.client.owner_ids:
        raise app_commands.CheckFailure("Only the bot owner can use this command.")
    return True


def channel_hint(bot: ComicsBot, guild: discord.Guild) -> str:
    channel = bot.comics_channel(guild)
    if channel:
        return f"New strips are posted in {channel.mention}."
    return (
        "No comics channel is set for this server yet, so nothing will be posted until "
        "an admin runs `/comics-admin channel-set`."
    )


def _choices(names, current: str) -> list[app_commands.Choice[str]]:
    current = current.casefold()
    matches = sorted((n for n in names if current in n.casefold()), key=str.casefold)
    return [app_commands.Choice(name=n[:100], value=n) for n in matches[:25]]


class ComicsGroup(app_commands.Group):
    def __init__(self, bot: ComicsBot) -> None:
        super().__init__(name="comics", description="Subscribe to comics in this server", guild_only=True)
        self.bot = bot

    @app_commands.command(description="List all comics the bot knows about")
    async def available(self, interaction: discord.Interaction) -> None:
        state = self.bot.state
        if not state.comics:
            await reply(interaction, "No comics have been added yet. An admin can add one with `/comics-admin add`.")
            return
        subscribed = state.guild(interaction.guild_id)["subscriptions"]
        lines = ["**Available comics** (✅ = subscribed in this server)"]
        for name in sorted(state.comics, key=str.casefold):
            mark = "✅" if name in subscribed else "▫️"
            lines.append(f"{mark} **{name}**: <{state.comics[name]['url']}>")
        await reply(interaction, "\n".join(lines))

    @app_commands.command(description="List the comics this server is subscribed to")
    async def subscriptions(self, interaction: discord.Interaction) -> None:
        state = self.bot.state
        subscribed = sorted(state.guild(interaction.guild_id)["subscriptions"], key=str.casefold)
        if not subscribed:
            await reply(interaction, "This server has no subscriptions yet. See `/comics available`.")
            return
        lines = ["**Subscribed comics**"]
        lines += [f"• **{name}**: <{state.comics[name]['url']}>" for name in subscribed if name in state.comics]
        lines.append(channel_hint(self.bot, interaction.guild))
        await reply(interaction, "\n".join(lines))

    @app_commands.command(description="Subscribe this server to a comic")
    @app_commands.describe(name="Comic name from /comics available")
    async def subscribe(
        self, interaction: discord.Interaction, name: app_commands.Range[str, 1, NAME_MAX_LENGTH]
    ) -> None:
        state = self.bot.state
        comic = state.find_comic(name.strip())
        if comic is None:
            await reply(
                interaction, f'Could not find a comic called "{name}". See `/comics available`.', ephemeral=True
            )
            return
        subscriptions = state.guild(interaction.guild_id)["subscriptions"]
        if comic in subscriptions:
            await reply(interaction, f'This server is already subscribed to "{comic}".', ephemeral=True)
            return
        subscriptions[comic] = {"last_id": None}
        state.save()
        await reply(interaction, f'Subscribed to "{comic}"! {channel_hint(self.bot, interaction.guild)}')
        self.bot.run_in_background(self.bot.run_check(guild_id=interaction.guild_id, comic=comic))

    @subscribe.autocomplete("name")
    async def _subscribe_autocomplete(self, interaction: discord.Interaction, current: str):
        subscribed = self.bot.state.guild(interaction.guild_id)["subscriptions"]
        return _choices((n for n in self.bot.state.comics if n not in subscribed), current)

    @app_commands.command(description="Unsubscribe this server from a comic")
    @app_commands.describe(name="Comic name from /comics subscriptions")
    async def unsubscribe(
        self, interaction: discord.Interaction, name: app_commands.Range[str, 1, NAME_MAX_LENGTH]
    ) -> None:
        state = self.bot.state
        subscriptions = state.guild(interaction.guild_id)["subscriptions"]
        comic = state.find_comic(name.strip()) or name.strip()
        if subscriptions.pop(comic, None) is None:
            await reply(interaction, f'This server is not subscribed to "{name}".', ephemeral=True)
            return
        state.save()
        await reply(interaction, f'Unsubscribed from "{comic}".')

    @unsubscribe.autocomplete("name")
    async def _unsubscribe_autocomplete(self, interaction: discord.Interaction, current: str):
        return _choices(self.bot.state.guild(interaction.guild_id)["subscriptions"], current)

    @app_commands.command(description="Show how to use ComicsBot")
    async def help(self, interaction: discord.Interaction) -> None:
        text = (
            "**ComicsBot** posts new comic strips from RSS/Atom feeds.\n\n"
            "**Everyone**\n"
            "`/comics available`: all comics the bot knows about\n"
            "`/comics subscriptions`: comics this server is subscribed to\n"
            "`/comics subscribe <name>`: subscribe this server to a comic\n"
            "`/comics unsubscribe <name>`: unsubscribe from a comic\n\n"
            "**Admins** (Manage Server permission)\n"
            "`/comics-admin channel-set [channel]`: post comics in this (or the given) channel\n"
            "`/comics-admin channel-show`: show the comics channel\n"
            "`/comics-admin channel-clear`: stop posting comics in this server\n"
            "`/comics-admin add <name> <url>`: add a comic feed and subscribe to it\n\n"
            f"{channel_hint(self.bot, interaction.guild)}\n\n"
            f"Created by Dxcufgb (2022 - {discord.utils.utcnow().year})"
        )
        await reply(interaction, text, ephemeral=True)


class AdminGroup(app_commands.Group):
    def __init__(self, bot: ComicsBot) -> None:
        super().__init__(
            name="comics-admin",
            description="Configure ComicsBot for this server",
            guild_only=True,
            default_permissions=discord.Permissions(manage_guild=True),
        )
        self.bot = bot

    @app_commands.command(name="channel-set", description="Post comics in this channel (or the given one)")
    @app_commands.describe(channel="Channel to post comics in (defaults to the current channel)")
    async def channel_set(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await reply(interaction, "Please run this in, or pick, a normal text channel.", ephemeral=True)
            return
        perms = target.permissions_for(interaction.guild.me)
        missing = [
            label
            for label, ok in (
                ("View Channel", perms.view_channel),
                ("Send Messages", perms.send_messages),
                ("Embed Links", perms.embed_links),
            )
            if not ok
        ]
        if missing:
            await reply(
                interaction,
                f"I need these permissions in {target.mention} first: {', '.join(missing)}.",
                ephemeral=True,
            )
            return
        self.bot.state.guild(interaction.guild_id)["channel_id"] = target.id
        self.bot.state.save()
        await reply(interaction, f"Comics will now be posted in {target.mention}.")
        self.bot.run_in_background(self.bot.run_check(guild_id=interaction.guild_id))

    @app_commands.command(name="channel-show", description="Show the channel comics are posted in")
    async def channel_show(self, interaction: discord.Interaction) -> None:
        await reply(interaction, channel_hint(self.bot, interaction.guild), ephemeral=True)

    @app_commands.command(name="channel-clear", description="Stop posting comics in this server")
    async def channel_clear(self, interaction: discord.Interaction) -> None:
        self.bot.state.guild(interaction.guild_id)["channel_id"] = None
        self.bot.state.save()
        await reply(
            interaction,
            "Comics channel removed; nothing will be posted until `/comics-admin channel-set` is run again.",
        )

    @app_commands.command(description="Add a comic feed to the bot and subscribe this server to it")
    @app_commands.describe(name="Name for the comic", url="URL of the comic's RSS or Atom feed")
    async def add(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, NAME_MAX_LENGTH],
        url: app_commands.Range[str, 1, 2000],
    ) -> None:
        state = self.bot.state
        name = " ".join(name.split())
        url = url.strip()
        subscriptions = state.guild(interaction.guild_id)["subscriptions"]

        existing = state.find_comic_by_url(url)
        if existing:
            subscriptions.setdefault(existing, {"last_id": None})
            state.save()
            await reply(interaction, f'That feed already exists as "{existing}"; this server is now subscribed to it.')
            self.bot.run_in_background(self.bot.run_check(guild_id=interaction.guild_id, comic=existing))
            return
        if state.find_comic(name):
            await reply(interaction, f'A comic called "{name}" already exists. Pick another name.', ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            entries = await fetch_feed(self.bot.session, url, self.bot.config.allow_private_feed_urls)
        except FeedError as exc:
            await reply(interaction, f"Could not add that feed: {exc}.")
            return
        # Re-check: another add may have finished while the feed was downloading.
        if state.find_comic(name) or state.find_comic_by_url(url):
            await reply(interaction, f'"{name}" was added by someone else just now.')
            return
        state.comics[name] = {"url": url}
        subscriptions[name] = {"last_id": None}
        state.save()
        log.info("%s added comic %s (%s) in %s", interaction.user, name, url, interaction.guild)
        await reply(
            interaction,
            f'Added "{name}" ({len(entries)} entries in the feed) and subscribed this server. '
            + channel_hint(self.bot, interaction.guild),
        )
        self.bot.run_in_background(self.bot.run_check(guild_id=interaction.guild_id, comic=name))

    @app_commands.command(description="Bot owner: remove a comic from the bot for every server")
    @app_commands.check(is_bot_owner)
    async def delete(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, NAME_MAX_LENGTH]) -> None:
        state = self.bot.state
        comic = state.find_comic(name.strip())
        if comic is None:
            await reply(interaction, f'No comic called "{name}".', ephemeral=True)
            return
        del state.comics[comic]
        for guild_state in state.guilds.values():
            guild_state["subscriptions"].pop(comic, None)
        state.save()
        await reply(interaction, f'Removed "{comic}" from the bot.', ephemeral=True)

    @delete.autocomplete("name")
    async def _delete_autocomplete(self, interaction: discord.Interaction, current: str):
        return _choices(self.bot.state.comics, current)

    @app_commands.command(name="check-now", description="Bot owner: check all feeds right away")
    @app_commands.check(is_bot_owner)
    async def check_now(self, interaction: discord.Interaction) -> None:
        await reply(interaction, "Checking feeds...", ephemeral=True)
        self.bot.run_in_background(self.bot.run_check())

    @app_commands.command(description="Bot owner: restart the bot")
    @app_commands.check(is_bot_owner)
    async def restart(self, interaction: discord.Interaction) -> None:
        await reply(interaction, "Restarting...", ephemeral=True)
        log.warning("Restart requested by %s", interaction.user)
        self.bot.restart_requested = True
        await self.bot.close()


def register_commands(bot: ComicsBot) -> None:
    bot.tree.add_command(ComicsGroup(bot))
    bot.tree.add_command(AdminGroup(bot))

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = str(error) or "You can't use this command."
        else:
            log.exception(
                "Command %s failed", interaction.command and interaction.command.qualified_name, exc_info=error
            )
            message = "Something went wrong running that command; the error has been logged."
        try:
            await reply(interaction, message, ephemeral=True)
        except discord.HTTPException:
            pass
