/* Поддержать проект и «Нас поддерживают»; другие программы автора и флажок «От автора» — из flag.js.
   «Нас поддерживают» — общий список всех программ автора. Он приходит вместе с программой
   (supporters.json) и обновляется с каждой новой версией: экран берёт его у самой программы
   (/api/supporters), в интернет за списком никто не ходит. Вид и тексты — как в «Яндекс БК». */
(function () {
  'use strict';
  const App = window.App;
  const { esc, fmt, byId } = App;

  const PAGE = 9;
  let visible = PAGE;

  // ── Сумма, аватар и дата ──
  const CURRENCY = { RUB: '₽', USD: '$', EUR: '€', KZT: '₸', UAH: '₴', BYN: 'Br' };
  // Неразрывный пробел: «500 ₽» не разрывается на две строки
  const money = (amount, currency) => (amount == null ? '' : `${String(amount).includes('.')
    ? Number(amount).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : fmt.num(amount)} ${CURRENCY[currency] || currency || '₽'}`);
  // Первая буква целиком, даже если это эмодзи из двух половинок
  const initials = (name) => String(name || '?').replace(/\./g, '').split(/\s+/).filter(Boolean).slice(0, 2)
    .map((p) => Array.from(p)[0].toUpperCase()).join('') || '?';
  const avatar = (name, i) => `<span class="avatar t${i % 5}" aria-hidden="true">${esc(initials(name))}</span>`;
  // «сегодня», «вчера», «3 дня назад», «2 недели назад», иначе «12 сентября»
  function dayAgo(iso) {
    if (!iso) return '';
    const [y, mo, d] = iso.split('-').map(Number);
    const then = new Date(y, mo - 1, d);
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const days = Math.round((today - then) / 86400000);
    if (days <= 0) return 'сегодня';
    if (days === 1) return 'вчера';
    if (days < 7) return `${days} ${fmt.plural(days, 'день', 'дня', 'дней')} назад`;
    if (days < 28) {
      const w = Math.floor(days / 7);
      return w === 1 ? 'неделю назад' : `${w} ${fmt.plural(w, 'неделю', 'недели', 'недель')} назад`;
    }
    return then.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', ...(y !== now.getFullYear() ? { year: 'numeric' } : {}) });
  }

  function card(x, i) {
    return `<li class="supporter"><div class="supporter-top">${avatar(x.name, i)}
        <div class="grow"><div class="supporter-name" title="${esc(x.name)}">${esc(x.name)}</div>
          <div class="supporter-when">${esc(dayAgo(x.date))}</div></div>
        ${x.amount != null ? `<div class="supporter-sum">${esc(money(x.amount, x.currency))}</div>` : ''}</div>
      ${x.message ? `<blockquote>«${esc(x.message)}»</blockquote>` : ''}</li>`;
  }

  // «Больше всех помогли»: все донаты человека вместе, суммы в своих валютах
  function topCard(x, i) {
    const sums = (x.sums || []).map((s) => esc(money(s.amount, s.currency))).join(' + ');
    const when = x.donations > 1
      ? `${fmt.num(x.donations)} ${fmt.plural(x.donations, 'донат', 'доната', 'донатов')}, первый — ${esc(dayAgo(x.since))}`
      : esc(dayAgo(x.since));
    // У первого места вместо цифры значок, цифра остаётся для экранного диктора
    const rank = i ? i + 1 : `${App.icon('award')}<span class="sr-only">1</span>`;
    return `<li class="top-supporter"><span class="top-rank r${i + 1}">${rank}</span>
      ${avatar(x.name, i)}<div class="grow"><div class="supporter-name" title="${esc(x.name)}">${esc(x.name)}</div>
        <div class="supporter-when">${when}</div></div><div class="supporter-sum">${sums}</div></li>`;
  }

  function paintTop() {
    const wrap = byId('support-top-wrap');
    if (!wrap) return;
    const sp = App.supporters;
    const top = (sp && sp.top) || [];
    wrap.classList.toggle('hidden', !top.length);
    if (!top.length) return;
    App.text(byId('support-top-note'),
      `Все донаты одного человека складываются. Здесь те, кто поддержал проект на ${money(sp.top_min_rub || 500, 'RUB')} и больше`);
    App.html(byId('support-top'), top.map(topCard).join(''));
  }

  function paintList() {
    paintTop();
    const sp = App.supporters;
    const box = byId('support-list');
    if (!box) return;
    if (!sp) {
      App.html(box, `<div class="empty">${App.icon('hourglass')}Загружаю список…</div>`);
      App.text(byId('support-count'), '');
      App.text(byId('support-updated'), '');
      return;
    }
    const list = sp.supporters || [];
    const people = sp.people || 0;
    App.text(byId('support-count'), people
      ? `Спасибо ${fmt.num(people)} ${fmt.plural(people, 'человеку', 'людям', 'людям')}, которые помогли проекту` : '');
    App.text(byId('support-updated'), sp.updated ? `Последнее обновление списка: ${dayAgo(sp.updated)}` : '');
    if (!list.length) {
      App.html(box, `<div class="empty-box"><span class="icon-box">${App.icon('heart')}</span>
        <p>Здесь появятся имена тех, кто поддержал проект</p></div>`);
      return;
    }
    App.html(box, `<ul class="supporters">${list.slice(0, visible).map(card).join('')}</ul>
      ${visible < list.length ? `<div class="more-row"><button type="button" id="supporters-more" data-action="supporters-more">Показать ещё</button></div>` : ''}`);
  }

  // Список не меняется, пока программа работает: берём его у программы один раз
  App.supporters = null;
  let loading = false;
  async function loadSupporters() {
    if (App.supporters || loading) return;
    loading = true;
    try {
      const res = await App.api.get('/api/supporters');
      if (res.ok) App.supporters = res.data;
    } finally {
      loading = false;
    }
    paintList();
  }

  App.screens.support = {
    mount(el) {
      const I = App.icon;
      el.innerHTML = `<section class="card support-hero">
          <span class="badge-art">${I('heart')}</span>
          <h1>Поддержать проект</h1>
          <p class="sub">Программа бесплатная и открытая. Её делают в свободное время, чтобы люди не потеряли свои фото.
            Если она помогла — можно поддержать автора или просто рассказать о ней знакомым.</p>
          <p class="support-note">Помочь можно и без денег: поставьте звезду проекту на GitHub или сообщите об ошибке.</p>
          <div class="donate-row">
            <div class="donate-main">
              <a class="btn primary big" id="support-donate" href="https://dalink.to/davronsvv" target="_blank" rel="noopener">${I('heart')}Поддержать любой суммой</a>
              <p class="tiny">Или наведите камеру телефона на QR-код — откроется та же страница DonationAlerts.</p>
            </div>
            <img class="donate-qr" src="/static/donate-qr.png" width="132" height="132"
              alt="QR-код: поддержать проект через DonationAlerts (dalink.to/davronsvv)">
          </div>
          <div class="row actions">
            <a class="btn big" id="support-telegram" href="https://t.me/davronsv" target="_blank" rel="noopener">${I('send')}Канал в Telegram</a>
            <a class="btn big" id="support-youtube" href="https://www.youtube.com/@davronsv" target="_blank" rel="noopener">${I('video')}YouTube</a>
            <a class="btn big" id="support-github" href="https://github.com/PXSYS13/icloud_backup" target="_blank" rel="noopener">${I('code')}Проект на GitHub</a>
          </div>
        </section>
        <div id="support-apps"></div>
        <div id="support-flag"></div>
        <section class="hidden" id="support-top-wrap" aria-labelledby="support-top-title">
          <div class="supporters-head"><div><h2 id="support-top-title">Больше всех помогли</h2>
            <p id="support-top-note"></p></div></div>
          <ol class="top-supporters" id="support-top"></ol>
        </section>
        <section aria-labelledby="support-title">
          <div class="supporters-head"><div><h2 id="support-title">Нас поддерживают</h2>
            <p id="support-count"></p></div><span class="tiny" id="support-updated"></span></div>
          <div id="support-list"></div>
          <p class="tiny supporters-note">Список общий для всех программ автора и приходит вместе с программой: новые имена
            появляются с каждой новой версией. Имя и сообщение из доната видны всем — не хотите, подпишитесь «Аноним».
            Спасибо и участникам сообщества, которые помогли с кодом и идеями.</p>
        </section>`;
    },
    show() {
      visible = PAGE;
      paintList();
      loadSupporters();
    },
    update(s) {
      const links = s.links || {};
      if (links.donation) byId('support-donate').href = links.donation;
      if (links.telegram) byId('support-telegram').href = links.telegram;
      if (links.youtube) byId('support-youtube').href = links.youtube;
      if (links.github) byId('support-github').href = links.github;
      App.html(byId('support-apps'), App.flag.promos(s));
      App.html(byId('support-flag'), App.flag.privacy());
    },
  };

  App.action('supporters-more', () => {
    visible += PAGE;
    paintList();
    // Кнопку нарисовали заново: возвращаем на неё фокус, чтобы листать дальше с клавиатуры
    const more = byId('supporters-more');
    if (more) more.focus();
  });
})();
