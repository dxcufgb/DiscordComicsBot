import hashlib
import io
import json
import logging
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from comicsbot import __version__, updater
from comicsbot.updater import UpdateError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_release  # noqa: E402

TOKEN = "github_pat_secret_value_123"
API = "https://api.github.com/repos/dxcufgb/DiscordComicsBot"
CDN = "https://objects.githubusercontent.com/github-production-release-asset/1?X-Amz-Signature=abc"


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = headers or {}
        self.closed = False

    def header(self, name):
        return self._headers.get(name)

    def read(self, size):
        return self._body.read(size)

    def close(self):
        self.closed = True


class FakeGitHub:
    """Serves the releases/latest JSON and its assets like GitHub: API asset URLs redirect to a CDN."""

    def __init__(self, tag, files, *, draft=False, prerelease=False):
        self.files = dict(files)
        self.requests = []
        assets = [{"name": name, "url": f"{API}/releases/assets/{i}"} for i, name in enumerate(self.files)]
        self.release = {
            "tag_name": tag,
            "name": f"ComicsBot {tag}",
            "body": "## What's Changed\n* things",
            "html_url": f"https://github.com/dxcufgb/DiscordComicsBot/releases/tag/{tag}",
            "draft": draft,
            "prerelease": prerelease,
            "assets": assets,
        }
        self.cdn = {f"{CDN}&n={i}": name for i, name in enumerate(self.files)}
        self.overrides = {}

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        if url in self.overrides:
            return self.overrides[url]
        if url == f"{API}/releases/latest":
            return FakeResponse(200, json.dumps(self.release).encode())
        for i in range(len(self.files)):
            if url == f"{API}/releases/assets/{i}":
                return FakeResponse(302, headers={"Location": f"{CDN}&n={i}"})
        if url in self.cdn:
            return FakeResponse(200, self.files[self.cdn[url]])
        return FakeResponse(404)


@pytest.fixture(autouse=True)
def not_root(monkeypatch):
    """The root-only checks are tested on their own; the other tests run as any user (CI: not root)."""
    monkeypatch.setattr(updater, "_running_as_root", lambda: False)


@pytest.fixture(scope="module")
def release_files(tmp_path_factory):
    """Every file the Release workflow attaches (with a stand-in setup.exe)."""
    out = tmp_path_factory.mktemp("dist")
    build_release.build(out)
    (out / build_release.setup_name(__version__)).write_bytes(b"MZ setup")
    return {p.name: p.read_bytes() for p in build_release.finish(out, __version__)}


def make_install(path, version="2.1.0"):
    (path / "comicsbot").mkdir(parents=True)
    (path / "comicsbot" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (path / "ComicsBot.py").write_text("old bot\n")
    (path / "install.sh").write_text("old installer\n")
    (path / "VERSION").write_text(f"{version}\n")
    (path / ".env").write_text("DISCORD_TOKEN=discord-secret\n")
    (path / "state.json").write_text("{}\n")
    return path


# -- versions --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "installed", "newer"),
    [
        ("v2.3.0", "2.2.0", True),
        ("2.10.0", "2.9.9", True),
        ("v2.2.0", "2.2.0", False),
        ("v2.1.9", "2.2.0", False),
        ("v3.0", "2.99.99", True),
        ("v2.3.0", "2.3.0-rc1", True),
        ("v2.3.0-rc2", "2.3.0-rc1", True),
        ("v2.3.0-rc1", "2.3.0", False),
        ("v2.3.0-rc10", "2.3.0-rc9", True),
        ("v2.3.0-rc.10", "2.3.0-rc.9", True),
    ],
)
def test_version_comparison(candidate, installed, newer):
    assert updater.is_newer(candidate, installed) is newer


def test_bad_version_is_rejected():
    with pytest.raises(UpdateError, match="not a version"):
        updater.parse_version("latest")


def test_installed_version_reads_version_file(tmp_path):
    assert updater.installed_version(make_install(tmp_path, "2.1.0")) == "2.1.0"
    (tmp_path / "VERSION").unlink()
    assert updater.installed_version(tmp_path) == "2.1.0"  # falls back to __version__
    with pytest.raises(UpdateError, match="no ComicsBot release"):
        updater.installed_version(tmp_path / "missing")


