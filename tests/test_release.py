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


def _files(path):
    """{name below the top folder: content} and the set of top folders of an archive."""
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            return {n.split("/", 1)[1]: archive.read(n) for n in names}, {n.split("/")[0] for n in names}
    with tarfile.open(path) as tar:
        members = tar.getmembers()
        files = {m.name.split("/", 1)[1]: tar.extractfile(m).read() for m in members if m.isfile()}
        return files, {m.name.split("/")[0] for m in members}


def test_build_archives(tmp_path):
    paths = build_release.build(tmp_path, version="9.9.9")
    # While LEGACY_ASSETS is in place the versioned names keep the case of 2.3.2 (GitHub refuses two file names
    # that differ only in case); the fixed names are lowercase.
    assert [p.name for p in paths] == [
        "ComicsBot-9.9.9-linux.tar.gz",
        "comicsbot-linux.tar.gz",
        "ComicsBot-9.9.9-windows.zip",
        "comicsbot-windows.zip",
        "RELEASE_NOTES.md",
    ]
    linux, notes = tmp_path / "comicsbot-linux.tar.gz", tmp_path / "RELEASE_NOTES.md"
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

    with zipfile.ZipFile(tmp_path / "comicsbot-windows.zip") as archive:
        assert {n.split("/")[0] for n in archive.namelist()} == {"comicsbot-9.9.9"}
        names = {n.removeprefix(prefix) for n in archive.namelist()}
        assert {"ComicsBot.py", "comicsbot/bot.py", "install.ps1", "install.bat", "RELEASE_NOTES.md"} <= names
        assert not any(name.endswith(".sh") or name.startswith("tests/") for name in names)
        bat = archive.read(prefix + "start-bot.bat")
        assert bat.count(b"\r\n") == bat.count(b"\n")
        assert '__version__ = "9.9.9"' in archive.read(prefix + "comicsbot/__init__.py").decode()
        assert archive.read(prefix + "VERSION") == b"9.9.9\r\n"

    # The versioned archives hold the same files below the top folder updaters up to 2.3.2 require.
    for versioned, fixed in [
        ("ComicsBot-9.9.9-linux.tar.gz", linux.name),
        ("ComicsBot-9.9.9-windows.zip", "comicsbot-windows.zip"),
    ]:
        old_files, old_top = _files(tmp_path / versioned)
        new_files, new_top = _files(tmp_path / fixed)
        assert old_top == {"ComicsBot-9.9.9"} and new_top == {"comicsbot-9.9.9"}
        assert old_files == new_files

    text = notes.read_text()
    assert text.startswith("## Install")
    assert "| sudo bash -s comicsbot" in text and "cd comicsbot-9.9.9" in text
    assert "comicsbot-setup.exe" in text and "ComicsBot-9.9.9-setup.exe" in text
    assert not re.search(r"^## ", text[2:], re.M)  # one section: the bot leaves it out of update DMs


def test_finish_adds_copies_and_checksums(tmp_path):
    build_release.build(tmp_path, version="9.9.9")
    with pytest.raises(SystemExit, match="setup.exe"):
        build_release.finish(tmp_path, "9.9.9")
    (tmp_path / "ComicsBot-9.9.9-setup.exe").write_bytes(b"MZ setup")
    assets = build_release.finish(tmp_path, "v9.9.9")
    assert [p.name for p in assets] == [
        "ComicsBot-9.9.9-linux.tar.gz",
        "ComicsBot-9.9.9-windows.zip",
        "ComicsBot-9.9.9-setup.exe",
        "comicsbot-linux.tar.gz",
        "comicsbot-windows.zip",
        "comicsbot-setup.exe",
        "SHA256SUMS",
        "SHA256SUMS.txt",
    ]
    names = [p.name.casefold() for p in assets]
    assert len(set(names)) == len(names)  # GitHub compares file names ignoring case
    assert (tmp_path / "comicsbot-setup.exe").read_bytes() == b"MZ setup"
    assert (tmp_path / "release-assets.txt").read_text() == "".join(f"{p.name}\n" for p in assets)
    sums = (tmp_path / "SHA256SUMS.txt").read_text()
    assert sums == "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in assets[:-1])
    legacy = (tmp_path / "SHA256SUMS").read_text()
    assert legacy == "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in assets[:3])
    if shutil.which("sha256sum"):
        subprocess.run(["sha256sum", "--check", "--quiet", "SHA256SUMS.txt"], cwd=tmp_path, check=True)
        subprocess.run(["sha256sum", "--check", "--quiet", "SHA256SUMS"], cwd=tmp_path, check=True)


@pytest.mark.parametrize(
    "names",
    [["comicsbot-1.0.0-setup.exe", "ComicsBot-1.0.0-setup.exe"], ["SHA256SUMS", "sha256sums"], ["a.zip", "a.zip"]],
)
def test_file_names_equal_ignoring_case_fail_the_build(names):
    with pytest.raises(SystemExit, match="equal ignoring case"):
        build_release.check_unique_names(["other.txt", *names])


def test_finish_refuses_a_case_clash(tmp_path, monkeypatch):
    """What broke the first 2.4.0 release run: comicsbot-X.Y.Z-* next to the legacy ComicsBot-X.Y.Z-*."""
    monkeypatch.setattr(
        build_release,
        "fixed_names",
        lambda: {
            "linux": "comicsbot-linux.tar.gz",
            "windows": "comicsbot-windows.zip",
            "setup": "COMICSBOT-9.9.9-SETUP.EXE",
        },
    )
    build_release.build(tmp_path, version="9.9.9")
    (tmp_path / "ComicsBot-9.9.9-setup.exe").write_bytes(b"MZ setup")
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(SystemExit, match="equal ignoring case"):
        build_release.finish(tmp_path, "9.9.9")
    # Refused before copying anything (on Windows the copy itself would fail, the names being the same file).
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_lowercase_only_once_legacy_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(build_release, "VERSIONED_STEM", build_release.SLUG)
    build_release.build(tmp_path, version="9.9.9")
    (tmp_path / "comicsbot-9.9.9-setup.exe").write_bytes(b"MZ setup")
    names = [p.name for p in build_release.finish(tmp_path, "9.9.9")]
    assert names[:3] == ["comicsbot-9.9.9-linux.tar.gz", "comicsbot-9.9.9-windows.zip", "comicsbot-9.9.9-setup.exe"]
    assert _files(tmp_path / "comicsbot-9.9.9-linux.tar.gz")[1] == {"comicsbot-9.9.9"}
    # Same top folder, so the versioned and fixed archives are byte-identical again.
    assert (tmp_path / "comicsbot-9.9.9-linux.tar.gz").read_bytes() == (
        tmp_path / "comicsbot-linux.tar.gz"
    ).read_bytes()


def test_reproducible(tmp_path):
    first = build_release.build(tmp_path / "a", version="1.2.3")
    second = build_release.build(tmp_path / "b", version="1.2.3")
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()


def test_default_build_uses_the_package_version(tmp_path):
    paths = build_release.build(tmp_path)
    assert tmp_path / f"ComicsBot-{__version__}-linux.tar.gz" in paths


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
