"""Утилиты: пути, MD5, длинные пути Windows, проверка папок, форматирование, ссылки."""

from __future__ import annotations

import errno
import hashlib
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

APP_VERSION = "1.0.0"
APP_NAME = "iCloud БК"

# Символы, запрещённые в именах файлов на Windows. Слеши тоже: имя файла в iCloud
# может содержать «/», а локально это стало бы разделителем пути.
_WIN_FORBIDDEN = '<>:"|?*\\/'
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

IS_WINDOWS = sys.platform.startswith("win")
# Windows и macOS (APFS по умолчанию) не различают регистр имён
CASE_INSENSITIVE_PATHS = IS_WINDOWS or sys.platform == "darwin"
WIN_LONG_PATH_THRESHOLD = 240

# ── Папки внутри папки скачивания ───────────────────────────────────────────
DIR_DUP = "_duplicates"            # одинаковые файлы (по MD5) переезжают сюда
DIR_HEIC_ORIGINALS = "_heic_originals"  # режим «JPG-копии»: сюда уходят HEIC
# Папка на Яндекс Диске, куда заливается копия
YANDEX_ROOT = "/iCloud БК"

# ── Ссылки сообщества ────────────────────────────────────────────────────────
TELEGRAM_CHANNEL = "https://t.me/davronsv"
TELEGRAM_HANDLE = "@davronsv"
YOUTUBE_URL = "https://www.youtube.com/@davronsv"
DONATION_URL = "https://dalink.to/davronsvv"
GITHUB_URL = "https://github.com/PXSYS13/icloud_backup"
YADISK_BK_URL = "https://github.com/PXSYS13/yadisk_backup"
ICLOUDPD_URL = "https://github.com/icloud-photos-downloader/icloud_photos_downloader"

PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif",
    ".bmp", ".tiff", ".tif", ".raw", ".arw", ".cr2", ".cr3", ".nef", ".dng", ".orf",
    ".raf", ".rw2", ".pef", ".nrf", ".crw",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".3gp", ".3g2",
    ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts",
}
MEDIA_EXTS = PHOTO_EXTS | VIDEO_EXTS


