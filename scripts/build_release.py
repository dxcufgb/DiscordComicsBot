"""Build the release files for Linux and Windows.

    python scripts/build_release.py [--version 1.2.3] [--out dist]            # archives
    python scripts/build_release.py --version 1.2.3 --out dist --finish       # after the setup.exe is built

The version comes from the release tag (the Release workflow passes it with ``--version``) and is stamped into
the built copy only: ``comicsbot/__init__.py`` and ``VERSION`` in the archives. Nobody bumps the version in the
code. Without ``--version`` the repository's own ``__version__`` is used (local and CI builds).

The first step writes, into the output folder (``V`` = ``VERSIONED_STEM`` below, ``ComicsBot`` for now):

- ``V-X.Y.Z-linux.tar.gz``: one top folder ``V-X.Y.Z/`` with ``install.sh`` at its top
- ``V-X.Y.Z-windows.zip``: the same for Windows (``install.bat`` / ``install.ps1``)
- ``comicsbot-linux.tar.gz``, ``comicsbot-windows.zip``: the same files under fixed names, with the top folder
  ``comicsbot-X.Y.Z/``
- ``RELEASE_NOTES.md`` (also inside the archives: the bot DMs it to server admins after a manual upgrade; the
  Release workflow puts it above the generated notes)

``scripts/build_setup.ps1`` then builds ``V-X.Y.Z-setup.exe`` on Windows, and ``--finish`` adds
``comicsbot-setup.exe``, the legacy ``SHA256SUMS`` and ``SHA256SUMS.txt`` (``sha256sum`` format, every other
release file), checks that no two file names are equal ignoring case (GitHub refuses that), and writes the list of
files to attach to ``release-assets.txt``.

Only the files needed to install and run the bot are included (no tests, CI or development files), and
building the same commit with the same version twice gives byte-identical archives.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SLUG = "comicsbot"
# The release version, as the Release workflow checks it (a leading "v" is stripped first).
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?")

COMMON = ["ComicsBot.py", "comicsbot", "requirements.txt", ".env.example", "README.md"]
PLATFORM_FILES = {
    "linux": ["start-bot.sh", "install.sh"],
    "windows": ["start-bot.bat", "install.ps1", "install.bat"],
}
EXECUTABLE = {"start-bot.sh", "install.sh"}
ZIP_EPOCH = 315532800  # 1980-01-01, the earliest time a zip file can store
CRLF_SUFFIXES = {".bat", ".ps1"}
INIT_FILE = "comicsbot/__init__.py"
ASSET_LIST = "release-assets.txt"
CHECKSUMS = "SHA256SUMS.txt"

# --- LEGACY_ASSETS -----------------------------------------------------------------------------------------
# Installations on 2.3.2 and older with automatic updates on look for ComicsBot-X.Y.Z-linux.tar.gz /
# -windows.zip and SHA256SUMS, extract the archive by hand, accept only files below a top folder named exactly
# "ComicsBot-X.Y.Z/" (comicsbot/updater.py at 2.3.2, kept as tests/updater_2_3_2.py) and run the install.sh /
# install.ps1 in it. GitHub release file names are unique ignoring case, so comicsbot-X.Y.Z-* and ComicsBot-X.Y.Z-*
# cannot both be attached: while this block is in place the versioned files keep the old case ("ComicsBot",
# also their top folder), and SHA256SUMS lists them as before. The fixed names (comicsbot-linux.tar.gz, ...) and
# updaters after 2.3.2 do not care: those match the versioned names ignoring case and accept either top folder.
# To drop it once every installation runs a release after 2.3.2: set VERSIONED_STEM = SLUG (lowercase names and
# top folder), delete LEGACY_CHECKSUMS and its use in finish(), tests/test_legacy_update.py and
# tests/updater_2_3_2.py.
VERSIONED_STEM = "ComicsBot"
LEGACY_CHECKSUMS = "SHA256SUMS"  # sha256sum of the three versioned files (linux, windows, setup.exe)
# --- end of LEGACY_ASSETS ----------------------------------------------------------------------------------

RELEASE_NOTES = """\
## Install

**Linux** (systemd), as root:

```sh
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s {slug}
```

