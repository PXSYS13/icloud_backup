"""iCloud БК — веб-интерфейс.

Запуск:
  uv run webui.py           → http://127.0.0.1:8000 (если порт занят — следующий свободный)
  uv run webui.py --demo    → демо-движок без настоящего Apple ID (код 2FA: 123456)

Здесь только сборка приложения и запуск сервера. Логика — в пакете webapp/
(см. webapp/__init__.py), работа с iCloud — в icloud_engine.py.
"""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
import platform
import sys
import threading
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from utils import APP_VERSION, DONATION_URL, TELEGRAM_CHANNEL, ensure_utf8_stdio
from webapp import paths  # первым: читает .env, в нём бывает ICLOUD_BK_PORT
from webapp import account, control, logs, pages, ports, runtime, status

log = logging.getLogger("webui")

app = FastAPI(title="iCloud BK UI")
app.middleware("http")(ports.csrf_guard)
for _module in (status, account, control, logs, pages):
    app.include_router(_module.router)

# Windows берёт MIME из реестра, и там .js бывает text/plain — браузер такой скрипт не выполнит
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
if (paths.TEMPLATES_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(paths.TEMPLATES_DIR / "static")), name="static")


def _setup_logging() -> None:
    """Файл-лог сервера: действия пользователя, события скачивания, ошибки — без секретов."""
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler = RotatingFileHandler(paths.WEBUI_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(fmt)
    for name in ("webui", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.addHandler(handler)


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="iCloud БК — веб-интерфейс")
    parser.add_argument("--demo", action="store_true", help="демо-движок без настоящего Apple ID")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер (для разработки)")
    args = parser.parse_args()
    demo = args.demo or os.environ.get("ICLOUD_BK_DEMO") == "1"
    if demo:
        # Демо живёт отдельно от настоящих данных: своя база, своя сессия, своя папка
        os.environ["ICLOUD_BK_DEMO"] = "1"
        paths.DB_FILE = paths.PROJECT / "state_demo.db"
        paths.SESSION_DIR = paths.PROJECT / "session_demo"
    if args.no_browser:
        os.environ[ports.NO_BROWSER_ENV] = "1"

    _setup_logging()
    log.info("webui v%s старт: python %s, %s, проект %s%s", APP_VERSION, platform.python_version(),
             platform.platform(), paths.PROJECT, " (демо)" if demo else "")

    import uvicorn

    wanted = ports.PORT
    sock, port, running = ports.choose_port(wanted, 1 if ports.PORT_FIXED else ports.PORT_TRIES)
    if running is not None:
        ports.show_running_copy(port, running)
        return
    if sock is None:
        last = wanted + (0 if ports.PORT_FIXED else ports.PORT_TRIES - 1)
        if ports.PORT_FIXED:
            print(f"  Порт {wanted} из ICLOUD_BK_PORT занят другой программой. Укажите другой порт.")
        else:
            print(f"  Порты {wanted}–{last} заняты другими программами. Закройте лишние программы и запустите снова.")
        log.warning("не запустился: порты %d–%d заняты", wanted, last)
        sys.exit(1)
    ports.use_port(port)
    url = f"http://{ports.HOST}:{port}"

    runtime.init(demo)

    print("=" * 65)
    print(f"  iCloud БК — by Davron (PXSYS) · v{APP_VERSION}{' · ДЕМО' if demo else ''}")
    print(f"  Адрес: {url}")
    print(f"  Telegram: {TELEGRAM_CHANNEL}")
    print(f"  Поддержать: {DONATION_URL}")
    print("=" * 65)
    print()
    if port != wanted:
        print(f"  Порт {wanted} занят другой программой, поэтому программа работает на {url}")
        print()
    print("  Сервер запускается, браузер откроется автоматически.")
    print("  Чтобы остановить — закройте это окно или нажмите Ctrl+C.")
    print()

    if os.getenv(ports.NO_BROWSER_ENV) != "1":
        threading.Thread(target=ports.open_browser_when_ready, args=(ports.HOST, port), daemon=True).start()

    server = uvicorn.Server(uvicorn.Config(app, host=ports.HOST, port=port, log_level="warning"))
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        if runtime.pipeline.is_running():
            runtime.pipeline.stop()
            runtime.pipeline.wait_finished(10)


if __name__ == "__main__":
    main()