def test_checksums_parsing():
    digest = "a" * 64
    text = f"{digest}  ComicsBot-2.3.0-linux.tar.gz\n{'B' * 64} *ComicsBot-2.3.0-windows.zip\ngarbage\n"
    assert updater.parse_checksums(text) == {
        "ComicsBot-2.3.0-linux.tar.gz": digest,
        "ComicsBot-2.3.0-windows.zip": "b" * 64,
    }


# -- HTTP --------------------------------------------------------------------------


def test_token_only_goes_to_the_api_and_redirects_are_followed_without_it(release_files):
    name = f"comicsbot-{__version__}-linux.tar.gz"
    github = FakeGitHub(f"v{__version__}", release_files)
    release = updater.latest_release(TOKEN, github)
    data = updater.get_bytes(
        updater.asset_url(release, name),
        token=TOKEN,
        accept="application/octet-stream",
        fetch=github,
        max_bytes=10**8,
    )
    assert data == release_files[name]
    for url, headers in github.requests:
        if url.startswith("https://api.github.com/"):
            assert headers["Authorization"] == f"Bearer {TOKEN}"
        else:
            assert "Authorization" not in headers
            assert TOKEN not in json.dumps(headers)
    assert github.requests[-2][1]["Accept"] == "application/octet-stream"
    assert github.requests[-1][0].startswith(CDN)


def test_redirect_to_plain_http_is_refused():
    def fetch(url, headers):
        return FakeResponse(302, headers={"Location": "http://evil.example/x"})

    with pytest.raises(UpdateError, match="non-HTTPS"):
        updater.get_bytes(f"{API}/releases/assets/1", token=TOKEN, accept="*/*", fetch=fetch, max_bytes=100)


def test_token_is_not_sent_to_a_lookalike_host():
    seen = []

    def fetch(url, headers):
        seen.append(headers)
        return FakeResponse(200, b"ok")

    updater.get_bytes("https://api.github.com.evil.example/x", token=TOKEN, accept="*/*", fetch=fetch, max_bytes=10)
    assert "Authorization" not in seen[0]


def test_redirect_loop_is_stopped():
    def fetch(url, headers):
        return FakeResponse(302, headers={"Location": url})

    with pytest.raises(UpdateError, match="too many redirects"):
        updater.get_bytes(f"{API}/releases/latest", token=TOKEN, accept="*/*", fetch=fetch, max_bytes=100)


@pytest.mark.parametrize("declared", [True, False])
def test_download_size_is_capped(declared):
    headers = {"Content-Length": "1000"} if declared else {}

    def fetch(url, headers_=None):
        return FakeResponse(200, b"x" * 1000, headers)

    with pytest.raises(UpdateError, match="larger than"):
        updater.get_bytes(f"{API}/releases/latest", token=TOKEN, accept="*/*", fetch=fetch, max_bytes=999)


@pytest.mark.parametrize(("status", "message"), [(401, "token"), (403, "token"), (404, "not found"), (500, "HTTP 500")])
def test_http_errors_are_explained_without_the_token(status, message):
    def fetch(url, headers):
        return FakeResponse(status)

    with pytest.raises(UpdateError, match=message) as info:
        updater.latest_release(TOKEN, fetch)
    assert TOKEN not in str(info.value)


def test_drafts_and_prereleases_are_ignored(release_files):
    assert updater.latest_release(TOKEN, FakeGitHub("v9.0.0-rc1", release_files, prerelease=True)) is None
    assert updater.latest_release(TOKEN, FakeGitHub("v9.0.0", release_files, draft=True)) is None


def test_asset_urls_must_point_at_this_repository():
    release = updater.Release("v2.3.0", "2.3.0", {"SHA256SUMS": "https://example.com/SHA256SUMS"})
    with pytest.raises(UpdateError, match="unexpected download URL"):
        updater.asset_url(release, "SHA256SUMS")
    with pytest.raises(UpdateError, match="has no comicsbot"):
        updater.asset_url(release, "comicsbot-2.3.0-linux.tar.gz")


