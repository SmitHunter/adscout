"""Cross-process locks under the data dir.

- ``server.lock``: only one ``adscout serve`` runs at a time (its port is in ``server.port``).
- ``browser-profile.lock``: only one process drives the persistent browser profile at a time
  (the CLI scraper, ``adscout browser``, the server's searches and its browser window).
"""

import asyncio
import time
from pathlib import Path

from filelock import BaseFileLock, FileLock, Timeout

from adscout.sources.base import SourceError

SERVER_LOCK = "server.lock"
SERVER_PORT = "server.port"
PROFILE_LOCK = "browser-profile.lock"

PROFILE_TIMEOUT_S = 10.0
_POLL_S = 0.1

PROFILE_BUSY_MESSAGE = (
    "Another AdScout search is using the browser. Wait for it to finish, then try again."
)


def single_instance_message(port: int) -> str:
    return f"AdScout is already running at http://127.0.0.1:{port}"


class ProfileBusy(SourceError):
    """The browser profile is held by another AdScout process (or window)."""

    def __init__(self) -> None:
        super().__init__(PROFILE_BUSY_MESSAGE)


def server_lock(data_dir: Path) -> BaseFileLock:
    """The single-instance lock; ``acquire()`` raises ``filelock.Timeout`` at once if held."""
    data_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(data_dir / SERVER_LOCK, timeout=0, thread_local=False)


def read_server_port(data_dir: Path, default: int) -> int:
    """The port the running server wrote, or ``default`` if it is missing or unreadable."""
    try:
        text = (data_dir / SERVER_PORT).read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return int(text) if text.isdigit() and 0 < int(text) < 65536 else default


def write_server_port(data_dir: Path, port: int) -> None:
    (data_dir / SERVER_PORT).write_text(str(port), encoding="utf-8")


def profile_lock_dir(profile_dir: Path) -> Path:
    """Where the profile lock lives: the folder holding the browser profile
    (``data/browser-profile`` -> ``data``). Every user of the lock goes through this."""
    return profile_dir.parent


def profile_lock(data_dir: Path, timeout_s: float | None = None) -> BaseFileLock:
    """The browser-profile lock. Acquire it with ``acquire_profile``/``acquire_profile_async``,
    which wait up to ``timeout_s`` (default ``PROFILE_TIMEOUT_S``) and raise ``ProfileBusy``.

    Not thread-local: a lock taken on one thread may be released from another.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    timeout = PROFILE_TIMEOUT_S if timeout_s is None else timeout_s
    return FileLock(data_dir / PROFILE_LOCK, timeout=timeout, thread_local=False)


def acquire_profile(lock: BaseFileLock) -> None:
    """Block until the profile lock is ours (up to its timeout), else raise ``ProfileBusy``."""
    try:
        lock.acquire(poll_interval=_POLL_S)
    except Timeout:
        raise ProfileBusy() from None


async def acquire_profile_async(lock: BaseFileLock) -> None:
    """``acquire_profile`` without blocking the event loop (and safe to cancel)."""
    deadline = time.monotonic() + max(lock.timeout, 0)
    while True:
        try:
            lock.acquire(timeout=0)
            return
        except Timeout:
            if time.monotonic() >= deadline:
                raise ProfileBusy() from None
        await asyncio.sleep(_POLL_S)


def check_profile_free(lock_dir: Path) -> None:
    """Raise ``ProfileBusy`` if another process holds the profile right now.

    ``lock_dir`` is ``profile_lock_dir(settings.browser_profile_dir)``.
    """
    lock = profile_lock(lock_dir, timeout_s=0)
    acquire_profile(lock)
    lock.release()
