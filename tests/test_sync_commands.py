"""Slash commands are synced at every start, retried on failure, and a change is noticed."""

import asyncio

import pytest

from comicsbot import bot as bot_module
from comicsbot.bot import ComicsBot
from comicsbot.commands import register_commands
from comicsbot.config import Config
from comicsbot.storage import State


def make_bot(tmp_path, failures=0):
    state = State(tmp_path / "state.json")
    bot = ComicsBot(Config(token="x", base_dir=tmp_path), state)
    register_commands(bot)
    calls = []

    async def fake_sync():
        calls.append(1)
        if len(calls) <= failures:
            raise OSError("Discord unreachable")
        return bot.tree.get_commands()

    bot.tree.sync = fake_sync
    return bot, state, calls


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)
    return slept


def test_first_sync_records_the_hash_and_a_restart_sees_no_change(tmp_path, caplog):
    bot, state, calls = make_bot(tmp_path)
    caplog.set_level("INFO")
    assert asyncio.run(bot.sync_commands())
    assert state.commands_hash == bot.commands_hash()
    assert State.load(tmp_path / "state.json").commands_hash == state.commands_hash
    assert "changed since the last sync" in caplog.text

    caplog.clear()
    assert asyncio.run(bot.sync_commands())
    assert len(calls) == 2, "every start syncs, changed or not"
    assert "changed since the last sync" not in caplog.text


def test_changed_definitions_are_noticed(tmp_path):
    bot, state, _ = make_bot(tmp_path)
    state.commands_hash = "from-an-older-version"
    assert asyncio.run(bot.sync_commands())
    assert state.commands_hash == bot.commands_hash()


def test_failed_sync_is_retried(tmp_path, no_sleep):
    bot, state, calls = make_bot(tmp_path, failures=2)
    assert asyncio.run(bot.sync_commands(delays=(1, 2, 3)))
    assert len(calls) == 3
    assert no_sleep == [1, 2]
    assert state.commands_hash == bot.commands_hash()


def test_gives_up_after_the_last_retry_without_raising(tmp_path, caplog):
    bot, state, calls = make_bot(tmp_path, failures=99)
    assert not asyncio.run(bot.sync_commands(delays=(1, 1)))
    assert len(calls) == 3
    assert state.commands_hash is None, "a failed sync is not recorded"
    assert "after 3 attempts" in caplog.text
