"""Automatic updates for a ComicsBot installed from a release archive.

    python -I updater.py --install-dir DIR --token-file FILE [--check] [--log-file FILE] [--lock-file FILE]

Looks up the latest release of the private GitHub repository, and when it is newer than the
installed version downloads this platform's archive (``comicsbot-X.Y.Z-linux.tar.gz`` or
``comicsbot-X.Y.Z-windows.zip``, the name compared ignoring case: releases still use ``ComicsBot-X.Y.Z-...`` for
the updaters of 2.3.2 and older), checks it against the release's ``SHA256SUMS.txt``,
extracts it into a fresh temporary folder and runs that release's installer in upgrade mode
(the same thing a manual upgrade does), which keeps ``.env``, ``state.json`` and the logs. If the
installer fails, the previous program files are put back and, if the bot was running, it is restarted and
checked again after a short wait. If that rollback fails too, "ROLLBACK FAILED" is logged with the steps to
recover by hand, the backup is kept (``--backup-dir``) and the exit code is 3. With ``--check`` the exit
code is 10 when a newer release is available and 0 when the installed version is the latest.

A latest release without these files (one published by hand whose files are still being attached, or a
release of 2.3.2 or older, which used other names) is reported and left alone: nothing is changed, and the
next run tries again. Newer releases also carry the names the updaters of 2.3.2 and older look for (see
``LEGACY_ASSETS`` in scripts/build_release.py), so those keep updating to them.

It is started by the ``comicsbot-update`` systemd timer (Linux, as root) or the
``ComicsBot-Update`` scheduled task (Windows, as the account that runs the bot); see the README.
The installers copy this file out of the install folder and run it from there, so it must stay
self-contained: standard library only, no imports from the ``comicsbot`` package.

The GitHub token is read from ``--token-file``, sent only to api.github.com and never logged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePosixPath
from typing import IO, Protocol
from urllib.parse import urljoin, urlsplit

REPO = "dxcufgb/DiscordComicsBot"
API_HOST = "api.github.com"
LATEST_RELEASE_URL = f"https://{API_HOST}/repos/{REPO}/releases/latest"
ASSET_URL_PREFIX = f"https://{API_HOST}/repos/{REPO}/releases/assets/"
# Release file names: <SLUG>-X.Y.Z-linux.tar.gz / -windows.zip with one top folder <SLUG>-X.Y.Z/, and SHA256SUMS.txt.
SLUG = "comicsbot"
CHECKSUMS_ASSET = "SHA256SUMS.txt"
USER_AGENT = "ComicsBot-updater"

REQUEST_TIMEOUT = 60  # seconds, per request
MAX_REDIRECTS = 5
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_CHECKSUMS_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
INSTALLER_TIMEOUT = 20 * 60  # seconds
SERVICE_TIMEOUT = 5 * 60  # seconds, for stopping, restarting or querying the bot
ROLLBACK_WAIT = 15  # seconds the restored bot gets to start before it is checked
MAX_NOTES_CHARS = 100_000
# Release notes for the bot, which DMs them to server admins after the update (comicsbot/announce.py).
UPDATE_NOTICE = "update-notice.json"
# The bot's data folder inside the install folder (owned by the bot's account on Linux, where the program
# files belong to root); without it (Windows) the bot's data is in the install folder itself.
DATA_DIR_NAME = "data"

# Program files a release installs. They are backed up before an update and put back if it fails;
# everything else in the install folder (.env, the data, logs) is left to the installer. On Linux the
# virtual environment is root's too (the installer builds it) and is backed up as well.
PROGRAM_FILES = (
    "ComicsBot.py",
    "comicsbot",
    "requirements.txt",
    ".env.example",
    "README.md",
    "VERSION",
    "start-bot.sh",
    "install.sh",
    "start-bot.bat",
    "install.ps1",
    "install.bat",
    "RELEASE_NOTES.md",
)
LINUX_PROGRAM_FILES = (*PROGRAM_FILES, ".venv")
# Environment variables that must not reach the release installer.
SECRET_VARIABLES = ("DISCORD_TOKEN", "COMICSBOT_GITHUB_TOKEN", "INSTALL_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")

EXIT_OK = 0
EXIT_FAILED = 1
# The update failed and so did putting the previous version back: the bot needs a hand.
EXIT_ROLLBACK_FAILED = 3
# --check: a newer release is available.
EXIT_UPDATE_AVAILABLE = 10
EXIT_BUSY = 73

log = logging.getLogger("comicsbot.updater")


class UpdateError(Exception):
    """An update step failed; the message is safe to log (it never contains the token)."""


class UpdateBusy(Exception):
    """Another update is running."""


# -- versions ----------------------------------------------------------------

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?")


def parse_version(text: str) -> tuple:
    """A sortable key for versions like 2.2.0, v2.3.0 or 2.3.0-rc1 (a pre-release sorts before its release)."""
    match = _VERSION_RE.fullmatch(text.strip())
    if not match:
        raise UpdateError(f"not a version number: {text.strip()[:40]!r}")
    numbers = (int(match[1]), int(match[2]), int(match[3] or 0))
    if match[4] is None:
        return (numbers, (1,))
    # Compare numbers in pre-release labels as numbers, so rc10 sorts after rc9.
    parts = tuple(
        tuple((0, int(run), "") if run.isdigit() else (1, 0, run) for run in re.findall(r"\d+|\D+", part))
        for part in match[4].split(".")
    )
    return (numbers, (0, parts))


def is_newer(candidate: str, installed: str) -> bool:
    return parse_version(candidate) > parse_version(installed)


def installed_version(install_dir: Path) -> str:
    """The version a release installed: its VERSION file (or ``__version__`` as a fallback)."""
    version_file = install_dir / "VERSION"
    if version_file.is_file():
        version = version_file.read_text(encoding="utf-8").strip()
    else:
        init = install_dir / "comicsbot" / "__init__.py"
        match = (
            re.search(r'^__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"), re.M)
            if init.is_file()
            else None
        )
        if not match:
            raise UpdateError(f"no ComicsBot release is installed in {install_dir}")
        version = match[1]
    parse_version(version)
    return version


# -- HTTP --------------------------------------------------------------------


class Response(Protocol):
    status: int

    def header(self, name: str) -> str | None: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


# fetch(url, headers) -> Response. It must not follow redirects; http_get does that itself so the
# token is never sent anywhere but api.github.com.
Fetch = Callable[[str, Mapping[str, str]], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _UrllibResponse:
    def __init__(self, raw: IO[bytes], status: int, headers: Mapping[str, str]) -> None:
        self._raw = raw
        self.status = status
        self._headers = headers

    def header(self, name: str) -> str | None:
        return self._headers.get(name)

    def read(self, size: int) -> bytes:
        return self._raw.read(size)

    def close(self) -> None:
        self._raw.close()


_opener = urllib.request.build_opener(_NoRedirect)


def urllib_fetch(url: str, headers: Mapping[str, str]) -> Response:
    if urlsplit(url).scheme != "https":  # checked by http_get as well
        raise UpdateError("refusing a non-HTTPS URL")
    request = urllib.request.Request(url, headers=dict(headers))  # noqa: S310 - HTTPS only, checked above
    try:
        raw = _opener.open(request, timeout=REQUEST_TIMEOUT)
    except urllib.error.HTTPError as exc:  # 3xx (not followed) and 4xx/5xx
        return _UrllibResponse(exc, exc.code, exc.headers)
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"could not reach {urlsplit(url).hostname}: {reason}") from None
    return _UrllibResponse(raw, raw.status, raw.headers)


def http_get(
    url: str, *, token: str, accept: str, fetch: Fetch, max_bytes: int, sink: Callable[[bytes], object]
) -> None:
    """Download ``url``, passing the body to ``sink`` in chunks and following redirects by hand.

    The token goes only to https://api.github.com; a redirect elsewhere (GitHub's pre-signed asset
    download URL) is fetched without it. Every hop must be HTTPS, and the body is capped at ``max_bytes``.
    """
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        host = parts.hostname or "?"
        if parts.scheme != "https":
            raise UpdateError(f"refusing a non-HTTPS URL (host {host})")
        headers = {"Accept": accept, "User-Agent": USER_AGENT}
        if host == API_HOST and parts.port in (None, 443):
            headers["Authorization"] = f"Bearer {token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        response = fetch(url, headers)
        try:
            if response.status in (301, 302, 303, 307, 308):
                location = response.header("Location")
                if not location:
                    raise UpdateError(f"redirect without a location from {host}")
                url = urljoin(url, location)
                continue
            if response.status in (401, 403):
                raise UpdateError(
                    f"GitHub refused the request (HTTP {response.status}): the token in the token file is "
                    f"invalid, expired or has no read access to {REPO}"
                )
            if response.status == 404:
                raise UpdateError(
                    f"not found (HTTP 404) on {host}: {REPO} has no published release, "
                    "or the token cannot see the repository"
                )
            if response.status != 200:
                raise UpdateError(f"HTTP {response.status} from {host}")
            length = response.header("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise UpdateError(f"download from {host} is larger than {max_bytes} bytes")
            received = 0
            while chunk := response.read(64 * 1024):
                received += len(chunk)
                if received > max_bytes:
                    raise UpdateError(f"download from {host} is larger than {max_bytes} bytes")
                sink(chunk)
            return
        finally:
            response.close()
    raise UpdateError("too many redirects")


def get_bytes(url: str, *, token: str, accept: str, fetch: Fetch, max_bytes: int) -> bytes:
    data = bytearray()
    http_get(url, token=token, accept=accept, fetch=fetch, max_bytes=max_bytes, sink=data.extend)
    return bytes(data)


# -- releases ------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    assets: Mapping[str, str]  # asset name -> API download URL
    name: str = ""
    body: str = ""  # release notes (markdown)
    html_url: str = ""


def latest_release(token: str, fetch: Fetch) -> Release | None:
    """The latest published release, or None if GitHub reports a draft or pre-release."""
    body = get_bytes(
        LATEST_RELEASE_URL, token=token, accept="application/vnd.github+json", fetch=fetch, max_bytes=MAX_JSON_BYTES
    )
    try:
        data = json.loads(body)
        tag = str(data["tag_name"])
    except (ValueError, KeyError, TypeError):
        raise UpdateError("GitHub returned an unexpected answer for the latest release") from None
    if data.get("draft") or data.get("prerelease"):
        log.info("The latest release %s is a draft or pre-release; ignoring it", tag)
        return None
    version = tag.removeprefix("v")
    parse_version(version)
    assets = {}
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and isinstance(asset.get("name"), str) and isinstance(asset.get("url"), str):
            assets[asset["name"]] = asset["url"]
    html_url = data.get("html_url")
    return Release(
        tag=tag,
        version=version,
        assets=assets,
        name=str(data.get("name") or "")[:200],
        body=str(data.get("body") or "")[:MAX_NOTES_CHARS],
        html_url=html_url if isinstance(html_url, str) and html_url.startswith("https://github.com/") else "",
    )


def current_platform() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def archive_prefix(version: str) -> str:
    """The one top folder of a release archive."""
    return f"{SLUG}-{version}"


def archive_prefixes(version: str) -> tuple[str, ...]:
    """Top folders an archive may have: ``comicsbot-X.Y.Z``, or ``ComicsBot-X.Y.Z`` as long as releases keep the
    names of 2.3.2 and older for its updaters (GitHub file names are unique ignoring case, so not both)."""
    return (archive_prefix(version), f"ComicsBot-{version}")


def archive_name(version: str, platform: str) -> str:
    return f"{archive_prefix(version)}-" + ("windows.zip" if platform == "windows" else "linux.tar.gz")


def find_asset(release: Release, name: str) -> str | None:
    """The release file named ``name`` ignoring case (as GitHub compares them), or None."""
    found = [asset for asset in release.assets if asset.casefold() == name.casefold()]
    if len(found) > 1:
        raise UpdateError(f"release {release.tag} has more than one file named {name} (ignoring case)")
    return found[0] if found else None


def asset_url(release: Release, name: str) -> str:
    url = release.assets.get(name)
    if not url:
        raise UpdateError(f"release {release.tag} has no {name}")
    if not url.startswith(ASSET_URL_PREFIX):
        raise UpdateError(f"release {release.tag}: unexpected download URL for {name}")
    return url


def parse_checksums(text: str) -> dict[str, str]:
    """Parse ``sha256sum`` output: ``<hex>  <name>`` (or ``<hex> *<name>``) per line."""
    sums = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if match:
            sums[match[2].strip()] = match[1].lower()
    return sums


def download_asset(url: str, dest: Path, *, token: str, fetch: Fetch, max_bytes: int) -> str:
    """Download a release asset to ``dest`` and return its SHA-256."""
    digest = hashlib.sha256()

    with dest.open("wb") as handle:

        def sink(chunk: bytes) -> None:
            digest.update(chunk)
            handle.write(chunk)

        http_get(url, token=token, accept="application/octet-stream", fetch=fetch, max_bytes=max_bytes, sink=sink)
    return digest.hexdigest()


# -- extraction ----------------------------------------------------------------


def _member_path(name: str, prefixes: tuple[str, ...]) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or ".." in path.parts
        or path.parts[0] not in prefixes
    ):
        raise UpdateError(f"unsafe path in the release archive: {name[:80]!r}")
    return path


def extract_release(archive: Path, dest: Path, version: str, platform: str) -> Path:
    """Extract the archive into the empty folder ``dest``; return the release folder inside it.

    Only regular files and folders below one top folder, ``comicsbot-<version>/`` or ``ComicsBot-<version>/``
    (the same one for every member), are accepted (no links, devices, absolute paths or ``..``), and the total
    size is capped.
    """
    prefixes = archive_prefixes(version)
    total = 0

    def member_path(name: str) -> PurePosixPath:
        nonlocal prefixes
        path = _member_path(name, prefixes)
        prefixes = (path.parts[0],)  # the first member decides; all others must share it
        return path

    def write(path: PurePosixPath, data: bytes, mode: int) -> None:
        nonlocal total
        total += len(data)
        if total > MAX_EXTRACTED_BYTES:
            raise UpdateError("the release archive is too large when extracted")
        target = dest.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if platform != "windows":
            target.chmod(mode)

    if platform == "windows":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                path = member_path(info.filename.rstrip("/"))
                if info.is_dir():
                    dest.joinpath(*path.parts).mkdir(parents=True, exist_ok=True)
                    continue
                if info.file_size > MAX_EXTRACTED_BYTES:
                    raise UpdateError("the release archive is too large when extracted")
                write(path, zf.read(info), 0o644)
    else:
        import tarfile

        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                path = member_path(member.name.rstrip("/"))
                if member.isdir():
                    dest.joinpath(*path.parts).mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise UpdateError(f"unexpected link or special file in the release archive: {member.name[:80]!r}")
                if member.size > MAX_EXTRACTED_BYTES:
                    raise UpdateError("the release archive is too large when extracted")
                handle = tar.extractfile(member)
                data = handle.read() if handle else b""
                write(path, data, 0o755 if member.mode & 0o111 else 0o644)

    if len(prefixes) != 1:
        raise UpdateError("the release archive is empty")
    prefix = prefixes[0]
    release_dir = dest / prefix
    installer = "install.ps1" if platform == "windows" else "install.sh"
    if not (release_dir / installer).is_file() or not (release_dir / "ComicsBot.py").is_file():
        raise UpdateError(f"the release archive does not contain {prefix}/{installer}")
    version_file = release_dir / "VERSION"
    if version_file.is_file() and version_file.read_text(encoding="utf-8").strip() != version:
        raise UpdateError("the VERSION file in the release archive does not match the release tag")
    return release_dir


# -- backup / rollback -----------------------------------------------------------


def program_files(platform: str) -> tuple[str, ...]:
    return LINUX_PROGRAM_FILES if platform == "linux" else PROGRAM_FILES


def backup_program_files(install_dir: Path, backup_dir: Path, names: Iterable[str] = PROGRAM_FILES) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = install_dir / name
        if source.is_symlink():
            continue  # never dereference a link the bot's account may have planted (root reads these)
        if source.is_dir():
            shutil.copytree(source, backup_dir / name, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, backup_dir / name, follow_symlinks=False)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def restore_program_files(install_dir: Path, backup_dir: Path, names: Iterable[str] = PROGRAM_FILES) -> None:
    """Put the backed-up program files back (and drop ones the failed update added)."""
    owner = install_dir.stat()
    for name in names:
        target = install_dir / name
        _remove(target)
        saved = backup_dir / name
        if saved.is_dir():
            shutil.copytree(saved, target, symlinks=True)
        elif saved.is_file():
            shutil.copy2(saved, target)
        else:
            continue
        # Running as root on Linux: give the files the install folder's owner (root since 2.3), as the installer does.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            for path in [target, *(target.rglob("*") if target.is_dir() else [])]:
                os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)


def missing_after_restore(install_dir: Path, backup_dir: Path, names: Iterable[str] = PROGRAM_FILES) -> list[str]:
    """Names of backed-up program files that are not back in the install folder."""
    return [
        name
        for name in names
        if ((backup_dir / name).exists() or (backup_dir / name).is_symlink())
        and not ((install_dir / name).exists() or (install_dir / name).is_symlink())
    ]


def data_folder(install_dir: Path) -> Path:
    """The bot's data folder: ``<install>/data`` if it is a real folder (Linux installs), else the install folder."""
    data = install_dir / DATA_DIR_NAME
    return data if data.is_dir() and not data.is_symlink() else install_dir


