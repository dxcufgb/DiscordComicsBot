"""Installations on 2.3.2 or older update with their own updater, which knows only the old file names.

tests/updater_2_3_2.py is comicsbot/updater.py exactly as released in 2.3.2. It must be able to update to a
release built today from the legacy files (LEGACY_ASSETS in scripts/build_release.py): ComicsBot-X.Y.Z-*.tar.gz /
-windows.zip with the top folder ComicsBot-X.Y.Z/, and SHA256SUMS. Delete this test, tests/updater_2_3_2.py and
LEGACY_ASSETS together once no installation runs 2.3.2 or older.
"""

import hashlib
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests import updater_2_3_2 as old_updater
from tests.test_updater import TOKEN, FakeGitHub, FakeRunner, make_install

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_release  # noqa: E402

VERSION = "9.9.9"


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    build_release.build(out, version=VERSION)
    (out / build_release.setup_name(VERSION)).write_bytes(b"MZ setup")
    return out, {p.name: p.read_bytes() for p in build_release.finish(out, VERSION)}


@pytest.fixture(autouse=True)
def not_root(monkeypatch):
    monkeypatch.setattr(old_updater, "_running_as_root", lambda: False)


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_the_2_3_2_updater_installs_the_legacy_files(tmp_path, release, platform):
    _, files = release
    install = make_install(tmp_path / "app", "2.3.2")
    shipped = {}

    class Runner(FakeRunner):
        def __call__(self, command, timeout):
            for part in command:
                if part.endswith(("install.sh", "install.ps1")):
                    updater = Path(part).parent / "comicsbot" / "updater.py"
                    shipped["updater"] = updater.read_text(encoding="utf-8")
            return super().__call__(command, timeout)

    runner = Runner(install)
    github = FakeGitHub(f"v{VERSION}", files)
    code = old_updater.update(
        install, TOKEN, platform=platform, fetch=github, runner=runner, backup_root=tmp_path / "backups"
    )
    assert code == 0
    installer = next(c for c in runner.commands[1] if c.endswith(("install.sh", "install.ps1")))
    # The installer that runs is the new release's, from the old top folder name.
    assert Path(installer).parent.name == f"ComicsBot-{VERSION}"
    assert (install / "VERSION").read_text().strip() == VERSION
    # The installer puts in the new updater (install.sh / install.ps1 copy the release's comicsbot/updater.py).
    assert 'CHECKSUMS_ASSET = "SHA256SUMS.txt"' in shipped["updater"]


def test_legacy_archives_hold_the_same_files_as_the_new_ones(release):
    out, _ = release
    with (
        tarfile.open(out / f"comicsbot-{VERSION}-linux.tar.gz") as new,
        tarfile.open(out / f"ComicsBot-{VERSION}-linux.tar.gz") as old,
    ):
        new_files = {m.name.split("/", 1)[1]: new.extractfile(m).read() for m in new if m.isfile()}
        old_files = {m.name.split("/", 1)[1]: old.extractfile(m).read() for m in old if m.isfile()}
        assert {m.name.split("/", 1)[0] for m in old} == {f"ComicsBot-{VERSION}"}
    assert new_files == old_files
    with (
        zipfile.ZipFile(out / f"comicsbot-{VERSION}-windows.zip") as new,
        zipfile.ZipFile(out / f"ComicsBot-{VERSION}-windows.zip") as old,
    ):
        assert {n.split("/", 1)[1]: new.read(n) for n in new.namelist()} == {
            n.split("/", 1)[1]: old.read(n) for n in old.namelist()
        }
        assert {n.split("/", 1)[0] for n in old.namelist()} == {f"ComicsBot-{VERSION}"}


def test_legacy_checksums_have_todays_format(release):
    out, files = release
    names = [f"ComicsBot-{VERSION}-linux.tar.gz", f"ComicsBot-{VERSION}-windows.zip", f"ComicsBot-{VERSION}-setup.exe"]
    expected = "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in names)
    assert files["SHA256SUMS"].decode() == expected
    assert files[f"ComicsBot-{VERSION}-setup.exe"] == files[f"comicsbot-{VERSION}-setup.exe"]
