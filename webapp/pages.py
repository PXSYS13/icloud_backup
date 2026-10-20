"""Страницы и мелкие адреса: интерфейс (/), /api/hello."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from utils import APP_VERSION
from webapp import paths
from webapp.ports import APP_ID

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
