"""Веб-интерфейс: вход и код 2FA, статус без секретов, защита от чужих сайтов, настройки."""

from __future__ import annotations

from fastapi.testclient import TestClient

import webapp.runtime
import webui


def test_login_flow_with_2fa(client, fake_engine):
    st = client.get("/api/status").json()
    assert st["account"]["state"] == "none"
    assert st["config"]["photo_format"] == "originals" and st["config"]["by_month"] is True

    r = client.post("/api/login", json={"apple_id": "ivan@icloud.com", "password": "wrong"}).json()
    assert r["ok"] is False and r["kind"] == "auth"

    r = client.post("/api/login", json={"apple_id": "ivan@icloud.com", "password": "secret"}).json()
    assert r["ok"] is True and r["state"] == "needs_2fa"
    assert client.get("/api/status").json()["account"]["state"] == "needs_2fa"

    r = client.post("/api/login/code", json={"code": "000000"}).json()
    assert r["ok"] is False and r["kind"] == "code"
    r = client.post("/api/login/code", json={"code": "12 34 56"}).json()
    assert r["ok"] is True
    st = client.get("/api/status").json()
    assert st["account"]["state"] == "ok"
    assert st["account"]["apple_id_masked"] == "iv…n@icloud.com"
    assert "secret" not in client.get("/api/status").text

    r = client.post("/api/logout").json()
    assert r["ok"] and fake_engine.logged_out == 1
    assert client.get("/api/status").json()["account"]["state"] == "none"


def test_status_has_no_secrets_and_download_requires_login(client, tmp_path, monkeypatch):
    monkeypatch.setenv("YA_DISK_TOKEN", "y0_AgAAAABsecretsecretsecretsecret")
    st = client.get("/api/status").json()
    assert "secretsecret" not in str(st)
    assert st["config"]["ya_token_set"] is True
    r = client.post("/api/download/start").json()
    assert r["ok"] is False and r.get("action") == "login"


def test_csrf_guard_blocks_foreign_host_and_origin(fake_engine):
    c = TestClient(webui.app, headers={"Host": "evil.example"})
    assert c.get("/api/status").status_code == 403
    c = TestClient(webui.app, headers={"Host": "localhost:8000", "Origin": "http://evil.example"})
    assert c.post("/api/download/pause").status_code == 403
    c = TestClient(webui.app, headers={"Host": "localhost:8000", "Origin": "http://localhost:8000"})
    assert c.post("/api/download/pause").status_code == 200


def test_download_dir_validation(client, tmp_path):
    assert client.post("/api/config/download_dir", json={"download_dir": "C:\\"}).status_code == 400
    assert client.post("/api/config/download_dir", json={"download_dir": "C:\\Windows"}).status_code == 400
    r = client.post("/api/config/download_dir", json={"download_dir": str(tmp_path / "фото")})
    assert r.status_code == 200 and r.json()["ok"]
    st = client.get("/api/status").json()
    assert st["config"]["download_dir"] == str(tmp_path / "фото") and st["config"]["download_dir_set"]


def test_settings_and_open_folder(client):
    assert client.post("/api/settings", json={"photo_format": "webp"}).status_code == 400
    r = client.post("/api/settings", json={"photo_format": "both", "by_month": False}).json()
    assert r["ok"] and r["config"]["photo_format"] == "both" and r["config"]["by_month"] is False
    assert client.get("/api/status").json()["config"]["photo_format"] == "both"
    assert client.post("/api/open_folder", json={"which": "..\\etc"}).status_code == 400
    r = client.post("/api/open_folder", json={"which": "duplicates"}).json()
    assert r["ok"] is False  # папки ещё нет — открывать нечего


def test_index_and_hello(client):
    html = client.get("/").text
    assert "iCloud БК" in html and "__APP_VERSION__" not in html
    hello = client.get("/api/hello").json()
    assert hello["app"] == "icloud-backup"


def test_forget_list_removes_db(client, tmp_path):
    webapp.runtime.get_state().upsert_batch([{"id": "x", "filename": "a.jpg", "kind": "photo", "size": 1}])
    assert client.get("/api/status").json()["library"]["total"] == 1
    r = client.post("/api/forget_list").json()
    assert r["ok"], r
    assert client.get("/api/status").json()["library"]["total"] == 0