# -- extraction ----------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_extracts_the_real_release_archive(tmp_path, release_files, platform):
    name = updater.archive_name(__version__, platform)
    (tmp_path / name).write_bytes(release_files[name])
    dest = tmp_path / "out"
    dest.mkdir()
    release_dir = updater.extract_release(tmp_path / name, dest, __version__, platform)
    assert (release_dir / "comicsbot" / "updater.py").is_file()
    if platform == "linux":
        assert (release_dir / "install.sh").stat().st_mode & 0o111
        assert not (release_dir / "ComicsBot.py").stat().st_mode & 0o111


def _tar(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)


@pytest.mark.parametrize(
    "member",
    [
        "comicsbot-2.3.0/../../etc/passwd",
        "/etc/passwd",
        "Other-2.3.0/install.sh",
        "comicsbot-2.3.0/a\\..\\b",
        "ComicsBot-2.3.0/install.sh",  # the top folder of the legacy archives
    ],
)
def test_unsafe_tar_paths_are_refused(tmp_path, member):
    info = tarfile.TarInfo(member)
    info.size = 1
    _tar(tmp_path / "a.tar.gz", [(info, b"x")])
    (tmp_path / "out").mkdir()
    with pytest.raises(UpdateError, match="unsafe path"):
        updater.extract_release(tmp_path / "a.tar.gz", tmp_path / "out", "2.3.0", "linux")
    assert not (tmp_path / "etc").exists()


def test_links_in_tar_are_refused(tmp_path):
    info = tarfile.TarInfo("comicsbot-2.3.0/install.sh")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/shadow"
    _tar(tmp_path / "a.tar.gz", [(info, None)])
    (tmp_path / "out").mkdir()
    with pytest.raises(UpdateError, match="link or special file"):
        updater.extract_release(tmp_path / "a.tar.gz", tmp_path / "out", "2.3.0", "linux")


def test_unsafe_zip_paths_are_refused(tmp_path):
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as zf:
        zf.writestr("comicsbot-2.3.0/../evil.ps1", "x")
    (tmp_path / "out").mkdir()
    with pytest.raises(UpdateError, match="unsafe path"):
        updater.extract_release(tmp_path / "a.zip", tmp_path / "out", "2.3.0", "windows")


def test_archive_without_installer_is_refused(tmp_path):
    info = tarfile.TarInfo("comicsbot-2.3.0/README.md")
    info.size = 1
    _tar(tmp_path / "a.tar.gz", [(info, b"x")])
    (tmp_path / "out").mkdir()
    with pytest.raises(UpdateError, match="does not contain"):
        updater.extract_release(tmp_path / "a.tar.gz", tmp_path / "out", "2.3.0", "linux")


# -- the whole update ------------------------------------------------------------------


def is_status(command):
    return "is-active" in command or "-Command" in command


def is_restart(command):
    return command[-2:] == ["restart", "comicsbot"] or command[-1] == "-Restart"


class FakeRunner:
    """Plays the release installer and the service manager.

    ``running`` holds the answers to the successive "is the bot running?" checks (then: yes).
    """

    def __init__(self, install_dir, installer_code=0, running=(), restart_code=0):
        self.install_dir = install_dir
        self.installer_code = installer_code
        self.running = list(running)
        self.restart_code = restart_code
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append(command)
        if command[-1] in ("--yes", "-Unattended"):  # the release installer
            release_dir = Path(next(c for c in command if c.endswith(("install.sh", "install.ps1")))).parent
            # Like the real installer: copy the program files and leave .env and data alone.
            (self.install_dir / "ComicsBot.py").write_bytes((release_dir / "ComicsBot.py").read_bytes())
            (self.install_dir / "VERSION").write_bytes((release_dir / "VERSION").read_bytes())
            (self.install_dir / "start-bot.sh").write_text("new file\n")
            return self.installer_code
        if is_status(command):
            return 0 if (self.running.pop(0) if self.running else True) else 3
        if is_restart(command):
            return self.restart_code
        return 0


