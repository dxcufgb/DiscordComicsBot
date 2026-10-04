"""Build the release archives for Linux and Windows.

    python scripts/build_release.py [--version X.Y.Z] [--out dist]

Creates dist/ComicsBot-<version>-linux.tar.gz, dist/ComicsBot-<version>-windows.zip,
dist/SHA256SUMS and dist/RELEASE_NOTES.md. Only the files needed to install and run
the bot are included (no tests, CI or development files).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
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
CRLF_SUFFIXES = {".bat", ".ps1"}

RELEASE_NOTES = """\
## Install

**Linux** (systemd): download `ComicsBot-{version}-linux.tar.gz`, then

```sh
tar xzf ComicsBot-{version}-linux.tar.gz
cd ComicsBot-{version}
sudo ./install.sh
```

**Windows**: download `ComicsBot-{version}-windows.zip`, extract it and double-click `install.bat`
(run it as administrator with `install.bat -AtStartup` to start the bot at boot instead of at logon).

The installer sets up Python if needed, asks for the bot token, installs the dependencies and
starts the bot as a service. Running a newer release's installer upgrades in place and keeps
`.env` and all bot data. Coming from a git checkout? Add `--migrate-from /path/to/checkout`
(Linux) or `-MigrateFrom C:\\path\\to\\checkout` (Windows) to bring its settings and data along.

Verify downloads with `sha256sum -c SHA256SUMS`.
"""


def package_version(root: Path = ROOT) -> str:
    text = (root / "comicsbot" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("could not find __version__ in comicsbot/__init__.py")
    return match.group(1)


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


def build(version: str, out_dir: Path, root: Path = ROOT) -> list[Path]:
    expected = package_version(root)
    if version != expected:
        raise SystemExit(
            f"release version {version} does not match comicsbot.__version__ {expected}; "
            "bump the version in comicsbot/__init__.py and pyproject.toml first"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"ComicsBot-{version}"
    mtime = int(os.environ.get("SOURCE_DATE_EPOCH", time.time()))
    version_file = ("VERSION", f"{version}\n".encode())

    linux = out_dir / f"{prefix}-linux.tar.gz"
    with tarfile.open(linux, "w:gz") as tar:
        for name, data in [*collect(root, "linux"), version_file]:
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            info.mtime = mtime
            info.mode = 0o755 if Path(name).name in EXECUTABLE else 0o644
            tar.addfile(info, io.BytesIO(data))

    windows = out_dir / f"{prefix}-windows.zip"
    with zipfile.ZipFile(windows, "w", zipfile.ZIP_DEFLATED) as archive:
        stamp = time.gmtime(max(mtime, 315532800))[:6]  # zip cannot store dates before 1980
        for name, data in [*collect(root, "windows"), (version_file[0], version_file[1].replace(b"\n", b"\r\n"))]:
            archive.writestr(zipfile.ZipInfo(f"{prefix}/{name}", stamp), data, zipfile.ZIP_DEFLATED)

    sums = out_dir / "SHA256SUMS"
    sums.write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in (linux, windows)),
        encoding="utf-8",
    )
    notes = out_dir / "RELEASE_NOTES.md"
    notes.write_text(RELEASE_NOTES.format(version=version), encoding="utf-8")
    return [linux, windows, sums, notes]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="release version without the leading v (default: comicsbot.__version__)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist)")
    args = parser.parse_args()
    for path in build(args.version or package_version(), args.out):
        print(path)


if __name__ == "__main__":
    sys.exit(main())
