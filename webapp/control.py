"""Кнопки интерфейса: папка, настройки, ключ Яндекса, проверка медиатеки, скачивание,
пауза, обработка, открыть папку, забыть список."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils import DIR_DUP, DIR_HEIC_ORIGINALS, IS_WINDOWS, is_safe_download_dir, mask_token
from webapp import config, paths, runtime
from webapp.local_disk import free_space

log = logging.getLogger("webui")
router = APIRouter()


def _resp(t: tuple[bool, str]) -> dict:
    return {"ok": t[0], "msg": t[1]}


def _logged_in() -> bool:
    return runtime.get_engine().account().get("state") == "ok"


# ── Папка и настройки ──


class DownloadDirPayload(BaseModel):
    download_dir: str


@router.post("/api/config/download_dir")
def api_set_dir(p: DownloadDirPayload):
    d = (p.download_dir or "").strip()
    if runtime.pipeline.is_running():
        raise HTTPException(409, "Сначала остановите скачивание — потом можно сменить папку")
    if runtime.jobs.is_running():
        raise HTTPException(409, "Сначала дождитесь окончания обработки — потом можно сменить папку")
    ok, msg = is_safe_download_dir(d)
    if not ok:
        log.warning("папка отклонена: %r — %s", d, msg)
        raise HTTPException(400, msg)
    config.env_set(config.dir_key(), d)
    log.info("папка сохранена: %s", d)
    return {"ok": True, "download_dir": d, "free_space": free_space(d)}


class SettingsPayload(BaseModel):
    photo_format: str | None = None
    by_month: bool | None = None


@router.post("/api/settings")
def api_set_settings(p: SettingsPayload):
    updates: dict[str, str] = {}
    if p.photo_format is not None:
        if p.photo_format not in config.PHOTO_FORMATS:
            raise HTTPException(400, "Неизвестный формат фото")
        updates["PHOTO_FORMAT"] = p.photo_format
    if p.by_month is not None:
        if runtime.pipeline.is_running():
            raise HTTPException(409, "Сначала остановите скачивание — потом можно менять раскладку")
        updates["LAYOUT_BY_MONTH"] = "1" if p.by_month else "0"
    config.ensure_env_file()
    for key, value in updates.items():
        config.env_set_verified(key, value)
    log.info("настройки сохранены: %s", ", ".join(f"{k}={v}" for k, v in updates.items()) or "без изменений")
    return {"ok": True, "config": config.public_config()}


class TokenPayload(BaseModel):
    token: str


@router.post("/api/config/token/check")
def api_check_token(p: TokenPayload):
    import yandex_upload
    return yandex_upload.check_token((p.token or "").strip())


@router.post("/api/config/token")
def api_set_token(p: TokenPayload):
    import yandex_upload
    token = (p.token or "").strip()
    if not token:
        raise HTTPException(400, "Пустой ключ")
    check = yandex_upload.check_token(token)
    if not check.get("ok") and not check.get("unreachable"):
        return {"ok": False, "msg": check.get("msg") or "Яндекс не принял ключ"}
    config.env_set("YA_DISK_TOKEN", token)
    log.info("ключ Яндекса сохранён (%s)", mask_token(token))
    return {"ok": True, "check": check, "msg": "Ключ Яндекса сохранён"}


@router.get("/api/yandex/check")
def api_yandex_check():
    """Проверка сохранённого ключа: зовётся при открытии раздела «Яндекс Диск», не из опроса статуса."""
    import yandex_upload
    token = config.get_config()["ya_token"]
    if not token:
        return {"ok": False, "msg": "Ключ не задан"}
    return yandex_upload.check_token(token)


@router.post("/api/config/token/clear")
def api_clear_token():
    if paths.ENV_FILE.exists():
        config.env_set("YA_DISK_TOKEN", "", quote_mode="never")
    log.info("ключ Яндекса удалён пользователем")
    return {"ok": True, "msg": "Ключ Яндекса удалён"}


# ── Медиатека и скачивание ──


def _start(mode: str) -> dict:
    if not _logged_in():
        return {"ok": False, "msg": "Сначала войдите в Apple ID", "action": "login"}
    if runtime.jobs.is_running():
        return {"ok": False, "msg": "Сначала дождитесь окончания обработки"}
    cfg = config.get_config()
    if mode == "download":
        ok, msg = is_safe_download_dir(cfg["download_dir"])
        if not ok:
            return {"ok": False, "msg": f"Папка для фото не подходит: {msg}", "action": "folder"}
        try:
            os.makedirs(cfg["download_dir"], exist_ok=True)
        except OSError as e:
            return {"ok": False, "msg": f"Не получилось создать папку: {e}", "action": "folder"}
    ok, msg = runtime.pipeline.start(mode, runtime.get_engine(), runtime.get_state(), cfg)
    return {"ok": ok, "msg": msg}


@router.post("/api/scan")
def api_scan():
    """«Проверить медиатеку»: только список, без скачивания."""
    return _start("scan")


@router.post("/api/download/start")
def api_download_start():
    return _start("download")


@router.post("/api/download/pause")
def api_download_pause():
    return _resp(runtime.pipeline.pause())


@router.post("/api/download/resume")
def api_download_resume():
    return _resp(runtime.pipeline.resume())


@router.post("/api/download/stop")
def api_download_stop():
    return _resp(runtime.pipeline.stop())


@router.post("/api/retry_failed")
def api_retry_failed():
    """«Повторить для N файлов»: не скачавшиеся — снова в очередь, и сразу качать."""
    n = runtime.get_state().reset_failed_to_pending()
    if n == 0:
        return {"ok": False, "msg": "Нет файлов, которые не скачались", "count": 0}
    if runtime.pipeline.is_running():
        return {"ok": True, "msg": f"Вернул в очередь {n} файлов — они скачаются в этом же запуске или в следующем",
                "count": n}
    res = _start("download")
    res["count"] = n
    if res.get("ok"):
        res["msg"] = f"Вернул в очередь {n} файлов и запустил скачивание"
    return res


@router.get("/api/failed")
def api_failed(limit: int = 200):
    rows = runtime.get_state().get_failed(max(1, min(limit, 2000)))
    return {"items": [dict(r) for r in rows]}


# ── Обработка ──


class ProcessPayload(BaseModel):
    kind: str


@router.post("/api/process/start")
def api_process_start(p: ProcessPayload):
    kind = p.kind
    if kind not in ("heic", "dedupe", "layout", "yandex"):
        raise HTTPException(400, "Неизвестная задача")
    if runtime.pipeline.is_running():
        return {"ok": False, "msg": "Сначала остановите скачивание: обработка во время загрузки перепутает файлы"}
    cfg = config.get_config()
    state = runtime.get_state()
    root = cfg["download_dir"]
    if not os.path.isdir(root):
        return {"ok": False, "msg": "Папки с фото ещё нет — сначала скачайте медиатеку"}
    if kind == "heic":
        import heic_convert
        mode = cfg["photo_format"]
        if mode == "originals":
            return {"ok": False, "msg": "Выбраны «Оригиналы»: конвертировать нечего. Выберите «JPG-копии» или «Оба»"}

        def fn(progress, stop):
            return heic_convert.process_photos(state, root, mode, progress, stop)
    elif kind == "dedupe":
        import dedupe

        def fn(progress, stop):
            return dedupe.move_duplicates(state, root, progress, stop)
    elif kind == "layout":
        import organize

        def fn(progress, stop):
            return organize.relayout(state, root, cfg["by_month"], progress, stop)
    else:
        import yandex_upload
        token = cfg["ya_token"]
        if not token:
            return {"ok": False, "msg": "Сначала добавьте ключ доступа к Яндекс Диску", "action": "token"}

        def fn(progress, stop):
            return yandex_upload.upload_all(token, root, progress, stop)
    return _resp(runtime.jobs.start(kind, fn))


@router.post("/api/process/stop")
def api_process_stop():
    return _resp(runtime.jobs.stop())


# ── Папки ──


class OpenFolderPayload(BaseModel):
    which: str = "download"  # download | duplicates | heic


@router.post("/api/open_folder")
def api_open_folder(p: OpenFolderPayload):
    """Открывает папку скачивания в проводнике. Только настроенную — никаких чужих путей."""
    base = Path(config.get_config()["download_dir"])
    sub = {"download": "", "duplicates": DIR_DUP, "heic": DIR_HEIC_ORIGINALS}.get(p.which)
    if sub is None:
        raise HTTPException(400, "Неизвестная папка")
    path = base / sub if sub else base
    if not path.exists():
        return {"ok": False, "msg": "Папки пока нет — в ней ещё ничего нет"}
    try:
        if IS_WINDOWS:
            os.startfile(str(path))  # noqa: S606 — только папка из настроек
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"Не получилось открыть папку: {e}. Путь: {path}"}
    return {"ok": True, "msg": "Открыл папку", "path": str(path)}


# ── Забыть список ──


def safe_remove_db_files(base_file: Path) -> tuple[bool, str]:
    deadline = time.monotonic() + 3.0
    while True:
        errors = []
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(base_file) + suffix)
            if f.exists():
                try:
                    f.unlink()
                except OSError as e:
                    errors.append((f.name, e))
        if not errors:
            return True, "Список забыт. При следующем запуске программа соберёт его заново"
        if time.monotonic() >= deadline:
            details = "; ".join(f"{name}: {err}" for name, err in errors)
            return False, f"База занята другим процессом ({details}). Попробуйте позже."
        time.sleep(0.3)


@router.post("/api/forget_list")
def api_forget_list():
    if runtime.pipeline.is_running():
        runtime.pipeline.stop()
        runtime.pipeline.wait_finished(15)
    if runtime.jobs.is_running():
        return {"ok": False, "msg": "Сначала дождитесь окончания обработки"}
    runtime.close_state()
    log.info("список файлов забыт по просьбе пользователя")
    return _resp(safe_remove_db_files(paths.DB_FILE))