def run_update(install, release_files, runner, tmp_path, platform="linux", sleeps=None):
    return updater.update(
        install,
        TOKEN,
        platform=platform,
        fetch=FakeGitHub(f"v{__version__}", release_files),
        runner=runner,
        backup_root=tmp_path / "backups",
        sleep=(sleeps.append if sleeps is not None else lambda seconds: None),
    )


def test_up_to_date_does_nothing(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app", __version__)
    github = FakeGitHub(f"v{__version__}", release_files)
    runner = FakeRunner(install)
    caplog.set_level(logging.INFO)
    assert updater.update(install, TOKEN, platform="linux", fetch=github, runner=runner) == 0
    assert runner.commands == []
    assert len(github.requests) == 1
    assert "up to date" in caplog.text


def test_check_only_reports_without_downloading(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app")
    github = FakeGitHub(f"v{__version__}", release_files)
    runner = FakeRunner(install)
    caplog.set_level(logging.INFO)
    code = updater.update(install, TOKEN, platform="linux", check_only=True, fetch=github, runner=runner)
    assert code == updater.EXIT_UPDATE_AVAILABLE == 10
    assert runner.commands == []
    assert len(github.requests) == 1
    assert f"ComicsBot {__version__} is available (installed: 2.1.0)" in caplog.text


def test_check_only_up_to_date_is_zero(tmp_path, release_files):
    install = make_install(tmp_path / "app", __version__)
    github = FakeGitHub(f"v{__version__}", release_files)
    assert updater.update(install, TOKEN, platform="linux", check_only=True, fetch=github) == 0


@pytest.mark.parametrize("platform", ["linux", "windows"])
@pytest.mark.parametrize("missing", ["archive", "checksums"])
def test_release_without_new_style_files_is_left_alone(tmp_path, release_files, caplog, platform, missing):
    """A release of 2.3.2 or older (legacy names only) or one whose files are still being attached."""
    install = make_install(tmp_path / "app")
    gone = updater.archive_name(__version__, platform) if missing == "archive" else "SHA256SUMS.txt"
    files = {name: data for name, data in release_files.items() if name != gone}
    github = FakeGitHub(f"v{__version__}", files)
    runner = FakeRunner(install)
    caplog.set_level(logging.INFO)
    assert updater.update(install, TOKEN, platform=platform, fetch=github, runner=runner) == 0
    assert runner.commands == []
    assert len(github.requests) == 1  # nothing downloaded
    assert f"has no {gone}" in caplog.text
    assert (install / "ComicsBot.py").read_text() == "old bot\n"


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_update_runs_the_release_installer(tmp_path, release_files, caplog, platform):
    install = make_install(tmp_path / "app")
    github = FakeGitHub(f"v{__version__}", release_files)
    runner = FakeRunner(install)
    caplog.set_level(logging.DEBUG)
    assert (
        updater.update(install, TOKEN, platform=platform, fetch=github, runner=runner, backup_root=tmp_path / "b") == 0
    )
    assert len(runner.commands) == 2
    assert is_status(runner.commands[0])
    command = runner.commands[1]
    if platform == "linux":
        assert command[-3:] == ["--dir", str(install), "--yes"]
        assert command[1] == f"PYTHON={sys.executable}"  # the Python check_root_safety checked
        assert command[3].endswith(f"comicsbot-{__version__}/install.sh")
    else:
        assert command[-4:] == ["-InstallDir", str(install), "-Yes", "-Unattended"]
        assert command[5].endswith("install.ps1")
    assert (install / "VERSION").read_text().strip() == __version__
    assert (install / ".env").read_text() == "DISCORD_TOKEN=discord-secret\n"
    notice = json.loads((install / "update-notice.json").read_text())
    assert notice == {
        "version": __version__,
        "name": f"ComicsBot v{__version__}",
        "body": "## What's Changed\n* things",
        "html_url": f"https://github.com/dxcufgb/DiscordComicsBot/releases/tag/v{__version__}",
    }
    assert TOKEN not in (install / "update-notice.json").read_text()
    assert TOKEN not in caplog.text
    assert "X-Amz-Signature" not in caplog.text
    assert list((tmp_path / "b").iterdir()) == []  # the backup is gone after a good update


def test_checksum_mismatch_stops_before_installing(tmp_path, release_files):
    install = make_install(tmp_path / "app")
    files = dict(release_files)
    name = f"comicsbot-{__version__}-linux.tar.gz"
    files[name] = files[name] + b"tampered"
    runner = FakeRunner(install)
    with pytest.raises(UpdateError, match="checksum mismatch"):
        updater.update(install, TOKEN, platform="linux", fetch=FakeGitHub(f"v{__version__}", files), runner=runner)
    assert runner.commands == []
    assert (install / "ComicsBot.py").read_text() == "old bot\n"


def test_missing_checksum_entry_stops(tmp_path, release_files):
    install = make_install(tmp_path / "app")
    files = dict(release_files)
    files["SHA256SUMS.txt"] = b"0" * 64 + b"  something-else\n"
    with pytest.raises(UpdateError, match="no entry"):
        updater.update(
            install, TOKEN, platform="linux", fetch=FakeGitHub(f"v{__version__}", files), runner=FakeRunner(install)
        )


def test_failed_install_is_rolled_back(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1)
    sleeps = []
    caplog.set_level(logging.INFO)
    assert run_update(install, release_files, runner, tmp_path, sleeps=sleeps) == updater.EXIT_FAILED
    assert (install / "ComicsBot.py").read_text() == "old bot\n"
    assert (install / "VERSION").read_text() == "2.1.0\n"
    assert not (install / "start-bot.sh").exists()  # added by the failed update, removed again
    assert (install / ".env").read_text() == "DISCORD_TOKEN=discord-secret\n"
    assert (install / "state.json").exists()
    assert not (install / "update-notice.json").exists()
    assert [c[1:] for c in runner.commands[2:]] == [
        ["stop", "comicsbot"],
        ["restart", "comicsbot"],
        ["is-active", "--quiet", "comicsbot"],
    ]
    assert sleeps == [updater.ROLLBACK_WAIT]  # checked after a short wait
    assert "restored and restarted" in caplog.text
    assert "ROLLBACK FAILED" not in caplog.text
    assert list((tmp_path / "backups").iterdir()) == []


def test_rollback_does_not_start_a_bot_that_was_not_running(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1, running=[False])
    caplog.set_level(logging.INFO)
    assert run_update(install, release_files, runner, tmp_path) == updater.EXIT_FAILED
    assert [c[1:] for c in runner.commands[2:]] == [["stop", "comicsbot"]]
    assert (install / "VERSION").read_text() == "2.1.0\n"
    assert "was not running before the update" in caplog.text
    assert list((tmp_path / "backups").iterdir()) == []


def assert_rollback_failed(caplog, tmp_path, install):
    (backup,) = (tmp_path / "backups").iterdir()  # kept for recovery by hand
    assert (backup / "ComicsBot.py").read_text() == "old bot\n"
    message = next(r.getMessage() for r in caplog.records if "ROLLBACK FAILED" in r.getMessage())
    assert message.startswith("ROLLBACK FAILED: ")
    assert str(backup) in message
    assert f"sudo cp -a {backup}/. {install}/" in message
    assert TOKEN not in caplog.text


def test_rollback_that_does_not_come_back_up_keeps_the_backup(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1, running=[True, False])
    code = run_update(install, release_files, runner, tmp_path)
    assert code == updater.EXIT_ROLLBACK_FAILED != 0
    assert_rollback_failed(caplog, tmp_path, install)
    assert "is not running 15 seconds after it was started" in caplog.text


def test_rollback_that_cannot_restart_keeps_the_backup(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1, restart_code=1)
    assert run_update(install, release_files, runner, tmp_path) == updater.EXIT_ROLLBACK_FAILED
    assert_rollback_failed(caplog, tmp_path, install)
    assert "could not be started" in caplog.text
    assert not any(is_status(c) for c in runner.commands[3:])


def test_rollback_that_cannot_restore_keeps_the_backup(tmp_path, release_files, caplog, monkeypatch):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1)

    def broken_restore(install_dir, backup_dir, names):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(updater, "restore_program_files", broken_restore)
    assert run_update(install, release_files, runner, tmp_path) == updater.EXIT_ROLLBACK_FAILED
    assert_rollback_failed(caplog, tmp_path, install)
    assert "could not be put back" in caplog.text
    assert not any(is_restart(c) for c in runner.commands)


def test_rollback_that_leaves_files_missing_keeps_the_backup(tmp_path, release_files, caplog, monkeypatch):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1)
    real_restore = updater.restore_program_files

    def incomplete_restore(install_dir, backup_dir, names):
        real_restore(install_dir, backup_dir, names)
        (install_dir / "ComicsBot.py").unlink()

    monkeypatch.setattr(updater, "restore_program_files", incomplete_restore)
    assert run_update(install, release_files, runner, tmp_path) == updater.EXIT_ROLLBACK_FAILED
    assert_rollback_failed(caplog, tmp_path, install)
    assert "ComicsBot.py missing from" in caplog.text


