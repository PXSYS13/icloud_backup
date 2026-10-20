/* Помощь: частые вопросы, отчёт для поддержки, журналы, выход и «забыть список». */
(function () {
  'use strict';
  const App = window.App;
  const { esc, fmt, byId } = App;

  const FAQ = [
    ['Код подтверждения не приходит',
      'Попросите код вручную: на айфоне Настройки → ваше имя → «Пароль и безопасность» → «Получить код подтверждения». Код приходит и на другие ваши устройства Apple (iPad, Mac). Если на айфоне появился вопрос «Разрешить вход?», нажмите «Разрешить» — цифры появятся следом. Подождите минуту и попробуйте снова.'],
    ['Ошибка ACCESS_DENIED — Apple не даёт доступ',
      'Apple не отдаёт фото через веб. На айфоне выключите «Расширенную защиту данных» (Настройки → ваше имя → iCloud) и включите доступ к данным iCloud через интернет (там же; в iOS 26.4 и новее — в пункте iCloud.com → «Разрешить доступ к данным»). Подождите несколько минут, выйдите из программы кнопкой «Выйти» и войдите снова.'],
    ['Что такое «Расширенная защита данных» и зачем её выключать',
      'Это режим, в котором ключи шифрования фото хранятся только на ваших устройствах: Apple тогда не может отдать фото ни сайту icloud.com, ни программе. Выключение не удаляет фото. После скачивания защиту можно включить обратно.'],
    ['Пишет «Сессия истекла»',
      'Apple ограничивает срок входа — обычно около двух месяцев, но бывает и раньше. Нажмите «Войти снова», введите пароль и код. Скачивание продолжится с того же места: уже скачанное помнится в базе программы.'],
    ['Подписка iCloud+ закончилась или хранилище переполнено. Можно ли скачать фото?',
      'Да. Apple пишет, что данные остаются доступны после окончания подписки и фото можно скачивать. Новые фото с айфона в iCloud при этом не выгружаются, но то, что уже там, программа скачает.'],
    ['Файлы HEIC не открываются в Windows',
      'В разделе «Обработка» выберите «JPG-копии» или «Оба» и нажмите «Сделать JPG-копии сейчас». Другой путь — установить из Microsoft Store «Расширения для изображений HEIF». Оригиналы HEIC программа не удаляет.'],
    ['Что с Live Photos',
      'Live Photo лежит двумя файлами рядом: фото (HEIC или JPG) и его видеочасть с тем же именем и расширением .MOV. Просмотрщики на компьютере показывают фото, видео можно открыть отдельно.'],
    ['Места на диске не хватает',
      'Выберите папку на другом диске, например на внешнем: на главной в карточке «Папка для фото» → «Сменить папку». Уже скачанное останется в старой папке и второй раз качаться не будет. Если место закончится по ходу, программа остановится и подождёт.'],
    ['Почему загрузка в Яндекс Диск занимает место на Диске',
      'Безлимит для фото Яндекс даёт только автозагрузке из своего мобильного приложения. Всё, что заливается программой или через сайт, занимает обычное место тарифа.'],
    ['Где правки, которые я делал на айфоне',
      'Программа скачивает оригиналы — так, как снято. Отредактированные версии (кадрирование, фильтры) в этой версии не скачиваются. Это написано и в описании программы.'],
    ['Можно закрыть браузер или выключить компьютер',
      'Браузер — можно: программа работает в чёрном окне, его закрывать нельзя. Если выключили компьютер — просто запустите снова: всё скачанное помнится, программа продолжит с того же места.'],
    ['Это безопасно',
      'Программа работает только на вашем компьютере (адрес 127.0.0.1), никуда ничего не отправляет, кроме серверов Apple и, по вашему желанию, Яндекса. Пароль не сохраняется, сессия удаляется кнопкой «Выйти». В iCloud она только читает. Код открыт.'],
  ];

  let logsLoaded = false;

  App.screens.help = {
    mount(el) {
      const I = App.icon;
      const head = (ic, cls, title, sub) => `<div class="card-head"><span class="icon-box ${cls}">${I(ic)}</span>
        <div class="grow"><h2>${title}</h2>${sub ? `<span class="tiny">${sub}</span>` : ''}</div></div>`;
      el.innerHTML = `<div class="page-head"><h1>Помощь</h1><p>Ответы на частые вопросы и как получить помощь.</p></div>
        <div class="stack">
        <div class="card faq">${head('help', '', 'Частые вопросы', '')}
          ${FAQ.map(([q, a]) => `<details><summary>${esc(q)}</summary><div class="body"><p>${esc(a)}</p></div></details>`).join('')}</div>
        <div class="card">${head('lifebuoy', 'ok', 'Нужна помощь', '')}
          <p class="lead">Если что-то не работает — скачайте отчёт и пришлите его в Telegram. В отчёте журналы и состояние программы, но нет паролей, кодов и ключей.</p>
          <div class="row">
            <a class="btn primary" href="/api/logs/bundle">${I('download')}Скачать отчёт для поддержки</a>
            <a class="btn" id="help-telegram" href="https://t.me/davronsv" target="_blank" rel="noopener">${I('send')}Написать в Telegram</a>
            <a class="btn" id="help-github" href="https://github.com/PXSYS13/icloud_backup/issues" target="_blank" rel="noopener">${I('code')}Вопрос на GitHub</a>
          </div></div>
        <div class="card"><details id="help-logs-box"><summary>Журналы программы — для поддержки</summary><div class="body">
          <div class="row" style="margin-bottom:10px"><select id="help-log-select" style="max-width:360px"></select>
            <button data-action="help-log-show">Показать</button></div>
          <div class="console" id="help-log"></div></div></details></div>
        <div class="card">${head('alert', 'wait', 'Если что-то пошло не так', 'Нужно редко. Скачанные файлы не удаляются')}
          <details><summary>Забыть список файлов</summary><div class="body">
            <p class="small">Программа забудет, что уже скачала, и при следующем запуске соберёт список заново. Файлы в папке не удаляются, и то, что лежит на месте с тем же размером, второй раз качаться не будет. Файлы, которые вы перенесли из папки, скачаются заново.</p>
            <div class="row" style="margin-top:12px"><button class="danger" data-action="forget-list">Забыть список файлов</button></div></div></details>
          <details><summary>Выйти из Apple ID</summary><div class="body">
            <p class="small">Удаляет сессию с этого компьютера. Чтобы продолжить, нужно будет войти снова.</p>
            <div class="row" style="margin-top:12px"><button class="danger" data-action="logout">Выйти</button></div></div></details></div>
        </div>`;
      byId('help-logs-box').addEventListener('toggle', (e) => { if (e.target.open && !logsLoaded) loadLogs(); });
    },
    update(s) {
      const links = s.links || {};
      if (links.telegram) byId('help-telegram').href = links.telegram;
      if (links.github) byId('help-github').href = links.github + '/issues';
    },
  };

  async function loadLogs() {
    const res = await App.api.get('/api/logs');
    if (!res.ok) return;
    logsLoaded = true;
    App.html(byId('help-log-select'), (res.data.logs || []).map((l) =>
      `<option value="${esc(l.name)}">${esc(l.name)} · ${fmt.bytes(l.size)}</option>`).join(''));
  }

  App.action('help-log-show', async (btn) => {
    const name = byId('help-log-select').value;
    if (!name) return;
    btn.disabled = true;
    const res = await App.api.get(`/api/logs/tail?name=${encodeURIComponent(name)}&lines=300`);
    btn.disabled = false;
    App.text(byId('help-log'), res.ok ? (res.data.lines || []).join('\n') : res.error);
  });

  App.action('forget-list', async (btn) => {
    const yes = await App.confirm({ title: 'Забыть список файлов?', ok: 'Забыть список', danger: true,
      text: 'Скачанные файлы останутся в папке. При следующем запуске программа соберёт список заново и не будет качать то, что уже лежит на месте.' });
    if (yes) App.run(btn, () => App.api.post('/api/forget_list'));
  });
})();
