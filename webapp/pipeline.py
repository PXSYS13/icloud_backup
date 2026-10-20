"""Рабочий поток: список медиатеки и скачивание с докачкой.

Один поток, один файл за раз — так советует icloudpd (его `--threads-num` всегда 1):
Apple режет частые запросы, и надёжность важнее скорости. Прогресс и события читает
/api/status; пауза и остановка — через threading.Event. Всё, что скачано, записано
в state.db, поэтому после остановки или перезапуска работа продолжается с того же места.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

from icloud_engine import EngineError, MediaItem
from state import State, ensure_parent, local_copy_matches, plan_local_paths
from utils import (
    compute_md5,
    file_size_safe,
    files_text,
    is_local_disk_error,
    keep_awake,
    remove_quiet,
    to_long_path,
)

log = logging.getLogger("webui.pipeline")

CHUNK = 256 * 1024
# Паузы между повторами при ошибках сети и сбоях Apple: нарастают, потом держатся
BACKOFF_SEC = (5, 15, 45, 120, 300, 300, 300, 300)
# Столько попыток подряд на один файл, потом он помечается «не скачался» и очередь идёт дальше
MAX_ITEM_ATTEMPTS = 4
# Столько раз подряд пробуем получить список, если Apple обрывает его
MAX_LIST_ATTEMPTS = 8
# Список свежий столько секунд: повторный старт не собирает его заново
LISTING_FRESH_SEC = 6 * 3600
SPEED_WINDOW_SEC = 30


class _PauseRequested(Exception):
    pass


class _Stopped(Exception):
    pass


class SpeedMeter:
    """Скорость за последние 30 секунд: сумма байт / окно."""

    def __init__(self) -> None:
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def add(self, n: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, n))
            self._trim(now)

    def _trim(self, now: float) -> None:
        while self._samples and now - self._samples[0][0] > SPEED_WINDOW_SEC:
            self._samples.popleft()

    def bps(self) -> float:
        now = time.monotonic()
        with self._lock:
            self._trim(now)
            if not self._samples:
                return 0.0
            span = max(1.0, now - self._samples[0][0])
            return sum(n for _, n in self._samples) / span

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


class Pipeline:
    """Состояния: idle, scanning, downloading, paused, waiting, stopped, done,
    needs_login (сессия истекла), needs_action (нет места, нет доступа), error."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()
        self.deps: dict = {}  # engine, state, config — задаются в start()
        self.reset_view()

    def reset_view(self) -> None:
        self.state_name = "idle"
        self.mode: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.last_activity_at: float | None = None
        self.events: deque[dict] = deque(maxlen=60)
        self.error: dict | None = None
        self.current: dict | None = None
        self.listing_found = 0
        self.total_count: int | None = None
        self.session_files = 0
        self.session_bytes = 0
        self.wait_until: float | None = None
        self.wait_reason: str | None = None
        self.speed = SpeedMeter()

    # ── управление ──

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode: str, engine, state: State, config: dict) -> tuple[bool, str]:
        """mode: scan — только список; download — список (если старый) и скачивание."""
        with self._lock:
            if self.is_running():
                if self._pause.is_set():
                    self._pause.clear()
                    self._event("run", "Продолжаю скачивание")
                    return True, "Продолжаю"
                return False, "Уже идёт"
            self.deps = {"engine": engine, "state": state, "config": config}
            self._stop.clear()
            self._pause.clear()
            self.reset_view()
            self.mode = mode
            self.started_at = time.time()
            self.state_name = "scanning" if mode == "scan" else "downloading"
            self._thread = threading.Thread(target=self._run, args=(mode,), daemon=True, name="pipeline")
            self._thread.start()
            return True, "Запущено"

    def pause(self) -> tuple[bool, str]:
        if not self.is_running():
            return False, "Скачивание не идёт"
        if self._pause.is_set():
            return False, "Уже на паузе"
        self._pause.set()
        self._event("info", "Пауза: нажмите «Продолжить», когда будете готовы")
        return True, "Пауза"

    def resume(self) -> tuple[bool, str]:
        if not self.is_running():
            return False, "Скачивание не идёт — нажмите «Скачать»"
        self._pause.clear()
        self._event("run", "Продолжаю скачивание")
        return True, "Продолжаю"

    def stop(self) -> tuple[bool, str]:
        if not self.is_running():
            return False, "Ничего не запущено"
        self._stop.set()
        self._pause.clear()
        return True, "Останавливаю: уже скачанное сохранено"

    def wait_finished(self, timeout: float = 10.0) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    # ── сводка ──

    def status(self) -> dict:
        running = self.is_running()
        state_name = self.state_name
        if running and self._pause.is_set() and state_name == "downloading":
            state_name = "paused"
        speed = self.speed.bps() if running and state_name == "downloading" else 0.0
        return {
            "state": state_name, "mode": self.mode, "running": running, "paused": self._pause.is_set(),
            "started_at": self.started_at, "finished_at": self.finished_at,
            "last_activity_at": self.last_activity_at,
            "listing_found": self.listing_found, "total_count": self.total_count,
            "current": self.current, "speed_bps": speed,
            "session_files": self.session_files, "session_bytes": self.session_bytes,
            "events": list(reversed(self.events)), "error": self.error,
            "wait_until": self.wait_until, "wait_reason": self.wait_reason,
        }

    # ── внутреннее ──

    def _event(self, kind: str, text: str) -> None:
        self.events.append({"at": time.time(), "kind": kind, "text": text})
        self.last_activity_at = time.time()
        log.info("%s", text)

    def _touch(self) -> None:
        self.last_activity_at = time.time()

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise _Stopped()

    def _wait_if_paused(self) -> None:
        if not self._pause.is_set():
            return
        self.state_name = "paused"
        while self._pause.is_set():
            self._check_stop()
            time.sleep(0.3)
        self.state_name = "downloading"

    def _sleep_backoff(self, attempt: int, reason: str) -> None:
        delay = BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)]
        prev = self.state_name
        self.state_name = "waiting"
        self.wait_until = time.time() + delay
        self.wait_reason = reason
        self._event("wait", f"{reason}. Повтор через {delay} с")
        end = time.monotonic() + delay
        try:
            while time.monotonic() < end:
                self._check_stop()
                time.sleep(0.5)
        finally:
            self.wait_until = None
            self.wait_reason = None
            self.state_name = prev

    def _run(self, mode: str) -> None:
        engine = self.deps["engine"]
        try:
            with keep_awake():
                if mode == "scan":
                    self._scan_pass()
                else:
                    state: State = self.deps["state"]
                    if self._needs_scan(state):
                        self._scan_pass()
                    self._download_pass()
            self._finish("done")
        except _Stopped:
            self._event("info", "Остановлено. Всё скачанное сохранено, можно продолжить позже")
            self._finish("stopped")
        except EngineError as e:
            self._fail_engine(e)
        except Exception as e:  # noqa: BLE001
            log.exception("рабочий поток упал")
            self.error = {"kind": "other", "text": f"Неожиданная ошибка: {type(e).__name__}: {e}"}
            self._event("err", self.error["text"])
            self._finish("error")
        finally:
            self.current = None
            if engine is not None:
                pass

    def _finish(self, name: str) -> None:
        self.state_name = name
        self.finished_at = time.time()
        if name == "done":
            st = self.deps["state"].get_stats() if self.deps.get("state") else {}
            failed = st.get("failed", 0)
            if self.mode == "scan":
                self._event("ok", f"Список получен: {files_text(st.get('total', 0))}")
            elif failed:
                self._event("ok", f"Готово. Не скачались: {files_text(failed)} — их можно повторить")
            else:
                self._event("ok", "Готово: все файлы сохранены")

    def _fail_engine(self, e: EngineError) -> None:
        self.error = {"kind": e.kind, "text": e.text}
        self._event("err", e.text)
        if e.kind == "expired":
            self._finish("needs_login")
        elif e.kind in ("access_denied", "auth"):
            self._finish("needs_action")
        else:
            self._finish("error")

    # ── список ──

    def _needs_scan(self, state: State) -> bool:
        listed = state.get_meta("listed_at")
        if not listed:
            return True
        try:
            at = datetime.strptime(listed[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return True
        return time.time() - at > LISTING_FRESH_SEC

    def _iter_with_retry(self):
        """Перебор медиатеки с повторами: если Apple оборвал список, начинаем заново."""
        engine = self.deps["engine"]
        attempt = 0
        while True:
            self._check_stop()
            try:
                for item in engine.iter_items():
                    yield item
                return
            except EngineError as e:
                if e.kind == "expired":
                    self._reauth_or_raise(e)
                elif e.kind in ("network", "apple", "other"):
                    attempt += 1
                    if attempt >= MAX_LIST_ATTEMPTS:
                        raise
                    self._sleep_backoff(attempt - 1, e.text)
                else:
                    raise

    def _reauth_or_raise(self, e: EngineError) -> None:
        engine = self.deps["engine"]
        self._event("wait", "Сессия Apple истекла — продлеваю")
        try:
            engine.reauthenticate()
        except EngineError:
            raise e from None
        self._event("ok", "Сессия продлена")

    def _scan_pass(self) -> None:
        state: State = self.deps["state"]
        engine = self.deps["engine"]
        self.state_name = "scanning"
        self.listing_found = 0
        self._event("run", "Получаю у Apple список фото и видео")
        attempt = 0
        while True:
            self._check_stop()
            try:
                self.total_count = engine.count()
                break
            except EngineError as e:
                if e.kind == "expired":
                    self._reauth_or_raise(e)
                elif e.kind in ("network", "apple", "other") and attempt < MAX_LIST_ATTEMPTS:
                    attempt += 1
                    self._sleep_backoff(attempt - 1, e.text)
                else:
                    raise
        seen_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        batch: list[dict] = []
        found = 0
        for item in self._iter_with_retry():
            batch.append(item.as_row())
            found += 1
            self.listing_found = found
            self._touch()
            if len(batch) >= 100:
                state.upsert_batch(batch, seen_at)
                batch = []
            self._check_stop()
        if batch:
            state.upsert_batch(batch, seen_at)
        state.set_meta("listed_at", seen_at)
        self._event("ok", f"Список получен: {files_text(found)}")
        if self.mode != "scan":
            self.state_name = "downloading"

    # ── скачивание ──

    def _download_pass(self) -> None:
        state: State = self.deps["state"]
        cfg = self.deps["config"]
        self.state_name = "downloading"
        self.speed.reset()
        self._event("run", "Начинаю скачивание")
        for item in self._iter_with_retry():
            self._check_stop()
            self._wait_if_paused()
            row = state.get(item.id)
            if row is None:
                state.upsert_batch([item.as_row()])
                row = state.get(item.id)
            if row["status"] in ("downloaded", "skipped", "failed"):
                continue
            self._download_item(item, row, state, cfg)

    def _download_item(self, item: MediaItem, row, state: State, cfg: dict) -> None:
        local, live_local = plan_local_paths(state, row, cfg["download_dir"], cfg["by_month"])
        if local != row["local_path"] or live_local != row["live_local_path"]:
            state.set_local_paths(item.id, local, live_local)
        if not item.url:
            state.mark_failed(item.id, "no_url | Apple не дал ссылку на файл")
            return
        ok, md5 = self._fetch(item, item.url, local, item.size, main=True)
        if not ok:
            return
        if item.live_url and live_local:
            ok2, _ = self._fetch(item, item.live_url, live_local, item.live_size, main=False)
            if not ok2:
                return
        self._set_mtime(local, item.created_ts)
        if live_local:
            self._set_mtime(live_local, item.created_ts)
        state.mark_downloaded(item.id, local, live_local, md5)
        self.session_files += 1
        self.current = None
        if self.session_files in (1, 10, 100) or self.session_files % 500 == 0:
            self._event("ok", f"Скачано за этот запуск: {files_text(self.session_files)}")

    def _fetch(self, item: MediaItem, url: str, dest: str, size: int | None, main: bool) -> tuple[bool, str | None]:
        """Скачивает один файл в dest с докачкой через .part. (True, md5) или (False, None)."""
        engine = self.deps["engine"]
        state: State = self.deps["state"]
        name = os.path.basename(dest)
        if local_copy_matches(dest, size):
            md5 = compute_md5(dest) if main else None
            return True, md5
        part = dest + ".part"
        ensure_parent(dest)
        attempt = 0
        while True:
            self._check_stop()
            self._wait_if_paused()
            start = file_size_safe(part) or 0
            if size and start >= size:
                remove_quiet(part)
                start = 0
            self.current = {"name": name, "size": size or 0, "got": start, "kind": item.kind}
            try:
                hasher = self._stream_to_part(engine, url, part, start)
            except _PauseRequested:
                self._wait_if_paused()
                continue
            except _Stopped:
                raise
            except EngineError as e:
                if e.kind == "expired":
                    self._reauth_or_raise(e)
                    continue
                if e.kind in ("access_denied", "auth"):
                    raise
                attempt += 1
                if attempt >= MAX_ITEM_ATTEMPTS:
                    state.mark_failed(item.id, f"{e.kind} | {e.text}")
                    self._event("err", f"Не скачался {name}: {e.text}")
                    return False, None
                self._sleep_backoff(attempt - 1, e.text)
                continue
            except OSError as e:
                if is_local_disk_error(e):
                    raise EngineError("disk", "Не удалось записать файл на диск: нет места, нет прав или "
                                              "папка недоступна (отключён диск?). Освободите место или "
                                              "выберите другую папку.", str(e)) from e
                attempt += 1
                if attempt >= MAX_ITEM_ATTEMPTS:
                    state.mark_failed(item.id, f"network | {type(e).__name__}: {e}")
                    self._event("err", f"Не скачался {name}: обрыв связи")
                    return False, None
                self._sleep_backoff(attempt - 1, "Связь оборвалась")
                continue
            except Exception as e:  # noqa: BLE001 — requests и прочие обрывы потока
                log.warning("скачивание %s: %s: %s", name, type(e).__name__, str(e)[:200])
                attempt += 1
                if attempt >= MAX_ITEM_ATTEMPTS:
                    state.mark_failed(item.id, f"network | {type(e).__name__}")
                    self._event("err", f"Не скачался {name}: {type(e).__name__}")
                    return False, None
                self._sleep_backoff(attempt - 1, "Связь с Apple оборвалась")
                continue
            actual = file_size_safe(part)
            if size and actual != size:
                remove_quiet(part)
                attempt += 1
                if attempt >= MAX_ITEM_ATTEMPTS:
                    state.mark_failed(item.id, f"bad_file | размер не совпал: {actual} вместо {size}")
                    self._event("err", f"Не скачался {name}: файл пришёл неполным")
                    return False, None
                continue
            md5 = None
            if main:
                md5 = hasher.hexdigest() if hasher is not None else compute_md5(part)
            os.replace(to_long_path(part), to_long_path(dest))
            return True, md5

    def _stream_to_part(self, engine, url: str, part: str, start: int):
        """Льёт поток в .part. Возвращает md5-хешер, если качали с нуля, иначе None."""
        resp = engine.open_stream(url, start)
        try:
            status = getattr(resp, "status_code", 200)
            if not getattr(resp, "ok", status < 400):
                raise EngineError("apple", f"Apple не отдал файл (ответ {status})")
            if start and status == 200:
                start = 0  # сервер не умеет докачку — качаем заново
            hasher = hashlib.md5() if start == 0 else None
            got = start
            with open(to_long_path(part), "ab" if start else "wb") as f:
                for chunk in resp.iter_content(CHUNK):
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
                    self.speed.add(len(chunk))
                    self.session_bytes += len(chunk)
                    if self.current is not None:
                        self.current["got"] = got
                    self._touch()
                    if self._stop.is_set():
                        raise _Stopped()
                    if self._pause.is_set():
                        raise _PauseRequested()
            return hasher
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _set_mtime(path: str, created_ts: float | None) -> None:
        """Дата съёмки — как дата изменения файла: так её видят проводник и просмотрщики."""
        if not created_ts:
            return
        try:
            os.utime(to_long_path(path), (created_ts, created_ts))
        except OSError:
            pass