def test_windows_rollback_uses_install_ps1(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1, running=[True, False])
    assert run_update(install, release_files, runner, tmp_path, platform="windows") == updater.EXIT_ROLLBACK_FAILED
    assert [c[-1] for c in runner.commands[2:4]] == ["-Stop", "-Restart"]
    assert all(c[5] == str(install / "install.ps1") for c in runner.commands[2:4])
    assert "Get-ScheduledTask -TaskName 'ComicsBot'" in runner.commands[4][-1]
    assert "install.bat -Restart" in caplog.text


def test_installer_gets_no_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "discord-secret")
    monkeypatch.setenv("COMICSBOT_GITHUB_TOKEN", TOKEN)
    script = tmp_path / "env.py"
    script.write_text("import os; print([k for k in ('DISCORD_TOKEN', 'COMICSBOT_GITHUB_TOKEN') if k in os.environ])")
    seen = []
    monkeypatch.setattr(updater.log, "info", lambda fmt, *args: seen.append(fmt % args))
    assert updater.run_command([sys.executable, str(script)], 30) == 0
    assert seen == ["    []"]


# -- command line ------------------------------------------------------------------------


def test_main_reads_the_token_file_and_holds_the_lock(tmp_path, monkeypatch):
    install = make_install(tmp_path / "app", __version__)
    token_file = tmp_path / "conf" / "github-token"
    token_file.parent.mkdir()
    token_file.write_text(TOKEN + "\n")
    token_file.chmod(0o600)

    def fake_update(install_dir, token, **kwargs):
        assert token == TOKEN
        with pytest.raises(updater.UpdateBusy):
            with updater.update_lock(tmp_path / "conf" / "update.lock"):
                pass
        return 0

    monkeypatch.setattr(updater, "update", fake_update)
    args = ["--install-dir", str(install), "--token-file", str(token_file)]
    assert updater.main(args) == 0
    # The lock is free again afterwards.
    with updater.update_lock(tmp_path / "conf" / "update.lock"):
        pass


