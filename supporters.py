"""«Нас поддерживают»: список тех, кто поддержал программы автора, для экрана «Поддержать».

Список общий для всех программ автора. Его ведут в supporters.json в репозитории «Яндекс БК»
(туда сами попадают донаты DonationAlerts), а копия лежит рядом с этой программой. Копию раз
в час обновляет GitHub Actions (.github/workflows/supporters.yml), и люди получают свежий
список вместе с новой версией программы.

Сама программа за списком в интернет не ходит: здесь только чтение локального файла и
проверка записей (перенесено из supporters.py «Яндекс БК» без загрузки с GitHub).
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path

MAX_BYTES = 256 * 1024
MAX_ITEMS = 500
MAX_NAME = 40
MAX_MESSAGE = 160
ANONYMOUS = "Аноним"
# «Больше всех помогли»: все донаты человека вместе от этой суммы (supporters.json → top_min_rub)
TOP_MIN_RUB = 500
TOP_LIMIT = 10
# Только чтобы расставить людей по местам: суммы в разных валютах сравниваются в рублях по
# округлённому курсу. На экране каждая сумма остаётся в своей валюте
RUB_RATE = {"RUB": 1, "USD": 90, "EUR": 100, "KZT": 0.18, "UAH": 2.2, "BYN": 28}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY_RE = re.compile(r"[A-Z]{3}")


def _clean_text(value, limit: int) -> str:
    """Одна строка без лишних пробелов и невидимых знаков, не длиннее limit (обрезанное — с многоточием).

    Невидимые знаки (нулевой ширины, разворот текста справа налево) прятали бы ссылку от фильтра
    и меняли бы порядок букв на экране.
    """
    if not isinstance(value, str):
        return ""
    value = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in unicodedata.normalize("NFKC", value)
                    if unicodedata.category(ch) not in ("Cf", "Co", "Cs", "Cn"))
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _clean_date(value) -> str | None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _clean_amount(value) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 10 ** 9:
        return None
    value = round(float(value), 2)
    return int(value) if value.is_integer() else value


def normalize(raw) -> dict:
    """Проверенный список {"updated", "supporters"}: мусор отброшен, новые сверху.

    Порог «Больше всех помогли» (top_min_rub) попадает в ответ, только если он задан числом.
    Файл правит человек руками, поэтому ошибка в одной записи не должна прятать остальные.
    """
    items = raw.get("supporters") if isinstance(raw, dict) else None
    out: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        currency = item.get("currency")
        out.append({
            "name": _clean_text(item.get("name"), MAX_NAME) or ANONYMOUS,
            "amount": _clean_amount(item.get("amount")),
            "currency": currency if isinstance(currency, str) and _CURRENCY_RE.fullmatch(currency) else "RUB",
            "date": _clean_date(item.get("date")),
            "message": _clean_text(item.get("message"), MAX_MESSAGE) or None,
        })
    # Сортировка устойчивая: записи одного дня остаются в порядке файла. Обрезаем после неё:
    # донаты в файле идут первыми, и при 500+ донатах обрезка до сортировки теряла записи вручную
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    result = {"updated": _clean_date(raw.get("updated")) if isinstance(raw, dict) else None,
              "supporters": out[:MAX_ITEMS]}
    top_min = raw.get("top_min_rub") if isinstance(raw, dict) else None
    if isinstance(top_min, int) and not isinstance(top_min, bool) and 0 <= top_min < 10 ** 9:
        result["top_min_rub"] = top_min
    return result


def count_people(items: list[dict]) -> int:
    """Сколько людей: повторные донаты под одним именем — один человек, анонимы — каждый свой."""
    names = set()
    anonymous = 0
    for item in items:
        if item["name"] == ANONYMOUS:
            anonymous += 1
        else:
            names.add(item["name"].casefold())
    return len(names) + anonymous


def top_supporters(items: list[dict], min_rub: int | None = None, limit: int = TOP_LIMIT) -> list[dict]:
    """«Больше всех помогли»: люди, которые поддержали проект в сумме от min_rub рублей.

    Донаты под одним именем складываются, как в count_people. «Аноним» каждый раз другой
    человек и просил имя не показывать, поэтому в топ не попадает. Места — по сумме в рублях,
    при равной сумме выше тот, кто поддержал раньше. sums — суммы в своих валютах, крупная первой.
    """
    min_rub = TOP_MIN_RUB if min_rub is None else min_rub
    people: dict[str, dict] = {}
    for i, item in enumerate(items):
        if item["name"] == ANONYMOUS or item["amount"] is None:
            continue
        # Список отсортирован от новых к старым: имя остаётся таким, как человек подписался в последний раз
        p = people.setdefault(item["name"].casefold(),
                              {"name": item["name"], "sums": {}, "donations": 0, "since": None, "rub": 0.0})
        p["sums"][item["currency"]] = p["sums"].get(item["currency"], 0) + item["amount"]
        p["donations"] += 1
        p["rub"] += item["amount"] * RUB_RATE.get(item["currency"], 0)
        # Дата без времени: внутри дня раньше тот, кто ниже в списке
        p["oldest"] = i
        if item["date"] and (p["since"] is None or item["date"] < p["since"]):
            p["since"] = item["date"]
    ranked = sorted((p for p in people.values() if p["rub"] > 0 and p["rub"] >= min_rub),
                    key=lambda p: (-p["rub"], p["since"] or "9999-12-31", -p["oldest"]))
    return [{
        "name": p["name"],
        "donations": p["donations"],
        "since": p["since"],
        "sums": [{"amount": _clean_amount(amount), "currency": currency} for currency, amount
                 in sorted(p["sums"].items(), key=lambda kv: -kv[1] * RUB_RATE.get(kv[0], 0))],
    } for p in ranked[:limit]]


def load_local(path: Path) -> dict | None:
    """Копия списка, пришедшая вместе с программой (None — файла нет, он слишком большой или испорчен)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        with open(path, encoding="utf-8") as f:
            return normalize(json.load(f))
    # ValueError — не JSON или не UTF-8; RecursionError — тысячи вложенных скобок
    except (OSError, ValueError, RecursionError):
        return None
