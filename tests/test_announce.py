"""Update notices: version detection, release notes, recipients, embeds and sending."""

import asyncio
import json
import logging
from types import SimpleNamespace

import discord
import pytest

from comicsbot import announce
from comicsbot.bot import ComicsBot
from comicsbot.config import Config
from comicsbot.storage import State

# -- version detection ------------------------------------------------------------


def test_first_run_records_the_version_without_announcing(tmp_path):
    state = State(tmp_path / "state.json")
    assert announce.detect_update(state, "2.3.0") is None
    assert state.bot_version == "2.3.0"


def test_upgrade_is_detected_once(tmp_path):
    state = State(tmp_path / "state.json")
    state.bot_version = "2.2.0"
    assert announce.detect_update(state, "2.3.0") == "2.2.0"
    assert state.bot_version == "2.3.0"
    assert announce.detect_update(state, "2.3.0") is None


def test_rollback_keeps_the_highest_version(tmp_path):
    state = State(tmp_path / "state.json")
    state.bot_version = "2.3.0"
    assert announce.detect_update(state, "2.2.0") is None
    assert state.bot_version == "2.3.0"
    # Upgrading to 2.3.0 again does not announce it a second time.
    assert announce.detect_update(state, "2.3.0") is None


def test_unreadable_recorded_version_is_replaced(tmp_path):
    state = State(tmp_path / "state.json")
    state.bot_version = "garbage"
    assert announce.detect_update(state, "2.3.0") is None
    assert state.bot_version == "2.3.0"


def test_state_round_trips_the_version_and_loads_old_files(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "comics": {}, "guilds": {}, "christmas_year": None}))
    state = State.load(path)
    assert state.bot_version is None
    state.bot_version = "2.3.0"
    state.save()
    assert State.load(path).bot_version == "2.3.0"


# -- release notes ----------------------------------------------------------------


def write_notice(base, version, **extra):
    data = {"version": version, "name": f"ComicsBot {version}", "body": "## What's Changed\n* stuff", **extra}
    (base / announce.UPDATE_NOTICE).write_text(json.dumps(data))


def test_matching_notice_is_used_and_deleted(tmp_path):
    write_notice(tmp_path, "2.3.0", html_url="https://github.com/dxcufgb/DiscordComicsBot/releases/tag/v2.3.0")
    notes = announce.load_release_notes(tmp_path, "2.3.0")
    assert notes.body.startswith("## What's Changed")
    assert notes.url == "https://github.com/dxcufgb/DiscordComicsBot/releases/tag/v2.3.0"
    assert not (tmp_path / announce.UPDATE_NOTICE).exists()


def test_stale_notice_falls_back_to_bundled_notes(tmp_path):
    write_notice(tmp_path, "2.2.0")
    (tmp_path / announce.BUNDLED_NOTES).write_text("## Install\nbundled")
    notes = announce.load_release_notes(tmp_path, "2.3.0")
    assert notes.body == "## Install\nbundled"
    assert notes.url.endswith("/releases/tag/v2.3.0")
    assert not (tmp_path / announce.UPDATE_NOTICE).exists()


def test_notice_link_must_point_at_github(tmp_path):
    write_notice(tmp_path, "2.3.0", html_url="https://evil.example/")
    assert announce.load_release_notes(tmp_path, "2.3.0").url.startswith(announce.RELEASES_URL)


def test_no_notes_at_all(tmp_path):
    (tmp_path / announce.UPDATE_NOTICE).write_text("{not json")
    notes = announce.load_release_notes(tmp_path, "2.3.0")
    assert notes.body == ""
    embed = announce.build_embed(notes, ["A"])
    assert embed.title == "ComicsBot updated to v2.3.0"
    assert embed.description == "[Release on GitHub](https://github.com/dxcufgb/DiscordComicsBot/releases/tag/v2.3.0)"


# -- recipients ---------------------------------------------------------------------


