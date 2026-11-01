/* Поддержать проект; другие программы автора и флажок «От автора» — из flag.js. */
(function () {
  'use strict';
  const App = window.App;
  const { byId } = App;

  App.screens.support = {
    mount(el) {
      const I = App.icon;
      el.innerHTML = `<section class="card support-hero">
          <span class="badge-art">${I('heart')}</span>
          <h1>Поддержать проект</h1>
          <p class="sub">Программа бесплатная и открытая. Её делают в свободное время, чтобы люди не потеряли свои фото.
            Если она помогла — можно поддержать автора или просто рассказать о ней знакомым.</p>
          <p class="support-note">Помочь можно и без денег: поставьте звезду проекту на GitHub или сообщите об ошибке.</p>
          <div class="row actions">
            <a class="btn primary big" id="support-donate" href="https://dalink.to/davronsvv" target="_blank" rel="noopener">${I('heart')}Поддержать любой суммой</a>
            <a class="btn big" id="support-telegram" href="https://t.me/davronsv" target="_blank" rel="noopener">${I('send')}Канал в Telegram</a>
            <a class="btn big" id="support-youtube" href="https://www.youtube.com/@davronsv" target="_blank" rel="noopener">${I('video')}YouTube</a>
            <a class="btn big" id="support-github" href="https://github.com/PXSYS13/icloud_backup" target="_blank" rel="noopener">${I('code')}Проект на GitHub</a>
          </div>
        </section>
        <div id="support-apps"></div>
        <div id="support-flag"></div>`;
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
})();
