"""Демо-движок: тот же интерфейс, что у icloud_engine.ICloudPdEngine, но без Apple.

Запуск: `uv run webui.py --demo`. Вход принимает любой Apple ID и пароль, кроме пароля
«wrong» (показывает ошибку входа); код 2FA — 123456 (любой другой — «неверный код»).
Медиатека генерируется один раз в папку _demo_cloud/: JPG и HEIC с EXIF, Live Photo
(HEIC и MOV рядом), видео, точные дубли и снимки разных лет. Скачивание идёт с
ограниченной скоростью, чтобы было видно прогресс, паузу и докачку; один файл
«обрывается» при первой попытке — так виден повтор.
"""

from __future__ import annotations

import io
import json
import os
import random
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from icloud_engine import ACCOUNT_FILE, EngineError, MediaItem
from utils import atomic_write_text

DEMO_CODE = "123456"
MANIFEST = "manifest.json"
# Байт в секунду при демо-скачивании (ICLOUD_BK_DEMO_SPEED переопределяет)
DEFAULT_SPEED = 6 * 1024 * 1024
# Индекс файла, у которого первая попытка обрывается — чтобы показать повтор
FLAKY_INDEX = 4


class DemoStream:
    """Поток байтов из локального файла с ограничением скорости и поддержкой Range."""

    def __init__(self, path: Path, start: int, speed: int, flaky: bool):
        self.path = path
        self.start = start
        self.speed = max(64 * 1024, speed)
        self.flaky = flaky
        self.status_code = 206 if start else 200
        self.ok = True
        self.headers = {}

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        sent = 0
        with open(self.path, "rb") as f:
            f.seek(self.start)
            t0 = time.monotonic()
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    return
                sent += len(chunk)
                if self.flaky and sent > 256 * 1024:
                    raise ConnectionResetError("demo: соединение оборвалось")
                yield chunk
                # Держим скорость: отдаём не быстрее speed байт в секунду
                expected = sent / self.speed
                elapsed = time.monotonic() - t0
                if expected > elapsed:
                    time.sleep(min(0.5, expected - elapsed))

    def close(self) -> None:
        pass


