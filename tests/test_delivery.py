"""Fetch -> post -> persist, using a local feed server and a fake channel."""

import asyncio
from types import SimpleNamespace

from aiohttp import web

from comicsbot.bot import ComicsBot
from comicsbot.config import Config
from comicsbot.feeds import new_session
from comicsbot.storage import State

ITEM = "<item><title>{0}</title><link>https://example.com/{0}</link></item>"


def feed(*names):
    items = "".join(ITEM.format(n) for n in names)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


def test_check_posts_only_new_entries(tmp_path, monkeypatch):
    async def scenario():
        current = {"body": feed("two", "one")}

        async def handler(request):
            return web.Response(text=current["body"], content_type="application/rss+xml")

        async def redirect(request):
            raise web.HTTPFound("/feed")

        app = web.Application()
        app.router.add_get("/feed", handler)
        app.router.add_get("/redirect", redirect)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        state = State(tmp_path / "state.json")
        state.comics["Strip"] = {"url": f"http://127.0.0.1:{port}/redirect"}
        state.guild(1)["subscriptions"]["Strip"] = {"last_id": None}
        config = Config(token="x", base_dir=tmp_path, allow_private_feed_urls=True, christmas_greeting=False)
        bot = ComicsBot(config, state)
        bot.session = new_session()
        channel = FakeChannel()
        guild = SimpleNamespace(id=1, name="Test")
        monkeypatch.setattr(ComicsBot, "guilds", property(lambda self: [guild]))
        monkeypatch.setattr(bot, "comics_channel", lambda g: channel)

        try:
            await bot.run_check()  # new subscription: latest only
            assert channel.sent == ["two - https://example.com/two"]
            await bot.run_check()  # nothing new
            assert len(channel.sent) == 1
            current["body"] = feed("four", "three", "two", "one")
            await bot.run_check()  # two new strips, oldest first
            assert channel.sent[1:] == ["three - https://example.com/three", "four - https://example.com/four"]
            saved = State.load(tmp_path / "state.json")
            assert saved.guilds["1"]["subscriptions"]["Strip"]["last_id"] == "https://example.com/four"
        finally:
            await bot.session.close()
            await runner.cleanup()

    asyncio.run(scenario())
