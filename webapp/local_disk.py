"""Локальный диск: свободное место и файловая система папки скачивания.

Всё здесь дёргается из /api/status раз в 1,5 с, поэтому дорогое кешируется.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from utils import filesystem_type, is_fat_filesystem

# Запас, который оставляем на диске сверх размера файлов
RESERVE_BYTES = 2 * 1024 ** 3


def free_space(path: str) -> dict | None:
    """Свободное место на диске папки (или ближайшего существующего предка)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        usage = shutil.disk_usage(p)
    except OSError:
        return None
    fs = _fs_type_cached(str(p))
    return {"free": usage.free, "total": usage.total, "path": str(p), "fs": fs, "fat": is_fat_filesystem(fs)}


_FS_CACHE_SEC = 300
_fs_cache: dict[str, tuple[float, str | None]] = {}


def _fs_type_cached(path: str) -> str | None:
    now = time.monotonic()
    hit = _fs_cache.get(path)
    if hit and now - hit[0] < _FS_CACHE_SEC:
        return hit[1]
    fs = filesystem_type(path)
    _fs_cache[path] = (now, fs)
    return fs


def space_check(free: dict | None, bytes_pending: int) -> dict:
    """Хватит ли места: осталось скачать столько-то, свободно столько-то."""
    if not free or free.get("free") is None:
        return {"known": False, "short": False, "need": bytes_pending, "free": None}
    need = int(bytes_pending)
    avail = int(free["free"])
    return {"known": True, "short": need > 0 and avail < need + RESERVE_BYTES,
            "need": need, "free": avail, "missing": max(0, need + RESERVE_BYTES - avail)}
