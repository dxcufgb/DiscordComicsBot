import json

from comicsbot.config import load_config, parse_legacy_env, repair_env_file
from comicsbot.storage import State, load_or_migrate


def test_migrates_legacy_files(tmp_path):
    (tmp_path / "comics.json").write_text(
        json.dumps(
            {
                "XKCD": {"URL": "https://xkcd.com/atom.xml", "LastURL": "Old - https://xkcd.com/1/"},
                "Broken": {},
            }
        )
    )
    (tmp_path / "111-Comics.json").write_text(
        json.dumps(
            {
                "XKCD": {"LastURL": "A - title - https://xkcd.com/2/"},
                "Unknown": {"LastURL": ""},
            }
        )
    )
    (tmp_path / "222-Comics.json").write_text("{}")

    state = load_or_migrate(tmp_path / "state.json", {111: 999})

    assert state.comics == {"XKCD": {"url": "https://xkcd.com/atom.xml"}}
    assert state.guilds["111"] == {"channel_id": 999, "subscriptions": {"XKCD": {"last_id": "https://xkcd.com/2/"}}}
    assert state.guilds["222"] == {"channel_id": None, "subscriptions": {}}
    # Persisted, and loading again does not re-migrate.
    assert State.load(tmp_path / "state.json").to_dict() == state.to_dict()


def test_save_is_atomic_and_round_trips(tmp_path):
    state = State(tmp_path / "state.json")
    state.comics["A"] = {"url": "https://a"}
    state.guild(5)["subscriptions"]["A"] = {"last_id": None}
    state.save()
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
    loaded = State.load(tmp_path / "state.json")
    assert loaded.find_comic("a") == "A"
    assert loaded.find_comic_by_url("https://a/") == "A"
    assert loaded.subscribed_comics() == {"A"}


def test_parse_legacy_env():
    text = "TOKEN:abc.DEF-ghiCOMICS-CHANNEL-123:456COMICS-CHANNEL-789:1011"
    assert parse_legacy_env(text) == {
        "TOKEN": "abc.DEF-ghi",
        "COMICS-CHANNEL-123": "456",
        "COMICS-CHANNEL-789": "1011",
    }


def test_repair_env_and_load_config(tmp_path, monkeypatch):
    for key in ("DISCORD_TOKEN", "TOKEN", "INTERVAL", "LOG_LEVEL", "LOG-LEVEL", "OWNER_IDS"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("TOKEN:secretCOMICS-CHANNEL-123:456")
    repair_env_file(env)
    assert (tmp_path / ".env.broken-backup").exists()
    assert env.read_text() == "DISCORD_TOKEN=secret\nCOMICS-CHANNEL-123=456\n"

    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    config = load_config(tmp_path)
    assert config.token == "secret"
    assert config.legacy_channels == {123: 456}
    assert config.interval == 3600


def test_repair_leaves_valid_env_alone(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=x\nINTERVAL=600\n")
    repair_env_file(env)
    assert env.read_text() == "DISCORD_TOKEN=x\nINTERVAL=600\n"
    assert not (tmp_path / ".env.broken-backup").exists()