It asks for a fine-grained GitHub token with read-only *Contents* access to this repository (it is private).
Or download `{slug}-linux.tar.gz`, then

```sh
tar xzf {slug}-linux.tar.gz
cd {slug}-{version}
sudo ./install.sh
```

**Windows**: download and run `{slug}-setup.exe` (or `{stem}-{version}-setup.exe`, the same file). Or extract
`{slug}-windows.zip` and double-click `install.bat` (run it as administrator with `install.bat -AtStartup` to start
the bot at boot instead of at logon). The setup is not code-signed, so Windows SmartScreen may ask you to confirm
("More info" → "Run anyway").

The installer sets up Python if needed, asks for the bot token, installs the dependencies and
starts the bot as a service. Running a newer release's installer upgrades in place and keeps
`.env` and all bot data. Coming from a git checkout? Add `--migrate-from /path/to/checkout`
(Linux) or `-MigrateFrom C:\\path\\to\\checkout` (Windows) to bring its settings and data along.

With `UPDATE_NOTIFY=admins` (the default) the bot DMs update notices to server admins, which needs
**Server Members Intent** (Developer Portal → your application → Bot → Privileged Gateway Intents). Without it the
bot logs a warning and notifies the server owners only; set `UPDATE_NOTIFY=owner` or `off` in `.env` to silence it.

