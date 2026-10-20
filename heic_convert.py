"""Конвертация HEIC в JPG: копия рядом с оригиналом или замена с переносом HEIC.

Фото с iPhone приходят в HEIC, который Windows и многие программы не открывают.
Модуль делает из каждого скачанного HEIC обычный JPG, сохраняя EXIF (дату съёмки,
камеру, GPS), цветовой профиль и время изменения файла. HEIC никогда не удаляется:
в режиме «только JPG» он переезжает в папку _heic_originals.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Callable

import pillow_heif
from PIL import Image

from state import State
from utils import (
    DIR_HEIC_ORIGINALS,
    file_size_safe,
    is_heic,
    path_key,
    remove_quiet,
    short_id,
    to_long_path,
    with_suffix_before_ext,
)

log = logging.getLogger("webui.heic")

MODES = ("originals", "both", "jpg")

EXIF_ORIENTATION = 0x0112
EXIF_IFD = 0x8769
EXIF_MAKERNOTE = 0x927C
# Больше одного сегмента APP1 (EXIF) в JPEG не помещается — Pillow откажется писать
JPEG_EXIF_MAX = 65533

# Про поворот фото. libheif при декодировании сам применяет повороты, отражения и
# кадрирование из контейнера HEIC (боксы irot, imir, clap); pillow-heif 1.8.1 это
# не отключает (опции ignore_transformations в нём нет). Пиксели приходят уже
# повёрнутыми, а pillow-heif в режиме плагина (as_plugin._init_from_heif_file,
# misc.set_orientation) тут же сбрасывает тег EXIF Orientation в info["exif"] на 1,
# исходное значение кладёт в info["original_orientation"]. Если бы исходный тег
# попал в JPG, просмотрщик повернул бы фото второй раз. Проверено на HEIC
# с Orientation=6: pillow-heif отдаёт картинку с переставленными шириной и высотой
# и Orientation=1. Ниже тег на всякий случай проверяется ещё раз.
pillow_heif.register_heif_opener()


def jpg_path_for(heic_path: str) -> str:
    """IMG_0001.HEIC → IMG_0001.JPG, фото.heic → фото.jpg: регистр как у исходника."""
    stem, ext = os.path.splitext(heic_path)
    return stem + (".JPG" if ext.isupper() else ".jpg")


def convert_heic_to_jpg(src: str, dst: str, quality: int = 92) -> None:
    """Конвертирует один HEIC/HEIF в JPEG, сохраняя EXIF, ICC-профиль и mtime.

    Пишет во временный файл `dst + ".tmp"` и переименовывает: на диске либо нет
    файла, либо он целый. Время изменения копии равно времени оригинала: программа
    при скачивании ставит в mtime дату съёмки, копия должна её сохранить.
    """
    src_long = to_long_path(src)
    dst_long = to_long_path(dst)
    tmp = dst + ".tmp"
    st = os.stat(src_long)
    with Image.open(src_long) as img:
        img.load()
        exif = _exif_for_jpeg(img)
        icc = img.info.get("icc_profile")
        out = img if img.mode in ("RGB", "L") else img.convert("RGB")
        try:
            with open(to_long_path(tmp), "wb") as f:
                out.save(f, format="JPEG", quality=quality, exif=exif, icc_profile=icc)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            remove_quiet(tmp)
            raise
    os.replace(to_long_path(tmp), dst_long)
    os.utime(dst_long, ns=(st.st_atime_ns, st.st_mtime_ns))


def _exif_for_jpeg(img: Image.Image) -> bytes:
    """EXIF для JPEG: исходные байты как есть, но Orientation гарантированно 1.

    Байты не пересобираются без нужды: так MakerNote и прочие теги камеры
    остаются нетронутыми. Пересборка только если тег поворота всё же не 1
    или блок не влезает в JPEG (тогда убираем MakerNote).
    """
    raw = img.info.get("exif") or b""
    if not raw:
        return b""
    exif = img.getexif()
    if exif.get(EXIF_ORIENTATION, 1) != 1:
        exif[EXIF_ORIENTATION] = 1
        raw = exif.tobytes()
    if len(raw) > JPEG_EXIF_MAX:
        exif.get_ifd(EXIF_IFD).pop(EXIF_MAKERNOTE, None)
        raw = exif.tobytes()
    if not raw.startswith(b"Exif\x00\x00"):
        raw = b"Exif\x00\x00" + raw
    return raw


def process_photos(state: State, download_dir: str, mode: str,
                   progress_cb: Callable[[dict], None] | None = None,
                   stop_event: threading.Event | None = None) -> dict:
    """Обрабатывает все скачанные HEIC по выбранному режиму.

    originals — ничего не делать.
    both — рядом с каждым HEIC появляется JPG-копия; HEIC и запись в базе не меняются.
    jpg — JPG пишется рядом, затем HEIC переезжает (не удаляется) в _heic_originals
          с тем же относительным путём, а local_path в базе указывает на JPG.
          Видеочасть Live Photo не трогается.

    Берутся только записи со статусом downloaded и расширением .heic/.heif
    (фото и Live Photo). Повторный запуск безопасен: готовый непустой JPG не
    пересоздаётся (считается пропущенным; в режиме jpg оставшийся рядом HEIC всё
    равно переезжает, чтобы режим «только JPG» был выполнен). Ошибка одного файла
    пишется в журнал и считается, обработка идёт дальше. Ничего не удаляется.

    progress_cb(info) зовётся после каждого файла с ключами total, done, skipped,
    errors, current. stop_event останавливает обработку между файлами.
    Возвращает словарь total, done, skipped, errors, mode.
    """
    if mode not in MODES:
        raise ValueError(f"Неизвестный режим конвертации: {mode}")
    counts = {"total": 0, "done": 0, "skipped": 0, "errors": 0}
    if mode == "originals":
        return {**counts, "mode": mode}

    root = os.path.abspath(download_dir)
    all_rows = list(state.get_downloaded())
    taken = _taken_paths(all_rows)
    rows = [r for r in all_rows
            if r["kind"] in ("photo", "live") and r["local_path"] and is_heic(r["local_path"])]
    counts["total"] = len(rows)
    log.info("Конвертация HEIC, режим %s: %d файлов", mode, len(rows))

    for row in rows:
        if stop_event is not None and stop_event.is_set():
            log.info("Конвертация HEIC остановлена")
            break
        try:
            changed = _process_one(state, row, root, mode, taken)
        except Exception as e:  # noqa: BLE001
            counts["errors"] += 1
            log.warning("HEIC %s: %s", row["local_path"], e)
        else:
            counts["done" if changed else "skipped"] += 1
        if progress_cb is not None:
            progress_cb({**counts, "current": row["filename"]})

    log.info("Конвертация HEIC завершена: готово %d, пропущено %d, ошибок %d",
             counts["done"], counts["skipped"], counts["errors"])
    return {**counts, "mode": mode}


def _taken_paths(rows: list[sqlite3.Row]) -> dict[str, str]:
    """Ключ пути → id файла, которому он принадлежит в базе (один проход по списку)."""
    taken: dict[str, str] = {}
    for r in rows:
        for p in (r["local_path"], r["live_local_path"]):
            if p:
                taken.setdefault(path_key(p), r["id"])
    return taken


def _jpg_target(row: sqlite3.Row, taken: dict[str, str]) -> str:
    """Куда класть JPG. В iCloud бывают IMG_0001.HEIC и IMG_0001.JPG — разные фото;

    если имя занято другим файлом из базы, добавляем хвост из идентификатора,
    как при скачивании (state.plan_local_paths). Чужой файл не затирается.
    """
    jpg = jpg_path_for(row["local_path"])
    owner = taken.get(path_key(jpg))
    if owner is not None and owner != row["id"]:
        jpg = with_suffix_before_ext(jpg, "_" + short_id(row["id"]))
    return jpg


def _process_one(state: State, row: sqlite3.Row, root: str, mode: str,
                 taken: dict[str, str]) -> bool:
    """Один файл. True — что-то изменилось (JPG создан, HEIC перенесён, база обновлена)."""
    src = row["local_path"]
    jpg = _jpg_target(row, taken)
    src_size = file_size_safe(src)
    changed = False

    if not file_size_safe(jpg):
        if src_size is None:
            raise FileNotFoundError(f"HEIC не найден: {src}")
        convert_heic_to_jpg(src, jpg)
        log.debug("HEIC → JPG: %s", jpg)
        changed = True

    if mode == "jpg":
        # Сначала перенос, потом база: если перенос сорвётся, запись останется на HEIC
        # и следующий запуск повторит попытку
        if src_size is not None:
            moved = _move_to_originals(src, root)
            log.debug("HEIC перенесён: %s", moved)
            changed = True
        state.update_local_path(row["id"], local_path=jpg)
        changed = True
    return changed


def _move_to_originals(src: str, root: str) -> str:
    """Переносит HEIC в <root>/_heic_originals/<тот же относительный путь>. Без затирания."""
    rel = os.path.relpath(os.path.abspath(src), root)
    if rel.split(os.sep)[0] == "..":
        raise ValueError(f"файл вне папки скачивания: {src}")
    dst = os.path.join(root, DIR_HEIC_ORIGINALS, rel)
    if os.path.exists(to_long_path(dst)):
        raise FileExistsError(f"в {DIR_HEIC_ORIGINALS} уже есть такой файл: {dst}")
    os.makedirs(to_long_path(os.path.dirname(dst)), exist_ok=True)
    os.rename(to_long_path(src), to_long_path(dst))
    return dst
