"""Скачивание с докачкой на демо-движке: остановка и продолжение, .part, повтор при обрыве,
Live Photo рядом, одноимённые файлы, дата съёмки как дата изменения."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

import webapp.pipeline as pl
from demo_engine import DemoEngine
from state import State
from utils import compute_md5


def make_cloud(cloud: Path, n_items: int = 8, size: int = 200_000) -> list[dict]:
    """Маленькая демо-медиатека без картинок: случайные байты под именами iCloud."""
    cloud.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(n_items):
        data = os.urandom(size + i * 1000)
        name = f"IMG_{i:04d}.JPG"
        (cloud / name).write_bytes(data)
        row = {"id": f"id-{i}", "filename": name, "kind": "photo", "size": len(data),
               "checksum": base64.b64encode(hashlib.sha1(data).digest()).decode(),
               "created_ts": datetime(2020 + i % 3, 1 + i, 5, 12, 0, 0).timestamp(), "path": name}
        if i == 2:  # Live Photo: видеочасть рядом
            live = os.urandom(50_000)
            (cloud / "IMG_0002.MOV").write_bytes(live)
            row.update(kind="live", live_size=len(live), live_filename="IMG_0002.MOV", live_path="IMG_0002.MOV",
                       live_checksum="x")
        items.append(row)
    # Одноимённый файл из другого года — должен получить хвост в имени
    data = os.urandom(70_000)
    (cloud / "dup_IMG_0001.JPG").write_bytes(data)
    items.append({"id": "id-dupname", "filename": "IMG_0001.JPG", "kind": "photo", "size": len(data),
                  "checksum": "y", "created_ts": datetime(2018, 5, 5, 5, 5, 5).timestamp(), "path": "dup_IMG_0001.JPG"})
    (cloud / "manifest.json").write_text(json.dumps(items), encoding="utf-8")
    return items


@pytest.fixture
def engine(tmp_path):
    e = DemoEngine(tmp_path / "session", cloud_dir=tmp_path / "cloud")
    e.speed = 10 ** 9
    e.login("demo@icloud.com", "pw")
    assert e.submit_code("123456")
    return e


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(pl, "BACKOFF_SEC", (0.01, 0.01, 0.01))


def run(pipeline: pl.Pipeline, engine, state, cfg, mode="download", timeout=60):
    ok, msg = pipeline.start(mode, engine, state, cfg)
    assert ok, msg
    pipeline.wait_finished(timeout)
    assert not pipeline.is_running()
    return pipeline.status()


def test_download_all_then_resume_after_stop(tmp_path, engine):
    items = make_cloud(tmp_path / "cloud")
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": True}
    pipeline = pl.Pipeline()

    # Останавливаем после первых двух файлов — остальное должно докачаться вторым запуском
    ok, _ = pipeline.start("download", engine, state, cfg)
    assert ok
    deadline = time.time() + 30
    while time.time() < deadline and state.get_stats()["downloaded"] < 2:
        time.sleep(0.05)
    pipeline.stop()
    pipeline.wait_finished(30)
    st = state.get_stats()
    assert pipeline.status()["state"] == "stopped"
    assert 0 < st["downloaded"] < st["total"]

    status = run(pipeline, engine, state, cfg)
    assert status["state"] == "done", status
    st = state.get_stats()
    assert st["downloaded"] == len(items) == st["total"]
    assert st["failed"] == 0 and st["pending"] == 0
    # Файлы на месте, размеры совпадают, .part не осталось
    for raw in items:
        row = state.get(raw["id"])
        assert row["status"] == "downloaded"
        assert os.path.getsize(row["local_path"]) == raw["size"]
        assert compute_md5(row["local_path"]) == row["md5"]
        # Дата съёмки — как дата изменения
        assert abs(os.path.getmtime(row["local_path"]) - raw["created_ts"]) < 2
        # Раскладка по годам и месяцам
        d = datetime.fromtimestamp(raw["created_ts"])
        assert row["local_path"].replace("\\", "/").endswith(f"{d.year}/{d.year}-{d.month:02d}/{os.path.basename(row['local_path'])}")
    assert not list(Path(cfg["download_dir"]).rglob("*.part"))
    # Live Photo: видеочасть рядом с тем же именем
    live = state.get("id-2")
    assert live["live_local_path"].endswith("IMG_0002.MOV")
    assert os.path.dirname(live["live_local_path"]) == os.path.dirname(live["local_path"])
    assert os.path.getsize(live["live_local_path"]) == 50_000
    # Одноимённые файлы из разных лет не затирают друг друга
    a, b = state.get("id-1"), state.get("id-dupname")
    assert os.path.basename(a["local_path"]) == "IMG_0001.JPG"
    assert os.path.basename(b["local_path"]) == "IMG_0001.JPG"
    assert a["local_path"] != b["local_path"]


def test_part_file_is_resumed_not_restarted(tmp_path, engine):
    items = make_cloud(tmp_path / "cloud", n_items=3)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    # Половина файла уже лежит как .part — докачка должна продолжить с этого места
    raw = items[0]
    data = (tmp_path / "cloud" / raw["path"]).read_bytes()
    part = Path(cfg["download_dir"]) / (raw["filename"] + ".part")
    part.parent.mkdir(parents=True)
    part.write_bytes(data[: len(data) // 2])
    starts = []
    orig = engine.open_stream

    def spy(url, start=0):
        starts.append((url, start))
        return orig(url, start)

    engine.open_stream = spy
    pipeline = pl.Pipeline()
    status = run(pipeline, engine, state, cfg)
    assert status["state"] == "done"
    assert any(s == len(data) // 2 for _, s in starts), starts
    row = state.get(raw["id"])
    assert Path(row["local_path"]).read_bytes() == data
    assert row["md5"] == hashlib.md5(data).hexdigest()


def test_flaky_stream_is_retried(tmp_path, engine):
    """Пятый файл демо-движка обрывается при первой попытке — программа повторяет и докачивает."""
    make_cloud(tmp_path / "cloud", n_items=7, size=400_000)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    pipeline = pl.Pipeline()
    status = run(pipeline, engine, state, cfg)
    assert status["state"] == "done"
    st = state.get_stats()
    assert st["failed"] == 0 and st["downloaded"] == st["total"]
    assert any("Повтор через" in e["text"] for e in status["events"]), status["events"]


def test_scan_only_lists_without_downloading(tmp_path, engine):
    items = make_cloud(tmp_path / "cloud", n_items=4)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": True}
    pipeline = pl.Pipeline()
    status = run(pipeline, engine, state, cfg, mode="scan")
    assert status["state"] == "done"
    st = state.get_stats()
    assert st["total"] == len(items) and st["downloaded"] == 0
    assert st["live"] == 1 and st["photos"] == len(items) - 1
    assert state.get_meta("listed_at")
    assert not (tmp_path / "dl").exists()


def test_existing_file_of_same_size_is_not_downloaded_again(tmp_path, engine):
    """«Забыли список», а файлы лежат: докачка узнаёт их по размеру и не качает второй раз."""
    items = make_cloud(tmp_path / "cloud", n_items=3)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    raw = items[1]
    dst = Path(cfg["download_dir"]) / raw["filename"]
    dst.parent.mkdir(parents=True)
    dst.write_bytes((tmp_path / "cloud" / raw["path"]).read_bytes())
    opened = []
    orig = engine.open_stream
    engine.open_stream = lambda url, start=0: (opened.append(url), orig(url, start))[1]
    status = run(pl.Pipeline(), engine, state, cfg)
    assert status["state"] == "done"
    assert "demo://" + raw["path"] not in opened
    assert state.get(raw["id"])["status"] == "downloaded"
