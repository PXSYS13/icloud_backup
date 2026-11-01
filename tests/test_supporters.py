"""«Нас поддерживают» и QR-код доната на экране «Поддержать».

Список общий для всех программ автора и приходит вместе с программой (supporters.json). Его
обновляет GitHub Actions, а сама программа за ним в интернет не ходит — это проверяется здесь
по исходникам. Разметка списка строится в браузере, поэтому экранирование проверяем в Node.js
(без Node эти тесты пропускаются).
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import supporters
import webapp.paths
import webui

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "webui_assets" / "static" / "js"
SUPPORT_JS = JS / "screens" / "support.js"
WORKFLOW = ROOT / ".github" / "workflows" / "supporters.yml"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="нужен Node.js: разметка списка строится в браузере")


def entry(name="Анна", amount=100, currency="RUB", day="2026-10-20", message=None):
    return {"name": name, "amount": amount, "currency": currency, "date": day, "message": message}


def write_list(path: Path, items, **extra) -> Path:
    path.write_text(json.dumps({"updated": "2026-10-25", "supporters": items, **extra}, ensure_ascii=False),
                    encoding="utf-8")
    return path


# ── Проверка записей ──

def test_normalize_cleans_entries_and_sorts_newest_first():
    raw = {
        "updated": "2026-10-25",
        "top_min_rub": 300,
        "supporters": [
            entry("Старый", day="2026-01-02"),
            "мусор",
            entry("  Анна​ \n  П.‮ ", 1.5, "USD", "2026-10-21", "Спасибо\x00 большое"),
            entry("", 100, "rub", "2026-13-40"),
            entry("Сергей", True, "RUB", "2026-10-21", "   "),
            entry("Я" * 60, -5),
        ],
    }
    out = supporters.normalize(raw)
    assert out["updated"] == "2026-10-25" and out["top_min_rub"] == 300
    names = [x["name"] for x in out["supporters"]]
    # Записи одного дня остаются в порядке файла, без даты — в конце
    assert names[:2] == ["Анна П.", "Сергей"]
    anna = out["supporters"][0]
    assert anna == {"name": "Анна П.", "amount": 1.5, "currency": "USD", "date": "2026-10-21",
                    "message": "Спасибо большое"}
    sergey = out["supporters"][1]
    assert sergey["amount"] is None and sergey["message"] is None
    nameless = next(x for x in out["supporters"] if x["date"] is None and x["name"] == supporters.ANONYMOUS)
    assert nameless["currency"] == "RUB"
    long = next(x for x in out["supporters"] if x["name"].startswith("Я"))
    assert len(long["name"]) == supporters.MAX_NAME and long["name"].endswith("…") and long["amount"] is None
    assert len(out["supporters"]) == 5


def test_normalize_survives_wrong_shapes_and_keeps_only_numeric_threshold():
    for raw in (None, [], "текст", {"supporters": "нет"}, {"supporters": None}):
        assert supporters.normalize(raw) == {"updated": None, "supporters": []}
    for bad in ("500", True, -1, 1.5):
        assert "top_min_rub" not in supporters.normalize({"supporters": [], "top_min_rub": bad})
    many = {"supporters": [entry(f"N{i}") for i in range(supporters.MAX_ITEMS + 20)]}
    assert len(supporters.normalize(many)["supporters"]) == supporters.MAX_ITEMS


def test_count_people_merges_names_and_counts_each_anonymous():
    items = supporters.normalize({"supporters": [
        entry("Rufus"), entry("rufus"), entry("Аноним"), entry(""), entry("Ivan"),
    ]})["supporters"]
    assert supporters.count_people(items) == 4


def test_top_sums_one_person_across_currencies_and_skips_anonymous():
    items = supporters.normalize({"supporters": [
        entry("Rufus", 300, "RUB", "2026-10-21"),
        entry("rufus", 5, "EUR", "2026-10-22"),
        entry("Batl", 1000, "RUB", "2026-10-14"),
        entry("Ранний", 800, "RUB", "2026-10-01"),
        entry("Поздний", 800, "RUB", "2026-10-20"),
        entry("Аноним", 3000, "RUB", "2026-10-25"),
        entry("Мало", 499, "RUB", "2026-10-25"),
        entry("Без суммы", None, "RUB", "2026-10-25"),
    ]})["supporters"]
    top = supporters.top_supporters(items)
    assert [x["name"] for x in top] == ["Batl", "Ранний", "Поздний", "rufus"]
    rufus = top[-1]
    # Имя — как человек подписался в последний раз; сумма крупнее в рублях — первой
    assert rufus["donations"] == 2 and rufus["since"] == "2026-10-21"
    assert rufus["sums"] == [{"amount": 5, "currency": "EUR"}, {"amount": 300, "currency": "RUB"}]
    assert [x["name"] for x in supporters.top_supporters(items, min_rub=900)] == ["Batl"]
    assert len(supporters.top_supporters(items, min_rub=0, limit=2)) == 2


# ── Файл рядом с программой ──

def test_load_local_returns_none_for_missing_corrupt_or_huge_file(tmp_path):
    assert supporters.load_local(tmp_path / "нет.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text('{"supporters": [', encoding="utf-8")
    assert supporters.load_local(broken) is None
    not_utf8 = tmp_path / "cp1251.json"
    not_utf8.write_bytes('{"supporters": [{"name": "Анна"}]}'.encode("cp1251"))
    assert supporters.load_local(not_utf8) is None
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    assert supporters.load_local(deep) is None
    huge = tmp_path / "huge.json"
    huge.write_text(json.dumps({"supporters": [], "x": "a" * supporters.MAX_BYTES}), encoding="utf-8")
    assert supporters.load_local(huge) is None


def test_load_local_reads_valid_and_empty_file(tmp_path):
    data = supporters.load_local(write_list(tmp_path / "s.json", [entry("Анна")]))
    assert data["updated"] == "2026-10-25" and [x["name"] for x in data["supporters"]] == ["Анна"]
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    assert supporters.load_local(empty) == {"updated": None, "supporters": []}


def test_shipped_list_is_valid():
    path = ROOT / "supporters.json"
    assert path.stat().st_size <= supporters.MAX_BYTES
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw.get("supporters"), list)
    data = supporters.load_local(path)
    assert data is not None and len(data["supporters"]) == len(raw["supporters"])


# ── /api/supporters ──

def test_endpoint_returns_list_people_and_top(client):
    write_list(webapp.paths.SUPPORTERS_FILE, [
        entry("Batl", 1000, day="2026-10-14", message="<img src=x onerror=alert(1)>"),
        entry("Аноним", 100, day="2026-10-25"),
    ], top_min_rub=700)
    r = client.get("/api/supporters")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["updated"] == "2026-10-25"
    assert data["people"] == 2 and data["top_min_rub"] == 700
    assert [x["name"] for x in data["supporters"]] == ["Аноним", "Batl"]
    assert [x["name"] for x in data["top"]] == ["Batl"]
    # Экранирует экран; сервер отдаёт текст как есть, только без невидимых знаков
    assert data["supporters"][1]["message"] == "<img src=x onerror=alert(1)>"


def test_endpoint_without_file_or_with_broken_file_shows_empty_list(client):
    data = client.get("/api/supporters").json()
    assert data == {"ok": True, "updated": None, "supporters": [], "people": 0, "top": [],
                    "top_min_rub": supporters.TOP_MIN_RUB}
    webapp.paths.SUPPORTERS_FILE.write_text("не JSON", encoding="utf-8")
    assert client.get("/api/supporters").json()["supporters"] == []


def test_endpoint_is_guarded_like_others(fake_engine):
    c = TestClient(webui.app, headers={"Host": "evil.example"})
    assert c.get("/api/supporters").status_code == 403


# ── Без сети ──

NETWORK_MODULES = {"requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "socket", "ssl", "ftplib"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_supporters_code_makes_no_network_calls():
    assert imported_modules(ROOT / "supporters.py") <= {"__future__", "json", "re", "unicodedata", "datetime", "pathlib"}
    assert not imported_modules(ROOT / "webapp" / "pages.py") & NETWORK_MODULES
    for path in (ROOT / "supporters.py", ROOT / "webapp" / "pages.py"):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, path.name
    js = SUPPORT_JS.read_text(encoding="utf-8")
    for call in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "new Image", "githubusercontent"):
        assert call not in js, call
    # Единственный запрос экрана — к самой программе
    assert js.count("App.api.") == 1 and "App.api.get('/api/supporters')" in js


def test_qr_is_local_and_on_support_screen():
    qr = ROOT / "webui_assets" / "static" / "donate-qr.png"
    assert qr.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    js = SUPPORT_JS.read_text(encoding="utf-8")
    assert 'class="donate-qr" src="/static/donate-qr.png" width="132" height="132"' in js
    assert "Или наведите камеру телефона на QR-код — откроется та же страница DonationAlerts." in js
    assert js.count("https://dalink.to/davronsvv") == 1
    css = (ROOT / "webui_assets" / "static" / "app.css").read_text(encoding="utf-8")
    rule = next(line for line in css.splitlines() if line.startswith(".donate-qr{"))
    # Белый фон в любой теме: на тёмном QR-код камера телефона не прочитает
    assert "background:#fff" in rule and "padding:" in rule
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '<a href="https://dalink.to/davronsvv"><img src="webui_assets/static/donate-qr.png" width="180"' in readme


def test_workflow_refreshes_copy_without_secrets():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    for needle in ("workflow_dispatch", "if: github.repository == 'PXSYS13/icloud_backup'", "contents: write",
                   "curl -fsSL --max-time 30", "raw.githubusercontent.com/PXSYS13/yadisk_backup/main/supporters.json",
                   "256 * 1024", 'data.get("supporters"), list', 'git commit -m "chore: update supporters list"',
                   "github-actions[bot]", "git diff --quiet -- supporters.json"):
        assert needle in text, needle
    cron = next(line for line in text.splitlines() if "cron:" in line)
    minute, hour = cron.split('"')[1].split()[:2]
    assert minute.isdigit() and hour == "*"


# ── Разметка в браузере ──

PRELUDE = r"""
const fs = require('fs');
const els = new Map();
const el = (id) => {
  if (!els.has(id)) {
    els.set(id, { id, innerHTML: '', textContent: '', focused: false,
      classList: { hidden: false, toggle(name, on) { if (name === 'hidden') this.hidden = !!on; } },
      focus() { this.focused = true; } });
  }
  return els.get(id);
};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.matchMedia = () => ({ matches: false });
globalThis.document = { addEventListener() {}, querySelector: () => null, getElementById: el };
for (const file of __FILES__) (0, eval)(fs.readFileSync(file, 'utf8'));
const App = globalThis.App;
const out = (v) => console.log(JSON.stringify(v));
"""


def run_js(scenario: str):
    files = json.dumps([str(JS / "core.js"), str(SUPPORT_JS)])
    proc = subprocess.run([NODE, "-"], input=PRELUDE.replace("__FILES__", files) + scenario,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_node
def test_list_escapes_names_and_messages_and_pages_by_nine():
    evil = "<img src=x onerror=alert(1)>"
    r = run_js(f"""
      const evil = {json.dumps(evil)};
      const people = Array.from({{ length: 12 }}, (_, i) =>
        ({{ name: i ? `Имя ${{i}}` : evil, amount: 100, currency: 'RUB', date: '2026-10-20', message: i ? null : evil }}));
      App.supporters = {{ updated: '2026-10-25', people: 12, top_min_rub: 500, supporters: people,
        top: [{{ name: evil, donations: 2, since: '2026-10-01', sums: [{{ amount: 700, currency: 'RUB' }}] }}] }};
      App.screens.support.mount(el('screen-support'));
      App.screens.support.show();
      const first = el('support-list').innerHTML;
      App.actions['supporters-more']();
      out({{ first, all: el('support-list').innerHTML, top: el('support-top').innerHTML,
        topHidden: el('support-top-wrap').classList.hidden, count: el('support-count').textContent }});
    """)
    for html in (r["first"], r["top"]):
        assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert r["first"].count('class="supporter"') == 9 and 'data-action="supporters-more"' in r["first"]
    assert r["all"].count('class="supporter"') == 12 and "supporters-more" not in r["all"]
    assert r["topHidden"] is False and "700 ₽" in r["top"] and "2 доната" in r["top"]
    assert r["count"] == "Спасибо 12 людям, которые помогли проекту"


@needs_node
def test_empty_list_shows_placeholder_and_hides_top():
    r = run_js("""
      App.supporters = { updated: null, people: 0, top_min_rub: 500, supporters: [], top: [] };
      App.screens.support.mount(el('screen-support'));
      App.screens.support.show();
      out({ list: el('support-list').innerHTML, topHidden: el('support-top-wrap').classList.hidden });
    """)
    assert "Здесь появятся имена тех, кто поддержал проект" in r["list"]
    assert r["topHidden"] is True
