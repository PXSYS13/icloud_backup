"""Флажок «От автора» на итогах и «Другие программы автора» на «Поддержать».

Логика флажка живёт в браузере (webui_assets/static/js/flag.js), поэтому проверяем её в Node.js:
core.js и flag.js запускаются с подменёнными localStorage и document. Без Node.js эти тесты
пропускаются, остальные (разметка, ссылки, действия) идут всегда.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "webui_assets"
JS = ASSETS / "static" / "js"
FLAG_JS = JS / "flag.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="нужен Node.js: логика флажка проверяется в нём")

# Окружение браузера, которого хватает core.js и flag.js. Время и хранилище — под рукой у теста
PRELUDE = r"""
const fs = require('fs');
const store = new Map();
let broken = false;
globalThis.localStorage = {
  getItem(k) { if (broken) throw new Error('blocked'); return store.has(k) ? store.get(k) : null; },
  setItem(k, v) { if (broken) throw new Error('blocked'); store.set(k, String(v)); },
};
const clicks = [];
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
let reduced = false;
globalThis.matchMedia = () => ({ matches: reduced });
globalThis.document = {
  addEventListener(type, fn) { if (type === 'click') clicks.push(fn); },
  querySelector: () => null,
  getElementById: () => null,
};
for (const file of __FILES__) (0, eval)(fs.readFileSync(file, 'utf8'));
const App = globalThis.App;
const DAY = 864e5;
let now = 1800000000000;
const start = now;
Date.now = () => now;
const links = {
  telegram: 'https://t.me/davronsv', donation: 'https://dalink.to/davronsvv',
  vk_backup: 'https://github.com/PXSYS13/vk_backup', yadisk_bk: 'https://github.com/PXSYS13/yadisk_backup',
};
const status = (finishedAt, over = {}) => ({
  links,
  library: { total: 10, downloaded: 10, failed: 0, ...(over.library || {}) },
  job: { running: false, error: null, ...(over.job || {}) },
  pipeline: { state: 'done', mode: 'download', running: false, error: null, finished_at: finishedAt, ...(over.pipeline || {}) },
});
// Заход на итоги: экран показан, пришёл статус
function visit(s) { App.state.status = s; App.flag.visit(); return App.flag.view(s); }
function act(name, data = {}) { App.actions[name]({ dataset: data }); }
// Клик по ссылке «Подписаться» или «Поддержать» внутри карточки
function clickCardLink() {
  const target = { closest: (sel) => (sel.includes('data-flag-link') ? {} : null) };
  for (const fn of clicks) fn({ target });
}
const saved = () => JSON.parse(store.get('icb.flag') || 'null');
const out = (v) => console.log(JSON.stringify(v));
"""


def run_js(scenario: str):
    files = json.dumps([str(JS / "core.js"), str(FLAG_JS)])
    proc = subprocess.run([NODE, "-"], input=PRELUDE.replace("__FILES__", files) + scenario,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_node
def test_card_opens_after_download_and_calms_after_three_visits():
    seen = run_js("""
      const id = now / 1000;
      const seen = [];
      for (let i = 0; i < 4; i++) {
        const v = visit(status(id));
        seen.push({ card: !!v.card, pennant: !!v.pennant, glow: v.pennant.includes('glow') });
      }
      out(seen);
    """)
    assert [v["card"] for v in seen] == [True, True, True, False]
    assert all(v["pennant"] for v in seen)
    assert [v["glow"] for v in seen] == [True, False, False, False]


@needs_node
def test_not_now_closes_card_and_pennant_reopens_it():
    r = run_js("""
      const s = status(now / 1000);
      visit(s);
      act('flag-later');
      const closed = App.flag.view(s);
      act('flag-toggle');
      const reopened = App.flag.view(s);
      act('flag-toggle');
      const again = App.flag.view(s);
      out({ closed, reopened, again });
    """)
    assert r["closed"]["card"] == "" and 'aria-expanded="false"' in r["closed"]["pennant"]
    assert r["reopened"]["card"] and 'aria-expanded="true"' in r["reopened"]["pennant"]
    assert 'aria-controls="flag-card"' in r["reopened"]["pennant"]
    assert r["again"]["card"] == ""


@needs_node
def test_click_in_card_calms_it_and_remembers_only_time():
    r = run_js("""
      const s = status(now / 1000);
      visit(s);
      clickCardLink();
      out({ card: App.flag.view(s).card, saved: saved(), keys: [...store.keys()] });
    """)
    assert r["card"] == ""
    assert r["keys"] == ["icb.flag"]
    assert isinstance(r["saved"]["clickedAt"], (int, float))
    # Только время и отметки: ни адресов, ни того, какая из двух ссылок нажата
    raw = json.dumps(r["saved"])
    for word in ("t.me", "dalink", "telegram", "donat", "http"):
        assert word not in raw


@needs_node
def test_card_returns_only_after_new_download_and_30_days():
    r = run_js("""
      const first = now / 1000;
      visit(status(first));
      act('flag-later');
      now = start + 10 * DAY;
      const early = visit(status(now / 1000));       // новое скачивание, но месяц не прошёл
      now = start + 31 * DAY;
      const sameDownload = visit(status(first));     // месяц прошёл, а нового скачивания нет
      const late = visit(status(now / 1000));         // новое скачивание через месяц
      out({ early, sameDownload, late });
    """)
    assert r["early"]["card"] == "" and r["early"]["pennant"]
    assert r["sameDownload"]["card"] == ""
    assert r["late"]["card"] and "glow" in r["late"]["pennant"]


@needs_node
def test_click_in_card_moves_next_show_to_90_days():
    r = run_js("""
      visit(status(now / 1000));
      clickCardLink();
      now = start + 31 * DAY;
      const d31 = visit(status(now / 1000));
      now = start + 91 * DAY;
      const d91 = visit(status(now / 1000));
      out({ d31: !!d31.card, d91: !!d91.card });
    """)
    assert r == {"d31": False, "d91": True}


@needs_node
def test_never_shown_while_downloading_or_after_error():
    r = run_js("""
      const id = now / 1000;
      visit(status(id));  // флажок уже показан для этого скачивания
      const cases = {
        downloading: { pipeline: { state: 'downloading', running: true, finished_at: null } },
        paused: { pipeline: { state: 'paused', running: true, finished_at: null } },
        error: { pipeline: { state: 'error', error: { text: 'Нет сети' } } },
        needs_action: { pipeline: { state: 'needs_action', error: { text: 'Нужен вход' } } },
        stopped: { pipeline: { state: 'stopped' } },
        failed_files: { library: { failed: 2 } },
        not_complete: { library: { downloaded: 7 } },
        job_running: { job: { running: true } },
        job_error: { job: { error: 'Не получилось' } },
      };
      const res = {};
      for (const [name, over] of Object.entries(cases)) {
        const v = visit(status(id, over));
        res[name] = v.pennant + v.card;
      }
      out(res);
    """)
    assert r == {name: "" for name in r}
    assert len(r) == 9


@needs_node
def test_after_restart_remembered_download_keeps_pennant():
    r = run_js("""
      const restarted = (over = {}) => status(null, { pipeline: { state: 'idle', mode: null, ...over } });
      const before = visit(restarted());             // браузер ничего не помнит — молчим
      visit(status(now / 1000));
      act('flag-later');
      const after = visit(restarted());              // программу перезапустили: флажок на месте
      const scan = visit(restarted({ state: 'done', mode: 'scan', finished_at: now / 1000 + 5 }));
      out({ before: before.pennant + before.card, after, scan });
    """)
    assert r["before"] == ""
    assert r["after"]["pennant"] and r["after"]["card"] == ""
    # Обновить список — не новое скачивание: карточка сама не открывается
    assert r["scan"]["pennant"] and r["scan"]["card"] == ""


@needs_node
def test_hide_toggle_and_owned_programs():
    r = run_js("""
      const s = status(now / 1000);
      act('flag-hide');
      const hidden = visit(s);
      const privacyHidden = App.flag.privacy();
      act('flag-hide');
      const shown = visit(s);
      act('promo-own', { app: 'vk' });
      const cardNoVk = App.flag.view(s).card;
      const promosNoVk = App.flag.promos(s);
      act('promo-own', { app: 'yadisk' });
      out({ hidden: hidden.pennant + hidden.card, privacyHidden, privacy: App.flag.privacy(),
            shown: !!shown.card, cardNoVk, promosNoVk, promosNone: App.flag.promos(s), saved: saved() });
    """)
    assert r["hidden"] == ""
    assert 'aria-pressed="true"' in r["privacyHidden"] and 'aria-pressed="false"' in r["privacy"]
    assert "Скрыть флажок" in r["privacy"]
    assert "Программа никуда не сообщает, нажали ли вы на ссылку." in r["privacy"]
    assert r["shown"]
    assert "vk_backup" not in r["cardNoVk"] and "Будем на связи" in r["cardNoVk"]
    assert "yadisk_backup" in r["promosNoVk"] and "vk_backup" not in r["promosNoVk"]
    assert r["promosNone"] == ""
    assert r["saved"]["owned"] == {"vk": True, "yadisk": True}


@needs_node
def test_blocked_storage_falls_back_to_page_memory():
    r = run_js("""
      broken = true;
      const s = status(now / 1000);
      const first = visit(s);
      act('flag-later');
      out({ first: !!first.card, after: App.flag.view(s).card, stored: store.size });
    """)
    assert r == {"first": True, "after": "", "stored": 0}


@needs_node
def test_no_glow_with_reduced_motion():
    r = run_js("""
      reduced = true;
      out(visit(status(now / 1000)).pennant);
    """)
    assert r and "glow" not in r


@needs_node
def test_card_markup_texts_links_and_escaping():
    r = run_js("""
      const s = status(now / 1000);
      s.links = { ...links, telegram: 'https://t.me/x"><script>' };
      const v = visit(s);
      const html = v.pennant + v.card + App.flag.promos(s) + App.flag.privacy();
      const missing = [...html.matchAll(/data-action="([^"]+)"/g)].map((m) => m[1]).filter((n) => !App.actions[n]);
      out({ card: v.card, pennant: v.pennant, html, missing });
    """)
    card = r["card"]
    assert "От автора" in r["pennant"]
    for text in ("Будем на связи", "Подписаться", "Поддержать", "Не сейчас", "VK Backup",
                 "Фото сохранены. О сбоях сервисов и новых программах пишу в канале. "
                 "Программы бесплатные, взнос любой суммы — по желанию.",
                 "Бэкап ВКонтакте: переписки, фото, стена и друзья из официального архива ВК, без пароля."):
        assert text in card, text
    assert "<script>" not in r["html"] and "&quot;&gt;&lt;script&gt;" in card
    anchors = re.findall(r"<a\b[^>]*>", r["html"])
    assert len(anchors) == 5  # канал, взнос, VK Backup в карточке; VK Backup и «Яндекс БК» на «Поддержать»
    for a in anchors:
        assert 'target="_blank"' in a and 'rel="noopener noreferrer"' in a, a
    assert "style=" not in r["html"]
    assert r["missing"] == []


def test_every_action_attribute_has_a_handler():
    sources = [p.read_text(encoding="utf-8") for p in [ASSETS / "app.html", *JS.rglob("*.js")]]
    used = {name for text in sources for name in re.findall(r'data-action="([a-z][a-z0-9-]*)"', text)}
    handled = {name for text in sources for name in re.findall(r"App\.action\('([a-z][a-z0-9-]*)'", text)}
    assert {"flag-toggle", "flag-later", "flag-hide", "promo-own"} <= used
    assert used <= handled, f"нет обработчика: {sorted(used - handled)}"


def test_flag_makes_no_requests_and_keeps_one_key():
    text = FLAG_JS.read_text(encoding="utf-8")
    for call in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "App.api", "new Image"):
        assert call not in text, call
    assert set(re.findall(r"localStorage\.\w+\((\w+)", text)) == {"KEY"}
    assert "const KEY = 'icb.flag';" in text


def test_screens_use_flag_and_keep_beta_feedback():
    html = (ASSETS / "app.html").read_text(encoding="utf-8")
    order = [html.index(f"/static/js/{name}") for name in ("components.js", "flag.js", "screens/done.js")]
    assert order == sorted(order)
    done = (JS / "screens" / "done.js").read_text(encoding="utf-8")
    assert "App.flag.view(s)" in done and "App.flag.visit()" in done
    assert "Программа в тестовом режиме" in done and "done-feedback" in done
    assert "Программа помогла? Поддержите проект" not in done
    support = (JS / "screens" / "support.js").read_text(encoding="utf-8")
    assert "App.flag.promos(s)" in support and "App.flag.privacy()" in support
    # «Яндекс БК» теперь в общем блоке «Другие программы автора», а не отдельной ссылкой
    assert "yadisk_backup" not in support


def test_status_links_include_other_programs(client):
    links = client.get("/api/status").json()["links"]
    assert links["vk_backup"] == "https://github.com/PXSYS13/vk_backup"
    assert links["yadisk_bk"] == "https://github.com/PXSYS13/yadisk_backup"
    assert links["telegram"] == "https://t.me/davronsv" and links["donation"] == "https://dalink.to/davronsvv"
