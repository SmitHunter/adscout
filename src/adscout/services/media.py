"""Download ad media locally (CDN URLs expire) and fingerprint images by perceptual hash.

Files are content-addressed under ``media_dir/blobs/<sha256[:32]>.<ext>`` and shared by every
snapshot; ``media_dir/index.json`` maps a CDN URL path to its blob, so a re-check of the same
search doesn't download the same creative again. Files saved before blobs existed
(``media_dir/<snapshot_id>/<stem>.<ext>``) keep working and are reused as they are.
"""

import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import random
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import imagehash
from PIL import Image
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import load_ads, update_ad_media
from adscout.models import MediaItem
from adscout.sources import CancelToken

logger = logging.getLogger(__name__)

MediaProgress = Callable[[int, int], Awaitable[None] | None]

_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
}
_GAP_MIN = 0.3
_GAP_MAX = 0.8

BLOB_DIR = "blobs"
INDEX_FILE = "index.json"
_BLOB_NAME = re.compile(r"^[0-9a-f]{32}\.(?:" + "|".join(sorted(set(_EXTENSIONS.values()))) + r")$")


def _safe_stem(ad_archive_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", ad_archive_id)
    if not stem.strip("_"):
        return hashlib.sha1(ad_archive_id.encode(), usedforsecurity=False).hexdigest()[:16]
    return stem


def _safe_url(url: str) -> str:
    """scheme+host+path only: CDN query strings carry signatures."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _is_https(url: str) -> bool:
    try:
        return urlsplit(url).scheme.lower() == "https"
    except ValueError:
        return False


def _url_key(url: str) -> str | None:
    """The index key: the URL path. CDN hosts and signed query strings vary per fetch."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    return path if path.strip("/") else None


def _read_index(path: Path) -> dict[str, str]:
    """The URL-path -> blob-name index; empty when missing or unreadable (it is a cache)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Media index %s is unreadable; starting a new one", path.name)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


class _Skip(Exception):
    pass


def _targets(item: MediaItem) -> list[tuple[bool, str]]:
    """(is_video_file, url) pairs to save for one media item."""
    if item.type == "image":
        return [(False, item.url)]
    out = [(False, item.preview_url)] if item.preview_url else []
    return [*out, (True, item.url)]


async def _emit(progress: MediaProgress | None, done: int, total: int) -> None:
    if progress is None:
        return
    result = progress(done, total)
    if inspect.isawaitable(result):
        await result


class MediaDownloader:
    """Sequential, polite downloader; failures are skipped and logged, never raised."""

    def __init__(
        self,
        media_dir: Path,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        rng: random.Random | None = None,
        max_bytes: int = 30_000_000,
    ) -> None:
        self._dir = media_dir
        self._client = client
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._max = max_bytes
        self._hit_network = False
        self._index: dict[str, str] = {}
        self._new: dict[str, str] = {}

    @property
    def _blobs(self) -> Path:
        return self._dir / BLOB_DIR

    @property
    def _index_path(self) -> Path:
        return self._dir / INDEX_FILE

    def _save_index(self) -> None:
        """Merge this run's entries into the index on disk, written atomically."""
        if not self._new:
            return
        merged = {**_read_index(self._index_path), **self._new}
        tmp: Path | None = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self._dir, prefix=".index-", suffix=".tmp")
            tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, separators=(",", ":"), sort_keys=True)
            tmp.replace(self._index_path)
            tmp = None
            self._new = {}
        except OSError as exc:
            logger.warning("Could not save the media index: %s", type(exc).__name__)
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)

    def _indexed(self, key: str | None) -> str | None:
        """``blobs/<name>`` for an indexed URL path whose blob is still on disk."""
        if key is None:
            return None
        name = self._index.get(key)
        if name is None or not _BLOB_NAME.fullmatch(name):
            return None
        try:
            if (self._blobs / name).stat().st_size > 0:
                return f"{BLOB_DIR}/{name}"
        except OSError:
            pass
        return None

    async def download_snapshot(
        self,
        engine: Engine,
        snapshot_id: int,
        progress: MediaProgress | None,
        cancel: CancelToken | None,
    ) -> int:
        """Save the snapshot's media under ``media_dir`` and return the file count."""
        with Session(engine) as session:
            ads = load_ads(session, snapshot_id)
        total = sum(len(_targets(m)) for ad in ads for m in ad.media)
        await _emit(progress, 0, total)
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30, follow_redirects=True)
        saved = done = 0
        self._hit_network = False
        self._index = _read_index(self._index_path)
        self._new = {}
        try:
            for ad in ads:
                media = [m.model_copy() for m in ad.media]
                changed = False
                index = 0
                stop = False
                for item in media:
                    for is_video, url in _targets(item):
                        if cancel is not None and cancel.cancelled:
                            stop = True
                            break
                        stem = f"{_safe_stem(ad.ad_archive_id)}_{index}"
                        index += 1
                        rel = await self._fetch(client, snapshot_id, stem, url, is_video, cancel)
                        done += 1
                        if rel is not None:
                            saved += 1
                            changed = True
                            if is_video or item.type == "image":
                                item.local_path = rel
                            else:
                                item.poster_path = rel
                            if not is_video:
                                phash = await asyncio.to_thread(self._phash, self._dir / rel)
                                item.phash = phash or item.phash
                        await _emit(progress, done, total)
                    if stop:
                        break
                if changed:
                    with Session(engine) as session:
                        update_ad_media(session, snapshot_id, ad.ad_archive_id, media)
                if stop:
                    break
        finally:
            self._save_index()
            if owns:
                await client.aclose()
        return saved

    def _phash(self, path: Path) -> str | None:
        try:
            with Image.open(path) as img:
                return str(imagehash.phash(img))
        except Exception:
            logger.warning("Could not fingerprint %s", path.name)
            return None

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        snapshot_id: int,
        stem: str,
        url: str,
        is_video: bool,
        cancel: CancelToken | None,
    ) -> str | None:
        """Path relative to ``media_dir`` of the saved (or already present) file.

        Only ``https://`` URLs are fetched, and only a response of the expected kind
        (``is_video``: a video, else an image) is kept.
        """
        folder = self._dir / str(snapshot_id)
        # A file saved before blobs existed (its local_path already points at it).
        for known in _EXTENSIONS.values():
            existing = folder / f"{stem}.{known}"
            try:
                if existing.is_file() and existing.stat().st_size > 0:
                    return f"{snapshot_id}/{existing.name}"
            except OSError:
                continue
        if not _is_https(url):
            logger.warning("Skipping media %s: not https", _safe_url(url))
            return None
        key = _url_key(url)
        cached = self._indexed(key)
        if cached is not None:
            return cached
        if self._hit_network:
            gap = self._rng.uniform(_GAP_MIN, _GAP_MAX)
            if cancel is not None:
                if await cancel.sleep(gap):
                    return None
            else:
                await self._sleep(gap)
        self._hit_network = True
        try:
            ext, data = await self._get(client, url, is_video)
        except _Skip as exc:
            logger.warning("Skipping media %s: %s", _safe_url(url), exc)
            return None
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            logger.warning("Skipping media %s: %s", _safe_url(url), type(exc).__name__)
            return None
        name = f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
        final = self._blobs / name
        part: Path | None = None
        try:
            if not (final.is_file() and final.stat().st_size == len(data)):
                self._blobs.mkdir(parents=True, exist_ok=True)
                # A unique temporary name: another process may be writing the same blob.
                fd, tmp = tempfile.mkstemp(dir=self._blobs, prefix=f"{name}.", suffix=".part")
                part = Path(tmp)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                part.replace(final)
                part = None
        except OSError as exc:
            logger.warning("Could not save media %s: %s", _safe_url(url), type(exc).__name__)
            if part is not None:
                with contextlib.suppress(OSError):
                    part.unlink(missing_ok=True)
            return None
        if key is not None:
            self._index[key] = name
            self._new[key] = name
        return f"{BLOB_DIR}/{name}"

    async def _get(self, client: httpx.AsyncClient, url: str, is_video: bool) -> tuple[str, bytes]:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise _Skip(f"HTTP {resp.status_code}")
            if not _is_https(str(resp.url)):
                raise _Skip("redirected away from https")
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            ext = _EXTENSIONS.get(ctype)
            if ext is None:
                raise _Skip(f"unsupported content type {ctype or 'unknown'}")
            if ctype.startswith("video/") != is_video:
                expected = "a video" if is_video else "an image"
                raise _Skip(f"expected {expected}, got {ctype}")
            length = resp.headers.get("content-length")
            if length is not None and length.isdigit() and int(length) > self._max:
                raise _Skip("over size cap")
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > self._max:
                    raise _Skip("over size cap")
        return ext, bytes(buf)
