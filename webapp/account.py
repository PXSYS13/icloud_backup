"""Вход в Apple ID: логин и пароль, код 2FA, выход.

Пароль и код приходят в теле запроса, сразу уходят в движок и нигде не сохраняются;
в журнал попадает только замаскированный Apple ID.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from icloud_engine import EngineError
from utils import mask_apple_id
from webapp import runtime

log = logging.getLogger("webui")
router = APIRouter()


class LoginPayload(BaseModel):
    apple_id: str
    password: str


class CodePayload(BaseModel):
    code: str


def account_view() -> dict:
    engine = runtime.get_engine()
    acc = engine.account()
    return {
        "state": acc.get("state") or "none",
        "apple_id_masked": mask_apple_id(acc.get("apple_id") or ""),
        "restoring": not runtime.restore_info["done"],
        "demo": runtime.demo,
    }


@router.post("/api/login")
def api_login(p: LoginPayload):
    apple_id = (p.apple_id or "").strip()
    password = p.password or ""
    if not apple_id or "@" not in apple_id:
        return {"ok": False, "kind": "auth", "msg": "Введите Apple ID — это адрес электронной почты, на который "
                                                  "зарегистрирован iPhone."}
    if not password:
        return {"ok": False, "kind": "auth", "msg": "Введите пароль от Apple ID."}
    if runtime.pipeline.is_running():
        return {"ok": False, "kind": "other", "msg": "Сначала остановите скачивание."}
    engine = runtime.get_engine()
    try:
        result = engine.login(apple_id, password)
    except EngineError as e:
        log.warning("вход %s не удался: %s", mask_apple_id(apple_id), e.kind)
        return {"ok": False, "kind": e.kind, "msg": e.text}
    finally:
        password = ""  # noqa: F841 — не держим пароль дольше запроса
    log.info("вход %s: %s", mask_apple_id(apple_id), result)
    return {"ok": True, "state": result, "account": account_view()}


@router.post("/api/login/code")
def api_login_code(p: CodePayload):
    code = "".join(ch for ch in (p.code or "") if ch.isdigit())
    if len(code) != 6:
        return {"ok": False, "kind": "code", "msg": "Код состоит из шести цифр."}
    engine = runtime.get_engine()
    try:
        ok = engine.submit_code(code)
    except EngineError as e:
        return {"ok": False, "kind": e.kind, "msg": e.text}
    if not ok:
        log.info("код 2FA не подошёл")
        return {"ok": False, "kind": "code",
                "msg": "Код не подошёл. Проверьте цифры или попросите новый код на айфоне: "
                       "Настройки → ваше имя → «Пароль и безопасность» → «Получить код подтверждения»."}
    log.info("код 2FA принят, сессия сохранена")
    return {"ok": True, "state": "ok", "account": account_view()}


@router.post("/api/logout")
def api_logout():
    if runtime.pipeline.is_running():
        runtime.pipeline.stop()
        runtime.pipeline.wait_finished(15)
    engine = runtime.get_engine()
    engine.logout()
    runtime.restore_info.update(done=True, ok=False)
    log.info("выход: сессия удалена")
    return {"ok": True, "msg": "Вы вышли. Сессия удалена с этого компьютера", "account": account_view()}


@router.get("/api/account")
def api_account():
    return account_view()
