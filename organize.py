"""Раскладка по годам и месяцам: <папка>/2023/2023-07/IMG_0001.HEIC.

Для веб-интерфейса — relayout(state, …): переносит уже скачанные файлы из базы state.db
в папки по дате съёмки из базы (или обратно в одну папку, если раскладка выключена),
вместе с видеочастью Live Photo, и запоминает новые пути. Файлы не удаляются, опустевшие
папки убираются.

Командная строка (любая папка, без базы — дата из имени файла, EXIF или даты файла):
  uv run organize.py --input "D:/iCloudBackup"                 # отчёт
  uv run organize.py --input "D:/iCloudBackup" --action move
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time

from state import DUPLICATE_MARKER, State, month_folder
from utils import DIR_DUP, DIR_HEIC_ORIGINALS, ensure_utf8_stdio, is_media, is_safe_dir, to_long_path

SKIP_DIRS = {DIR_DUP, DIR_HEIC_ORIGINALS}
SKIP_SUFFIXES = (".part", ".tmp", ".db", ".db-wal", ".db-shm", ".log")

DATE_REGEXES = [
    re.compile(r"(19\d{2}|20\d{2})[-_.](\d{2})[-_.](\d{2})"),
    re.compile(r"(19\d{2}|20\d{2})(\d{2})(\d{2})"),
]


def unique_path(dst: str) -> str:
    if not os.path.exists(to_long_path(dst)):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while True:
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(to_long_path(cand)):
            return cand
        i += 1


def _inside(path: str, root: str) -> bool:
    try:
        p = os.path.normcase(os.path.abspath(path))
        r = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
        return p.startswith(r + os.sep)
    except ValueError:
        return False


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def remove_empty_dirs(root: str, skip: set[str] = SKIP_DIRS) -> int:
    cleaned = 0
    for dp, dirs, fns in os.walk(root, topdown=False):
        rel = os.path.relpath(dp, root)
        first = rel.split(os.sep)[0] if rel != "." else ""
        if first in skip or dp == root:
            continue
        try:
            if not os.listdir(dp):
                os.rmdir(dp)
                cleaned += 1
        except OSError:
            pass
    return cleaned


def relayout(state: State, download_dir: str, by_month: bool, progress_cb=None, stop_event=None) -> dict:
    """Переносит скачанные файлы по датам из базы (by_month) или в одну папку (не by_month)."""
    root = os.path.abspath(download_dir)
    rows = list(state.get_downloaded())
    out = {"total": len(rows), "moved": 0, "errors": 0, "by_month": by_month, "cleaned": 0}
    for n, row in enumerate(rows, 1):
        if stop_event is not None and stop_event.is_set():
            out["stopped"] = True
            break
        if (row["error"] or "").startswith(DUPLICATE_MARKER):
            continue
        folder = os.path.join(root, month_folder(row["created_ts"])) if by_month else root
        for column in ("local_path", "live_local_path"):
            src = row[column]
            if not src or not _inside(src, root) or _inside(src, os.path.join(root, DIR_DUP)):
                continue
            if not os.path.isfile(to_long_path(src)):
                continue
            dst = os.path.join(folder, os.path.basename(src))
            if _same(src, dst):
                continue
            dst = unique_path(dst)
            try:
                os.makedirs(to_long_path(folder), exist_ok=True)
                shutil.move(to_long_path(src), to_long_path(dst))
                state.update_local_path(row["id"], **{column: dst})
                out["moved"] += 1
            except OSError:
                out["errors"] += 1
        if progress_cb is not None and (n % 20 == 0 or n == len(rows)):
            progress_cb({"total": len(rows), "done": n, "moved": out["moved"], "errors": out["errors"],
                         "current": row["filename"]})
    out["cleaned"] = remove_empty_dirs(root)
    out["text"] = (f"Переложено файлов: {out['moved']}" if out["moved"]
                   else "Все файлы уже лежат по местам")
    return out


# ── Командная строка: любая папка без базы ─────────────────────────────────


def _parse_exif_date(val) -> tuple[int, int] | None:
    s = str(val if not isinstance(val, bytes) else val.decode("utf-8", "ignore")).strip()
    m = re.match(r"^(19\d{2}|20\d{2})[:\-.](\d{2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1970 <= y <= 2100 and 1 <= mo <= 12:
            return y, mo
    return None


def get_exif_date(path: str) -> tuple[int, int] | None:
    try:
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            dt = exif.get(0x0132)
            try:
                ifd = exif.get_ifd(0x8769)
                if ifd:
                    dt = ifd.get(0x9003) or ifd.get(0x9004) or dt
            except Exception:  # noqa: BLE001
                pass
            return _parse_exif_date(dt) if dt else None
    except Exception:  # noqa: BLE001
        return None


def get_date(path: str) -> tuple[int, int]:
    """Год и месяц: из EXIF, иначе из имени файла (2023-07-15), иначе из даты файла."""
    exif_date = get_exif_date(path)
    if exif_date:
        return exif_date
    name = os.path.basename(path)
    for rx in DATE_REGEXES:
        m = rx.search(name)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            if 1970 <= y <= 2100 and 1 <= mo <= 12:
                return y, mo
    try:
        t = time.localtime(os.path.getmtime(path))
        return t.tm_year, t.tm_mon
    except OSError:
        return 1970, 1


def target_path(root: str, src: str) -> str:
    y, mo = get_date(src)
    return os.path.join(root, str(y), f"{y}-{mo:02d}", os.path.basename(src))


def scan(root: str) -> list[str]:
    files = []
    skip = {os.path.join(root, d) for d in SKIP_DIRS}
    for dp, dirs, fns in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(dp, d) not in skip]
        for fn in fns:
            if fn.lower().endswith(SKIP_SUFFIXES) or not is_media(fn):
                continue
            files.append(os.path.join(dp, fn))
    return files


def main() -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--action", default="report-only", choices=["report-only", "move"])
    args = ap.parse_args()
    root = os.path.abspath(args.input)
    ok, why = is_safe_dir(root, must_exist=True)
    if not ok:
        sys.exit(f"Опасный путь: {why}")
    files = scan(root)
    plan = [(src, target_path(root, src)) for src in files]
    moving = [(s, d) for s, d in plan if not _same(s, d)]
    print(f"Файлов: {len(files)}, переедут: {len(moving)}")
    for s, d in moving[:5]:
        print(f"  {os.path.relpath(s, root)}  ->  {os.path.relpath(d, root)}")
    if args.action == "report-only":
        print("Это только отчёт: файлы не перемещались. Добавьте --action move.")
        return 0
    moved = err = 0
    for s, d in moving:
        try:
            d = unique_path(d)
            os.makedirs(to_long_path(os.path.dirname(d)), exist_ok=True)
            shutil.move(to_long_path(s), to_long_path(d))
            moved += 1
        except OSError as e:
            err += 1
            if err <= 5:
                print(f"  не перенёс {s}: {e}")
    print(f"Переложено: {moved}, ошибок: {err}, удалено пустых папок: {remove_empty_dirs(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