def member(user_id, admin, bot=False):
    perms = discord.Permissions(administrator=admin)
    return SimpleNamespace(id=user_id, bot=bot, guild_permissions=perms)


GUILDS = [
    SimpleNamespace(name="Alpha", owner_id=1, members=[member(1, True), member(2, True), member(3, False)]),
    SimpleNamespace(name="Beta", owner_id=2, members=[member(2, True), member(9, True, bot=True)]),
]


def test_admins_are_owners_plus_administrators_deduplicated():
    assert announce.collect_recipients(GUILDS, "admins") == {1: ["Alpha"], 2: ["Alpha", "Beta"]}


def test_owner_and_off_modes():
    assert announce.collect_recipients(GUILDS, "owner") == {1: ["Alpha"], 2: ["Beta"]}
    assert announce.collect_recipients(GUILDS, "off") == {}


# -- embed ---------------------------------------------------------------------------


def test_long_notes_are_truncated_with_a_link():
    notes = announce.ReleaseNotes(version="2.3.0", body="x" * 10_000)
    embed = announce.build_embed(notes, [f"Server {i}" for i in range(500)])
    assert len(embed.description) <= announce.DESCRIPTION_LIMIT
    assert embed.description.endswith(
        "[Full release notes](https://github.com/dxcufgb/DiscordComicsBot/releases/tag/v2.3.0)"
    )
    assert "…" in embed.description
    assert len(embed) <= announce.EMBED_LIMIT
    assert len(embed.fields[0].value) <= announce.FIELD_LIMIT


def test_short_notes_are_kept():
    embed = announce.build_embed(announce.ReleaseNotes(version="2.3.0", body="Fixed things"), ["Beta", "alpha"])
    assert embed.description == "Fixed things"
    assert embed.fields[0].value == "alpha, Beta"


# -- sending -------------------------------------------------------------------------


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="nope"), "message")


class FakeUser:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def send(self, **kwargs):
        if self.error:
            raise self.error
        self.sent.append(kwargs)


class FakeClient:
    def __init__(self, users):
        self.users = users

    def get_user(self, user_id):
        return self.users.get(user_id) if user_id != 3 else None

    async def fetch_user(self, user_id):
        if user_id not in self.users:
            raise http_error(discord.NotFound, 404)
        return self.users[user_id]


def test_failures_are_logged_and_do_not_stop_the_loop(caplog):
    users = {
        1: FakeUser(http_error(discord.Forbidden, 403)),
        2: FakeUser(http_error(discord.HTTPException, 500)),
        3: FakeUser(),  # not cached: fetched
        5: FakeUser(),
    }
    recipients = {1: ["A"], 2: ["A"], 3: ["B"], 4: ["B"], 5: ["A", "B"]}
    caplog.set_level(logging.INFO)
    notes = announce.ReleaseNotes(version="2.3.0", body="secret-free notes")
    sent = asyncio.run(announce.send_announcements(FakeClient(users), notes, recipients, pause=0))
    assert sent == 2
    assert users[3].sent and users[5].sent
    assert users[5].sent[0]["embed"].fields[0].value == "A, B"
    assert users[5].sent[0]["allowed_mentions"].everyone is False
    assert "secret-free notes" not in caplog.text
    assert "DMs closed" in caplog.text


# -- the bot ---------------------------------------------------------------------------


def make_bot(tmp_path, monkeypatch, **config):
    state = State(tmp_path / "state.json")
    bot = ComicsBot(Config(token="x", base_dir=tmp_path, christmas_greeting=False, **config), state)
    monkeypatch.setattr(ComicsBot, "guilds", property(lambda self: GUILDS))
    sent = []

    async def fake_send(client, notes, recipients, pause=0):
        sent.append((notes, recipients))
        return len(recipients)

    monkeypatch.setattr(announce, "send_announcements", fake_send)
    return bot, state, sent


def run_ready(bot):
    async def scenario():
        await bot.on_ready()
        await bot.on_ready()  # a reconnect must not announce again
        await asyncio.gather(*bot._background)

    asyncio.run(scenario())


