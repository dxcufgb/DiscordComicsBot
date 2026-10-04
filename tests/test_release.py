import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_release  # noqa: E402


def test_versions_match():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == build_release.package_version()


def test_build_archives(tmp_path):
    version = build_release.package_version()
    linux, windows, sums, notes = build_release.build(version, tmp_path)
    prefix = f"ComicsBot-{version}/"

    with tarfile.open(linux) as tar:
        members = {m.name.removeprefix(prefix): m for m in tar.getmembers()}
        assert {"ComicsBot.py", "comicsbot/bot.py", "install.sh", "start-bot.sh", "VERSION"} <= set(members)
        assert not any(name.startswith(("tests/", ".github/", "scripts/")) or name.endswith(".bat") for name in members)
        assert members["install.sh"].mode == 0o755
        assert members["ComicsBot.py"].mode == 0o644
        assert b"\r\n" not in tar.extractfile(members["install.sh"]).read()
        assert tar.extractfile(members["VERSION"]).read() == f"{version}\n".encode()

    with zipfile.ZipFile(windows) as archive:
        names = {n.removeprefix(prefix) for n in archive.namelist()}
        assert {"ComicsBot.py", "comicsbot/bot.py", "install.ps1", "install.bat", "start-bot.bat"} <= names
        assert not any(name.endswith(".sh") or name.startswith("tests/") for name in names)
        bat = archive.read(prefix + "start-bot.bat")
        assert bat.count(b"\r\n") == bat.count(b"\n")

    assert re.fullmatch(rf"[0-9a-f]{{64}}  {linux.name}\n[0-9a-f]{{64}}  {windows.name}\n", sums.read_text())
    assert version in notes.read_text()


def test_version_mismatch_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="does not match"):
        build_release.build("0.0.0", tmp_path)
