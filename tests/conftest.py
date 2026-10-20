"""Общая настройка тестов: корень проекта в sys.path, изоляция от файлов проекта."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_project_files(tmp_path, monkeypatch):
    """Веб-интерфейс читает и пишет .env, session/ и state.db в корне проекта.

    Тесты не должны ни зависеть от них, ни портить их: подменяем пути на временные.
    """
    import webapp.paths
    import webapp.pipeline
    import webapp.processing
    import webapp.runtime

    monkeypatch.setattr(webapp.paths, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(webapp.paths, "DB_FILE", tmp_path / "state.db")
    monkeypatch.setattr(webapp.paths, "SESSION_DIR", tmp_path / "session")
    monkeypatch.setattr(webapp.paths, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(webapp.paths, "WEBUI_LOG", tmp_path / "logs" / "webui.log")
    monkeypatch.setattr(webapp.paths, "DEMO_CLOUD_DIR", tmp_path / "_demo_cloud")
    for key in ("PHOTO_FORMAT", "LAYOUT_BY_MONTH", "YA_DISK_TOKEN", "ICLOUD_BK_DEMO"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    # Общие объекты — с чистого листа, чтобы тесты не мешали друг другу
    webapp.runtime.close_state()
    monkeypatch.setattr(webapp.runtime, "pipeline", webapp.pipeline.Pipeline())
    monkeypatch.setattr(webapp.runtime, "jobs", webapp.processing.Jobs())
    monkeypatch.setattr(webapp.runtime, "demo", False)
    yield
    webapp.runtime.close_state()


class FakeEngine:
    """Движок для тестов интерфейса: без сети, без файлов. Код 2FA — 123456."""

    def __init__(self, items=None):
        self.apple_id = None
        self.state = "none"
        self.items = list(items or [])
        self.logged_out = 0

    def login(self, apple_id, password):
        from icloud_engine import EngineError
        if password == "wrong":
            raise EngineError("auth", "Apple не принял Apple ID или пароль.")
        self.apple_id = apple_id
        self.state = "needs_2fa"
        return "needs_2fa"

    def submit_code(self, code):
        if code != "123456":
            return False
        self.state = "ok"
        return True

    def restore(self):
        return self.state == "ok"

    def logout(self):
        self.state = "none"
        self.apple_id = None
        self.logged_out += 1

    def account(self):
        return {"apple_id": self.apple_id, "state": self.state}

    def reauthenticate(self):
        pass

    def count(self):
        return len(self.items)

    def iter_items(self):
        yield from self.items

    def open_stream(self, url, start=0):
        raise NotImplementedError


@pytest.fixture
def fake_engine():
    import webapp.runtime
    engine = FakeEngine()
    webapp.runtime.set_engine(engine)
    return engine


@pytest.fixture
def client(fake_engine):
    from fastapi.testclient import TestClient
    import webui
    return TestClient(webui.app, headers={"Host": "localhost:8000"})