Verify downloads with `sha256sum -c --ignore-missing SHA256SUMS.txt`.
"""


def check_version(text: str) -> str:
    """The release version from ``1.2.3`` or ``v1.2.3``; anything else is refused."""
    version = text.strip().removeprefix("v")
    if not VERSION_RE.fullmatch(version):
        raise SystemExit(f"not a release version (expected 1.2.3 or 1.2.3-rc.1): {text!r}")
    return version


def package_version(root: Path = ROOT) -> str:
    """The repository's own version (the last release's): comicsbot.__version__, which pyproject.toml must match."""
    text = (root / INIT_FILE).read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit(f"could not find __version__ in {INIT_FILE}")
    version = match.group(1)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    if not project or project.group(1) != version:
        raise SystemExit(f"pyproject.toml version does not match comicsbot.__version__ ({version})")
    return version


def stamp_version(data: bytes, version: str) -> bytes:
    """``comicsbot/__init__.py`` with ``__version__`` set to the release version."""
    stamped, count = re.subn(rb'(?m)^__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"'.encode(), data)
    if count != 1:
        raise SystemExit(f"could not stamp the version into {INIT_FILE}")
    return stamped


def source_date_epoch(root: Path = ROOT) -> int:
    """Reproducible timestamps: SOURCE_DATE_EPOCH, else the last commit time."""
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],  # noqa: S607 - git from PATH is intended
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return int(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return ZIP_EPOCH


def collect(root: Path, platform: str, version: str | None = None) -> list[tuple[str, bytes]]:
    """Return (relative path, content) pairs for one platform, with fixed line endings and the version stamped."""
    files: list[tuple[str, bytes]] = []
    for name in COMMON + PLATFORM_FILES[platform]:
        path = root / name
        if path.is_dir():
            members = sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
        elif path.is_file():
            members = [path]
        else:
            raise SystemExit(f"missing release file: {name}")
        for member in members:
            relative = member.relative_to(root).as_posix()
            data = member.read_bytes().replace(b"\r\n", b"\n")
            if relative == INIT_FILE and version is not None:
                data = stamp_version(data, version)
            if member.suffix in CRLF_SUFFIXES:
                data = data.replace(b"\n", b"\r\n")
            files.append((relative, data))
    return files


def write_tar(path: Path, prefix: str, files: list[tuple[str, bytes]], mtime: int) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in sorted(files):
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            info.mtime = mtime
            info.mode = 0o755 if Path(name).name in EXECUTABLE else 0o644
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    # A fixed gzip header (no file name, fixed time) keeps the archive reproducible.
    with path.open("wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=mtime, filename="") as gz:
        gz.write(raw.getvalue())


def write_zip(path: Path, prefix: str, files: list[tuple[str, bytes]], mtime: int) -> None:
    stamp = time.gmtime(max(mtime, ZIP_EPOCH))[:6]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files):
            info = zipfile.ZipInfo(f"{prefix}/{name}", stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)


def archive_names(version: str) -> dict[str, str]:
    return {
        "linux": f"{VERSIONED_STEM}-{version}-linux.tar.gz",
        "windows": f"{VERSIONED_STEM}-{version}-windows.zip",
    }


def fixed_names() -> dict[str, str]:
    """Names that never change, so releases/latest/download/<name> links keep working."""
    return {"linux": f"{SLUG}-linux.tar.gz", "windows": f"{SLUG}-windows.zip", "setup": f"{SLUG}-setup.exe"}


def setup_name(version: str) -> str:
    return f"{VERSIONED_STEM}-{version}-setup.exe"


def check_unique_names(names: list[str]) -> None:
    """GitHub refuses a release file whose name equals another one ignoring case."""
    seen: dict[str, str] = {}
    for name in names:
        other = seen.setdefault(name.casefold(), name)
        if other != name or names.count(name) > 1:
            raise SystemExit(f"release file names {other!r} and {name!r} are equal ignoring case")


def build(out_dir: Path, root: Path = ROOT, version: str | None = None) -> list[Path]:
    """Build the archives (versioned and fixed names) and RELEASE_NOTES.md; return their paths."""
    version = check_version(version) if version else package_version(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    mtime = source_date_epoch(root)
    notes_text = RELEASE_NOTES.format(version=version, slug=SLUG, stem=VERSIONED_STEM)
    notes_file = ("RELEASE_NOTES.md", notes_text.encode())
    contents = {
        "linux": [*collect(root, "linux", version), ("VERSION", f"{version}\n".encode()), notes_file],
        "windows": [*collect(root, "windows", version), ("VERSION", f"{version}\r\n".encode()), notes_file],
    }
    writers = {"linux": write_tar, "windows": write_zip}

    built: list[Path] = []
    for platform, name in archive_names(version).items():
        path = out_dir / name
        writers[platform](path, f"{VERSIONED_STEM}-{version}", contents[platform], mtime)
        fixed = out_dir / fixed_names()[platform]
        writers[platform](fixed, f"{SLUG}-{version}", contents[platform], mtime)
        built += [path, fixed]
    check_unique_names([p.name for p in built])

    notes = out_dir / "RELEASE_NOTES.md"
    notes.write_text(notes_text, encoding="utf-8")
    return [*built, notes]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finish(out_dir: Path, version: str) -> list[Path]:
    """Add the setup.exe copy and the checksum files; return every file to attach to the release."""
    version = check_version(version)
    versioned = [*(out_dir / name for name in archive_names(version).values()), out_dir / setup_name(version)]
    fixed = [out_dir / name for name in fixed_names().values()]
    for path in [*versioned, *fixed[:2]]:
        if not path.is_file():
            raise SystemExit(f"missing {path}: build the archives (and the setup.exe) first")
    # Before writing anything: on Windows two names that differ only in case are the same file, so a copy would
    # fail (or overwrite) before a later check could report the clash.
    check_unique_names([p.name for p in [*versioned, *fixed]] + [LEGACY_CHECKSUMS, CHECKSUMS])
    shutil.copyfile(versioned[2], fixed[2])
    assets = [*versioned, *fixed]

    # LEGACY_ASSETS: SHA256SUMS as up to 2.3.2 (the three versioned files).
    legacy_sums = out_dir / LEGACY_CHECKSUMS
    legacy_sums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in versioned), encoding="utf-8")
    assets.append(legacy_sums)

    sums = out_dir / CHECKSUMS
    sums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in assets), encoding="utf-8")
    assets.append(sums)
    check_unique_names([p.name for p in assets])
    (out_dir / ASSET_LIST).write_text("".join(f"{p.name}\n" for p in assets), encoding="utf-8")
    return assets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="release version, e.g. 1.2.3 (default: comicsbot.__version__)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist)")
    parser.add_argument(
        "--finish", action="store_true", help=f"add the setup.exe copies and checksums, list the files in {ASSET_LIST}"
    )
    args = parser.parse_args()
    if args.finish:
        paths = finish(args.out, args.version or package_version())
    else:
        paths = build(args.out, version=args.version)
    for path in paths:
        print(path)


if __name__ == "__main__":
    sys.exit(main())
