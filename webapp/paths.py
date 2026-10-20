"""Где лежат файлы программы.

Модули читают пути в момент вызова (paths.DB_FILE), а не копируют их при импорте:
tests/conftest.py подменяет их на временные, и подмена должна доходить до всех.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

PROJECT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT / ".env"
# .env читаем раньше всего остального: в нём бывает ICLOUD_BK_PORT, а порт выбирается при импорте
load_dotenv(ENV_FILE)

DB_FILE = PROJECT / "state.db"
SESSION_DIR = PROJECT / "session"
TEMPLATES_DIR = PROJECT / "webui_assets"
LOG_DIR = PROJECT / "logs"
WEBUI_LOG = LOG_DIR / "webui.log"
DEMO_CLOUD_DIR = PROJECT / "_demo_cloud"
