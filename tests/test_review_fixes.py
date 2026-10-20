"""Правки по ревью: готовый .part не качается заново, удалённые в iCloud файлы уходят из очереди,
JPG-копии переезжают вместе с HEIC, уборка пустых папок трогает только свои."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

import dedupe
import organize
import webapp.pipeline as pl
from demo_engine import DemoEngine
from state import GONE_MARKER, State
from tests.test_pipeline import make_cloud, run
from utils import DIR_DUP, compute_md5


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(pl, "BACKOFF_SEC", (0.01, 0.01, 0.01))


def _engine(tmp_path):
    e = DemoEngine(tmp_path / "session", cloud_dir=tmp_path / "cloud")
    e.speed = 10 ** 9
    e.login("demo@icloud.com", "pw")
    assert e.submit_code("123456")
    return e


def test_complete_part_file_is_renamed_not_redownloaded(tmp_path):
    engine = _engine(tmp_path)
    items = make_cloud(tmp_path / "cloud", n_items=2)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    raw = items[0]
    data = (tmp_path / "cloud" / raw["path"]).read_bytes()
    part = Path(cfg["download_dir"]) / (raw["filename"] + ".part")
    part.parent.mkdir(parents=True)
    part.write_bytes(data)  # скачан целиком, но не переименован
    opened = []
    orig = engine.open_stream
    engine.open_stream = lambda url, start=0: (opened.append(url), orig(url, start))[1]
    status = run(pl.Pipeline(), engine, state, cfg)
    assert status["state"] == "done"
    assert "demo://" + raw["path"] not in opened
    row = state.get(raw["id"])
    assert Path(row["local_path"]).read_bytes() == data and row["md5"] == compute_md5(row["local_path"])
    assert not part.exists()


def test_items_deleted_in_icloud_leave_the_queue(tmp_path):
    engine = _engine(tmp_path)
    items = make_cloud(tmp_path / "cloud", n_items=4)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    assert run(pl.Pipeline(), engine, state, cfg, mode="scan")["state"] == "done"
    total = state.get_stats()["total"]
    # Два файла удалили в iCloud: новый список их не содержит
    manifest = tmp_path / "cloud" / "manifest.json"
    manifest.write_text(json.dumps(items[2:]), encoding="utf-8")
    assert run(pl.Pipeline(), engine, state, cfg, mode="scan")["state"] == "done"
    st = state.get_stats()
    assert st["gone"] == 2 and st["total"] == total - 2 and st["pending"] == total - 2
    assert state.get(items[0]["id"])["error"].startswith(GONE_MARKER)
    # Скачивание доходит до 100% без «вечных» двух файлов
    status = run(pl.Pipeline(), engine, state, cfg)
    assert status["state"] == "done"
    st = state.get_stats()
    assert st["downloaded"] == st["total"]
    # Файл вернули из «Недавно удалённых» — он снова в очереди
    manifest.write_text(json.dumps(items), encoding="utf-8")
    assert run(pl.Pipeline(), engine, state, cfg, mode="scan")["state"] == "done"
    assert state.get(items[0]["id"])["status"] == "pending"
    assert state.get_stats()["gone"] == 0


def test_download_pass_retires_deleted_items(tmp_path):
    engine = _engine(tmp_path)
    items = make_cloud(tmp_path / "cloud", n_items=3)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    state.upsert_batch([dict(items[0], id="ghost")])  # в базе есть, в iCloud уже нет
    status = run(pl.Pipeline(), engine, state, cfg)
    assert status["state"] == "done"
    assert state.get("ghost")["status"] == "skipped"
    assert state.get_stats()["pending"] == 0


def _seed_heic_with_twin(tmp_path: Path):
    root = tmp_path / "dl"
    state = State(str(tmp_path / "state.db"))
    same = os.urandom(3000)
    rows = []
    for aid, dt in (("a", datetime(2021, 3, 1)), ("b", datetime(2022, 7, 9))):
        folder = root / f"{dt.year}/{dt.year}-{dt.month:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        heic = folder / f"IMG_{aid}.HEIC"
        heic.write_bytes(same)
        (folder / f"IMG_{aid}.JPG").write_bytes(b"jpg-copy-" + aid.encode())
        rows.append({"id": aid, "filename": heic.name, "kind": "photo", "size": len(same), "checksum": aid,
                     "created_ts": dt.timestamp()})
    state.upsert_batch(rows)
    for aid in ("a", "b"):
        p = next(root.rglob(f"IMG_{aid}.HEIC"))
        state.mark_downloaded(aid, str(p), None, compute_md5(str(p)))
    return state, root


def test_jpg_twin_moves_with_heic_in_dedupe_and_relayout(tmp_path):
    state, root = _seed_heic_with_twin(tmp_path)
    res = dedupe.move_duplicates(state, str(root))
    assert res["moved"] == 1
    moved = {p.name for p in (root / DIR_DUP).rglob("*") if p.is_file()}
    assert moved == {"IMG_b.HEIC", "IMG_b.JPG"}
    assert not (root / "2022").exists() or not any((root / "2022").rglob("IMG_b.*"))
    organize.relayout(state, str(root), by_month=False)
    assert {p.name for p in root.iterdir() if p.is_file()} == {"IMG_a.HEIC", "IMG_a.JPG"}


def test_remove_empty_dirs_touches_only_own_folders(tmp_path):
    root = tmp_path / "dl"
    for d in ("2021/2021-03", "2022", "без даты", "Мои документы/пусто", "random"):
        (root / d).mkdir(parents=True)
    cleaned = organize.remove_empty_dirs(str(root))
    assert cleaned == 4  # 2021-03, 2021, 2022, «без даты»
    assert (root / "Мои документы" / "пусто").exists() and (root / "random").exists()
    assert not (root / "2021").exists() and not (root / "без даты").exists()
