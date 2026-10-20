"""Загрузка скачанной медиатеки на Яндекс Диск.

Папка скачивания целиком уезжает в папку «/iCloud БК» на Диске с той же структурой
подпапок. Перед загрузкой каждый файл сверяется с Диском по MD5: такой же файл уже
там — пропускаем; файл с тем же именем, но другим содержимым — кладём рядом под
именем с хвостом «_abc123» (первые шесть знаков MD5). На Диске ничего не
перезаписывается и не удаляется.

Служебные папки «_duplicates» и «_heic_originals», скрытые файлы, недокачанные
«.part» и базы программы на Диск не попадают. Ключ доступа (токен) в журналы и
ответы функций не пишется.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

import yadisk

from utils import (
    DIR_DUP,
    DIR_HEIC_ORIGINALS,
    YANDEX_ROOT,
    compute_md5,
    file_size_safe,
    is_media,
    ru_bytes,
    to_long_path,
    with_suffix_before_ext,
)

log = logging.getLogger("webui.yandex")

# (connect 30 с, read 300 с): большие видео на медленной сети не должны рваться по таймауту
DEFAULT_TIMEOUT = (30.0, 300.0)
# Паузы перед повтором при обрыве связи или сбое серверов Яндекса (50x): три повтора
RETRY_PAUSES = (5, 15, 45)
# Пауза при 429: Яндекс просит подождать, ждём один раз и пробуем снова
RATE_LIMIT_WAIT_SEC = 60
# Столько файлов подряд не ушли из-за сети — дальше ждать бессмысленно, останавливаемся
NETWORK_GIVE_UP = 5
# Сколько путей неудачных файлов возвращаем в итоге
FAILED_FILES_LIMIT = 20
# Пауза между повторами; тесты подменяют на пустышку
SLEEP = time.sleep

SKIP_DIRS = frozenset({DIR_DUP, DIR_HEIC_ORIGINALS})
SKIP_SUFFIXES = (".part", ".tmp", ".db", ".db-wal", ".db-shm", ".log")

MSG_AUTH = "Яндекс не принял ключ доступа — получите новый"
MSG_RATE_LIMIT = ("Яндекс просит обращаться реже (лимит запросов) — "
                  "программа подождёт минуту и продолжит")
MSG_RATE_LIMIT_GAVE_UP = ("Яндекс просит обращаться реже (лимит запросов) — "
                          "повторите загрузку через несколько минут")
MSG_UNREACHABLE = "Яндекс Диск не отвечает — проверьте интернет и повторите позже"
MSG_NO_TOKEN = "Ключ доступа к Яндекс Диску не задан"
SPACE_HINT = ("Файлы, которые загружает программа, занимают обычное место на Диске: "
              "безлимит действует только для автозагрузки из мобильного приложения "
              "Яндекс Диска.")
MSG_DISK_FULL = "На Яндекс Диске кончилось место. " + SPACE_HINT

AUTH_ERRORS = (yadisk.exceptions.UnauthorizedError, yadisk.exceptions.ForbiddenError)
# Обрыв связи, таймаут и сбои серверов Яндекса (500, 502, 503, 504): повторяем с паузой
RETRY_ERRORS = (
    yadisk.exceptions.YaDiskConnectionError,
    yadisk.exceptions.RequestTimeoutError,
    yadisk.exceptions.InternalServerError,
    yadisk.exceptions.BadGatewayError,
    yadisk.exceptions.UnavailableError,
    yadisk.exceptions.GatewayTimeoutError,
)
# «Папка уже есть» при mkdir — не ошибка (DirectoryExistsError — подкласс PathExistsError)
EXISTS_ERRORS = (yadisk.exceptions.PathExistsError,)

ProgressCb = Callable[[dict], None]


# ── клиент и аккаунт ─────────────────────────────────────────────────────────


def make_client(token: str) -> yadisk.Client:
    """Клиент REST API с единым таймаутом. Закрывать через .close()."""
    return yadisk.Client(token=token, default_args={"timeout": DEFAULT_TIMEOUT})


def account_info(client: yadisk.Client) -> dict:
    """Логин и место на Диске: {login, used, total, free} в байтах."""
    info = client.get_disk_info()
    user = getattr(info, "user", None)
    used = int(getattr(info, "used_space", None) or 0)
    total = int(getattr(info, "total_space", None) or 0)
    return {
        "login": getattr(user, "login", None) or "",
        "used": used,
        "total": total,
        "free": max(0, total - used),
    }


def check_token(token: str) -> dict:
    """Проверяет ключ доступа: {ok, msg, login, used, total, free, unreachable}.

    unreachable — Яндекс не ответил (сеть, сбой серверов). Это не повод просить
    новый ключ: во время сбоев старый ключ жив.
    """
    out = {"ok": False, "msg": "", "login": "", "used": 0, "total": 0, "free": 0,
           "unreachable": False}
    if not token or not token.strip():
        out["msg"] = MSG_NO_TOKEN
        return out
    cli = make_client(token)
    try:
        acc = account_info(cli)
    except AUTH_ERRORS as e:
        log.warning("Яндекс не принял ключ доступа: %s", type(e).__name__)
        out["msg"] = MSG_AUTH
        return out
    except Exception as e:  # noqa: BLE001 — любой сбой здесь значит «не дозвонились»
        log.warning("Яндекс не ответил на проверку ключа: %s: %s", type(e).__name__, e)
        out["msg"] = MSG_UNREACHABLE
        out["unreachable"] = True
        return out
    finally:
        _close_quiet(cli)
    out.update(acc)
    out["ok"] = True
    out["msg"] = (f"Аккаунт {acc['login']}: свободно {ru_bytes(acc['free'])} "
                  f"из {ru_bytes(acc['total'])}")
    return out


def _close_quiet(client: yadisk.Client) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001 — закрытие не должно ронять выход
        pass


# ── что загружать ───────────────────────────────────────────────────────────


def plan_upload(download_dir: str) -> list[dict]:
    """Список файлов на загрузку: [{local, rel, size}], rel — путь через «/».

    Служебные папки, скрытые имена, недокачанные и служебные файлы не берём;
    только фото и видео. Порядок — по rel, чтобы загрузка шла предсказуемо.
    """
    root = os.path.abspath(download_dir)
    out: list[dict] = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in files:
            if name.startswith(".") or name.lower().endswith(SKIP_SUFFIXES) or not is_media(name):
                continue
            local = os.path.join(cur, name)
            size = file_size_safe(local)
            if size is None:
                continue  # файл исчез, пока шли по папке
            rel = os.path.relpath(local, root).replace("\\", "/")
            out.append({"local": local, "rel": rel, "size": size})
    out.sort(key=lambda f: f["rel"])
    return out


# ── повторы ──────────────────────────────────────────────────────────────────


def _with_retry(fn: Callable[[], object], rel: str, notify: Callable[[str, str], None]):
    """Вызывает fn с повторами: сеть и 50x — паузы 5, 15, 45 с; 429 — минута, один раз."""
    waited_429 = False
    attempt = 0
    while True:
        try:
            return fn()
        except yadisk.exceptions.TooManyRequestsError:
            if waited_429:
                raise
            waited_429 = True
            log.warning("Яндекс вернул 429 (лимит запросов), жду %d с: %s", RATE_LIMIT_WAIT_SEC, rel)
            notify(rel, MSG_RATE_LIMIT)
            SLEEP(RATE_LIMIT_WAIT_SEC)
        except RETRY_ERRORS as e:
            if attempt >= len(RETRY_PAUSES):
                raise
            pause = RETRY_PAUSES[attempt]
            attempt += 1
            log.warning("%s: %s — повтор %d из %d через %d с: %s",
                        type(e).__name__, e, attempt, len(RETRY_PAUSES), pause, rel)
            notify(rel, f"Яндекс не ответил — повтор {attempt} из {len(RETRY_PAUSES)} "
                        f"через {pause} с: {rel}")
            SLEEP(pause)


def _classify(e: BaseException) -> tuple[str, str]:
    """(вид, текст) ошибки одного файла.

    auth и space — загружать дальше бессмысленно; network — сеть, после нескольких
    подряд останавливаемся; file — беда только с этим файлом.
    """
    if isinstance(e, AUTH_ERRORS):
        return "auth", MSG_AUTH
    if isinstance(e, yadisk.exceptions.InsufficientStorageError):
        return "space", MSG_DISK_FULL
    if isinstance(e, yadisk.exceptions.TooManyRequestsError):
        return "network", MSG_RATE_LIMIT_GAVE_UP
    if isinstance(e, RETRY_ERRORS):
        return "network", MSG_UNREACHABLE
    return "file", f"{type(e).__name__}: {e}"


# ── Диск: сверка и папки ─────────────────────────────────────────────────────


def _meta_or_none(client: yadisk.Client, path: str):
    try:
        return client.get_meta(path)
    except yadisk.exceptions.PathNotFoundError:
        return None


def _same_file(meta, md5: str) -> bool:
    remote_md5 = getattr(meta, "md5", None) or ""
    return getattr(meta, "type", None) == "file" and remote_md5.lower() == md5.lower()


def _remote_target(client: yadisk.Client, remote: str, md5: str) -> tuple[str, bool]:
    """Куда класть файл на Диске и лежит ли он там уже: (путь, уже есть).

    Путь свободен — туда. Там такой же файл (по MD5) — уже есть. Там другой файл —
    кладём рядом под именем с хвостом «_abc123», если и он не оказался тем же файлом.
    """
    meta = _meta_or_none(client, remote)
    if meta is None:
        return remote, False
    if _same_file(meta, md5):
        return remote, True
    alt = with_suffix_before_ext(remote, "_" + md5[:6])
    meta = _meta_or_none(client, alt)
    if meta is None:
        return alt, False
    return alt, _same_file(meta, md5)


def _ensure_remote_dirs(client: yadisk.Client, remote: str, made: set[str],
                        notify: Callable[[str, str], None]) -> None:
    """Создаёт на Диске все папки пути к файлу (от корня вниз); уже созданные помнит."""
    parent = remote.rsplit("/", 1)[0]
    chain: list[str] = []
    while parent and parent not in made:
        chain.append(parent)
        parent = parent.rsplit("/", 1)[0]
    for path in reversed(chain):
        try:
            _with_retry(lambda p=path: client.mkdir(p), path, notify)
        except EXISTS_ERRORS:
            pass
        made.add(path)


# ── загрузка ─────────────────────────────────────────────────────────────────


def upload_all(
    token: str,
    download_dir: str,
    progress_cb: ProgressCb | None = None,
    stop_event: threading.Event | None = None,
    client: yadisk.Client | None = None,
) -> dict:
    """Загружает папку скачивания в «/iCloud БК» на Диске. Один проход, один поток.

    Сначала сверяет каждый файл с Диском по MD5 и считает, сколько байт ещё нужно
    загрузить; места не хватает — останавливается до загрузки. Потом загружает файл
    за файлом, повторяя при сбоях сети. progress_cb(info) зовётся после каждого файла:
    total, done, skipped, failed, bytes_total, bytes_done, current, msg, stage.
    stop_event останавливает между файлами.

    Возвращает {ok: True, total, done, skipped, failed, bytes_done, bytes_total,
    remote_root, failed_files} (ok True и при ошибках отдельных файлов) или
    {ok: False, kind: auth | space | network, msg} вместе с теми же счётчиками.
    client — для тестов; без него создаётся свой и закрывается в конце.
    """
    cli = client if client is not None else make_client(token)
    try:
        return _upload_with(cli, download_dir, progress_cb, stop_event)
    finally:
        if client is None:
            _close_quiet(cli)


def _upload_with(client: yadisk.Client, download_dir: str, progress_cb: ProgressCb | None,
                 stop_event: threading.Event | None) -> dict:
    counters = {"total": 0, "done": 0, "skipped": 0, "failed": 0,
                "bytes_total": 0, "bytes_done": 0, "stage": "check"}
    failed_files: list[str] = []

    def notify(current: str, msg: str | None = None) -> None:
        if progress_cb is not None:
            progress_cb(dict(counters, current=current, msg=msg))

    def result(ok: bool = True, **extra) -> dict:
        out = {"ok": ok, **counters, "remote_root": YANDEX_ROOT,
               "failed_files": failed_files[:FAILED_FILES_LIMIT]}
        out.pop("stage")
        out.update(extra)
        return out

    def mark_failed(rel: str, text: str) -> None:
        counters["failed"] += 1
        failed_files.append(rel)
        log.error("FAIL | %s | %s", rel, text)

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    try:
        acc = account_info(client)
    except AUTH_ERRORS as e:
        log.warning("Яндекс не принял ключ доступа: %s", type(e).__name__)
        return result(ok=False, kind="auth", msg=MSG_AUTH)
    except Exception as e:  # noqa: BLE001 — любой сбой здесь значит «не дозвонились»
        log.warning("Яндекс не ответил: %s: %s", type(e).__name__, e)
        return result(ok=False, kind="network", msg=MSG_UNREACHABLE)

    plan = plan_upload(download_dir)
    counters["total"] = len(plan)
    log.info("Загрузка на Диск: аккаунт %s, свободно %s, файлов в папке %d",
             acc["login"], ru_bytes(acc["free"]), len(plan))

    # Этап 1: сверка с Диском. Считаем, что и куда класть и сколько байт нужно.
    jobs: list[tuple[dict, str]] = []
    net_streak = 0
    for item in plan:
        if stopped():
            return result(stopped=True)
        rel = item["rel"]
        try:
            md5 = compute_md5(item["local"])
            remote, already = _with_retry(
                lambda: _remote_target(client, f"{YANDEX_ROOT}/{rel}", md5), rel, notify)
        except Exception as e:  # noqa: BLE001 — один битый файл не должен ронять всё
            kind, text = _classify(e)
            if kind in ("auth", "space"):
                return result(ok=False, kind=kind, msg=text)
            mark_failed(rel, text)
            net_streak = net_streak + 1 if kind == "network" else 0
            if net_streak >= NETWORK_GIVE_UP:
                return result(ok=False, kind="network", msg=text)
            continue
        net_streak = 0
        if already:
            counters["skipped"] += 1
            log.info("SKIP | уже на Диске | %s", rel)
        else:
            jobs.append((item, remote))
            counters["bytes_total"] += item["size"]
        notify(rel)

    need, free = counters["bytes_total"], acc["free"]
    if need > free:
        msg = (f"На Яндекс Диске не хватает места: нужно {ru_bytes(need)}, "
               f"свободно {ru_bytes(free)}. {SPACE_HINT}")
        log.warning("Не хватает места на Диске: нужно %s, свободно %s", ru_bytes(need), ru_bytes(free))
        return result(ok=False, kind="space", msg=msg)

    # Этап 2: сама загрузка, файл за файлом.
    counters["stage"] = "upload"
    made: set[str] = set()
    net_streak = 0
    for item, remote in jobs:
        if stopped():
            return result(stopped=True)
        rel = item["rel"]
        try:
            _ensure_remote_dirs(client, remote, made, notify)
            _with_retry(lambda: client.upload(to_long_path(item["local"]), remote, overwrite=False),
                        rel, notify)
        except Exception as e:  # noqa: BLE001 — один битый файл не должен ронять всё
            kind, text = _classify(e)
            if kind in ("auth", "space"):
                return result(ok=False, kind=kind, msg=text)
            mark_failed(rel, text)
            net_streak = net_streak + 1 if kind == "network" else 0
            if net_streak >= NETWORK_GIVE_UP:
                return result(ok=False, kind="network", msg=text)
        else:
            net_streak = 0
            counters["done"] += 1
            counters["bytes_done"] += item["size"]
            log.info("OK | %s | %s", rel, remote)
        notify(rel)

    log.info("Итог загрузки: загружено %d, пропущено %d, ошибок %d, %s",
             counters["done"], counters["skipped"], counters["failed"], ru_bytes(counters["bytes_done"]))
    return result()
