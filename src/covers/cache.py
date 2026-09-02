"""On-disk cover cache. Uncapped and never evicted: a single-user library is
bounded by its own item count, and the directory is safe to delete."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_MAGIC_MEDIA_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

SNIFF_BYTES = 16


def image_media_type(data: bytes) -> str | None:
    """The media type *data* really is, never the one a server claimed."""
    for magic, media_type in _MAGIC_MEDIA_TYPES:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def cache_path(cache_dir: Path, db_id: int, cover_url: str) -> Path:
    # Named from the URL too, so a cover that moves is not a stale hit.
    digest = hashlib.sha256(cover_url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{db_id}-{digest}"


def store(path: Path, data: bytes) -> None:
    # Renamed into place: a reader must never open a half-written download.
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    partial.write_bytes(data)
    os.replace(partial, path)
