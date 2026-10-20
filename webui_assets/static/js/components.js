/* Общие части экранов: подключения в меню, кольцо прогресса, лента событий,
   последние файлы, «Где мои файлы», папка для фото, баннеры, общие действия. */
(function () {
  'use strict';
  const App = window.App;
  const { esc, fmt } = App;
  const icon = App.icon;

  const KIND_LABEL = { photo: 'фото', video: 'видео', live: 'Live Photo' };
  const KIND_ICON = { photo: 'image', video: 'video', live: 'image' };
  App.KIND_LABEL = KIND_LABEL;

  // ── Подключения в боковом меню: Apple ID и папка ──
  App.renderHeader = (s, offline) => {
    const chips = App.byId('chips');
    if (!chips) return;
    if (offline || !s) {
      App.html(chips, `<div class="chip err"><span class="chip-label"><span class="dot"></span>${
        offline ? 'Программа не отвечает' : 'Подключаюсь…'}</span>
        <span class="chip-value">${offline ? 'окно программы закрыто?' : 'секунду'}</span></div>`);
      return;
    }
    const acc = s.account || {};
    const apple = acc.state === 'ok' ? ['ok', acc.apple_id_masked || 'вошли']
      : acc.state === 'needs_2fa' ? ['warn', 'нужен код подтверждения']
        : acc.restoring ? ['', 'проверяю сессию…'] : ['', 'не вошли'];
    const cfg = s.config || {};
    const folder = cfg.download_dir_set ? ['ok', cfg.download_dir || ''] : ['warn', 'папка по умолчанию'];
    const chip = ([cls, value], label, route) =>
      `<a class="chip ${cls}" href="${route}" title="${esc(`${label}: ${value}`)}">` +
      `<span class="chip-label"><span class="dot"></span>${label}</span>` +
      `<span class="chip-value">${esc(label === 'Папка' ? fmt.shortPath(value, 30) : value)}</span></a>`;
    App.html(chips, chip(apple, 'Apple ID', '#/login') + chip(folder, 'Папка', '#/home'));
    App.text(App.byId('brand-version'), `версия ${s.app_version || ''}${s.demo ? ' · демо' : ''}`);
    App.text(App.byId('footer-text'), `iCloud БК ${s.app_version || ''}`);
  };

  // ── Кольцо прогресса ──
  App.ring = ({ done = 0, fail = 0, total = 0, label = '', caption = '', spin = false }) => {
    const R = 54, C = 2 * Math.PI * R, MIN = 3;
    const circle = (cls, len, start) =>
      `<circle class="${cls}" cx="64" cy="64" r="${R}" stroke-dasharray="${len.toFixed(1)} ${C.toFixed(1)}" stroke-dashoffset="${(-start).toFixed(1)}"/>`;
    let arcs = '';
    if (spin) {
      arcs = circle('arc-spin', C * 0.22, 0);
    } else if (total > 0) {
      const parts = [['arc-done', done], ['arc-fail', fail]].filter(([, n]) => n > 0);
      const lens = parts.map(([, n]) => Math.max(MIN, Math.min(1, n / total) * C));
      const sum = lens.reduce((a, b) => a + b, 0);
      if (sum > C) lens[lens.indexOf(Math.max(...lens))] -= sum - C;
      const gap = parts.length > 1 ? 1.5 : 0;
      let start = 0;
      arcs = parts.map(([cls], i) => { const out = circle(cls, Math.max(0, lens[i] - gap), start); start += lens[i]; return out; }).join('');
    }
    const m = /^(.*\d)%$/.exec(label);
    const big = m ? `${esc(m[1])}<span class="pct-sign">%</span>` : esc(label);
    const long = String(m ? m[1] : label).length > 4 ? ' long' : '';
    return `<div class="ring" role="img" aria-label="${esc(`${label} ${caption}`)}">
      <svg viewBox="0 0 128 128"><circle class="track" cx="64" cy="64" r="${R}"/>${arcs}</svg>
      <div class="center"><div class="pct${long}">${big}</div><div class="cap">${esc(caption)}</div></div></div>`;
  };

  // ── Лента «Что происходит» ──
  App.feedCard = (events) => {
    const items = (events || []).slice(0, 8).map((e) => `<li class="${esc(e.kind)}">
        <time>${fmt.hhmm(e.at)}</time><span class="f-dot"></span>
        <div class="f-text">${esc(e.text)}</div></li>`).join('');
    return `<div class="card"><div class="card-head"><div class="grow"><h3>Что происходит</h3>
      <span class="tiny">Главные события, новые сверху</span></div></div>
      ${items ? `<ul class="feed">${items}</ul>` : `<div class="empty">${icon('hourglass')}Событий пока нет</div>`}</div>`;
  };

  // ── Последние скачанные файлы ──
  App.recentCard = (list, now) => {
    now = now || Date.now() / 1000;
    const rows = (list || []).slice(0, 6).map((f) => {
      const when = f.at ? (now - f.at < 3600 ? fmt.ago(now - f.at, f.at) : fmt.hhmm(f.at)) : '';
      return `<li><span class="f-ico">${icon(KIND_ICON[f.kind] || 'file')}</span>
        <div class="f-main"><span class="f-name" title="${esc(f.name)}">${esc(f.name)}</span>
        <span class="f-meta">${fmt.bytes(f.size)} · ${KIND_LABEL[f.kind] || ''}</span></div>
        ${when ? `<span class="f-time">${esc(when)}</span>` : ''}</li>`;
    }).join('');
    if (!rows) return '';
    return `<div class="card"><div class="card-head"><div class="grow"><h3>Последние скачанные</h3>
      <span class="tiny">Эти файлы уже лежат в вашей папке</span></div></div><ul class="files">${rows}</ul></div>`;
  };

  // ── Папка для фото: где лежат файлы и как сменить папку ──
  App.folderCard = (s, { editable = true } = {}) => {
    const cfg = s.config || {};
    const dir = cfg.download_dir || '';
    const fsp = s.free_space || {};
    const free = fsp.free != null ? `свободно ${fmt.bytes(fsp.free)}` : '';
    const fat = fsp.fat ? `<p class="tiny" style="margin-top:8px;color:var(--warn)">Диск в формате ${esc(fsp.fs || 'FAT32')}: файлы больше 4 ГБ на него не запишутся. Для больших видео нужен NTFS или exFAT.</p>` : '';
    const busy = (s.pipeline || {}).running || (s.job || {}).running;
    const note = cfg.download_dir_set ? '' : '<p class="tiny" style="margin-top:8px">Это папка по умолчанию внутри папки программы. Лучше выбрать папку на диске, где хватит места, например на внешнем.</p>';
    const layout = cfg.by_month ? 'по годам и месяцам: 2023/2023-07' : 'все файлы в одной папке';
    return `<div class="card" id="folder-card"><div class="card-head"><span class="icon-box">${icon('folder')}</span>
      <div class="grow"><h3>Папка для фото</h3><span class="tiny">${esc(free)}${free ? ' · ' : ''}${layout}</span></div></div>
      <div class="path-box" title="${esc(dir)}">${icon('hdd')}<code>${esc(dir)}</code></div>${note}${fat}
      <div class="row" style="margin-top:14px;gap:8px">
        <button class="small" data-action="open-folder" data-which="download">${icon('folder')}Открыть папку</button>
        <button class="small ghost" data-action="copy-path" data-path="${esc(dir)}">${icon('copy')}Скопировать путь</button>
        ${editable ? (busy ? '<span class="tiny">Сменить папку можно после остановки скачивания</span>'
          : `<button class="small ghost" data-action="toggle-folder-form">${icon('sliders')}Сменить папку</button>`) : ''}
      </div>
      <div class="access-form hidden" data-folder-form style="margin-top:14px">
        <label class="field"><span>Куда сохранять — полный путь к папке</span>
          <input type="text" data-field="folder-input" spellcheck="false" placeholder="D:\\iCloudBackup" value="${esc(dir)}"></label>
        <p class="tiny">Корень диска (C:\\ или E:\\) выбрать нельзя — папку программа создаст сама. Если сменить папку посреди
          скачивания, скачанное останется в старой папке, а остальное докачается в новую.</p>
        <div class="row" style="margin-top:10px"><button class="primary small" data-action="folder-save">${icon('check')}Сохранить папку</button></div>
      </div></div>`;
  };

  App.action('toggle-folder-form', (btn) => {
    const wrap = btn.closest('.card').querySelector('[data-folder-form]');
    wrap.classList.toggle('hidden');
    if (!wrap.classList.contains('hidden')) wrap.querySelector('input').focus();
  });
  App.action('folder-save', (btn) => App.run(btn, async () => {
    const input = btn.closest('.card').querySelector('[data-field="folder-input"]');
    const dir = input.value.trim();
    if (!dir) return { ok: false, error: 'Укажите папку — например D:\\iCloudBackup' };
    const res = await App.api.post('/api/config/download_dir', { download_dir: dir });
    if (res.ok) {
      btn.closest('[data-folder-form]').classList.add('hidden');
      return { ok: true, data: { msg: 'Папка сохранена' } };
    }
    return res;
  }));

  // ── Баннеры ──
  App.spaceBanner = (s) => {
    const sp = s.space || {};
    if (!sp.short) return '';
    return `<div class="banner safety"><span class="b-icon">${icon('hdd')}</span><div>
      <h3>Места на диске может не хватить</h3>
      <p>Осталось скачать ${fmt.bytes(sp.need)}, а на диске с папкой свободно ${fmt.bytes(sp.free)}. Выберите папку на другом
        диске, например на внешнем, или освободите место. Если место закончится, программа остановится и подождёт — скачанное
        не пропадёт.</p></div></div>`;
  };
  App.errorBanner = (title, text, buttons = '') =>
    `<div class="banner action"><span class="b-icon">${icon('alert')}</span><div>
      <h3>${esc(title)}</h3><p>${esc(text)}</p>${buttons ? `<div class="row actions">${buttons}</div>` : ''}</div></div>`;
  App.noticeBanner = (title, text, buttons = '') =>
    `<div class="banner notice"><span class="b-icon">${icon('info')}</span><div>
      <h3>${esc(title)}</h3><p>${esc(text)}</p>${buttons ? `<div class="row actions">${buttons}</div>` : ''}</div></div>`;
  App.safetyNote = () => `<p class="safety-note">${icon('alert')}<span>Пока всё не скачано, не удаляйте фото из iCloud и не
    выключайте «Фото iCloud» на айфоне. Программа из iCloud только читает — удалить там что-то она не может.</span></p>`;

  // ── Текст ошибки движка с кнопкой по ситуации ──
  App.errorActions = (err) => {
    if (!err) return '';
    switch (err.kind) {
      case 'expired': return `<button class="primary" data-action="go-login">${icon('key')}Войти снова</button>`;
      case 'access_denied': return `<a class="btn primary" href="#/prepare">${icon('phone')}Что включить на айфоне</a>` +
        `<button data-action="download-start">${icon('refresh')}Попробовать снова</button>`;
      case 'disk': return `<button class="primary" data-action="download-start">${icon('play')}Я освободил место — продолжить</button>` +
        `<button data-action="toggle-folder-form-home">${icon('folder')}Выбрать другую папку</button>`;
      default: return `<button class="primary" data-action="download-start">${icon('refresh')}Попробовать снова</button>`;
    }
  };

  // ── Общие действия ──
  App.action('go-login', () => App.go('#/login'));
  App.action('go-home', () => App.go('#/home'));
  App.action('go-prepare', () => App.go('#/prepare'));
  App.action('download-start', (btn) => App.run(btn, () => App.api.post('/api/download/start')));
  App.action('scan-start', (btn) => App.run(btn, () => App.api.post('/api/scan')));
  App.action('download-pause', (btn) => App.run(btn, () => App.api.post('/api/download/pause')));
  App.action('download-resume', (btn) => App.run(btn, () => App.api.post('/api/download/resume')));
  App.action('download-stop', async (btn) => {
    const yes = await App.confirm({ title: 'Остановить скачивание?', ok: 'Остановить',
      text: 'Уже скачанное сохранено. «Продолжить» докачает остальное без повторов.' });
    if (yes) App.run(btn, () => App.api.post('/api/download/stop'));
  });
  App.action('retry-failed', (btn) => App.run(btn, () => App.api.post('/api/retry_failed')));
  App.action('open-folder', (btn) => App.run(btn, () => App.api.post('/api/open_folder', { which: btn.dataset.which || 'download' })));
  App.action('copy-path', async (btn) => {
    try {
      await navigator.clipboard.writeText(btn.dataset.path || '');
      App.toast('Путь скопирован', 'ok');
    } catch (e) {
      App.toast(btn.dataset.path || '', 'info', 8000);
    }
  });
  App.action('toggle-folder-form-home', () => {
    App.go('#/home');
    setTimeout(() => {
      const wrap = document.querySelector('#folder-card [data-folder-form]');
      if (wrap) { wrap.classList.remove('hidden'); wrap.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    }, 100);
  });
  App.action('process-start', (btn) => App.run(btn, () => App.api.post('/api/process/start', { kind: btn.dataset.kind })));
  App.action('process-stop', (btn) => App.run(btn, () => App.api.post('/api/process/stop')));
  App.action('logout', async (btn) => {
    const yes = await App.confirm({ title: 'Выйти из Apple ID?', ok: 'Выйти', danger: true,
      text: 'Сессия будет удалена с этого компьютера. Скачанные файлы останутся. Чтобы продолжить скачивание, нужно будет войти снова и ввести код.' });
    if (yes) {
      const res = await App.run(btn, () => App.api.post('/api/logout'));
      if (res && res.ok) App.go('#/home');
    }
  });

  // ── Ход задачи обработки (общая карточка для «Обработки» и «Яндекс Диска») ──
  App.jobCard = (job, kinds) => {
    if (!job || !job.kind || !kinds.includes(job.kind)) return '';
    const p = job.progress || {};
    const total = p.total || 0;
    const done = p.done || 0;
    if (job.running) {
      const bar = total ? `<div class="bar"><span class="done" style="width:${fmt.pct(done, total).toFixed(2)}%"></span></div>` : '';
      return `<div class="card job-box"><div class="card-head"><span class="icon-box info">${icon('activity')}</span>
        <div class="grow"><h3>${esc(job.title)}</h3><span class="tiny">${total ? `${fmt.num(done)} из ${fmt.num(total)}` : 'готовлюсь…'}${
          p.current ? ` · ${esc(p.current)}` : ''}</span></div>
        <button class="small" data-action="process-stop">${icon('stop')}Остановить</button></div>${bar}
        ${p.msg ? `<p class="tiny">${esc(p.msg)}</p>` : ''}</div>`;
    }
    if (job.error) return App.errorBanner(`${job.title}: не получилось`, job.error);
    const r = job.result || {};
    if (r.ok === false) return App.errorBanner(`${job.title}: остановлено`, r.msg || 'Не получилось');
    const text = r.text || (r.done != null ? `Готово: ${fmt.num(r.done)} ${fmt.plural(r.done, 'файл', 'файла', 'файлов')}` : 'Готово');
    return `<div class="ok-box" style="margin-top:0">${icon('check')}<span>${esc(job.title)}. ${esc(text)}${
      r.skipped ? `, пропущено ${fmt.num(r.skipped)}` : ''}${r.errors || r.failed ? `, ошибок ${fmt.num(r.errors || r.failed)}` : ''}</span></div>`;
  };
})();