def test_on_ready_announces_an_upgrade_once(tmp_path, monkeypatch, caplog):
    bot, state, sent = make_bot(tmp_path, monkeypatch)
    state.bot_version = "0.1.0"
    write_notice(tmp_path, announce.__version__)
    caplog.set_level(logging.INFO)
    run_ready(bot)
    assert len(sent) == 1
    notes, recipients = sent[0]
    assert notes.body.startswith("## What's Changed")
    assert recipients == {1: ["Alpha"], 2: ["Alpha", "Beta"]}
    assert bot.intents.members
    assert State.load(tmp_path / "state.json").bot_version == announce.__version__


def test_members_intent_only_for_admins(tmp_path, monkeypatch):
    for mode, expected in (("admins", True), ("owner", False), ("off", False)):
        bot, _, _ = make_bot(tmp_path, monkeypatch, update_notify=mode)
        assert bot.intents.members is expected


def test_on_ready_first_run_and_off(tmp_path, monkeypatch):
    bot, state, sent = make_bot(tmp_path, monkeypatch)
    run_ready(bot)
    assert sent == []
    assert State.load(tmp_path / "state.json").bot_version == announce.__version__

    (tmp_path / "x").mkdir()
    bot, state, sent = make_bot(tmp_path / "x", monkeypatch, update_notify="off")
    state.bot_version = "0.1.0"
    run_ready(bot)
    assert sent == []
    assert state.bot_version == announce.__version__


def test_on_ready_never_crashes(tmp_path, monkeypatch, caplog):
    bot, state, sent = make_bot(tmp_path, monkeypatch)
    state.bot_version = "0.1.0"

    def boom(*args):
        raise RuntimeError("broken")

    monkeypatch.setattr(announce, "load_release_notes", boom)
    run_ready(bot)
    assert "Could not check whether ComicsBot was updated" in caplog.text
    assert state.bot_version == announce.__version__  # recorded first: not announced again


@pytest.mark.parametrize("value", ["sometimes", "ALL"])
def test_update_notify_is_validated(tmp_path, monkeypatch, value):
    from comicsbot.config import ConfigError, load_config

    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("UPDATE_NOTIFY", value)
    with pytest.raises(ConfigError, match="UPDATE_NOTIFY"):
        load_config(tmp_path)


def test_config_defaults(tmp_path, monkeypatch):
    from comicsbot.config import load_config

    monkeypatch.delenv("UPDATE_NOTIFY", raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    config = load_config(tmp_path)
    assert config.update_notify == "admins"
    assert not hasattr(config, "members_intent")


# -- install section and intent error ----------------------------------------------------


def test_install_section_is_stripped():
    body = "## Install\n\nRun install.sh\n\n\n## What's Changed\n* Fix by @x\n\n**Full Changelog**: link"
    assert announce.strip_install_section(body) == "## What's Changed\n* Fix by @x\n\n**Full Changelog**: link"
    assert announce.strip_install_section("## Install\n\nonly steps\n### Sub\nmore") == ""
    assert announce.strip_install_section("## What's Changed\n* a") == "## What's Changed\n* a"
    embed = announce.build_embed(announce.ReleaseNotes(version="2.3.0", body="## Install\nsteps"), ["A"])
    assert embed.description.startswith("[Release on GitHub](")


def test_missing_members_intent_exits_with_a_config_error(tmp_path, monkeypatch, caplog):
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("comicsbot_main", root / "ComicsBot.py")
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    def refuse(self, token, **kwargs):
        raise discord.PrivilegedIntentsRequired(shard_id=None)

    monkeypatch.setattr(ComicsBot, "run", refuse)
    config = Config(token="secret-token", base_dir=tmp_path)
    code = main.run_bot(config, logging.getLogger("comicsbot"))
    assert code == main.EXIT_CONFIG_ERROR == 78
    assert "Server Members Intent" in caplog.text
    assert "Traceback" not in caplog.text and "secret-token" not in caplog.text
