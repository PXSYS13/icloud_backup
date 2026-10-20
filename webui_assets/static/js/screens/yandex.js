/* Яндекс Диск: ключ доступа и загрузка копии с той же структурой папок. */
(function () {
  'use strict';
  const App = window.App;
  const { esc, fmt, byId } = App;
  const icon = App.icon;

  const POLYGON_URL = 'https://yandex.ru/dev/disk/poligon/';
  const GUIDE_URL = 'https://github.com/PXSYS13/yadisk_backup/blob/main/INSTRUCTION_GET_TOKEN.md';
  let check = null;   // последняя проверка ключа {ok, login, used, total, free}

  async function loadCheck(s) {
    const cfg = (s || {}).config || {};
    if (!cfg.ya_token_set) { check = null; return; }
    const res = await App.api.get('/api/yandex/check');
    check = res.data || null;
  }

  function tokenRow(s) {
    const cfg = s.config || {};
    if (cfg.ya_token_set) {
      const acc = check && check.ok ? `Подключён: ${esc(check.login || '')}, свободно ${fmt.bytes(check.free || 0)} из ${fmt.bytes(check.total || 0)}`
        : check && check.unreachable ? 'Яндекс сейчас не ответил на проверку — ключ, скорее всего, в порядке'
          : check && !check.ok ? esc(check.msg || 'Яндекс не принял ключ') : `Ключ сохранён (${esc(cfg.ya_token_masked)})`;
      return `<div class="state"><span class="dot ${check && check.ok ? 'ok' : check && !check.unreachable ? 'err' : ''}"></span><span>${acc}</span></div>
        <div class="row mt-sm"><button class="small" data-action="toggle-token-form">Заменить ключ</button>
          <button class="small danger" data-action="token-clear">Удалить ключ</button></div>`;
    }
    return `<div class="state"><span class="dot"></span><span>Ключ не задан — без него загрузка невозможна</span></div>
      <div class="row mt-sm"><button class="small primary" data-action="toggle-token-form">${icon('key')}Ввести ключ</button></div>`;
  }

  App.screens.yandex = {
    mount(el) {
      el.innerHTML = `<div class="page-head"><h1>Яндекс Диск</h1>
          <p>По желанию: копия скачанного в папке «iCloud БК» на вашем Диске, с теми же папками по годам и месяцам.</p></div>
        <div id="ya-job"></div>
        <div class="stack">
          <div class="banner safety"><span class="b-icon">${icon('info')}</span><div>
            <h2>Загрузка занимает обычное место на Диске</h2>
            <p>Файлы, которые заливает программа, считаются как любые другие и занимают место вашего тарифа. В безлимит для фото попадает только
              автозагрузка из мобильного приложения Яндекс Диска. Перед загрузкой программа проверит, хватит ли места.</p></div></div>
          <div class="card"><div class="card-head"><span class="icon-box">${icon('key')}</span><div class="grow"><h2>Ключ доступа к Диску</h2>
              <span class="tiny">Тот же, что в «Яндекс БК». Хранится только на этом компьютере</span></div></div>
            <div id="ya-token"></div>
            <div class="access-form hidden mt-md" id="ya-token-form">
              <ol class="small muted">
                <li>Нажмите «Открыть страницу Яндекса» и войдите в аккаунт, на Диск которого заливаете.</li>
                <li>Нажмите там «Получить OAuth-токен» и скопируйте длинную строку целиком.</li>
                <li>Вставьте её сюда и нажмите «Проверить и сохранить».</li></ol>
              <div class="row mt-sm"><a class="btn" href="${POLYGON_URL}" target="_blank" rel="noopener">${icon('external')}Открыть страницу Яндекса</a>
                <a class="btn ghost" href="${GUIDE_URL}" target="_blank" rel="noopener">${icon('book')}Инструкция с картинками</a></div>
              <label class="field"><span>Ключ доступа (токен)</span>
                <input type="password" data-field="token-input" autocomplete="off" spellcheck="false" placeholder="y0_…"></label>
              <div class="err-box hidden" role="alert" id="token-error" data-form-error>${icon('alert')}<span></span></div>
              <div class="row"><button class="primary" data-action="token-save">${icon('check')}Проверить и сохранить</button></div></div></div>
          <div class="card"><div class="card-head"><span class="icon-box accent">${icon('cloud')}</span><div class="grow"><h2>Загрузить копию</h2>
              <span class="tiny" id="ya-sub"></span></div></div>
            <p class="lead">Программа пройдёт по папке с фото, пропустит файлы, которые на Диске уже есть (сверяет по содержимому), и зальёт остальное
              в «iCloud БК». Папки «_duplicates» и «_heic_originals» не заливаются. Если Яндекс попросит обращаться реже (ошибка 429), программа подождёт минуту и продолжит.</p>
            <div class="row"><button class="primary big" data-action="process-start" data-kind="yandex" id="ya-start" aria-describedby="ya-note">${icon('cloud-down')}Загрузить в Яндекс Диск</button>
              <span class="tiny" id="ya-note"></span></div></div>
        </div>`;
    },
    show() { loadCheck(App.state.status).then(() => App.render()); },
    update(s) {
      const cfg = s.config || {};
      const lib = s.library || {};
      const busy = (s.pipeline || {}).running || (s.job || {}).running;
      App.html(byId('ya-token'), tokenRow(s));
      App.html(byId('ya-job'), App.jobCard(s.job, ['yandex']));
      byId('ya-start').disabled = busy || !cfg.ya_token_set || !lib.downloaded;
      App.text(byId('ya-sub'), lib.downloaded ? `Скачано ${fmt.files(lib.downloaded)} · ${fmt.bytes(lib.bytes_done || 0)}` : 'Сначала скачайте фото');
      App.text(byId('ya-note'), !cfg.ya_token_set ? 'Сначала добавьте ключ доступа' : !lib.downloaded ? 'Сначала скачайте фото'
        : busy ? 'Дождитесь окончания скачивания или другой задачи' : '');
    },
  };

  App.action('toggle-token-form', () => {
    const f = byId('ya-token-form');
    f.classList.toggle('hidden');
    if (!f.classList.contains('hidden')) f.querySelector('input').focus();
  });
  App.action('token-save', (btn) => App.run(btn, async () => {
    const form = byId('ya-token-form');
    const input = form.querySelector('[data-field="token-input"]');
    const token = input.value.trim();
    const res = token ? await App.api.post('/api/config/token', { token })
      : { ok: false, error: 'Вставьте ключ — длинную строку со страницы Яндекса' };
    App.formError(form, input, res.ok ? '' : res.error);
    if (res.ok) {
      input.value = '';
      form.classList.add('hidden');
      check = (res.data || {}).check || null;
    }
    return res;
  }));
  App.action('token-clear', async (btn) => {
    const yes = await App.confirm({ title: 'Удалить ключ доступа к Диску?', ok: 'Удалить', danger: true,
      text: 'Загрузка в Яндекс Диск станет недоступна, пока вы не введёте ключ снова. Скачанные файлы останутся.' });
    if (yes) { check = null; App.run(btn, () => App.api.post('/api/config/token/clear')); }
  });
})();