class DemoEngine:
    def __init__(self, session_dir: Path, cloud_dir: Path | None = None) -> None:
        self.session_dir = Path(session_dir)
        self.cloud_dir = Path(cloud_dir) if cloud_dir else Path(__file__).resolve().parent / "_demo_cloud"
        self._apple_id: str | None = None
        self._state = "none"
        self._lock = threading.Lock()
        self._flaky_done: set[str] = set()
        try:
            self.speed = int(os.environ.get("ICLOUD_BK_DEMO_SPEED") or DEFAULT_SPEED)
        except ValueError:
            self.speed = DEFAULT_SPEED

    # ── вход ──

    def login(self, apple_id: str, password: str) -> str:
        apple_id = apple_id.strip()
        if not apple_id or "@" not in apple_id:
            raise EngineError("auth", "Введите Apple ID — это адрес электронной почты.")
        if not password:
            raise EngineError("auth", "Введите пароль.")
        if password == "wrong":
            raise EngineError("auth", "Apple не принял Apple ID или пароль. Проверьте их и попробуйте снова.")
        if password == "denied":
            raise EngineError("access_denied",
                              "Apple не даёт доступ к фото через веб (ACCESS_DENIED). На айфоне: Настройки → "
                              "ваше имя → iCloud → включите «Доступ к данным iCloud в вебе» и выключите "
                              "«Расширенную защиту данных». Подождите пару минут и попробуйте снова.")
        time.sleep(0.8)
        with self._lock:
            self._apple_id = apple_id
            self._state = "needs_2fa"
            self.session_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.session_dir / ACCOUNT_FILE,
                              json.dumps({"apple_id": apple_id, "demo": True}, ensure_ascii=False))
        return "needs_2fa"

    def submit_code(self, code: str) -> bool:
        code = "".join(ch for ch in code if ch.isdigit())
        with self._lock:
            if self._state != "needs_2fa":
                raise EngineError("other", "Сначала введите Apple ID и пароль.")
            time.sleep(0.5)
            if code != DEMO_CODE:
                return False
            self._state = "ok"
            atomic_write_text(self.session_dir / "demo.session", "ok")
            return True

    def restore(self) -> bool:
        with self._lock:
            try:
                data = json.loads((self.session_dir / ACCOUNT_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if not (self.session_dir / "demo.session").is_file():
                return False
            self._apple_id = str(data.get("apple_id") or "demo@icloud.com")
            self._state = "ok"
            return True

    def logout(self) -> None:
        with self._lock:
            self._apple_id = None
            self._state = "none"
            if self.session_dir.exists():
                for child in self.session_dir.iterdir():
                    try:
                        child.unlink()
                    except OSError:
                        pass

    def account(self) -> dict:
        return {"apple_id": self._apple_id, "state": self._state}

    def reauthenticate(self) -> None:
        if self._state != "ok":
            raise EngineError("expired", "Сессия истекла. Войдите заново.")

    # ── медиатека ──

    def _require(self) -> None:
        if self._state != "ok":
            raise EngineError("expired", "Нужно войти в Apple ID.")

    def _manifest(self) -> list[dict]:
        self._require()
        path = self.cloud_dir / MANIFEST
        if not path.is_file():
            generate_library(self.cloud_dir)
        return json.loads(path.read_text(encoding="utf-8"))

    def count(self) -> int:
        time.sleep(0.5)
        return len(self._manifest())

    def iter_items(self) -> Iterator[MediaItem]:
        for raw in self._manifest():
            time.sleep(0.02)
            yield MediaItem(id=raw["id"], filename=raw["filename"], kind=raw["kind"], size=raw["size"],
                            checksum=raw["checksum"], created_ts=raw["created_ts"],
                            url="demo://" + raw["path"], live_size=raw.get("live_size"),
                            live_checksum=raw.get("live_checksum"), live_filename=raw.get("live_filename"),
                            live_url=("demo://" + raw["live_path"]) if raw.get("live_path") else None)

    def open_stream(self, url: str, start: int = 0) -> DemoStream:
        self._require()
        rel = url.removeprefix("demo://")
        path = self.cloud_dir / rel
        if not path.is_file():
            raise EngineError("apple", "Демо: файла нет в облаке")
        manifest = self._manifest()
        flaky = False
        if len(manifest) > FLAKY_INDEX and manifest[FLAKY_INDEX]["path"] == rel and rel not in self._flaky_done:
            self._flaky_done.add(rel)
            flaky = True
        return DemoStream(path, start, self.speed, flaky)


# ── Генерация демо-медиатеки ────────────────────────────────────────────────


def _jpeg_bytes(width: int, height: int, color: tuple, dt: datetime, exif: bool = True, seed: int = 0) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(40):
        x, y = rnd.randrange(width), rnd.randrange(height)
        r = rnd.randrange(20, 160)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    buf = io.BytesIO()
    if exif:
        img.save(buf, format="JPEG", quality=85, exif=_exif_bytes(dt))
    else:
        img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _exif_bytes(dt: datetime) -> bytes:
    """EXIF с датой съёмки и геометкой — чтобы проверить, что JPG-копии их сохраняют."""
    from PIL import Image

    exif = Image.Exif()
    stamp = dt.strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0132] = stamp          # DateTime
    exif[0x010F] = "Apple"        # Make
    exif[0x0110] = "iPhone 13"    # Model
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = stamp           # DateTimeOriginal
    ifd[0x9004] = stamp           # DateTimeDigitized
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (55.0, 45.0, 20.88)   # 55°45'20.88" — Москва
    gps[3] = "E"
    gps[4] = (37.0, 37.0, 16.44)
    return exif.tobytes()


def _heic_bytes(width: int, height: int, color: tuple, dt: datetime, seed: int) -> bytes | None:
    """HEIC через pillow-heif. None — кодировщик недоступен (тогда файл будет JPG)."""
    try:
        import pillow_heif
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    try:
        pillow_heif.register_heif_opener()
        img = Image.new("RGB", (width, height), color)
        draw = ImageDraw.Draw(img)
        rnd = random.Random(seed)
        for _ in range(30):
            x, y = rnd.randrange(width), rnd.randrange(height)
            r = rnd.randrange(20, 140)
            draw.rectangle((x - r, y - r, x + r, y + r), fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
        buf = io.BytesIO()
        img.save(buf, format="HEIF", quality=80, exif=_exif_bytes(dt))
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — нет кодировщика HEVC в сборке
        return None


def _video_bytes(size: int, seed: int) -> bytes:
    rnd = random.Random(seed)
    # Заголовок как у QuickTime, дальше шум — для скачивания и дублей содержимое не важно
    head = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  "
    return head + rnd.randbytes(max(0, size - len(head)))


def generate_library(cloud_dir: Path) -> list[dict]:
    """Создаёт демо-медиатеку и manifest.json. Повторный вызов перезаписывает её."""
    import base64
    import hashlib

    cloud_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    counter = 1000

    def add(filename: str, data: bytes, dt: datetime, kind: str, live: bytes | None = None) -> None:
        nonlocal counter
        counter += 1
        path = cloud_dir / filename
        path.write_bytes(data)
        checksum = base64.b64encode(hashlib.sha1(data).digest()).decode()
        row = {"id": f"demo-{counter}-{hashlib.md5(filename.encode()).hexdigest()[:6]}",
               "filename": filename, "kind": kind, "size": len(data), "checksum": checksum,
               "created_ts": dt.timestamp(), "path": filename}
        if live is not None:
            live_name = os.path.splitext(filename)[0] + ".MOV"
            (cloud_dir / live_name).write_bytes(live)
            row.update(live_size=len(live), live_filename=live_name, live_path=live_name,
                       live_checksum=base64.b64encode(hashlib.sha1(live).digest()).decode())
        items.append(row)

    years = [2017, 2019, 2021, 2022, 2023, 2024, 2025]
    n = 0
    for yi, year in enumerate(years):
        for k in range(3):
            n += 1
            dt = datetime(year, 2 + (k * 4) % 11, 3 + k * 7, 10 + k, 15, 0)
            color = ((yi * 37 + k * 50) % 256, (yi * 91) % 256, (k * 70 + 30) % 256)
            heic = _heic_bytes(960, 720, color, dt, seed=n) if k % 2 == 0 else None
            if heic is not None:
                add(f"IMG_{n:04d}.HEIC", heic, dt, "photo")
            else:
                add(f"IMG_{n:04d}.JPG", _jpeg_bytes(1200, 900, color, dt, seed=n), dt, "photo")
    # Live Photo: HEIC (или JPG) и видеочасть рядом
    for k in range(3):
        n += 1
        dt = datetime(2024, 6 + k, 12, 18, 30, 0)
        heic = _heic_bytes(960, 720, (200, 120 + k * 30, 80), dt, seed=n)
        live = _video_bytes(900_000 + k * 150_000, seed=n)
        if heic is not None:
            add(f"IMG_{n:04d}.HEIC", heic, dt, "live", live=live)
        else:
            add(f"IMG_{n:04d}.JPG", _jpeg_bytes(1200, 900, (200, 120, 80), dt, seed=n), dt, "live", live=live)
    # Видео
    for k in range(4):
        n += 1
        dt = datetime(2020 + k, 8, 1 + k * 5, 12, 0, 0)
        add(f"IMG_{n:04d}.MOV", _video_bytes(4_500_000 + k * 2_000_000, seed=n), dt, "video")
    # Точные дубли: те же байты под другими именами и датами
    for src_index in (1, 7):
        src = items[src_index]
        data = (cloud_dir / src["path"]).read_bytes()
        n += 1
        dt = datetime.fromtimestamp(src["created_ts"])
        add(f"IMG_{n:04d}{os.path.splitext(src['filename'])[1]}", data, dt, src["kind"])
        n += 1
        add(f"IMG_{n:04d}{os.path.splitext(src['filename'])[1]}", data, datetime(2025, 1, 9, 9, 0, 0), src["kind"])
    # Одноимённые файлы из разных лет — проверка хвоста в имени
    n += 1
    add("IMG_0001.JPG", _jpeg_bytes(800, 600, (10, 200, 10), datetime(2018, 5, 5, 5, 5, 5), seed=n),
        datetime(2018, 5, 5, 5, 5, 5), "photo")
    # Снимок без EXIF и с датой из iCloud
    n += 1
    add(f"IMG_{n:04d}.JPG", _jpeg_bytes(640, 480, (90, 90, 200), datetime(2022, 12, 31, 23, 59, 0), exif=False, seed=n),
        datetime(2022, 12, 31, 23, 59, 0), "photo")
    items.sort(key=lambda r: r["created_ts"])
    atomic_write_text(cloud_dir / MANIFEST, json.dumps(items, ensure_ascii=False, indent=1))
    return items


if __name__ == "__main__":
    out = generate_library(Path(__file__).resolve().parent / "_demo_cloud")
    total = sum(i["size"] + (i.get("live_size") or 0) for i in out)
    print(f"Демо-медиатека: {len(out)} файлов, {total / 1e6:.1f} МБ")
