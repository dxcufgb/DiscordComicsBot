import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_release  # noqa: E402

from comicsbot import __version__  # noqa: E402


def test_default_version_comes_from_package():
    assert build_release.package_version() == __version__


def test_pyproject_must_match(tmp_path):
    shutil.copytree(ROOT / "comicsbot", tmp_path / "comicsbot")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(re.sub(r'(?m)^version = ".*"', 'version = "0.0.1"', pyproject))
    with pytest.raises(SystemExit, match="does not match"):
        build_release.package_version(tmp_path)


@pytest.mark.parametrize("text", ["1.2.3", "v1.2.3", "10.0.0-rc.1", "1.2.3-beta-2"])
def test_release_versions_are_accepted(text):
    assert build_release.check_version(text) == text.removeprefix("v")


@pytest.mark.parametrize("text", ["1.2", "01.2.3", "1.2.3.4", "1.2.3-", "1.2.3/../x", "latest", "vv1.2.3"])
def test_bad_versions_are_refused(text):
    with pytest.raises(SystemExit, match="not a release version"):
        build_release.check_version(text)


def test_build_archives(tmp_path):
    version = "9.9.9"
    paths = build_release.build(tmp_path, version=version)
    names = {p.name for p in paths}
    assert names == {
        "comicsbot-9.9.9-linux.tar.gz",
        "comicsbot-9.9.9-windows.zip",
        "comicsbot-linux.tar.gz",
        "comicsbot-windows.zip",
        "ComicsBot-9.9.9-linux.tar.gz",
        "ComicsBot-9.9.9-windows.zip",
        "RELEASE_NOTES.md",
    }
    linux, windows, notes = (
        tmp_path / "comicsbot-9.9.9-linux.tar.gz",
        tmp_path / "comicsbot-9.9.9-windows.zip",
        tmp_path / "RELEASE_NOTES.md",
    )
    prefix = "comicsbot-9.9.9/"

    with tarfile.open(linux) as tar:
        assert {m.name.split("/")[0] for m in tar.getmembers()} == {"comicsbot-9.9.9"}
        members = {m.name.removeprefix(prefix): m for m in tar.getmembers()}
        assert {"ComicsBot.py", "comicsbot/bot.py", "install.sh", "start-bot.sh", "VERSION", "RELEASE_NOTES.md"} <= set(
            members
        )
        assert tar.extractfile(members["RELEASE_NOTES.md"]).read().decode() == notes.read_text()
        assert not any(name.startswith(("tests/", ".github/", "scripts/")) or name.endswith(".bat") for name in members)
        assert members["install.sh"].mode == 0o755
        assert members["ComicsBot.py"].mode == 0o644
        assert b"\r\n" not in tar.extractfile(members["install.sh"]).read()
        # The version is stamped from the tag into the built copy only.
        assert tar.extractfile(members["VERSION"]).read() == b"9.9.9\n"
        init = tar.extractfile(members["comicsbot/__init__.py"]).read().decode()
        assert '__version__ = "9.9.9"\n' in init
        assert init.replace("9.9.9", __version__) == (ROOT / "comicsbot" / "__init__.py").read_text()
    assert f'__version__ = "{__version__}"' in (ROOT / "comicsbot" / "__init__.py").read_text()

    with zipfile.ZipFile(windows) as archive:
        assert {n.split("/")[0] for n in archive.namelist()} == {"comicsbot-9.9.9"}
        names = {n.removeprefix(prefix) for n in archive.namelist()}
        assert {"ComicsBot.py", "comicsbot/bot.py", "install.ps1", "install.bat", "RELEASE_NOTES.md"} <= names
        assert not any(name.endswith(".sh") or name.startswith("tests/") for name in names)
        bat = archive.read(prefix + "start-bot.bat")
        assert bat.count(b"\r\n") == bat.count(b"\n")
        assert '__version__ = "9.9.9"' in archive.read(prefix + "comicsbot/__init__.py").decode()
        assert archive.read(prefix + "VERSION") == b"9.9.9\r\n"

    assert (tmp_path / "comicsbot-linux.tar.gz").read_bytes() == linux.read_bytes()
    assert (tmp_path / "comicsbot-windows.zip").read_bytes() == windows.read_bytes()
    text = notes.read_text()
    assert text.startswith("## Install")
    assert "| sudo bash -s comicsbot" in text and "cd comicsbot-9.9.9" in text
    assert "comicsbot-9.9.9-setup.exe" in text
    assert not re.search(r"^## ", text[2:], re.M)  # one section: the bot leaves it out of update DMs


def test_finish_adds_copies_and_checksums(tmp_path):
    build_release.build(tmp_path, version="9.9.9")
    with pytest.raises(SystemExit, match="setup.exe"):
        build_release.finish(tmp_path, "9.9.9")
    (tmp_path / "comicsbot-9.9.9-setup.exe").write_bytes(b"MZ setup")
    assets = build_release.finish(tmp_path, "v9.9.9")
    assert [p.name for p in assets] == [
        "comicsbot-9.9.9-linux.tar.gz",
        "comicsbot-9.9.9-windows.zip",
        "comicsbot-9.9.9-setup.exe",
        "comicsbot-linux.tar.gz",
        "comicsbot-windows.zip",
        "comicsbot-setup.exe",
        "ComicsBot-9.9.9-linux.tar.gz",
        "ComicsBot-9.9.9-windows.zip",
        "ComicsBot-9.9.9-setup.exe",
        "SHA256SUMS",
        "SHA256SUMS.txt",
    ]
    assert (tmp_path / "release-assets.txt").read_text() == "".join(f"{p.name}\n" for p in assets)
    sums = (tmp_path / "SHA256SUMS.txt").read_text()
    assert sums == "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in assets[:-1])
    if shutil.which("sha256sum"):
        subprocess.run(["sha256sum", "--check", "--quiet", "SHA256SUMS.txt"], cwd=tmp_path, check=True)
        subprocess.run(["sha256sum", "--check", "--quiet", "SHA256SUMS"], cwd=tmp_path, check=True)


def test_reproducible(tmp_path):
    first = build_release.build(tmp_path / "a", version="1.2.3")
    second = build_release.build(tmp_path / "b", version="1.2.3")
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()


def test_default_build_uses_the_package_version(tmp_path):
    paths = build_release.build(tmp_path)
    assert tmp_path / f"comicsbot-{__version__}-linux.tar.gz" in paths


def test_bad_version_is_refused_on_the_command_line(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/build_release.py", "--out", str(tmp_path), "--version", "v1.2"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not a release version" in result.stderr
    assert not list(tmp_path.iterdir())
