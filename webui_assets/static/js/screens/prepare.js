/* Подготовка: что включить на айфоне, чтобы Apple отдал фото через веб. */
(function () {
  'use strict';
  const App = window.App;
  const { byId } = App;
  const icon = App.icon;

  const ITEMS = [
    { key: 'adp', title: 'Выключите «Расширенную защиту данных»',
      text: 'С этой защитой Apple шифрует фото так, что через веб их не отдаёт никому — и программа получит ошибку ACCESS_DENIED. После скачивания защиту можно включить обратно.',
      path: 'Настройки → ваше имя → iCloud → «Расширенная защита данных» → Выключить' },
    { key: 'web', title: 'Включите доступ к данным iCloud через интернет',
      text: 'Программа работает так же, как сайт icloud.com. Без этого переключателя Apple не пускает к фото ни сайт, ни программу.',
      path: 'iOS до 26.3: Настройки → ваше имя → iCloud → «Доступ к данным iCloud в интернете». iOS 26.4 и новее: Настройки → ваше имя → iCloud → iCloud.com → «Разрешить доступ к данным»' },
    { key: 'wait', title: 'Подождите пару минут и держите айфон рядом',
      text: 'Настройки доходят до серверов Apple не сразу. При входе Apple пришлёт на айфон код из шести цифр — его нужно будет ввести в программе.',
      path: '' },
  ];
  const checked = new Set();

  function render() {
    const list = ITEMS.map((it) => `<li>
        <input type="checkbox" class="cbx" data-key="${it.key}" ${checked.has(it.key) ? 'checked' : ''} aria-label="${App.esc(it.title)}">
        <span><b>${App.esc(it.title)}</b><p>${App.esc(it.text)}</p>${it.path ? `<span class="path">${App.esc(it.path)}</span>` : ''}</span></li>`).join('');
    byId('prepare-list').innerHTML = list;
    const all = ITEMS.every((it) => checked.has(it.key));
    byId('prepare-next').innerHTML = `<button class="${all ? 'primary' : ''} big" data-action="go-login">${icon('key')}Всё готово — войти в Apple ID${icon('arrow')}</button>` +
      (all ? '' : '<span class="tiny">Отметьте пункты, когда сделаете. Можно войти и так, но без них Apple не отдаст фото.</span>');
  }

  App.screens.prepare = {
    mount(el) {
      el.innerHTML = `<a class="back" href="#/home">${icon('back')}На главную</a>
        <div class="page-head"><h1>Подготовьте айфон</h1>
          <p>Два переключателя в настройках iCloud. Без них Apple не отдаёт фото через веб и отвечает ошибкой ACCESS_DENIED. Это займёт минуту.</p></div>
        <div class="card"><ul class="checklist" id="prepare-list"></ul>
          <div class="row" id="prepare-next" style="margin-top:20px"></div></div>
        <div class="mt"><div class="banner notice"><span class="b-icon">${icon('shield')}</span><div>
          <h3>В iCloud ничего не изменится</h3>
          <p>Программа только читает медиатеку и сохраняет файлы на этот компьютер. Удалить или изменить что-то в iCloud она не может — таких команд в ней нет.</p></div></div></div>`;
      el.querySelector('#prepare-list').addEventListener('change', (e) => {
        const key = e.target.dataset.key;
        if (!key) return;
        if (e.target.checked) checked.add(key); else checked.delete(key);
        render();
      });
      render();
    },
    update() {},
  };
})();
