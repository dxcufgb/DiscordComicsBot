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


def test_version_comes_from_package():
    assert build_release.package_version() == __version__


def test_pyproject_must_match(tmp_path):
    shutil.copytree(ROOT / "comicsbot", tmp_path / "comicsbot")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(re.sub(r'(?m)^version = ".*"', 'version = "0.0.1"', pyproject))
    with pytest.raises(SystemExit, match="does not match"):
        build_release.package_version(tmp_path)


def test_build_archives(tmp_path):
    linux, windows, sums, notes = build_release.build(tmp_path, expect_version=f"v{__version__}")
    prefix = f"ComicsBot-{__version__}/"

    with tarfile.open(linux) as tar:
        members = {m.name.removeprefix(prefix): m for m in tar.getmembers()}
        assert {"ComicsBot.py", "comicsbot/bot.py", "install.sh", "start-bot.sh", "VERSION"} <= set(members)
        assert not any(name.startswith(("tests/", ".github/", "scripts/")) or name.endswith(".bat") for name in members)
        assert members["install.sh"].mode == 0o755
        assert members["ComicsBot.py"].mode == 0o644
        assert b"\r\n" not in tar.extractfile(members["install.sh"]).read()
        assert tar.extractfile(members["VERSION"]).read() == f"{__version__}\n".encode()

    with zipfile.ZipFile(windows) as archive:
        names = {n.removeprefix(prefix) for n in archive.namelist()}
        assert {"ComicsBot.py", "comicsbot/bot.py", "install.ps1", "install.bat", "start-bot.bat"} <= names
        assert not any(name.endswith(".sh") or name.startswith("tests/") for name in names)
        bat = archive.read(prefix + "start-bot.bat")
        assert bat.count(b"\r\n") == bat.count(b"\n")

    assert re.fullmatch(rf"[0-9a-f]{{64}}  {linux.name}\n[0-9a-f]{{64}}  {windows.name}\n", sums.read_text())
    assert notes.read_text().startswith("## Install")
    assert f"ComicsBot-{__version__}-linux.tar.gz" in notes.read_text()


def test_reproducible(tmp_path):
    first = build_release.build(tmp_path / "a")
    second = build_release.build(tmp_path / "b")
    for a, b in zip(first[:2], second[:2], strict=True):
        assert a.read_bytes() == b.read_bytes()


@pytest.mark.parametrize("tag", [__version__, f"v{__version__}"])
def test_expected_version_accepts_tag_with_or_without_v(tmp_path, tag):
    build_release.build(tmp_path, expect_version=tag)


def test_wrong_tag_is_rejected(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/build_release.py", "--out", str(tmp_path), "--expect-version", "v0.0.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "does not match" in result.stderr
