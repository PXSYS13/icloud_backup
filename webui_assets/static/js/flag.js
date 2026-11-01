/* Флажок «От автора» и «Другие программы автора» (как в VK Backup). Одно предложение «Будем на связи»:
   канал, где автор пишет о сбоях сервисов, и взнос, на который живут бесплатные программы.
   Без всплывающих окон: карточка сама открыта на итогах после скачивания и потом успокаивается.
   Программа по этим ссылкам ничего не запрашивает, открывает их сам человек; что флажок уже
   показан — помнит только браузер, в одном ключе localStorage, без отметки «на что нажали». */
(function () {
  'use strict';
  const App = window.App;
  const { esc } = App;
  const icon = App.icon;

  const KEY = 'icb.flag';
  const VISITS = 3;          // столько заходов на итоги карточка открыта сама, дальше — только флажок
  const AGAIN_DAYS = 30;     // после нового скачивания покажем снова не раньше, чем через месяц
  const AGAIN_CLICKED = 90;  // …а если человек уже переходил по ссылке из карточки — через три
  const DAY_MS = 864e5;

  const PROMOS = {
    vk: { link: 'vk_backup', icon: 'users', name: 'VK Backup',
      text: 'Бэкап ВКонтакте: переписки, фото, стена и друзья из официального архива ВК, без пароля.' },
    yadisk: { link: 'yadisk_bk', icon: 'hdd', name: '«Яндекс БК»',
      text: 'Сохраняет весь Яндекс.Диск на компьютер и работает, даже когда Яндекс недоступен.' },
  };

  // ── Что помнит браузер: время показа и отметки, никаких «на что нажали» ──
  // Браузер не даёт localStorage (запрещены данные сайтов) — помним до перезагрузки страницы
  let memory = {};
  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      const s = raw === null ? memory : JSON.parse(raw);
      return s && typeof s === 'object' ? { ...s } : {};
    } catch (e) {
      return { ...memory };
    }
  }
  function save(patch) {
    const s = { ...load(), ...patch };
    memory = s;
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* хватит памяти страницы */ }
    return s;
  }

  // Скачивание медиатеки закончилось целиком и сейчас ничего не идёт: только тогда уместно о чём-то
  // просить. Номер скачивания — время его конца (pipeline.finished_at). После перезапуска программа
  // это время забывает, и за него сходит то, что помнит браузер
  function finishedId(s) {
    const pipe = (s && s.pipeline) || {}, lib = (s && s.library) || {}, job = (s && s.job) || {};
    if (pipe.running || job.running || pipe.error || job.error) return null;
    if (!lib.total || (lib.downloaded || 0) < lib.total || lib.failed) return null;
    if (pipe.state === 'done' && pipe.mode === 'download' && pipe.finished_at) return pipe.finished_at;
    return pipe.state === 'idle' || pipe.state === 'done' ? load().lastBackupId || null : null;
  }

  // Закончилось новое скачивание: пора ли снова раскрыть карточку. Пауза считается от прошлого
  // полного показа до конца этого скачивания — просто зайти через месяц, без нового скачивания, мало
  function noteDownload(id) {
    const f = load();
    if (f.hidden || f.lastBackupId === id) return;
    const pause = (f.clickedAt ? AGAIN_CLICKED : AGAIN_DAYS) * DAY_MS;
    if (f.lastFullAt && id * 1000 < f.lastFullAt + pause) return;
    save({ lastFullAt: Date.now(), lastBackupId: id, visits: 0, expanded: null, dismissed: null, clickedAt: null });
  }

  function noteVisit(id) {
    const f = load();
    if (f.lastBackupId === id) save({ visits: (f.visits || 0) + 1 });
  }

  function isOpen(id) {
    const f = load();
    if (f.hidden) return false;
    if (f.expanded === id) return true;
    if (f.lastBackupId !== id || f.dismissed === id) return false;
    return (f.visits || 0) <= VISITS;
  }

  // Перерисовать экран и вернуть фокус туда, где он был по смыслу: кнопку пересоздаёт App.html
  function redraw(focus) {
    App.render();
    const el = focus && document.querySelector(focus);
    if (el) el.focus();
  }

  // ── Куски для экранов ──
  const link = (url, label, cls, ico = '', flagLink = false) => (url ? `<a class="${cls}"${flagLink ? ' data-flag-link' : ''}
    href="${esc(url)}" target="_blank" rel="noopener noreferrer">${ico ? icon(ico) : ''}${esc(label)}</a>` : '');

  function promo(key, links) {
    const p = PROMOS[key];
    if (!p || (load().owned || {})[key]) return '';
    return `<div class="app-promo" data-promo="${esc(key)}"><span class="icon-box accent">${icon(p.icon)}</span>
      <div class="promo-text"><b>${esc(p.name)}</b><div class="tiny">${esc(p.text)}</div></div>
      <div class="row promo-actions">${link(links[p.link], 'Скачать', 'btn small')}
        <button type="button" class="small ghost" data-action="promo-own" data-app="${esc(key)}">Уже есть</button></div></div>`;
  }

  let visitPending = false; // зашли на итоги: засчитать заход, когда станет известно скачивание

  App.flag = {
    KEY,
    load,
    finishedId,
    visit() { visitPending = true; },

    // Флажок у заголовка итогов и карточка «Будем на связи» — пока идёт скачивание или после ошибки пусто
    view(s) {
      const id = finishedId(s);
      if (!id || load().hidden) return { pennant: '', card: '' };
      noteDownload(id);
      if (visitPending) { noteVisit(id); visitPending = false; }
      const f = load();
      const open = isOpen(id);
      const glow = open && (f.visits || 0) <= 1 && !f.expanded && !App.reducedMotion();
      const pennant = `<button type="button" class="pennant${glow ? ' glow' : ''}" data-action="flag-toggle"
        aria-expanded="${open}"${open ? ' aria-controls="flag-card"' : ''}>${icon('flag')}От автора</button>`;
      if (!open) return { pennant, card: '' };
      const links = (s && s.links) || {};
      const card = `<section class="banner donate flag-card" id="flag-card" aria-labelledby="flag-card-title">
        <span class="b-icon">${icon('send')}</span><div>
          <div class="flag-head"><div class="grow"><h2 id="flag-card-title">Будем на связи</h2>
            <span class="tiny">От автора программы</span></div>
            <button type="button" class="small ghost" data-action="flag-later">Не сейчас</button></div>
          <p>Фото сохранены. О сбоях сервисов и новых программах пишу в канале. Программы бесплатные, взнос любой суммы — по желанию.</p>
          <div class="row actions">${link(links.telegram, 'Подписаться', 'btn primary', 'send', true)}${link(links.donation, 'Поддержать', 'btn', 'heart', true)}</div>
          ${promo('vk', links)}</div></section>`;
      return { pennant, card };
    },

    // «Другие программы автора» на экране «Поддержать»; «Уже есть» убирает карточку насовсем
    promos(s) {
      const links = (s && s.links) || {};
      const cards = Object.keys(PROMOS).map((key) => promo(key, links)).join('');
      return cards ? `<div class="card mt"><h2>Другие программы автора</h2><div class="promos">${cards}</div></div>` : '';
    },

    privacy() {
      const hidden = !!load().hidden;
      return `<div class="card mt"><h2>Без слежки</h2>
        <p class="lead">Программа никуда не сообщает, нажали ли вы на ссылку. Ссылки открываются в вашем браузере, а сама она
          к этим сайтам не обращается. Что флажок «От автора» показали и закрыли, помнит только этот браузер.</p>
        <div class="row"><button type="button" class="small flag-hide" data-action="flag-hide" aria-pressed="${hidden}">
          ${icon('flag')}Скрыть флажок</button></div></div>`;
    },
  };

  App.action('flag-toggle', () => {
    const id = finishedId(App.state.status);
    if (!id) return;
    save(isOpen(id) ? { expanded: null, dismissed: id } : { expanded: id });
    redraw('.pennant');
  });
  App.action('flag-later', () => {
    const id = finishedId(App.state.status);
    if (!id) return;
    save({ expanded: null, dismissed: id });
    redraw('.pennant');
  });
  App.action('flag-hide', () => {
    save({ hidden: !load().hidden });
    redraw('[data-action="flag-hide"]');
  });
  App.action('promo-own', (btn) => {
    const key = btn.dataset.app;
    if (!PROMOS[key]) return;
    save({ owned: { ...(load().owned || {}), [key]: true } });
    redraw();
  });

  // Перешли в канал или к взносу из карточки — значит, увидели: карточка сворачивается, а снова
  // откроется сама не раньше чем через три месяца. Запоминаем только время, не то, куда нажали.
  // Ссылки на экране «Поддержать» и в других местах флажок не трогают
  document.addEventListener('click', (ev) => {
    const a = ev.target.closest && ev.target.closest('.flag-card a[data-flag-link]');
    const id = a && finishedId(App.state.status);
    if (!id) return;
    save({ clickedAt: Date.now(), expanded: null, dismissed: id });
    setTimeout(() => redraw('.pennant'), 0); // сначала браузер откроет ссылку, потом карточка уйдёт
  });
})();
