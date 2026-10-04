"""Single-instance lock, so two copies of the bot never post twice or fight over state.json."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    """An exclusive OS-level file lock; the OS releases it if the process dies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        handle = open(self.path, "a+")  # noqa: SIM115 - kept open while the lock is held
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AlreadyRunning(f"another ComicsBot instance holds {self.path}") from None
        self._file = handle

    def release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
