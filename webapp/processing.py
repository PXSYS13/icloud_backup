"""Задачи обработки: HEIC в JPG, дубли, раскладка, загрузка в Яндекс Диск.

Одна задача за раз, в фоновом потоке. Каждая функция задачи получает progress_cb и
stop_event и возвращает словарь-итог; интерфейс читает Jobs.status().
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger("webui.jobs")

JOB_TITLES = {
    "heic": "Конвертация HEIC в JPG",
    "dedupe": "Поиск одинаковых файлов",
    "layout": "Раскладка по годам и месяцам",
    "yandex": "Загрузка в Яндекс Диск",
}


class Jobs:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.kind: str | None = None
        self.progress: dict = {}
        self.result: dict | None = None
        self.error: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, kind: str, fn: Callable[[Callable[[dict], None], threading.Event], dict]) -> tuple[bool, str]:
        with self._lock:
            if self.is_running():
                return False, f"Сначала дождитесь: {JOB_TITLES.get(self.kind, self.kind)}"
            self._stop.clear()
            self.kind = kind
            self.progress = {}
            self.result = None
            self.error = None
            self.started_at = time.time()
            self.finished_at = None
            self._thread = threading.Thread(target=self._run, args=(fn,), daemon=True, name=f"job-{kind}")
            self._thread.start()
            return True, f"Запущено: {JOB_TITLES.get(kind, kind)}"

    def stop(self) -> tuple[bool, str]:
        if not self.is_running():
            return False, "Ничего не запущено"
        self._stop.set()
        return True, "Останавливаю"

    def wait_finished(self, timeout: float = 10.0) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    def _run(self, fn) -> None:
        def progress(info: dict) -> None:
            self.progress = dict(info)

        try:
            self.result = fn(progress, self._stop)
        except Exception as e:  # noqa: BLE001
            log.exception("задача %s упала", self.kind)
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.finished_at = time.time()

    def status(self) -> dict:
        return {
            "running": self.is_running(), "kind": self.kind,
            "title": JOB_TITLES.get(self.kind or "", ""),
            "progress": self.progress, "result": self.result, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "stopping": self._stop.is_set() and self.is_running(),
        }
