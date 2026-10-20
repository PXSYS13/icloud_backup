"""Ссылка Apple устарела (403) — программа пересобирает список и качает по свежей ссылке."""

from __future__ import annotations

import pytest

import webapp.pipeline as pl
from demo_engine import DemoEngine
from state import State
from tests.test_pipeline import make_cloud, run


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(pl, "BACKOFF_SEC", (0.01, 0.01, 0.01))


class Rejected:
    status_code = 403
    ok = False

    def iter_content(self, chunk_size):
        return iter(())

    def close(self):
        pass


def _engine(tmp_path):
    e = DemoEngine(tmp_path / "session", cloud_dir=tmp_path / "cloud")
    e.speed = 10 ** 9
    e.login("demo@icloud.com", "pw")
    assert e.submit_code("123456")
    return e


def test_rejected_url_triggers_relist_with_fresh_links(tmp_path):
    engine = _engine(tmp_path)
    make_cloud(tmp_path / "cloud", n_items=3)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    orig = engine.open_stream
    rejected = {"n": 0}

    def flaky_open(url, start=0):
        if url.endswith("IMG_0001.JPG") and rejected["n"] == 0:
            rejected["n"] += 1
            return Rejected()
        return orig(url, start)

    engine.open_stream = flaky_open
    status = run(pl.Pipeline(), engine, state, cfg)
    assert status["state"] == "done", status
    assert rejected["n"] == 1
    assert state.get_stats()["downloaded"] == 4  # 3 + одноимённый файл из другого года
    assert any("список заново" in e["text"] for e in status["events"])


def test_url_rejected_three_times_marks_file_failed(tmp_path):
    engine = _engine(tmp_path)
    make_cloud(tmp_path / "cloud", n_items=3)
    state = State(str(tmp_path / "state.db"))
    cfg = {"download_dir": str(tmp_path / "dl"), "by_month": False}
    orig = engine.open_stream
    engine.open_stream = lambda url, start=0: Rejected() if url.endswith("IMG_0002.JPG") else orig(url, start)
    status = run(pl.Pipeline(), engine, state, cfg)
    assert status["state"] == "done", status
    st = state.get_stats()
    assert st["failed"] == 1 and st["downloaded"] == 3
    assert state.get("id-2")["error"].startswith("url |")
