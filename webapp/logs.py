"""Журналы программы и отчёт для поддержки (ZIP с логами и support-info.json без секретов)."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from utils import APP_VERSION
from webapp import config, paths, runtime
from webapp.account import account_view
from webapp.local_disk import free_space

log = logging.getLogger("webui")
router = APIRouter()

_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.log(\.\d+)?$")


def log_path(name: str) -> Path:
    if not _LOG_NAME_RE.match(name or ""):
        raise HTTPException(400, "Недопустимое имя лога")
    p = (paths.LOG_DIR / name).resolve()
    if paths.LOG_DIR.resolve() not in p.parents:
        raise HTTPException(400, "Недопустимое имя лога")
    if not p.is_file():
        raise HTTPException(404, "Лог не найден")
    return p


def list_logs() -> list[dict]:
    out = []
    if not paths.LOG_DIR.exists():
        return out
    for p in sorted(paths.LOG_DIR.glob("*")):
        if not p.is_file() or not _LOG_NAME_RE.match(p.name):
            continue
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def tail_file(path: Path, lines: int) -> list[str]:
    lines = max(1, min(lines, 2000))
    chunk = 64 * 1024
    data = b""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0 and data.count(b"\n") <= lines:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


def support_info() -> dict:
    """Диагностика для поддержки: версия, окружение, настройки без секретов, состояние."""
    cfg = config.get_config()
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": APP_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config": config.public_config(cfg),
        "account": account_view(),
        "free_space": free_space(cfg["download_dir"]),
        "library": runtime.get_state().get_stats(),
        "pipeline": runtime.pipeline.status(),
        "job": runtime.jobs.status(),
    }


def build_support_bundle() -> Path:
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    for old in paths.LOG_DIR.glob("icloud-logs-*.zip"):
        try:
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    name = f"icloud-logs-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    bundle = paths.LOG_DIR / name
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in list_logs():
            try:
                zf.write(paths.LOG_DIR / entry["name"], entry["name"])
            except OSError:
                pass
        zf.writestr("support-info.json", json.dumps(support_info(), ensure_ascii=False, indent=2, default=str))
    log.info("собран архив логов для поддержки: %s", name)
    return bundle


@router.get("/api/logs")
def api_logs():
    return {"logs": list_logs(), "dir": str(paths.LOG_DIR)}


@router.get("/api/logs/tail")
def api_logs_tail(name: str, lines: int = 200):
    p = log_path(name)
    return {"name": name, "lines": tail_file(p, lines), "size": p.stat().st_size}


@router.get("/api/logs/bundle")
def api_logs_bundle():
    bundle = build_support_bundle()
    return FileResponse(str(bundle), media_type="application/zip", filename=bundle.name)
