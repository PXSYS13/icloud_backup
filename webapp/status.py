"""/api/status — всё, что интерфейс опрашивает раз в 1,5 секунды.

Ключи ответа — API для фронтенда: переименовывать их нельзя.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from utils import (
    APP_VERSION,
    DONATION_URL,
    GITHUB_URL,
    ICLOUDPD_URL,
    TELEGRAM_CHANNEL,
    TELEGRAM_HANDLE,
    YADISK_BK_URL,
    YANDEX_ROOT,
    YOUTUBE_URL,
)
from webapp import config, runtime
from webapp.account import account_view
from webapp.local_disk import free_space, space_check

router = APIRouter()


@router.get("/api/status")
def api_status():
    cfg = config.get_config()
    state = runtime.get_state()
    library = state.get_stats()
    pipe = runtime.pipeline.status()
    free = free_space(cfg["download_dir"])
    remaining = library["bytes_pending"]
    speed = pipe.get("speed_bps") or 0
    eta = (remaining / speed) if speed > 0 and remaining > 0 else None
    pipe["eta_sec"] = eta
    return {
        "app_version": APP_VERSION,
        "now": time.time(),
        "links": {
            "donation": DONATION_URL, "telegram": TELEGRAM_CHANNEL, "telegram_handle": TELEGRAM_HANDLE,
            "youtube": YOUTUBE_URL, "github": GITHUB_URL, "yadisk_bk": YADISK_BK_URL, "icloudpd": ICLOUDPD_URL,
            "yandex_root": YANDEX_ROOT,
        },
        "config": config.public_config(cfg),
        "account": account_view(),
        "library": library,
        "recent": state.recent_downloaded(8),
        "pipeline": pipe,
        "job": runtime.jobs.status(),
        "free_space": free,
        "space": space_check(free, remaining),
        "demo": runtime.demo,
    }
