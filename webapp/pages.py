"""Страницы и мелкие адреса: интерфейс (/), /api/hello, /api/supporters."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

import supporters
from utils import APP_VERSION
from webapp import paths
from webapp.ports import APP_ID

log = logging.getLogger("webui")

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index():
    html_path = paths.TEMPLATES_DIR / "app.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Интерфейс не установлен</h1>")
    html = html_path.read_text(encoding="utf-8")
    # ?v=версия у статики — после обновления браузер не возьмёт старый JS из кэша
    return HTMLResponse(html.replace("__APP_VERSION__", APP_VERSION))


@router.get("/api/hello")
def api_hello():
    """Кто держит порт: вторая копия программы находит по нему первую."""
    return {"app": APP_ID, "version": APP_VERSION, "folder": str(paths.PROJECT)}


@router.get("/api/supporters")
def api_supporters():
    """«Нас поддерживают»: копия общего списка, которая пришла вместе с программой.

    Программа читает только свой supporters.json и в интернет за списком не ходит. Нет файла
    или он испорчен — пустой список: экран покажет заглушку, а не ошибку.
    """
    data = supporters.load_local(paths.SUPPORTERS_FILE)
    if data is None:
        log.warning("список «Нас поддерживают» не прочитан: %s", paths.SUPPORTERS_FILE.name)
        data = {"updated": None, "supporters": []}
    top_min = data.get("top_min_rub", supporters.TOP_MIN_RUB)
    return {"ok": True, **data, "people": supporters.count_people(data["supporters"]),
            "top": supporters.top_supporters(data["supporters"], top_min), "top_min_rub": top_min}
