"""Общие объекты программы: движок iCloud, база состояния, рабочий поток, задачи обработки.

Создаются один раз при запуске (init) и читаются модулями через функции, а не копиями:
тесты подменяют движок и пути, и подмена должна доходить до всех.
"""

from __future__ import annotations

import logging
import threading
import time

from icloud_engine import Engine, make_engine, session_files_present
from state import State
from webapp import paths
from webapp.pipeline import Pipeline
from webapp.processing import Jobs

log = logging.getLogger("webui")

_engine: Engine | None = None
_state: State | None = None
_state_path: str | None = None
_state_lock = threading.Lock()
demo = False
# Восстановление сессии при старте идёт в фоне: Apple отвечает не мгновенно
restore_info: dict = {"done": False, "ok": False, "at": None}

pipeline = Pipeline()
jobs = Jobs()


def init(demo_mode: bool, restore: bool = True) -> None:
    """Создаёт движок (настоящий или демо) и в фоне пробует продолжить сохранённую сессию."""
    global _engine, demo
    demo = demo_mode
    _engine = make_engine(paths.SESSION_DIR, demo_mode)
    restore_info.update(done=False, ok=False, at=None)
    if restore and session_files_present(paths.SESSION_DIR):
        threading.Thread(target=_restore, daemon=True, name="session-restore").start()
    else:
        restore_info.update(done=True, ok=False, at=time.time())


def _restore() -> None:
    try:
        ok = bool(_engine.restore()) if _engine is not None else False
        log.info("сессия Apple %s", "восстановлена" if ok else "не восстановлена: нужен вход")
    except Exception as e:  # noqa: BLE001
        log.warning("восстановление сессии: %s: %s", type(e).__name__, e)
        ok = False
    restore_info.update(done=True, ok=ok, at=time.time())


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine(paths.SESSION_DIR, demo)
        restore_info.update(done=True, ok=False, at=time.time())
    return _engine


def set_engine(engine: Engine) -> None:
    """Для тестов: подменить движок."""
    global _engine
    _engine = engine
    restore_info.update(done=True, ok=engine.account().get("state") == "ok", at=time.time())


def get_state() -> State:
    """База состояния по текущему paths.DB_FILE (тесты меняют путь на временный)."""
    global _state, _state_path
    with _state_lock:
        path = str(paths.DB_FILE)
        if _state is None or _state_path != path:
            if _state is not None:
                _state.close()
            _state = State(path)
            _state_path = path
        return _state


def close_state() -> None:
    global _state, _state_path
    with _state_lock:
        if _state is not None:
            _state.close()
        _state = None
        _state_path = None
