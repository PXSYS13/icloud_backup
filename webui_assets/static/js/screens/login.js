/* Вход: Apple ID и пароль, затем код подтверждения в том же окне; выход. */
(function () {
  'use strict';
  const App = window.App;
  const { esc, byId } = App;
  const icon = App.icon;

  let mode = null;      // 'creds' | 'code' | 'ok' — что сейчас на экране
  let error = '';
  let errorField = ''; // какое поле не приняли: apple_id | password | code
  let appleId = '';    // введённый Apple ID переживает перерисовку формы после ошибки
  let busy = false;

  function wanted(s) {
    const st = ((s || {}).account || {}).state;
    if (st === 'ok') return 'ok';
    if (st === 'needs_2fa') return 'code';
    return 'creds';
  }

  function render(s) {
    const acc = (s || {}).account || {};
    const box = byId('login-box');
    box.setAttribute('aria-busy', busy ? 'true' : 'false');
    const err = error ? `<div class="err-box" role="alert" id="login-error">${icon('alert')}<span>${esc(error)}</span></div>` : '';
    const invalid = (field) => (error && errorField === field ? ' aria-invalid="true" aria-describedby="login-error"' : '');
    const focusError = () => {
      const field = error && !busy ? box.querySelector(`[data-field="${errorField}"]`) : null;
      if (field) setTimeout(() => field.focus(), 0);
    };
    if (mode === 'ok') {
      box.innerHTML = `<div class="card-head"><span class="icon-box ok">${icon('check')}</span><div class="grow"><h2>Вы вошли</h2>
          <span class="tiny">${esc(acc.apple_id_masked || '')}</span></div></div>
        <p class="lead">Сессия сохранена на этом компьютере: при следующем запуске входить заново не нужно. Пароль программа не хранит.</p>
        <div class="row"><a class="btn primary big" href="#/home">${icon('download')}К скачиванию${icon('arrow')}</a>
          <button class="danger" data-action="logout">${icon('power')}Выйти</button></div>`;
      return;
    }
    if (mode === 'code') {
      box.innerHTML = `<div class="card-head"><span class="icon-box accent">${icon('phone')}</span><div class="grow"><h2>Код подтверждения</h2>
          <span class="tiny">Apple прислал его на айфон и другие ваши устройства Apple</span></div></div>
        <label class="field"><span>Шесть цифр с айфона</span>
          <input type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="7" class="code-input" data-field="code" placeholder="••••••"${invalid('code')} ${busy ? 'disabled' : ''}></label>
        ${err}
        <div class="row mt-sm">
          <button class="primary big" data-action="login-code" ${busy ? 'disabled' : ''}>${icon('check')}${busy ? 'Проверяю…' : 'Подтвердить'}</button>
          <button class="link" data-action="login-restart">Ввести Apple ID заново</button></div>
        <p class="tiny mt-md">Код не пришёл? На айфоне: Настройки → ваше имя → «Пароль и безопасность» → «Получить код подтверждения».
          Если код пришёл с вопросом «Разрешить вход?» — нажмите «Разрешить», и цифры появятся следом.</p>`;
      const input = box.querySelector('[data-field="code"]');
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') box.querySelector('[data-action="login-code"]').click(); });
      if (!busy) setTimeout(() => input.focus(), 50);
      return;
    }
    box.innerHTML = `<div class="card-head"><span class="icon-box accent">${icon('key')}</span><div class="grow"><h2>Войдите в Apple ID</h2>
        <span class="tiny">Тот же, что на айфоне. Пароль уходит только в Apple и нигде не сохраняется</span></div></div>
      <label class="field"><span>Apple ID (электронная почта)</span>
        <input type="email" data-field="apple_id" autocomplete="username" spellcheck="false" placeholder="name@icloud.com" value="${esc(appleId)}"${invalid('apple_id')} ${busy ? 'disabled' : ''}></label>
      <label class="field"><span>Пароль</span>
        <input type="password" data-field="password" autocomplete="current-password"${invalid('password')} ${busy ? 'disabled' : ''}></label>
      ${err}
      <div class="row mt-sm">
        <button class="primary big" data-action="login-submit" ${busy ? 'disabled' : ''}>${icon('arrow')}${busy ? 'Вхожу…' : 'Войти'}</button>
        <a class="btn" href="#/prepare">${icon('phone')}Что включить на айфоне</a></div>
      ${(s || {}).demo ? '<p class="tiny mt-md">Демо: подойдут любой Apple ID и пароль, код подтверждения — 123456. Пароль «wrong» покажет ошибку входа, «denied» — ошибку ACCESS_DENIED.</p>' : ''}`;
    for (const input of box.querySelectorAll('input')) {
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') box.querySelector('[data-action="login-submit"]').click(); });
    }
    focusError();
  }

  function fail(text, field) {
    error = text;
    errorField = field;
    render(App.state.status);
  }

  App.screens.login = {
    mount(el) {
      el.innerHTML = `<a class="back" href="#/home">${icon('back')}На главную</a>
        <div class="page-head"><h1>Вход</h1><p>Apple ID и пароль, затем код подтверждения с айфона — всё здесь, в одном окне.</p></div>
        <div class="card login-card" id="login-box"></div>
        <div class="mt"><div class="banner notice"><span class="b-icon">${icon('shield')}</span><div>
          <h2>Что хранится на компьютере</h2>
          <p>Только сессия — файлы, по которым Apple узнаёт этот компьютер без пароля. Они лежат в папке «session» рядом с программой и удаляются кнопкой «Выйти». Пароль и коды не сохраняются и не попадают в журналы.</p></div></div></div>`;
    },
    show() {
      error = '';
      errorField = '';
      busy = false;
      mode = wanted(App.state.status);
      render(App.state.status);
    },
    update(s) {
      const w = wanted(s);
      if (w !== mode && !busy) {
        // Состояние сменилось снаружи (сессия истекла, вошли в другой вкладке)
        if (mode === 'creds' && w === 'code') return; // ответ на наш запрос придёт сам
        mode = w;
        error = '';
        errorField = '';
        render(s);
      }
    },
  };

  App.action('login-submit', async () => {
    const box = byId('login-box');
    const apple_id = box.querySelector('[data-field="apple_id"]').value.trim();
    const password = box.querySelector('[data-field="password"]').value;
    appleId = apple_id;
    if (!apple_id) { fail('Введите Apple ID — адрес электронной почты.', 'apple_id'); return; }
    if (!password) { fail('Введите пароль.', 'password'); return; }
    busy = true; error = ''; errorField = ''; render(App.state.status);
    const res = await App.api.post('/api/login', { apple_id, password });
    busy = false;
    if (!res.ok) {
      mode = 'creds';
      fail(res.error || 'Не получилось войти', 'password');
      return;
    }
    const st = res.data.state;
    mode = st === 'ok' ? 'ok' : 'code';
    render(App.state.status);
    App.refresh();
    if (st === 'ok') App.toast('Вы вошли', 'ok');
  });

  App.action('login-code', async () => {
    const box = byId('login-box');
    const code = box.querySelector('[data-field="code"]').value.replace(/\D/g, '');
    if (code.length !== 6) { fail('Код состоит из шести цифр.', 'code'); return; }
    busy = true; error = ''; errorField = ''; render(App.state.status);
    const res = await App.api.post('/api/login/code', { code });
    busy = false;
    if (!res.ok) {
      const kind = (res.data || {}).kind;
      mode = kind === 'code' ? 'code' : 'creds';
      fail(res.error || 'Код не подошёл', mode === 'code' ? 'code' : 'password');
      return;
    }
    mode = 'ok';
    render(App.state.status);
    App.toast('Вы вошли. Сессия сохранена', 'ok');
    await App.refresh();
    App.go('#/home');
  });

  App.action('login-restart', () => { mode = 'creds'; error = ''; errorField = ''; render(App.state.status); });
})();