def test_main_reports_a_missing_token_file(tmp_path, caplog):
    install = make_install(tmp_path / "app")
    code = updater.main(["--install-dir", str(install), "--token-file", str(tmp_path / "nope" / "token")])
    assert code == 1
    assert "cannot read the GitHub token file" in caplog.text


def test_main_refuses_a_concurrent_run(tmp_path):
    install = make_install(tmp_path / "app")
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN)
    lock = tmp_path / "update.lock"
    with updater.update_lock(lock):
        args = ["--install-dir", str(install), "--token-file", str(token_file), "--lock-file", str(lock)]
        assert updater.main(args) == updater.EXIT_BUSY


def test_token_file_must_hold_only_the_token(tmp_path):
    path = tmp_path / "token"
    path.write_text("")
    with pytest.raises(UpdateError, match="empty"):
        updater.read_token(path)
    path.write_text("GITHUB_TOKEN = abc\n")
    with pytest.raises(UpdateError, match="only the token") as info:
        updater.read_token(path)
    assert "abc" not in str(info.value)


def test_updater_is_self_contained():
    """The installers run a copy of updater.py outside the package with python -I."""
    source = (ROOT / "comicsbot" / "updater.py").read_text(encoding="utf-8")
    assert "from comicsbot" not in source and "import comicsbot" not in source
    assert "from ." not in source


