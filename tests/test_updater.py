import hashlib
import io
import json
import logging
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
        self.release = {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets}
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


@pytest.fixture(scope="module")
def release_files(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    linux, windows, sums, _notes = build_release.build(out)
    return {p.name: p.read_bytes() for p in (linux, windows, sums)}


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
    name = f"ComicsBot-{__version__}-linux.tar.gz"
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
    with pytest.raises(UpdateError, match="has no ComicsBot"):
        updater.asset_url(release, "ComicsBot-2.3.0-linux.tar.gz")


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
    ["ComicsBot-2.3.0/../../etc/passwd", "/etc/passwd", "Other-2.3.0/install.sh", "ComicsBot-2.3.0/a\\..\\b"],
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
    info = tarfile.TarInfo("ComicsBot-2.3.0/install.sh")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/shadow"
    _tar(tmp_path / "a.tar.gz", [(info, None)])
    (tmp_path / "out").mkdir()
    with pytest.raises(UpdateError, match="link or special file"):
        updater.extract_release(tmp_path / "a.tar.gz", tmp_path / "out", "2.3.0", "linux")


def test_unsafe_zip_paths_are_refused(tmp_path):
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as zf:
        zf.writestr("ComicsBot-2.3.0/../evil.ps1", "x")
    (tmp_path / "out").mkdir()
    with pytest.raises(UpdateError, match="unsafe path"):
        updater.extract_release(tmp_path / "a.zip", tmp_path / "out", "2.3.0", "windows")


def test_archive_without_installer_is_refused(tmp_path):
    info = tarfile.TarInfo("ComicsBot-2.3.0/README.md")
    info.size = 1
    _tar(tmp_path / "a.tar.gz", [(info, b"x")])
    (tmp_path / "out").mkdir()
    with pytest.raises(UpdateError, match="does not contain"):
        updater.extract_release(tmp_path / "a.tar.gz", tmp_path / "out", "2.3.0", "linux")


# -- the whole update ------------------------------------------------------------------


class FakeRunner:
    def __init__(self, install_dir, installer_code=0):
        self.install_dir = install_dir
        self.installer_code = installer_code
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append(command)
        if command[-1] in ("--yes", "-Unattended"):  # the release installer
            release_dir = Path(command[1] if command[-1] == "--yes" else command[5]).parent
            # Like the real installer: copy the program files and leave .env and data alone.
            (self.install_dir / "ComicsBot.py").write_bytes((release_dir / "ComicsBot.py").read_bytes())
            (self.install_dir / "VERSION").write_bytes((release_dir / "VERSION").read_bytes())
            (self.install_dir / "start-bot.sh").write_text("new file\n")
            return self.installer_code
        return 0


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
    assert updater.update(install, TOKEN, platform="linux", check_only=True, fetch=github, runner=runner) == 0
    assert runner.commands == []
    assert len(github.requests) == 1
    assert f"ComicsBot {__version__} is available (installed: 2.1.0)" in caplog.text


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_update_runs_the_release_installer(tmp_path, release_files, caplog, platform):
    install = make_install(tmp_path / "app")
    github = FakeGitHub(f"v{__version__}", release_files)
    runner = FakeRunner(install)
    caplog.set_level(logging.DEBUG)
    assert updater.update(install, TOKEN, platform=platform, fetch=github, runner=runner) == 0
    assert len(runner.commands) == 1
    command = runner.commands[0]
    if platform == "linux":
        assert command[-3:] == ["--dir", str(install), "--yes"]
        assert command[1].endswith(f"ComicsBot-{__version__}/install.sh")
    else:
        assert command[-4:] == ["-InstallDir", str(install), "-Yes", "-Unattended"]
        assert command[5].endswith("install.ps1")
    assert (install / "VERSION").read_text().strip() == __version__
    assert (install / ".env").read_text() == "DISCORD_TOKEN=discord-secret\n"
    assert TOKEN not in caplog.text
    assert "X-Amz-Signature" not in caplog.text


def test_checksum_mismatch_stops_before_installing(tmp_path, release_files):
    install = make_install(tmp_path / "app")
    files = dict(release_files)
    name = f"ComicsBot-{__version__}-linux.tar.gz"
    files[name] = files[name] + b"tampered"
    runner = FakeRunner(install)
    with pytest.raises(UpdateError, match="checksum mismatch"):
        updater.update(install, TOKEN, platform="linux", fetch=FakeGitHub(f"v{__version__}", files), runner=runner)
    assert runner.commands == []
    assert (install / "ComicsBot.py").read_text() == "old bot\n"


def test_missing_checksum_entry_stops(tmp_path, release_files):
    install = make_install(tmp_path / "app")
    files = dict(release_files)
    files["SHA256SUMS"] = b"0" * 64 + b"  something-else\n"
    with pytest.raises(UpdateError, match="no entry"):
        updater.update(
            install, TOKEN, platform="linux", fetch=FakeGitHub(f"v{__version__}", files), runner=FakeRunner(install)
        )


def test_failed_install_is_rolled_back(tmp_path, release_files, caplog):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1)
    caplog.set_level(logging.INFO)
    code = updater.update(
        install, TOKEN, platform="linux", fetch=FakeGitHub(f"v{__version__}", release_files), runner=runner
    )
    assert code == 1
    assert (install / "ComicsBot.py").read_text() == "old bot\n"
    assert (install / "VERSION").read_text() == "2.1.0\n"
    assert not (install / "start-bot.sh").exists()  # added by the failed update, removed again
    assert (install / ".env").read_text() == "DISCORD_TOKEN=discord-secret\n"
    assert (install / "state.json").exists()
    assert [c[1:] for c in runner.commands[1:]] == [["stop", "comicsbot"], ["restart", "comicsbot"]]
    assert "restored and restarted" in caplog.text


def test_windows_rollback_uses_install_ps1(tmp_path, release_files):
    install = make_install(tmp_path / "app")
    runner = FakeRunner(install, installer_code=1)
    updater.update(
        install, TOKEN, platform="windows", fetch=FakeGitHub(f"v{__version__}", release_files), runner=runner
    )
    assert [c[-1] for c in runner.commands[1:]] == ["-Stop", "-Restart"]
    assert all(c[5] == str(install / "install.ps1") for c in runner.commands[1:])


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
    sums = updater.parse_checksums(release_files["SHA256SUMS"].decode())
    for platform in ("linux", "windows"):
        name = updater.archive_name(__version__, platform)
        assert sums[name] == hashlib.sha256(release_files[name]).hexdigest()
