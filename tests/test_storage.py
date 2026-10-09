import json
import logging
from logging.handlers import RotatingFileHandler

from comicsbot.config import Config, load_config, parse_legacy_env, repair_env_file
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


def test_data_folder_defaults_to_data_when_it_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    config = load_config(tmp_path)
    assert config.data_dir == tmp_path  # Windows installs and runs from source: next to the program
    assert config.state_file == tmp_path / "state.json"
    (tmp_path / "data").mkdir()  # Linux installs
    config = load_config(tmp_path)
    assert config.data_dir == tmp_path / "data"
    assert (config.state_file, config.log_file, config.lock_file) == (
        tmp_path / "data" / "state.json",
        tmp_path / "data" / "discord.log",
        tmp_path / "data" / "bot.lock",
    )
    assert Config(token="x", base_dir=tmp_path).data_dir == tmp_path / "data"


def test_data_folder_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("DATA_DIR", "var")
    assert load_config(tmp_path).data_dir == tmp_path / "var"
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "elsewhere"))
    assert load_config(tmp_path).data_dir == tmp_path / "elsewhere"


def test_main_writes_only_into_the_data_folder(tmp_path, monkeypatch):
    import importlib.util
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("comicsbot_main_data", root / "ComicsBot.py")
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    (tmp_path / ".env").write_text("DISCORD_TOKEN=x\nDATA_DIR=data\n")
    # main() loads .env into os.environ; keep that inside this test.
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "DATA_DIR")})
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    seen = []
    monkeypatch.setattr(main, "run_bot", lambda config, log: seen.append(config) or 0)
    try:
        assert main.main() == 0
    finally:
        for handler in logging.getLogger().handlers[:]:
            if isinstance(handler, RotatingFileHandler):
                handler.close()
                logging.getLogger().removeHandler(handler)
    assert seen[0].data_dir == tmp_path / "data"
    assert sorted(os.listdir(tmp_path)) == [".env", "data"]
    assert sorted(os.listdir(tmp_path / "data")) == ["bot.lock", "discord.log"]


def test_unwritable_data_folder_is_a_config_error(tmp_path, monkeypatch, capsys):
    import importlib.util
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("comicsbot_main_data2", root / "ComicsBot.py")
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    (tmp_path / "file").write_text("")
    (tmp_path / ".env").write_text(f"DISCORD_TOKEN=x\nDATA_DIR={tmp_path / 'file' / 'data'}\n")
    # main() loads .env into os.environ; keep that inside this test.
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "DATA_DIR")})
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    assert main.main() == main.EXIT_CONFIG_ERROR
    assert "cannot write to the data folder" in capsys.readouterr().err
