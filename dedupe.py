"""Одинаковые файлы: ищем по MD5 и размеру, переносим в _duplicates, ничего не удаляем.

Для веб-интерфейса — move_duplicates(state, …): работает по базе state.db, где MD5
каждого скачанного файла уже посчитан при скачивании. Остаётся самый ранний по дате
съёмки, остальные переезжают в `_duplicates/` с той же структурой папок (видеочасть
Live Photo — вместе с фото). Повторный запуск ничего не ломает.

Командная строка (любая папка, без базы):
  uv run dedupe.py --input "D:/iCloudBackup"              # отчёт
  uv run dedupe.py --input "D:/iCloudBackup" --action move
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections import defaultdict

from state import DUPLICATE_MARKER, State
from utils import (
    DIR_DUP,
    DIR_HEIC_ORIGINALS,
    compute_md5,
    ensure_utf8_stdio,
    is_heic,
    is_media,
    is_safe_dir,
    ru_bytes,
    to_long_path,
)

SKIP_DIRS = {DIR_DUP, DIR_HEIC_ORIGINALS}
SKIP_SUFFIXES = (".part", ".tmp", ".db", ".db-wal", ".db-shm", ".log")


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


def jpg_twin(path: str) -> str | None:
    """JPG-копия рядом с HEIC (режим «Оба»), если она есть."""
    if not is_heic(path):
        return None
    from heic_convert import jpg_path_for
    twin = jpg_path_for(path)
    return twin if os.path.isfile(to_long_path(twin)) else None


def _move(src: str, root: str, dup_root: str) -> str:
    rel = os.path.relpath(src, root)
    dst = unique_path(os.path.join(dup_root, rel))
    os.makedirs(to_long_path(os.path.dirname(dst)), exist_ok=True)
    shutil.move(to_long_path(src), to_long_path(dst))
    return dst


def move_duplicates(state: State, download_dir: str, progress_cb=None, stop_event=None) -> dict:
    """Переносит дубли из базы в <download_dir>/_duplicates. Возвращает итог для интерфейса."""
    root = os.path.abspath(download_dir)
    dup_root = os.path.join(root, DIR_DUP)
    groups = state.duplicate_groups()
    out = {"groups": len(groups), "moved": 0, "bytes": 0, "errors": 0, "examples": [], "folder": dup_root}
    total = sum(len(g) - 1 for g in groups)
    done = 0
    for group in groups:
        if stop_event is not None and stop_event.is_set():
            out["stopped"] = True
            break
        keep = group[0]
        for dup in group[1:]:
            done += 1
            if (dup["error"] or "").startswith(DUPLICATE_MARKER):
                continue  # уже перенесён раньше
            src = dup["local_path"]
            if not src or not _inside(src, root) or _inside(src, dup_root) or not os.path.isfile(to_long_path(src)):
                continue
            try:
                new_main = _move(src, root, dup_root)
                new_live = None
                live = dup["live_local_path"]
                if live and _inside(live, root) and os.path.isfile(to_long_path(live)):
                    new_live = _move(live, root, dup_root)
                # JPG-копия (режим «Оба») едет вместе с HEIC, иначе осталась бы сиротой
                twin = jpg_twin(src)
                if twin:
                    _move(twin, root, dup_root)
                state.move_to_duplicates(dup["id"], new_main, keep["id"])
                if new_live:
                    state.update_local_path(dup["id"], live_local_path=new_live)
                out["moved"] += 1
                out["bytes"] += int(dup["size"] or 0)
                if len(out["examples"]) < 3:
                    out["examples"].append({"dup": os.path.relpath(src, root), "keep": os.path.relpath(keep["local_path"], root)})
            except OSError:
                out["errors"] += 1
            if progress_cb is not None:
                progress_cb({"total": total, "done": done, "moved": out["moved"], "errors": out["errors"],
                             "current": os.path.basename(src)})
    out["text"] = (f"Одинаковых файлов перенесено: {out['moved']} ({ru_bytes(out['bytes'])}) в папку _duplicates"
                   if out["moved"] else "Одинаковых файлов не найдено")
    return out


# ── Командная строка: любая папка без базы ─────────────────────────────────


def scan_folder(root: str) -> list[dict]:
    files = []
    skip = {os.path.join(root, d) for d in SKIP_DIRS}
    for dp, dirs, fns in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(dp, d) not in skip]
        for fn in fns:
            if fn.lower().endswith(SKIP_SUFFIXES) or not is_media(fn):
                continue
            full = os.path.join(dp, fn)
            try:
                files.append({"path": full, "size": os.path.getsize(full)})
            except OSError:
                pass
    return files


def find_dup_paths(files: list[dict]) -> dict[str, str]:
    """{путь дубля: путь оригинала}: MD5 считаем только у файлов одинакового размера."""
    by_size: dict[int, list[dict]] = defaultdict(list)
    for f in files:
        if f["size"] > 0:
            by_size[f["size"]].append(f)
    out: dict[str, str] = {}
    for same in by_size.values():
        if len(same) < 2:
            continue
        by_md5: dict[str, list[dict]] = defaultdict(list)
        for f in same:
            try:
                by_md5[compute_md5(f["path"])].append(f)
            except OSError:
                pass
        for grp in by_md5.values():
            if len(grp) > 1:
                grp.sort(key=lambda x: (os.path.getmtime(x["path"]), x["path"]))
                for d in grp[1:]:
                    out[d["path"]] = grp[0]["path"]
    return out


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
    files = scan_folder(root)
    dups = find_dup_paths(files)
    total = sum(os.path.getsize(p) for p in dups)
    print(f"Файлов: {len(files)}, одинаковых: {len(dups)} ({ru_bytes(total)})")
    for d, k in list(dups.items())[:10]:
        print(f"  {os.path.relpath(d, root)}  — такой же, как  {os.path.relpath(k, root)}")
    if args.action == "report-only":
        print("Это только отчёт: файлы не перемещались. Добавьте --action move, чтобы перенести их в _duplicates.")
        return 0
    moved = 0
    for d in dups:
        try:
            _move(d, root, os.path.join(root, DIR_DUP))
            moved += 1
        except OSError as e:
            print(f"  не перенёс {d}: {e}")
    print(f"Перенесено в _duplicates: {moved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