def write_update_notice(folder: Path, release: Release) -> Path:
    """Leave the release notes for the bot (no secrets) in its data folder, readable and deletable by its account."""
    path = folder / UPDATE_NOTICE
    data = {"version": release.version, "name": release.name, "body": release.body, "html_url": release.html_url}
    # mkstemp never opens an existing file or link; os.replace renames over whatever has the name (no following).
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".update-notice-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Through the open file, not the path: the bot's user can rename things in its folder.
            if hasattr(os, "fchown") and os.geteuid() == 0:
                owner = folder.stat()
                os.fchown(f.fileno(), owner.st_uid, owner.st_gid)
            if hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), 0o640)
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


# -- root safety ---------------------------------------------------------------


def _running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _changeable(path: Path, uid: int, gid: int) -> bool:
    """Whether an account other than ``uid`` (root) could change ``path`` (not following a link)."""
    st = path.lstat()
    if st.st_uid != uid:
        return True
    if stat.S_ISLNK(st.st_mode):
        return False  # a link's own mode means nothing; only its owner (or its folder's) can replace it
    return bool(st.st_mode & stat.S_IWOTH) or (bool(st.st_mode & stat.S_IWGRP) and st.st_gid != gid)


def _changeable_chain(path: Path, uid: int, gid: int, top: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if _changeable(candidate, uid, gid):
            return candidate
        if candidate == top:
            break
    return None


def unsafe_path(
    install_dir: Path,
    executables: Iterable[Path] = (),
    *,
    uid: int = 0,
    gid: int = 0,
    top: Path = Path("/"),
) -> Path | None:
    """The first path root would run or read code from that another account could change, or None.

    Checks the install folder resolved through links, everything in it except the bot's data folder and
    ``.env``, the folder's parents, and each of ``executables`` (as given and resolved) with its parents.
    ``uid``/``gid``/``top`` exist for the tests (normally root and every parent up to ``/``).
    """
    try:
        root = install_dir.resolve(strict=True)
    except OSError:
        return install_dir
    for dirpath, dirnames, filenames in os.walk(root, onerror=None):  # os.walk does not follow links
        current = Path(dirpath)
        if current == root:
            dirnames[:] = [d for d in dirnames if d != DATA_DIR_NAME]
            filenames = [f for f in filenames if f != ".env"]
        for name in (*dirnames, *filenames):
            if _changeable(current / name, uid, gid):
                return current / name
    bad = _changeable_chain(root, uid, gid, top)
    if bad is not None:
        return bad
    for executable in executables:
        executable = Path(os.path.abspath(executable))
        if not executable.exists():
            continue
        for path in (executable, executable.resolve()):
            bad = _changeable_chain(path, uid, gid, top)
            if bad is not None:
                return bad
    return None


def check_root_safety(install_dir: Path) -> None:
    """Refuse to work as root on an install that another account could change (it would get root)."""
    executables = [Path(sys.executable), Path(__file__), install_dir / ".venv" / "bin" / "python"]
    bad = unsafe_path(install_dir, executables)
    if bad is not None:
        raise UpdateError(
            f"{bad} can be changed by an account other than root, and the updater runs as root; refusing to "
            "update. Install with a release's install.sh (it makes the program files root-owned) into a folder "
            "only root can change, such as /opt/comicsbot, and use a Python installed system-wide"
        )


# -- installer -----------------------------------------------------------------

Runner = Callable[[list[str], int], int]


def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"))


def installer_command(release_dir: Path, install_dir: Path, platform: str) -> list[str]:
    if platform == "windows":
        return [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(release_dir / "install.ps1"),
            "-InstallDir",
            str(install_dir),
            "-Yes",
            "-Unattended",
        ]
    # The same Python as this updater (checked by check_root_safety), not whichever one PATH finds first.
    return [
        shutil.which("env") or "/usr/bin/env",
        f"PYTHON={sys.executable}",
        shutil.which("bash") or "/bin/bash",
        str(release_dir / "install.sh"),
        "--dir",
        str(install_dir),
        "--yes",
    ]


def service_command(install_dir: Path, platform: str, action: str) -> list[str]:
    """Stop or restart the bot, or check that it runs (``action`` is "stop", "restart" or "status")."""
    if action == "status":
        if platform == "windows":
            task = "(Get-ScheduledTask -TaskName 'ComicsBot' -ErrorAction Stop)"
            check = f"if ({task}.State -eq 'Running') {{ exit 0 }}; exit 1"
            return [_powershell(), "-NoProfile", "-NonInteractive", "-Command", check]
        return [shutil.which("systemctl") or "/usr/bin/systemctl", "is-active", "--quiet", "comicsbot"]
    if platform == "windows":
        script = str(install_dir / "install.ps1")
        switch = "-Stop" if action == "stop" else "-Restart"
        return [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
            "-InstallDir",
            str(install_dir),
            switch,
        ]
    return [shutil.which("systemctl") or "/usr/bin/systemctl", action, "comicsbot"]


def run_command(command: list[str], timeout: int) -> int:
    """Run a command without the secrets in its environment, logging its output line by line."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in SECRET_VARIABLES}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(  # noqa: S603 - fixed commands built above, no shell
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error(
            "%s did not finish within %d seconds", Path(command[1] if len(command) > 1 else command[0]).name, timeout
        )
        return -1
    except OSError as exc:
        log.error("Could not run %s: %s", command[0], exc)
        return -1
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if line.strip():
            log.info("    %s", line.rstrip())
    return result.returncode


# -- the update ------------------------------------------------------------------


def read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise UpdateError(f"cannot read the GitHub token file {path}: {exc.strerror}") from None
    if not token:
        raise UpdateError(f"the GitHub token file {path} is empty")
    if any(c.isspace() for c in token):
        raise UpdateError(f"the GitHub token file {path} must contain only the token")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        log.warning("%s is readable by other users; it should have mode 600", path)
    return token


def update(
    install_dir: Path,
    token: str,
    *,
    platform: str,
    check_only: bool = False,
    fetch: Fetch = urllib_fetch,
    runner: Runner = run_command,
    backup_root: Path | None = None,
    sleep: Callable[[float], object] = time.sleep,
) -> int:
    installed = installed_version(install_dir)
    release = latest_release(token, fetch)
    if release is None:
        return EXIT_OK
    if not is_newer(release.version, installed):
        log.info("ComicsBot %s is up to date (latest release: %s)", installed, release.tag)
        return EXIT_OK
    log.info("ComicsBot %s is available (installed: %s)", release.version, installed)
    if check_only:
        return EXIT_UPDATE_AVAILABLE

    wanted = archive_name(release.version, platform)
    name = find_asset(release, wanted)
    sums_name = find_asset(release, CHECKSUMS_ASSET)
    missing = [asset for asset, found in ((wanted, name), (CHECKSUMS_ASSET, sums_name)) if found is None]
    if missing or name is None or sums_name is None:
        # Not an error: a release published by hand gets its files a few minutes later, and a release of 2.3.2 or
        # older has no SHA256SUMS.txt. Nothing is changed; the next run looks again.
        log.warning(
            "Release %s has no %s (yet); not updating. Its files may still be being attached, or it uses file "
            "names of ComicsBot 2.3.2 and older; install it by hand if it stays like this",
            release.tag,
            " or ".join(missing),
        )
        return EXIT_OK
    archive_url = asset_url(release, name)
    sums_url = asset_url(release, sums_name)
    with tempfile.TemporaryDirectory(prefix="comicsbot-update-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        sums = parse_checksums(
            get_bytes(
                sums_url, token=token, accept="application/octet-stream", fetch=fetch, max_bytes=MAX_CHECKSUMS_BYTES
            ).decode("utf-8", "replace")
        )
        if name not in sums:
            raise UpdateError(f"{CHECKSUMS_ASSET} of release {release.tag} has no entry for {name}")
        log.info("Downloading %s", name)
        archive = work / name
        digest = download_asset(archive_url, archive, token=token, fetch=fetch, max_bytes=MAX_ARCHIVE_BYTES)
        if digest != sums[name]:
            raise UpdateError(f"checksum mismatch for {name}: the download is corrupt or was tampered with")
        log.info("Checksum OK (sha256 %s)", digest)

        extracted = work / "extract"
        extracted.mkdir()
        release_dir = extract_release(archive, extracted, release.version, platform)
        # Outside the temporary folder: it is kept when the rollback fails, for recovery by hand.
        if backup_root is not None:
            backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix=f"ComicsBot-{installed}-", dir=backup_root))
        names = program_files(platform)
        try:
            was_running = runner(service_command(install_dir, platform, "status"), SERVICE_TIMEOUT) == 0
            backup_program_files(install_dir, backup, names)
            # Written before the installer starts the new version, which reads (and deletes) it on startup.
            notice = write_update_notice(data_folder(install_dir), release)
        except BaseException:
            shutil.rmtree(backup, ignore_errors=True)
            raise
        log.info("Installing ComicsBot %s into %s", release.version, install_dir)
        code = runner(installer_command(release_dir, install_dir, platform), INSTALLER_TIMEOUT)
        if code == 0:
            shutil.rmtree(backup, ignore_errors=True)
            log.info("ComicsBot was updated from %s to %s", installed, release.version)
            return EXIT_OK

        log.error("The installer failed (exit code %s); restoring ComicsBot %s", code, installed)
        notice.unlink(missing_ok=True)
        return roll_back(install_dir, backup, names, installed, platform, was_running, runner, sleep)


def roll_back(
    install_dir: Path,
    backup: Path,
    names: tuple[str, ...],
    installed: str,
    platform: str,
    was_running: bool,
    runner: Runner,
    sleep: Callable[[float], object],
) -> int:
    """Put the previous version back and check that it worked; keep the backup if it did not."""
    runner(service_command(install_dir, platform, "stop"), SERVICE_TIMEOUT)
    try:
        restore_program_files(install_dir, backup, names)
    except OSError as exc:
        log.exception("Could not restore the previous program files")
        return rollback_failed(
            f"the previous program files could not be put back ({exc})", install_dir, backup, installed, platform
        )
    missing = missing_after_restore(install_dir, backup, names)
    if missing:
        return rollback_failed(
            f"{', '.join(missing)} missing from {install_dir} after the restore",
            install_dir,
            backup,
            installed,
            platform,
        )
    if not was_running:
        shutil.rmtree(backup, ignore_errors=True)
        log.info("ComicsBot %s was restored (it was not running before the update, so it was not started)", installed)
        return EXIT_FAILED
    if runner(service_command(install_dir, platform, "restart"), SERVICE_TIMEOUT) != 0:
        return rollback_failed(
            f"the restored ComicsBot {installed} could not be started", install_dir, backup, installed, platform
        )
    sleep(ROLLBACK_WAIT)
    if runner(service_command(install_dir, platform, "status"), SERVICE_TIMEOUT) != 0:
        return rollback_failed(
            f"the restored ComicsBot {installed} is not running {ROLLBACK_WAIT} seconds after it was started",
            install_dir,
            backup,
            installed,
            platform,
        )
    shutil.rmtree(backup, ignore_errors=True)
    log.info("ComicsBot %s was restored and restarted", installed)
    return EXIT_FAILED


def rollback_failed(reason: str, install_dir: Path, backup: Path, installed: str, platform: str) -> int:
    if platform == "windows":
        steps = (
            f"run install.bat -Stop in {install_dir}, copy everything in {backup} into {install_dir}, then run "
            f"install.bat -Restart there and check {install_dir / 'discord.log'}; or install a release again with "
            "its setup.exe or install.bat"
        )
    else:
        steps = (
            f"sudo cp -a {backup}/. {install_dir}/ && sudo systemctl restart comicsbot, then check "
            "systemctl status comicsbot and journalctl -u comicsbot; or install a release again with its "
            "sudo ./install.sh"
        )
    log.error(
        "ROLLBACK FAILED: %s. ComicsBot %s may not be running. The previous program files are kept in %s "
        "(not deleted). To recover by hand: %s. .env and the bot's data are not part of the backup and were left "
        "where they are.",
        reason,
        installed,
        backup,
        steps,
    )
    return EXIT_ROLLBACK_FAILED


@contextmanager
def update_lock(path: Path) -> Iterator[None]:
    """An exclusive OS-level lock, released automatically if the process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise UpdateBusy(str(path)) from None
    try:
        yield
    finally:
        os.close(fd)


def setup_logging(log_file: Path | None) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if sys.stderr is not None:  # None under pythonw.exe (the Windows scheduled task)
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
        root.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update ComicsBot to the latest GitHub release.")
    parser.add_argument("--install-dir", type=Path, required=True, help="the ComicsBot install folder")
    parser.add_argument("--token-file", type=Path, required=True, help="file with the GitHub token")
    parser.add_argument("--lock-file", type=Path, help="lock file (default: update.lock next to the token file)")
    parser.add_argument("--log-file", type=Path, help="also log to this file")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="where the previous version is backed up during an update (default: backups next to the token file)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=f"only report whether an update is available (exit code {EXIT_UPDATE_AVAILABLE} if it is, 0 if not)",
    )
    parser.add_argument("--platform", choices=["linux", "windows"], default=current_platform(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    setup_logging(args.log_file)
    install_dir = args.install_dir.resolve()
    lock_file = args.lock_file or args.token_file.parent / "update.lock"
    try:
        with update_lock(lock_file):
            if args.platform == "linux" and _running_as_root():
                check_root_safety(install_dir)
            token = read_token(args.token_file)
            backup_root = args.backup_dir or args.token_file.parent / "backups"
            return update(install_dir, token, platform=args.platform, check_only=args.check, backup_root=backup_root)
    except UpdateBusy:
        log.warning("Another update is already running; not starting a second one")
        return EXIT_BUSY
    except UpdateError as exc:
        log.error("Update failed: %s", exc)
        return EXIT_FAILED
    except Exception:
        log.exception("Update failed unexpectedly")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
