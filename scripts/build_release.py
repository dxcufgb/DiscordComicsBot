"""Build the release archives for Linux and Windows.

    python scripts/build_release.py [--expect-version v2.2.0] [--out dist]

The version always comes from ``__version__`` in comicsbot/__init__.py (pyproject.toml must
match). Creates dist/ComicsBot-<version>-linux.tar.gz, dist/ComicsBot-<version>-windows.zip,
dist/SHA256SUMS and dist/RELEASE_NOTES.md (also inside both archives: the bot DMs it to server
admins after a manual upgrade). Only the files needed to install and run the bot
are included (no tests, CI or development files), and building the same commit twice gives
byte-identical archives.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COMMON = ["ComicsBot.py", "comicsbot", "requirements.txt", ".env.example", "README.md"]
PLATFORM_FILES = {
    "linux": ["start-bot.sh", "install.sh"],
    "windows": ["start-bot.bat", "install.ps1", "install.bat"],
}
EXECUTABLE = {"start-bot.sh", "install.sh"}
ZIP_EPOCH = 315532800  # 1980-01-01, the earliest time a zip file can store
CRLF_SUFFIXES = {".bat", ".ps1"}

RELEASE_NOTES = """\
## Install

**Linux** (systemd): download `ComicsBot-{version}-linux.tar.gz`, then

```sh
tar xzf ComicsBot-{version}-linux.tar.gz
cd ComicsBot-{version}
sudo ./install.sh
```

**Windows**: download and run `ComicsBot-{version}-setup.exe`. Or extract `ComicsBot-{version}-windows.zip`
and double-click `install.bat` (run it as administrator with `install.bat -AtStartup` to start the bot at boot
instead of at logon). The setup is not code-signed, so Windows SmartScreen may ask you to confirm
("More info" → "Run anyway").

The installer sets up Python if needed, asks for the bot token, installs the dependencies and
starts the bot as a service. Running a newer release's installer upgrades in place and keeps
`.env` and all bot data. Coming from a git checkout? Add `--migrate-from /path/to/checkout`
(Linux) or `-MigrateFrom C:\\path\\to\\checkout` (Windows) to bring its settings and data along.

The bot needs **Server Members Intent** turned on (Developer Portal → your application → Bot →
Privileged Gateway Intents), or Discord refuses the login. Turn it on before upgrading from 2.2.0 or older.

Verify downloads with `sha256sum -c SHA256SUMS`.
"""


def package_version(root: Path = ROOT) -> str:
    """The release version: comicsbot.__version__, which pyproject.toml must match."""
    text = (root / "comicsbot" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("could not find __version__ in comicsbot/__init__.py")
    version = match.group(1)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    if not project or project.group(1) != version:
        raise SystemExit(f"pyproject.toml version does not match comicsbot.__version__ ({version})")
    return version


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


def collect(root: Path, platform: str) -> list[tuple[str, bytes]]:
    """Return (relative path, content) pairs for one platform, with fixed line endings."""
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
            data = member.read_bytes().replace(b"\r\n", b"\n")
            if member.suffix in CRLF_SUFFIXES:
                data = data.replace(b"\n", b"\r\n")
            files.append((member.relative_to(root).as_posix(), data))
    return files


def build(out_dir: Path, root: Path = ROOT, expect_version: str | None = None) -> list[Path]:
    version = package_version(root)
    if expect_version and expect_version.removeprefix("v") != version:
        raise SystemExit(
            f"tag version {expect_version} does not match comicsbot.__version__ {version}; "
            "bump the version in comicsbot/__init__.py and pyproject.toml first"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"ComicsBot-{version}"
    mtime = source_date_epoch(root)
    version_file = ("VERSION", f"{version}\n".encode())
    notes_text = RELEASE_NOTES.format(version=version)
    notes_file = ("RELEASE_NOTES.md", notes_text.encode())

    linux = out_dir / f"{prefix}-linux.tar.gz"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in sorted([*collect(root, "linux"), version_file, notes_file]):
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            info.mtime = mtime
            info.mode = 0o755 if Path(name).name in EXECUTABLE else 0o644
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    # A fixed gzip header (no file name, fixed time) keeps the archive reproducible.
    with linux.open("wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=mtime, filename="") as gz:
        gz.write(raw.getvalue())

    windows = out_dir / f"{prefix}-windows.zip"
    stamp = time.gmtime(max(mtime, ZIP_EPOCH))[:6]
    windows_files = [
        *collect(root, "windows"),
        (version_file[0], version_file[1].replace(b"\n", b"\r\n")),
        notes_file,
    ]
    with zipfile.ZipFile(windows, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(windows_files):
            info = zipfile.ZipInfo(f"{prefix}/{name}", stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)

    sums = out_dir / "SHA256SUMS"
    sums.write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in (linux, windows)),
        encoding="utf-8",
    )
    notes = out_dir / "RELEASE_NOTES.md"
    notes.write_text(notes_text, encoding="utf-8")
    return [linux, windows, sums, notes]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect-version", help="fail unless comicsbot.__version__ equals this (e.g. the git tag)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist)")
    args = parser.parse_args()
    for path in build(args.out, expect_version=args.expect_version):
        print(path)


if __name__ == "__main__":
    sys.exit(main())