def test_release_checksums_cover_the_archives(release_files):
    sums = updater.parse_checksums(release_files["SHA256SUMS.txt"].decode())
    for platform in ("linux", "windows"):
        name = updater.archive_name(__version__, platform)
        assert sums[name] == hashlib.sha256(release_files[name]).hexdigest()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_backup_never_follows_symlinks_in_the_install_folder(tmp_path):
    install, backup = tmp_path / "install", tmp_path / "backup"
    install.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("root only")
    (install / "README.md").symlink_to(secret)
    (install / "VERSION").write_text("1.0.0\n")
    updater.backup_program_files(install, backup)
    assert (backup / "VERSION").read_text() == "1.0.0\n"
    assert not (backup / "README.md").exists()
    updater.restore_program_files(install, backup)
    assert not (install / "README.md").exists()
    assert secret.read_text() == "root only"


def test_update_notice_goes_into_the_data_folder(tmp_path, release_files):
    install = make_install(tmp_path / "app")
    (install / "data").mkdir()
    runner = FakeRunner(install)
    assert run_update(install, release_files, runner, tmp_path) == 0
    assert json.loads((install / "data" / "update-notice.json").read_text())["version"] == __version__
    assert not (install / "update-notice.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_data_folder_link_is_not_used(tmp_path):
    install = make_install(tmp_path / "app")
    (tmp_path / "elsewhere").mkdir()
    (install / "data").symlink_to(tmp_path / "elsewhere")
    assert updater.data_folder(install) == install


def test_linux_rollback_covers_the_virtual_environment(tmp_path, release_files):
    install = make_install(tmp_path / "app")
    (install / ".venv" / "bin").mkdir(parents=True)
    (install / ".venv" / "bin" / "python").write_text("old venv\n")
    (install / "data").mkdir()
    (install / "data" / "state.json").write_text("data\n")

    class BreakingRunner(FakeRunner):
        def __call__(self, command, timeout):
            if command[-1] == "--yes":  # the failing installer has already replaced the venv
                (self.install_dir / ".venv" / "bin" / "python").write_text("half-upgraded\n")
            return super().__call__(command, timeout)

    runner = BreakingRunner(install, installer_code=1)
    assert run_update(install, release_files, runner, tmp_path) == updater.EXIT_FAILED
    assert (install / ".venv" / "bin" / "python").read_text() == "old venv\n"
    assert (install / "data" / "state.json").read_text() == "data\n"
    assert ".venv" not in updater.program_files("windows")  # the updater runs from it there


# -- running as root on Linux ---------------------------------------------------------------


def make_root_only(tmp_path):
    """An install that is safe when "root" is the current user (the tests do not run as root)."""
    install = make_install(tmp_path / "app")
    (install / "data").mkdir()
    (install / "data" / "state.json").write_text("{}")
    (install / ".venv" / "bin").mkdir(parents=True)
    python = tmp_path / "usr" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("")
    (install / ".venv" / "bin" / "python").symlink_to(python)
    for path in [tmp_path, *tmp_path.rglob("*")]:
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)
    # The bot's data folder and .env may be writable by the bot.
    (install / "data").chmod(0o777)
    (install / "data" / "state.json").chmod(0o666)
    (install / ".env").chmod(0o666)
    return install, python


