"""Настройки программы в .env: папка, формат фото, раскладка по месяцам, ключ Яндекса.

Всё живёт в .env, чтобы пережить перезапуск программы. Кто хочет подменить настройки
в тесте, подменяет config.get_config: модули зовут её через модуль, а не копией.
"""

from __future__ import annotations

import os

from dotenv import dotenv_values, load_dotenv, set_key
from fastapi import HTTPException

from utils import mask_token
from webapp import paths

PHOTO_FORMATS = ("originals", "jpg", "both")
PHOTO_FORMAT_LABELS = {"originals": "Оригиналы", "jpg": "JPG-копии", "both": "Оба"}


def env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def is_demo() -> bool:
    return env_flag("ICLOUD_BK_DEMO", False)


def dir_key() -> str:
    """В демо папка своя (DEMO_DOWNLOAD_DIR): демо-файлы не должны попасть в настоящую папку с фото."""
    return "DEMO_DOWNLOAD_DIR" if is_demo() else "DOWNLOAD_DIR"


def get_config() -> dict:
    """Перечитывает .env заново."""
    load_dotenv(paths.ENV_FILE, override=True)
    try:
        saved = dotenv_values(paths.ENV_FILE) if paths.ENV_FILE.exists() else {}
    except Exception:  # noqa: BLE001
        saved = {}
    fmt = (os.getenv("PHOTO_FORMAT", "") or "originals").strip().lower()
    key = dir_key()
    default_dir = str(paths.PROJECT / ("downloads_demo" if is_demo() else "downloads"))
    return {
        "download_dir": (os.getenv(key, default_dir) or "").strip() or default_dir,
        # Папку выбирали осознанно (есть в .env), а не взяли дефолт
        "download_dir_set": bool((saved.get(key) or "").strip()),
        "ya_token": (os.getenv("YA_DISK_TOKEN", "") or "").strip(),
        "photo_format": fmt if fmt in PHOTO_FORMATS else "originals",
        "by_month": env_flag("LAYOUT_BY_MONTH", True),
        "demo": is_demo(),
    }


def public_config(cfg: dict | None = None) -> dict:
    """Настройки для интерфейса: без секретов."""
    cfg = cfg or get_config()
    return {
        "download_dir": cfg["download_dir"],
        "download_dir_set": cfg["download_dir_set"],
        "photo_format": cfg["photo_format"],
        "photo_format_labels": dict(PHOTO_FORMAT_LABELS),
        "by_month": cfg["by_month"],
        "ya_token_masked": mask_token(cfg["ya_token"]),
        "ya_token_set": bool(cfg["ya_token"]),
    }


def ensure_env_file() -> None:
    if not paths.ENV_FILE.exists():
        paths.ENV_FILE.write_text("", encoding="utf-8")


def env_set(key: str, value: str, quote_mode: str = "always") -> None:
    """Пишет ключ в .env и сразу перечитывает .env в окружение программы.

    python-dotenv пишет через временный файл и замену — файл либо старый, либо новый.
    """
    ensure_env_file()
    set_key(str(paths.ENV_FILE), key, value, quote_mode=quote_mode)
    load_dotenv(paths.ENV_FILE, override=True)
    os.environ[key] = value


def env_set_verified(key: str, value: str) -> None:
    """Пишет ключ в .env и перечитывает: то, что прочтёт программа, обязано совпасть."""
    ensure_env_file()
    set_key(str(paths.ENV_FILE), key, value, quote_mode="always")
    try:
        saved = dotenv_values(paths.ENV_FILE).get(key)
    except Exception:  # noqa: BLE001
        saved = None
    if (saved or "") != value:
        raise HTTPException(500, "Не получилось сохранить настройку: файл .env прочитался иначе. "
                                 "Скачайте отчёт для поддержки во вкладке «Помощь»")
    os.environ[key] = value