def is_photo(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in PHOTO_EXTS


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def is_media(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in MEDIA_EXTS


def is_heic(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".heic", ".heif")


# ── Не давать компьютеру уснуть ──────────────────────────────────────────────


@contextmanager
def keep_awake() -> Iterator[None]:
    """Не даёт компьютеру уснуть, пока идёт скачивание (Windows и macOS).

    На Windows флаг действует для вызывающего потока — звать из потока, который живёт
    всё время скачивания. Экран при этом гаснуть может, засыпать система — нет.
    """
    caffeinate = None
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:  # noqa: BLE001
            pass
    elif sys.platform == "darwin":
        try:
            caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            caffeinate = None
    try:
        yield
    finally:
        if IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:  # noqa: BLE001
                pass
        if caffeinate is not None:
            caffeinate.terminate()


# ── Санитизация путей ────────────────────────────────────────────────────────


def sanitize_component(name: str) -> str:
    """Чистит ОДИН компонент пути (имя файла или папки) от запрещённых символов."""
    if not name:
        return "_"
    cleaned = []
    for ch in name:
        if ch in _WIN_FORBIDDEN or ord(ch) < 32:
            cleaned.append("_")
        else:
            cleaned.append(ch)
    out = "".join(cleaned).rstrip(" .")
    if not out or out in (".", ".."):
        out = "_"
    stem = out.split(".", 1)[0].upper()
    if stem in _WIN_RESERVED:
        out = "_" + out
    return out


def to_long_path(path: str) -> str:
    r"""Для Windows добавляет префикс \\?\ к длинному пути. На других платформах — как есть."""
    if not IS_WINDOWS:
        return path
    abs_path = os.path.abspath(path)
    if len(abs_path) < WIN_LONG_PATH_THRESHOLD:
        return abs_path
    if abs_path.startswith("\\\\?\\"):
        return abs_path
    if abs_path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + abs_path[2:]
    return "\\\\?\\" + abs_path


def _strip_long_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def path_key(path: str) -> str:
    """Ключ занятого пути: пути, которые ФС считает одним файлом, дают один ключ."""
    p = os.path.normpath(path)
    return p.lower() if CASE_INSENSITIVE_PATHS else p


def short_id(asset_id: str, n: int = 7) -> str:
    """Короткий устойчивый хвост из идентификатора iCloud: для одноимённых файлов."""
    return hashlib.sha256(asset_id.encode("utf-8", errors="replace")).hexdigest()[:n]


def with_suffix_before_ext(path: str, suffix: str) -> str:
    stem, ext = os.path.splitext(path)
    return f"{stem}{suffix}{ext}"


def safe_int(val: object, default: int = 4, min_val: int = 1, max_val: int = 16) -> int:
    try:
        n = int(val)  # type: ignore[arg-type]
        return max(min_val, min(max_val, n))
    except (ValueError, TypeError):
        return default


# ── Проверка безопасности целевой папки ─────────────────────────────────────

_WIN_SYSTEM_DIR_RE = re.compile(
    r"^[a-z]:\\(windows|program files|program files \(x86\)|programdata"
    r"|\$recycle\.bin|users\\all users)(\\|$)"
)
_WIN_SYSTEM_ENV_VARS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
_POSIX_SYSTEM_DIRS = ("/etc", "/usr", "/bin", "/sbin", "/var", "/lib", "/system", "/library")


def _is_drive_root(path: str) -> bool:
    return len(path) <= 3 and path.endswith((":\\", ":/"))


def is_safe_dir(path: str, *, must_exist: bool = False) -> tuple[bool, str]:
    r"""Можно ли использовать папку как цель скачивания или обработки.

    Запрещены: пустые и относительные пути, корни дисков (C:\, D:\, /), домашняя папка
    пользователя целиком, системные папки (Windows, Program Files, ProgramData,
    корзина, /etc, /usr и другие).

    must_exist=False — папку можно создать: достаточно существующего предка.
    must_exist=True — папка обязана существовать (обработка уже скачанного).
    """
    if not path or not path.strip():
        return False, "Пустой путь"
    p_str = _strip_long_prefix(path.strip())
    raw_norm = p_str.lower().replace("/", "\\")

    if _is_drive_root(raw_norm):
        return False, "Нельзя использовать корень диска целиком (укажите конкретную папку)"
    if _WIN_SYSTEM_DIR_RE.match(raw_norm):
        return False, f"Системная папка запрещена: {p_str}"

    try:
        if not Path(p_str).is_absolute():
            return False, "Путь должен быть абсолютным"
        abs_path = os.path.abspath(p_str)
    except (OSError, ValueError):
        return False, "Невалидный путь"

    norm = abs_path.lower().replace("/", "\\")

    if _is_drive_root(abs_path) or norm in ("\\", "/"):
        return False, "Нельзя использовать корень диска целиком (укажите конкретную папку)"

    home_dir = os.path.expanduser("~")
    if home_dir and home_dir != "~":
        home_norm = os.path.abspath(home_dir).lower().replace("/", "\\")
        if norm == home_norm or raw_norm == home_norm:
            return False, ("Нельзя использовать домашнюю папку пользователя целиком "
                           "(укажите конкретную папку для фото)")

    for var in _WIN_SYSTEM_ENV_VARS:
        val = os.environ.get(var)
        if val:
            val_norm = os.path.abspath(val).lower().replace("/", "\\")
            if norm == val_norm or norm.startswith(val_norm + "\\"):
                return False, f"Системная папка запрещена: {val}"

    if _WIN_SYSTEM_DIR_RE.match(norm):
        return False, f"Системная папка запрещена: {abs_path}"

    norm_posix = abs_path.replace("\\", "/").lower()
    for bad in _POSIX_SYSTEM_DIRS:
        if norm_posix == bad or norm_posix.startswith(bad + "/"):
            return False, f"Системная папка запрещена: {bad}"

    if must_exist:
        if not os.path.isdir(abs_path):
            return False, "Папка не существует"
        return True, "OK"

    parent = Path(abs_path).parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.exists():
        return False, "Родительская директория недоступна"
    return True, "OK"


def is_safe_download_dir(path: str) -> tuple[bool, str]:
    return is_safe_dir(path, must_exist=False)


# ── SQLite ───────────────────────────────────────────────────────────────────


def sqlite_ro_uri(db_path: str | os.PathLike) -> str:
    """URI для read-only подключения; экранирует «#» и прочие спецсимволы пути."""
    return Path(db_path).resolve().as_uri() + "?mode=ro"


@contextmanager
def sqlite_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Явная транзакция: BEGIN IMMEDIATE → COMMIT, при любой ошибке — ROLLBACK."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    else:
        conn.execute("COMMIT")


# ── Файлы и форматирование ──────────────────────────────────────────────────


def compute_md5(local_path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with open(to_long_path(local_path), "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def file_size_safe(local_path: str) -> int | None:
    try:
        return os.path.getsize(to_long_path(local_path))
    except OSError:
        return None


def remove_quiet(path: str) -> None:
    try:
        os.remove(to_long_path(path))
    except OSError:
        pass


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    """Пишет файл через временный и os.replace: файл либо старый, либо новый целиком."""
    path = str(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


LOCAL_DISK_ERRNOS = frozenset({
    errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS, errno.EEXIST,
    errno.EISDIR, errno.ENOTDIR, errno.ENAMETOOLONG, errno.ENOENT,
})


def is_local_disk_error(exc: BaseException) -> bool:
    """OSError локальной файловой системы (не сеть): место, права, отсутствующий диск."""
    return (
        isinstance(exc, OSError)
        and not isinstance(exc, (ConnectionError, TimeoutError))
        and getattr(exc, "errno", None) in LOCAL_DISK_ERRNOS
    )


_FAT_FILESYSTEMS = frozenset({"fat", "fat12", "fat16", "fat32", "vfat", "msdos"})


def is_fat_filesystem(fs: str | None) -> bool:
    return bool(fs) and fs.strip().lower() in _FAT_FILESYSTEMS


def fs_type_from_mounts(path: str, mounts: str) -> str | None:
    best, fs = None, None
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[1])
        inside = path == mount or path.startswith(mount.rstrip("/") + "/")
        if inside and (best is None or len(mount) > len(best)):
            best, fs = mount, fields[2]
    return fs


def filesystem_type(path: str) -> str | None:
    """Файловая система тома, где лежит папка: NTFS, exFAT, FAT32… None — не узнать."""
    try:
        p = os.path.abspath(_strip_long_prefix(path))
        while not os.path.exists(p):
            parent = os.path.dirname(p)
            if parent == p:
                return None
            p = parent
        if IS_WINDOWS:
            return _windows_fs_type(p)
        if sys.platform.startswith("linux"):
            with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
                return fs_type_from_mounts(os.path.realpath(p), f.read())
    except (OSError, ValueError):
        return None
    return None


def _windows_fs_type(path: str) -> str | None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    volume = ctypes.create_unicode_buffer(1024)
    if not kernel32.GetVolumePathNameW(path, volume, len(volume)):
        return None
    name = ctypes.create_unicode_buffer(64)
    if not kernel32.GetVolumeInformationW(volume.value, None, 0, None, None, None, name, len(name)):
        return None
    return name.value or None


def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def ru_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return (f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}").replace(".", ",")
        n /= 1024
    return f"{n:.1f} ТБ"


def human_duration_ru(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or math.isnan(seconds):
        return "—"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}ч {m:02d}м {s:02d}с"
    if m > 0:
        return f"{m}м {s:02d}с"
    return f"{s}с"


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(3, "файл", "файла", "файлов") → «файла»."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def files_text(n: int) -> str:
    return f"{int(n):,}".replace(",", " ") + " " + plural(n, "файл", "файла", "файлов")


def hhmm(ts: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else ""


def ensure_utf8_stdio() -> None:
    """stdout/stderr в UTF-8 — кириллица в консоли Windows без крякозябр."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def mask_apple_id(apple_id: str) -> str:
    """ivan.petrov@icloud.com → iv…v@icloud.com — чтобы логин не светился целиком."""
    if not apple_id or "@" not in apple_id:
        return "…" if not apple_id else apple_id[:2] + "…"
    name, _, domain = apple_id.partition("@")
    if len(name) <= 2:
        return f"{name[:1]}…@{domain}"
    return f"{name[:2]}…{name[-1]}@{domain}"


def mask_token(token: str) -> str:
    """Маскирует токен Яндекса: y0_xxx…xxxx (NN симв.)"""
    if not token or len(token) < 8:
        return ""
    if len(token) <= 12:
        return token[:3] + "***"
    return f"{token[:6]}…{token[-4:]} ({len(token)} симв.)"