def unsafe(tmp_path, install, executables=()):
    return updater.unsafe_path(install, executables, uid=os.getuid(), gid=os.getgid(), top=tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_root_only_install_is_accepted(tmp_path):
    install, python = make_root_only(tmp_path)
    assert unsafe(tmp_path, install, [python, install / ".venv" / "bin" / "python"]) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
@pytest.mark.parametrize(
    "target", ["ComicsBot.py", "comicsbot", "comicsbot/__init__.py", ".venv/bin", ".", "..", "VERSION"]
)
def test_install_writable_by_others_is_refused(tmp_path, target):
    install, _ = make_root_only(tmp_path)
    path = install / target
    path.chmod(path.stat().st_mode | 0o002)
    assert unsafe(tmp_path, install) == path.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_group_writable_is_refused_unless_the_group_is_root(tmp_path):
    install, _ = make_root_only(tmp_path)
    (install / "ComicsBot.py").chmod(0o664)
    other_group = (install / "ComicsBot.py").stat().st_gid + 1
    assert updater.unsafe_path(install, uid=os.getuid(), gid=other_group, top=tmp_path) == install / "ComicsBot.py"
    # gid=<our group> plays "the group is root": group-writable by root's group is fine.
    assert (
        updater.unsafe_path(install, uid=os.getuid(), gid=(install / "ComicsBot.py").stat().st_gid, top=tmp_path)
        is None
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_file_owned_by_another_account_is_refused(tmp_path):
    install, _ = make_root_only(tmp_path)
    # Pretend root is some other uid: then everything here belongs to "another account".
    assert updater.unsafe_path(install, uid=os.getuid() + 1, top=tmp_path) is not None


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_install_folder_is_checked_through_links(tmp_path):
    install, _ = make_root_only(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    real = shared / "app"
    install.rename(real)
    install.symlink_to(real)
    assert unsafe(tmp_path, install) == shared


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_writable_python_is_refused(tmp_path):
    install, python = make_root_only(tmp_path)
    python.parent.chmod(0o777)
    assert unsafe(tmp_path, install, [python]) == python.parent
    # Through the venv's link as well.
    assert unsafe(tmp_path, install, [install / ".venv" / "bin" / "python"]) == python.parent
    python.parent.chmod(0o755)
    other = tmp_path / "home" / "bin"
    other.mkdir(parents=True)
    other.chmod(0o777)
    (other / "python3").symlink_to(python)
    assert unsafe(tmp_path, install, [other / "python3"]) == other  # the link could be pointed elsewhere


def test_main_refuses_an_unsafe_install_as_root(tmp_path, monkeypatch, caplog):
    install = make_install(tmp_path / "app")
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN)
    monkeypatch.setattr(updater, "_running_as_root", lambda: True)
    monkeypatch.setattr(updater, "update", lambda *a, **k: pytest.fail("must not update"))
    args = ["--install-dir", str(install), "--token-file", str(token_file), "--platform", "linux"]
    if os.name == "posix" and os.geteuid() != 0:
        # Not root: everything here belongs to "another account".
        assert updater.main(args) == updater.EXIT_FAILED
    else:
        monkeypatch.setattr(updater, "unsafe_path", lambda *a, **k: install / "ComicsBot.py")
        assert updater.main(args) == updater.EXIT_FAILED
    assert "can be changed by an account other than root" in caplog.text
    assert "refusing to update" in caplog.text
    assert TOKEN not in caplog.text


def test_main_checks_the_install_and_pythons_as_root(tmp_path, monkeypatch):
    install = make_install(tmp_path / "app")
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN)
    seen = []
    monkeypatch.setattr(updater, "_running_as_root", lambda: True)
    monkeypatch.setattr(updater, "unsafe_path", lambda path, executables: seen.append((path, list(executables))))
    monkeypatch.setattr(updater, "update", lambda *a, **k: 0)
    assert updater.main(["--install-dir", str(install), "--token-file", str(token_file), "--platform", "linux"]) == 0
    assert seen == [
        (
            install.resolve(),
            [Path(sys.executable), Path(updater.__file__), install.resolve() / ".venv" / "bin" / "python"],
        )
    ]
    seen.clear()
    assert updater.main(["--install-dir", str(install), "--token-file", str(token_file), "--platform", "windows"]) == 0
    assert seen == []
