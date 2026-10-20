"""Адаптер к icloudpd: единственное место, где программа говорит с iCloud.

Узкий интерфейс (см. класс Engine): войти, подтвердить код 2FA, восстановить сессию,
выйти, посчитать и перечислить файлы, открыть поток скачивания. Если Apple или icloudpd
что-то поменяют, править нужно только этот модуль. Демо-движок с тем же интерфейсом —
в demo_engine.py.

icloudpd подключён как библиотека (чистый wheel py3-none-any), а не через командную
строку: так код 2FA из нашего интерфейса передаётся прямым вызовом
`validate_2fa_code`, а список файлов и ссылки на скачивание берутся из объектов
`PhotoAsset` без разбора чужого вывода. Подробности — docs/DECISIONS.md.

Из iCloud только читаем: ни один метод этого модуля ничего не удаляет и не меняет.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from utils import atomic_write_text

log = logging.getLogger("webui.engine")

# icloudpd и pyicloud_ipd на уровне DEBUG печатают заголовки и тела ответов Apple:
# держим их на WARNING, чтобы в журналы не попали токены сессии
for _name in ("pyicloud_ipd", "icloudpd", "urllib3"):
    logging.getLogger(_name).setLevel(logging.WARNING)

ACCOUNT_FILE = "account.json"
DOMAIN = "com"


class EngineError(Exception):
    """Ошибка движка с понятным человеку текстом.

    kind: auth (неверный логин или пароль), code (неверный код 2FA),
    access_denied (Apple не даёт доступ к фото через веб), expired (сессия истекла),
    network (нет связи), apple (сбой или отказ Apple — повторить позже), other.
    """

    def __init__(self, kind: str, text: str, detail: str = ""):
        super().__init__(text)
        self.kind = kind
        self.text = text
        self.detail = detail


@dataclass
class MediaItem:
    """Один файл медиатеки. Ссылки на скачивание живут недолго — качать сразу."""

    id: str
    filename: str          # IMG_0001.HEIC — уже с расширением под тип файла
    kind: str              # photo | video | live
    size: int
    checksum: str | None
    created_ts: float | None
    url: str | None
    live_size: int | None = None
    live_checksum: str | None = None
    live_filename: str | None = None   # IMG_0001.MOV — видеочасть Live Photo
    live_url: str | None = None

    def as_row(self) -> dict:
        return {"id": self.id, "filename": self.filename, "kind": self.kind, "size": self.size,
                "checksum": self.checksum, "created_ts": self.created_ts,
                "live_size": self.live_size, "live_checksum": self.live_checksum,
                "live_filename": self.live_filename}


class Stream(Protocol):
    """Поток байтов файла: то, что отдаёт open_stream."""

    status_code: int

    @property
    def ok(self) -> bool: ...
    def iter_content(self, chunk_size: int) -> Iterator[bytes]: ...
    def close(self) -> None: ...


class Engine(Protocol):
    """Узкий интерфейс движка. ICloudPdEngine и DemoEngine реализуют его одинаково."""

    def login(self, apple_id: str, password: str) -> str: ...
    """Возвращает 'ok' (сессия готова) или 'needs_2fa' (ждём код). Ошибки — EngineError."""

    def submit_code(self, code: str) -> bool: ...
    """True — код принят, сессия доверенная. False — неверный код, можно ввести снова."""

    def restore(self) -> bool: ...
    """Пробует продолжить сохранённую сессию без пароля. True — вошли."""

    def logout(self) -> None: ...
    """Забывает сессию и удаляет её файлы."""

    def account(self) -> dict: ...
    """{'apple_id': ..., 'state': 'none' | 'needs_2fa' | 'ok'}"""

    def count(self) -> int: ...
    def iter_items(self) -> Iterator[MediaItem]: ...
    def open_stream(self, url: str, start: int = 0) -> Stream: ...
    def reauthenticate(self) -> None: ...


# ── Настоящий движок ────────────────────────────────────────────────────────


class ICloudPdEngine:
    """Вход, список и скачивание через библиотеку icloudpd (pyicloud_ipd)."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        self._icloud: Any = None
        self._apple_id: str | None = None
        self._state = "none"
        self._two_step_device: dict | None = None  # устаревшая 2SA: выбранное устройство
        self._lock = threading.RLock()

    # ── вход ──

    def login(self, apple_id: str, password: str) -> str:
        from pyicloud_ipd.base import PyiCloudService

        apple_id = apple_id.strip()
        with self._lock:
            self._state = "none"
            self._icloud = None
            self._two_step_device = None
            self.session_dir.mkdir(parents=True, exist_ok=True)
            # Пароль живёт только внутри этого вызова: icloudpd получает его через
            # функцию и не сохраняет (keyring не используем)
            holder = {"password": password}

            def provider() -> str | None:
                return holder.pop("password", None)

            try:
                icloud = PyiCloudService(DOMAIN, apple_id, provider,
                                         cookie_directory=str(self.session_dir))
            except Exception as e:  # noqa: BLE001
                raise self._translate(e, during_login=True) from None
            finally:
                holder.clear()
            if not icloud.data:
                raise EngineError("auth", "Apple не принял пароль. Проверьте Apple ID и пароль.")
            self._icloud = icloud
            self._apple_id = apple_id
            self._write_account(apple_id)
            return self._after_auth()

    def _after_auth(self) -> str:
        icloud = self._icloud
        try:
            if icloud.requires_2fa:
                if not icloud.trigger_push_notification():
                    log.info("Apple не подтвердил отправку кода на устройства — ждём код")
                self._state = "needs_2fa"
                return "needs_2fa"
            if icloud.requires_2sa:
                devices = icloud.trusted_devices
                if not devices:
                    raise EngineError("apple", "Apple требует двухэтапную проверку, но у аккаунта нет "
                                               "доверенных устройств. Войдите на icloud.com и проверьте настройки.")
                self._two_step_device = dict(devices[0])
                if not icloud.send_verification_code(self._two_step_device):
                    raise EngineError("apple", "Apple не смог отправить код на доверенное устройство. "
                                               "Попробуйте ещё раз через минуту.")
                self._state = "needs_2fa"
                return "needs_2fa"
        except EngineError:
            raise
        except Exception as e:  # noqa: BLE001
            raise self._translate(e, during_login=True) from None
        self._state = "ok"
        return "ok"

    def submit_code(self, code: str) -> bool:
        code = "".join(ch for ch in code if ch.isdigit())
        with self._lock:
            if self._icloud is None or self._state != "needs_2fa":
                raise EngineError("other", "Сначала введите Apple ID и пароль.")
            if len(code) != 6:
                return False
            try:
                if self._two_step_device is not None:
                    ok = self._icloud.validate_verification_code(self._two_step_device, code)
                else:
                    ok = self._icloud.validate_2fa_code(code)
            except Exception as e:  # noqa: BLE001
                raise self._translate(e, during_login=True) from None
            if not ok:
                return False
            self._two_step_device = None
            self._state = "ok"
            return True

    def restore(self) -> bool:
        from pyicloud_ipd.base import PyiCloudService

        with self._lock:
            apple_id = self._read_account()
            if not apple_id:
                return False
            try:
                icloud = PyiCloudService(DOMAIN, apple_id, lambda: None,
                                         cookie_directory=str(self.session_dir))
            except Exception as e:  # noqa: BLE001
                log.info("сессию восстановить не удалось: %s", type(e).__name__)
                return False
            # Без пароля icloudpd только проверяет сохранённый токен; пустой data —
            # токен истёк, нужно войти заново
            if not icloud.data or icloud.requires_2fa or icloud.requires_2sa:
                return False
            self._icloud = icloud
            self._apple_id = apple_id
            self._state = "ok"
            return True

    def logout(self) -> None:
        with self._lock:
            self._icloud = None
            self._apple_id = None
            self._state = "none"
            self._two_step_device = None
            if self.session_dir.exists():
                for child in self.session_dir.iterdir():
                    try:
                        if child.is_dir():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                    except OSError as e:
                        log.warning("не удалось удалить файл сессии %s: %s", child.name, e)

    def account(self) -> dict:
        return {"apple_id": self._apple_id or self._read_account(), "state": self._state}

    def reauthenticate(self) -> None:
        """Сессия Apple протухла посреди скачивания: продлить по сохранённому токену."""
        with self._lock:
            if self._icloud is None:
                raise EngineError("expired", "Сессия истекла. Войдите заново.")
            try:
                self._icloud.authenticate(force_refresh=True)
            except Exception as e:  # noqa: BLE001
                raise self._translate(e) from None
            if not self._icloud.data or self._icloud.requires_2fa:
                self._state = "none"
                raise EngineError("expired", "Сессия Apple истекла. Войдите заново — скачивание "
                                             "продолжится с того же места.")
            self._icloud._photos = None  # сервис фото держит старые параметры сессии

    # ── медиатека ──

    def _photos(self):
        if self._icloud is None or self._state != "ok":
            raise EngineError("expired", "Нужно войти в Apple ID.")
        try:
            return self._icloud.photos
        except Exception as e:  # noqa: BLE001
            raise self._translate(e) from None

    def count(self) -> int:
        try:
            return int(len(self._photos().all))
        except EngineError:
            raise
        except Exception as e:  # noqa: BLE001
            raise self._translate(e) from None

    def iter_items(self) -> Iterator[MediaItem]:
        """Вся медиатека по дате съёмки, старые первыми. Ссылки в элементах свежие."""
        album = self._photos().all
        it = iter(album)
        while True:
            try:
                asset = next(it)
            except StopIteration:
                return
            except EngineError:
                raise
            except Exception as e:  # noqa: BLE001
                raise self._translate(e) from None
            item = self._to_item(asset)
            if item is not None:
                yield item

    def _to_item(self, asset) -> MediaItem | None:
        from icloudpd.base import lp_filename_original
        from pyicloud_ipd.asset_version import calculate_version_filename
        from pyicloud_ipd.item_type import AssetItemType
        from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize

        try:
            versions = asset.versions
        except Exception as e:  # noqa: BLE001 — у редких записей нет полей версий
            log.warning("у файла %s нет данных о версиях: %s", getattr(asset, "id", "?"), e)
            return None
        original = versions.get(AssetVersionSize.ORIGINAL)
        if original is None:
            log.warning("у файла %s нет оригинала — пропускаю", asset.id)
            return None
        base = asset.filename
        name = calculate_version_filename(base, original, AssetVersionSize.ORIGINAL,
                                          lp_filename_original, asset.item_type)
        is_video = asset.item_type == AssetItemType.MOVIE
        live = None if is_video else versions.get(LivePhotoVersionSize.ORIGINAL)
        kind = "video" if is_video else ("live" if live is not None else "photo")
        try:
            created_ts = asset.asset_date.timestamp()
        except Exception:  # noqa: BLE001
            created_ts = None
        item = MediaItem(id=asset.id, filename=name, kind=kind, size=int(original.size),
                         checksum=original.checksum, created_ts=created_ts, url=original.url)
        if live is not None:
            item.live_size = int(live.size)
            item.live_checksum = live.checksum
            item.live_filename = lp_filename_original(name)
            item.live_url = live.url
        return item

    def open_stream(self, url: str, start: int = 0):
        from pyicloud_ipd.services.photos import download_asset

        session = self._photos().session
        try:
            return download_asset(session, url, start)
        except Exception as e:  # noqa: BLE001
            raise self._translate(e) from None

    # ── служебное ──

    def _account_path(self) -> Path:
        return self.session_dir / ACCOUNT_FILE

    def _write_account(self, apple_id: str) -> None:
        atomic_write_text(self._account_path(), json.dumps({"apple_id": apple_id}, ensure_ascii=False))

    def _read_account(self) -> str | None:
        try:
            data = json.loads(self._account_path().read_text(encoding="utf-8"))
            apple_id = str(data.get("apple_id") or "").strip()
            return apple_id or None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _translate(exc: BaseException, during_login: bool = False) -> EngineError:
        """Исключение icloudpd → понятный текст по-русски (детали — только в журнал)."""
        from pyicloud_ipd.exceptions import (
            PyiCloudAPIResponseException,
            PyiCloudConnectionErrorException,
            PyiCloudFailedLoginException,
            PyiCloudFailedMFAException,
            PyiCloudServiceNotActivatedException,
            PyiCloudServiceUnavailableException,
        )

        name = type(exc).__name__
        text = str(exc)
        code = str(getattr(exc, "code", "") or "")
        log.warning("icloudpd: %s: %s", name, text[:300])
        if isinstance(exc, EngineError):
            return exc
        if isinstance(exc, PyiCloudConnectionErrorException):
            return EngineError("network", "Нет связи с серверами Apple. Проверьте интернет; программа "
                                          "повторит попытку сама.", text)
        if isinstance(exc, PyiCloudServiceUnavailableException):
            return EngineError("apple", "Apple временно отказывает в обслуживании (ответ 503). Обычно это "
                                        "проходит за несколько минут — программа подождёт и повторит.", text)
        if isinstance(exc, PyiCloudFailedLoginException):
            return EngineError("auth", "Apple не принял Apple ID или пароль. Проверьте их и попробуйте снова. "
                                       "Если включён вход по ключу безопасности (FIDO), программа работать не сможет.",
                               text)
        if isinstance(exc, PyiCloudFailedMFAException):
            return EngineError("code", "Код не подошёл. Попросите новый код на айфоне и введите его снова.", text)
        if isinstance(exc, PyiCloudServiceNotActivatedException):
            if "indexing" in text.lower():
                return EngineError("apple", "Apple ещё индексирует медиатеку iCloud Фото. Подождите несколько "
                                            "минут и попробуйте снова.", text)
            return EngineError("apple", "iCloud у этого Apple ID не настроен до конца. Зайдите на icloud.com, "
                                        "завершите настройку и попробуйте снова.", text)
        if isinstance(exc, PyiCloudAPIResponseException):
            if code == "ACCESS_DENIED" or "ACCESS_DENIED" in text:
                return EngineError("access_denied",
                                   "Apple не даёт доступ к фото через веб (ACCESS_DENIED). На айфоне: "
                                   "Настройки → ваше имя → iCloud → включите «Доступ к данным iCloud в вебе» "
                                   "и выключите «Расширенную защиту данных». Подождите пару минут и попробуйте снова.",
                                   text)
            if "Invalid global session" in text or code in ("421", "450", "500"):
                return EngineError("expired", "Сессия Apple истекла. Войдите заново — скачивание продолжится "
                                              "с того же места.", text)
            if code in ("401", "403") or "Invalid username/password" in text:
                return EngineError("auth", "Apple не принял Apple ID или пароль. Проверьте их и попробуйте снова.", text)
            if code == "429" or "throttl" in text.lower():
                return EngineError("apple", "Apple просит обращаться реже (слишком много запросов). Программа "
                                            "сделает паузу и продолжит сама.", text)
            return EngineError("apple", f"Apple ответил ошибкой ({code or name}). Программа повторит попытку позже.",
                               text)
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)) and not isinstance(exc, FileNotFoundError):
            return EngineError("network", "Нет связи с серверами Apple. Проверьте интернет; программа "
                                          "повторит попытку сама.", text)
        if during_login:
            return EngineError("other", f"Не получилось войти: {name}. Попробуйте ещё раз; если повторится — "
                                        "скачайте отчёт для поддержки во вкладке «Помощь».", text)
        return EngineError("other", f"Неожиданная ошибка: {name}. Программа повторит попытку позже.", text)


def make_engine(session_dir: Path, demo: bool) -> Engine:
    """Настоящий движок или демо — одна точка выбора для всего приложения."""
    if demo:
        from demo_engine import DemoEngine
        return DemoEngine(session_dir)
    return ICloudPdEngine(session_dir)


def session_files_present(session_dir: Path) -> bool:
    try:
        return (Path(session_dir) / ACCOUNT_FILE).is_file()
    except OSError:
        return False


__all__ = ["Engine", "EngineError", "ICloudPdEngine", "MediaItem", "Stream", "make_engine",
           "session_files_present", "os"]
