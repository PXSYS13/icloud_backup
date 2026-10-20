/* Итог: сколько спасено, где лежит, что дальше, как поддержать проект. */
(function () {
  'use strict';
  const App = window.App;
  const { esc, fmt, byId } = App;
  const icon = App.icon;

  App.screens.done = {
    mount(el) {
      el.innerHTML = `<a class="back" href="#/home">${icon('back')}На главную</a>
        <section class="card hero" id="done-hero"></section>
        <div class="mt" id="done-folder"></div>
        <div class="mt"><section class="banner donate"><span class="b-icon">${icon('heart')}</span><div>
          <h2>Программа помогла? Поддержите проект</h2>
          <p>Программа бесплатная, её делает один человек в свободное время, чтобы люди не потеряли свои фото. Любая сумма помогает развивать её дальше.</p>
          <div class="row actions"><a class="btn primary" id="done-donate" href="https://dalink.to/davronsvv" target="_blank" rel="noopener">${icon('heart')}Поддержать проект</a>
            <a class="btn" id="done-telegram" href="https://t.me/davronsv" target="_blank" rel="noopener">${icon('send')}Telegram-канал</a>
            <a class="btn" id="done-yadisk" href="https://github.com/PXSYS13/yadisk_backup" target="_blank" rel="noopener">${icon('hdd')}«Яндекс БК» для Яндекс Диска</a></div></div></section></div>
        <div class="card mt"><h2>Что дальше</h2>
          <div class="links-grid">
            <a href="#/process"><span class="ico">${icon('sliders')}</span><span>Обработка: JPG-копии, дубли, раскладка</span></a>
            <a href="#/yandex"><span class="ico">${icon('cloud')}</span><span>Залить копию в Яндекс Диск</span></a>
            <a href="#/help"><span class="ico">${icon('help')}</span><span>Помощь и частые вопросы</span></a>
          </div>
          <p class="tiny mt-md">Проверьте папку: откройте несколько фото и видео из разных лет. Пока не убедились, что всё на месте, ничего не удаляйте из iCloud.</p></div>`;
    },
    update(s) {
      const lib = s.library || {};
      const links = s.links || {};
      const photos = (lib.photos || 0) + (lib.live || 0);
      const complete = lib.total && (lib.downloaded || 0) >= lib.total && !lib.failed;
      const title = complete ? `Готово: спасено ${fmt.num(photos)} ${fmt.plural(photos, 'фото', 'фото', 'фото')} и ${fmt.num(lib.videos || 0)} ${fmt.plural(lib.videos || 0, 'видео', 'видео', 'видео')} (${fmt.bytes(lib.bytes_done || 0)})`
        : `Скачано ${fmt.num(lib.downloaded || 0)} из ${fmt.filesOf(lib.total || 0)}`;
      const sub = complete ? 'Вся медиатека iCloud Фото лежит на вашем компьютере: оригиналы, Live Photos и видео. В iCloud ничего не изменилось.'
        : lib.failed ? `Не скачались ${fmt.files(lib.failed)}. Вернитесь на главную и нажмите «Повторить».` : 'Скачивание ещё не закончено — продолжите его на главной.';
      App.html(byId('done-hero'), `<div class="hero-top"><span class="pill ${complete ? 'ok' : 'wait'}"><span class="pdot"></span>${complete ? 'Готово' : 'Не всё скачано'}</span></div>
        <div class="hero-body"><div class="hero-art ${complete ? 'ok' : ''}"><span>${icon(complete ? 'check' : 'download')}</span></div>
          <div><h1>${esc(title)}</h1><p class="sub">${esc(sub)}</p>
            <div class="row actions"><button class="primary big" data-action="open-folder" data-which="download">${icon('folder')}Открыть папку</button>
              ${complete ? '' : `<a class="btn big" href="#/home">${icon('back')}На главную</a>`}</div></div></div>
        <div class="stats"><div class="stat ok"><div class="s-label"><span class="s-dot"></span>Фото</div><div class="s-value">${fmt.num(lib.photos || 0)}</div><div class="s-sub">${fmt.bytes(lib.bytes_photos || 0)}</div></div>
          <div class="stat ok"><div class="s-label"><span class="s-dot"></span>Live Photo</div><div class="s-value">${fmt.num(lib.live || 0)}</div><div class="s-sub">${fmt.bytes(lib.bytes_live || 0)}</div></div>
          <div class="stat ok"><div class="s-label"><span class="s-dot"></span>Видео</div><div class="s-value">${fmt.num(lib.videos || 0)}</div><div class="s-sub">${fmt.bytes(lib.bytes_videos || 0)}</div></div>
          <div class="stat"><div class="s-label"><span class="s-dot"></span>Всего</div><div class="s-value">${fmt.bytes(lib.bytes_done || 0)}</div><div class="s-sub">${fmt.files(lib.downloaded || 0)}</div></div></div>`);
      App.html(byId('done-folder'), App.folderCard(s, { editable: false }));
      if (links.donation) byId('done-donate').href = links.donation;
      if (links.telegram) byId('done-telegram').href = links.telegram;
      if (links.yadisk_bk) byId('done-yadisk').href = links.yadisk_bk;
    },
  };
})();
