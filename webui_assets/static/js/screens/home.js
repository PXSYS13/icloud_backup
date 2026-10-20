/* Главная: подготовка и вход, медиатека (сколько фото, видео, Live Photos, по годам),
   скачивание с прогрессом, пауза, продолжение, итог. */
(function () {
  'use strict';
  const App = window.App;
  const { esc, fmt, byId } = App;
  const icon = App.icon;

  const STALE_SEC = 180;
  let lastDoneAt = null;

  const nowOf = (s) => s.now || Date.now() / 1000;

  // Что показывать: setup (не вошли), code (ждём код), ready (вошли)
  function phase(s) {
    const acc = s.account || {};
    if (acc.state === 'ok') return 'ready';
    if (acc.state === 'needs_2fa') return 'code';
    return 'setup';
  }

  function pill(s, offline) {
    const p = (cls, text) => `<span class="pill ${cls}"><span class="pdot"></span>${esc(text)}</span>`;
    if (offline) return p('err', 'Программа не отвечает');
    const ph = phase(s);
    if (ph === 'setup') return p('', (s.account || {}).restoring ? 'Проверяю сессию' : 'Нужен вход');
    if (ph === 'code') return p('wait', 'Ждём код подтверждения');
    const lib = s.library || {};
    const pipe = s.pipeline || {};
    const since = pipe.started_at && pipe.running ? ` · ${fmt.duration(nowOf(s) - pipe.started_at)}` : '';
    switch (pipe.state) {
      case 'scanning': return p('live', 'Получаю список' + since);
      case 'downloading': return p('live', 'Идёт скачивание' + since);
      case 'paused': return p('wait', 'На паузе');
      case 'waiting': return p('wait', 'Жду Apple' + (pipe.wait_until ? ` · повтор в ${fmt.hhmmss(pipe.wait_until)}` : ''));
      case 'needs_login': return p('err', 'Нужно войти снова');
      case 'needs_action': case 'error': return p('err', 'Нужно ваше действие');
      case 'done':
        if (lib.total) return p('ok', pipe.mode === 'scan' ? 'Список получен' : 'Готово');
        return p('ok', 'Готово к скачиванию'); // список забыли после скачивания — как при первом запуске
      case 'stopped': return p('', 'Остановлено');
      default:
        // Плашка и заголовок рядом не должны спорить: после полного скачивания — «Всё скачано»
        if (lib.total && (lib.downloaded || 0) >= lib.total && !lib.failed) return p('ok', 'Всё скачано');
        return p('ok', 'Готово к скачиванию');
    }
  }

  function heartbeat(s, offline) {
    const pipe = s.pipeline || {};
    if (offline || !pipe.running) return '';
    const last = pipe.last_activity_at;
    if (!last) return `<span class="heartbeat">${icon('activity')}Запускаю…</span>`;
    const ago = nowOf(s) - last;
    if (ago > STALE_SEC) {
      return `<span class="heartbeat stale">${icon('hourglass')}Последний отклик ${esc(fmt.ago(ago, last))} — Apple отвечает медленно или качается большой файл</span>`;
    }
    return `<span class="heartbeat">${icon('activity')}Программа работает · последнее действие ${esc(fmt.ago(ago, last))}</span>`;
  }

  function visual(s, offline) {
    const lib = s.library || {};
    const pipe = s.pipeline || {};
    if (offline) return `<div class="hero-art off">${icon('power')}</div>`;
    const ph = phase(s);
    if (ph === 'setup') return `<div class="hero-art">${icon('cloud-down')}</div>`;
    if (ph === 'code') return `<div class="hero-art">${icon('phone')}</div>`;
    if (pipe.state === 'scanning') {
      return App.ring({ spin: true, label: pipe.listing_found ? fmt.num(pipe.listing_found) : '…',
        caption: pipe.listing_found ? 'файлов уже найдено' : 'получаю список' });
    }
    const total = lib.total || 0;
    if (!total) return `<div class="hero-art">${icon('download')}</div>`;
    const done = lib.downloaded || 0;
    if (done >= total && !lib.failed) {
      return `<div class="hero-art ok" role="img" aria-label="Все файлы сохранены"><span>${icon('check')}</span></div>`;
    }
    return App.ring({ done, fail: lib.failed || 0, total, label: fmt.pctText(done, total), caption: 'сохранено' });
  }

  function headline(s, offline) {
    const lib = s.library || {};
    const pipe = s.pipeline || {};
    if (offline) return ['Программа не отвечает', 'Похоже, окно программы закрыто. Запустите её снова — всё скачанное сохранено, она продолжит с того же места.'];
    const ph = phase(s);
    if (ph === 'setup') {
      return ['Спасём ваши фото из iCloud', 'Три шага: включить два переключателя на айфоне, войти в Apple ID и нажать «Скачать». Всё остаётся на этом компьютере, в iCloud ничего не меняется.'];
    }
    if (ph === 'code') return ['Apple прислал код на ваш айфон', 'Введите шесть цифр — и можно начинать.'];
    const total = lib.total || 0;
    const done = lib.downloaded || 0;
    const left = Math.max(0, (lib.pending || 0));
    switch (pipe.state) {
      case 'scanning': return ['Смотрю, что лежит в iCloud', 'Это занимает от минуты до нескольких минут — зависит от размера медиатеки.'];
      case 'downloading': return [`Скачиваю: ${fmt.num(done)} из ${fmt.filesOf(total)}`,
        left ? `Осталось ${fmt.files(left)}, ${fmt.bytes(lib.bytes_pending || 0)}.${pipe.eta_sec ? ` Примерно ${fmt.duration(pipe.eta_sec)}.` : ''} Можно свернуть браузер, но не закрывайте чёрное окно программы.` : 'Заканчиваю.'];
      case 'paused': return ['Пауза', 'Скачанное сохранено. Нажмите «Продолжить», когда будете готовы.'];
      case 'waiting': return ['Жду ответа Apple', (pipe.wait_reason || 'Apple отвечает с перебоями') + '. Программа повторит сама — ничего делать не нужно.'];
      case 'needs_login': return ['Сессия Apple истекла', 'Так бывает: Apple ограничивает срок входа. Войдите снова — скачивание продолжится с того же места.'];
      case 'needs_action': case 'error': return ['Нужно ваше действие', (pipe.error || {}).text || 'Что-то пошло не так — подробности ниже.'];
      case 'stopped': return ['Остановлено', `Скачано ${fmt.num(done)} из ${fmt.filesOf(total)}. «Продолжить» докачает остальное без повторов.`];
      case 'done':
        if (!total) return ['Готово к скачиванию', 'Сначала посмотрим, сколько фото и видео лежит в iCloud и хватит ли места на диске, — потом скачаем всё.'];
        if (pipe.mode === 'scan') return [`В iCloud ${fmt.files(total)}`, `${fmt.bytes(lib.bytes_total || 0)}. Проверьте папку и место на диске — и можно скачивать.`];
        if (lib.failed) return [`Готово: скачано ${fmt.num(done)} из ${fmt.filesOf(total)}`, `Не скачались ${fmt.files(lib.failed)} — их можно повторить.`];
        return ['Готово: все файлы сохранены', 'Посмотрите итоги — там путь к папке и что можно сделать дальше.'];
      default:
        if (!total) return ['Готово к скачиванию', 'Сначала посмотрим, сколько фото и видео лежит в iCloud и хватит ли места на диске, — потом скачаем всё.'];
        if (done >= total && !lib.failed) return ['Всё скачано', 'Медиатека целиком лежит в вашей папке. Можно обработать файлы или залить копию в Яндекс Диск.'];
        if (done) return [`Скачано ${fmt.num(done)} из ${fmt.filesOf(total)}`, `Осталось ${fmt.files(left)}, ${fmt.bytes(lib.bytes_pending || 0)}. Нажмите «Продолжить».`];
        return [`В iCloud ${fmt.files(total)}`, `${fmt.bytes(lib.bytes_total || 0)}: ${fmt.num(lib.photos || 0)} фото, ${fmt.num(lib.live || 0)} Live Photo, ${fmt.num(lib.videos || 0)} видео. Проверьте папку и место на диске — и можно скачивать.`];
    }
  }

  function actions(s, offline) {
    if (offline) return '';
    const ph = phase(s);
    const acc = s.account || {};
    if (ph === 'setup') {
      if (acc.restoring) return '';
      return `<button class="primary big" data-action="go-prepare">${icon('phone')}Подготовить айфон${icon('arrow')}</button>` +
        `<button class="big" data-action="go-login">Я уже всё включил — войти</button>`;
    }
    if (ph === 'code') return `<button class="primary big" data-action="go-login">${icon('key')}Ввести код</button>`;
    const lib = s.library || {};
    const pipe = s.pipeline || {};
    const busy = (s.job || {}).running;
    const total = lib.total || 0;
    const done = lib.downloaded || 0;
    switch (pipe.state) {
      case 'scanning': return `<button data-action="download-stop">${icon('stop')}Остановить</button>`;
      case 'downloading': return `<button class="big" data-action="download-pause">${icon('hourglass')}Пауза</button>` +
        `<button data-action="download-stop">${icon('stop')}Остановить</button>`;
      case 'paused': return `<button class="primary big" data-action="download-resume">${icon('play')}Продолжить</button>` +
        `<button data-action="download-stop">${icon('stop')}Остановить</button>`;
      case 'waiting': return `<button data-action="download-stop">${icon('stop')}Остановить</button>`;
      case 'needs_login': return `<button class="primary big" data-action="go-login">${icon('key')}Войти снова</button>`;
      case 'needs_action': case 'error': return App.errorActions(pipe.error);
      default: {
        if (busy) return '<span class="tiny">Идёт обработка файлов — дождитесь её окончания</span>';
        if (pipe.state === 'done' && pipe.mode !== 'scan' && !lib.failed && done >= total && total) {
          return `<a class="btn primary big" href="#/done">${icon('check')}Посмотреть итоги${icon('arrow')}</a>` +
            `<button data-action="open-folder" data-which="download">${icon('folder')}Открыть папку</button>`;
        }
        if (!total) {
          return `<button class="primary big" data-action="scan-start">${icon('list')}Проверить медиатеку</button>` +
            `<button class="big" data-action="download-start">${icon('download')}Сразу скачать всё</button>`;
        }
        if (done >= total && !lib.failed) {
          return `<a class="btn primary big" href="#/done">${icon('check')}Итоги${icon('arrow')}</a>` +
            `<a class="btn" href="#/process">${icon('sliders')}Обработать файлы</a>` +
            `<button data-action="scan-start">${icon('refresh')}Проверить новые фото</button>`;
        }
        const label = done ? 'Продолжить' : 'Скачать всё';
        return `<button class="primary big" data-action="download-start">${icon(done ? 'play' : 'download')}${label}</button>` +
          (lib.failed ? `<button data-action="retry-failed">${icon('refresh')}Повторить для ${fmt.files(lib.failed)}</button>` : '') +
          `<button data-action="scan-start">${icon('refresh')}Обновить список</button>`;
      }
    }
  }

  function tile(cls, label, value, sub) {
    return `<div class="stat ${cls}"><div class="s-label"><span class="s-dot"></span>${esc(label)}</div>
      <div class="s-value">${value}</div><div class="s-sub">${sub}</div></div>`;
  }

  function stats(s, offline) {
    if (offline) return '';
    const ph = phase(s);
    if (ph !== 'ready') {
      return `<ol class="how">
        <li><span class="n">1</span><div><b>Подготовьте айфон</b>Два переключателя в настройках iCloud — иначе Apple не отдаст фото</div></li>
        <li><span class="n">2</span><div><b>Войдите в Apple ID</b>Пароль и код подтверждения. Пароль программа не сохраняет</div></li>
        <li><span class="n">3</span><div><b>Нажмите «Скачать»</b>Дальше программа всё сделает сама, можно продолжать позже</div></li></ol>`;
    }
    const lib = s.library || {};
    const pipe = s.pipeline || {};
    const free = s.free_space && s.free_space.free != null ? fmt.bytes(s.free_space.free) : '—';
    if (!lib.total) {
      return `<div class="stats">${tile('', 'В iCloud', '?', 'список получу при проверке')}
        ${tile('', 'Свободно', free, 'на диске с папкой')}</div>`;
    }
    const done = tile('ok', 'Скачано', fmt.num(lib.downloaded || 0), `${fmt.bytes(lib.bytes_done || 0)} · из ${fmt.filesOf(lib.total)}`);
    const left = tile('', 'Осталось', fmt.num(lib.pending || 0), lib.pending
      ? `${fmt.bytes(lib.bytes_pending || 0)}${pipe.eta_sec ? ` · ≈ ${fmt.duration(pipe.eta_sec)}` : ''}` : 'очередь пуста');
    let third;
    if (pipe.state === 'downloading' && pipe.speed_bps) third = tile('run', 'Скорость', fmt.bytes(pipe.speed_bps) + '/с', `за этот запуск ${fmt.bytes(pipe.session_bytes || 0)}`);
    else if (pipe.state === 'downloading') third = tile('run', 'Скорость', '…', 'считаю — нужно полминуты');
    else if (lib.failed) third = tile('err', 'Не скачались', fmt.num(lib.failed), 'можно повторить');
    else third = tile('', 'Свободно', free, 'на диске с папкой');
    const fourth = tile('', 'Всего в iCloud', fmt.bytes(lib.bytes_total || 0),
      `${fmt.num(lib.photos || 0)} фото · ${fmt.num(lib.live || 0)} Live · ${fmt.num(lib.videos || 0)} видео`);
    return `<div class="stats">${done}${left}${third}${fourth}</div>`;
  }

  function currentFile(s) {
    const pipe = s.pipeline || {};
    const cur = pipe.current;
    if (!cur || pipe.state !== 'downloading') return '';
    const pct = cur.size ? ` · ${fmt.pctText(cur.got, cur.size)}` : '';
    return `<div class="current-file">${icon(cur.kind === 'video' ? 'video' : 'image')}<span class="name" title="${esc(cur.name)}">${esc(cur.name)}</span>
      <span class="num">${fmt.bytes(cur.got)}${cur.size ? ` из ${fmt.bytes(cur.size)}` : ''}${pct}</span></div>`;
  }

  // ── Медиатека: виды файлов и годы ──
  function libraryCard(s) {
    const lib = s.library || {};
    if (!lib.total) return '';
    const kind = (ic, n, b, l) => `<div class="kind"><span class="icon-box">${icon(ic)}</span><div><b>${fmt.num(n)}</b><span class="l">${l} · ${fmt.bytes(b)}</span></div></div>`;
    const years = (lib.years || []).map((y) => {
      const pct = fmt.pct(y.done, y.count);
      return `<tr><td><b>${esc(y.year)}</b></td><td class="num">${fmt.files(y.count)}</td><td class="num">${fmt.bytes(y.bytes)}</td>
        <td class="col-progress"><span class="tiny">${y.done >= y.count ? 'скачано' : `${fmt.num(y.done)} из ${fmt.num(y.count)}`}</span>
        <div class="bar thin"><span class="done" style="width:${pct.toFixed(2)}%"></span></div></td></tr>`;
    }).join('');
    const listed = lib.listed_at ? `список от ${esc(listedText(lib.listed_at))}` : '';
    return `<div class="card"><div class="card-head"><span class="icon-box accent">${icon('cloud')}</span>
      <div class="grow"><h2>Медиатека iCloud</h2><span class="tiny">${fmt.files(lib.total)} · ${fmt.bytes(lib.bytes_total || 0)}${listed ? ` · ${listed}` : ''}</span></div></div>
      <div class="kinds">${kind('image', lib.photos || 0, lib.bytes_photos || 0, 'фото')}${kind('image', lib.live || 0, lib.bytes_live || 0, 'Live Photo')}${kind('video', lib.videos || 0, lib.bytes_videos || 0, 'видео')}</div>
      ${years ? `<div class="scroll years mt-md"><table class="list"><thead><tr><th>Год</th><th>Файлов</th><th>Размер</th><th>Скачано</th></tr></thead><tbody>${years}</tbody></table></div>` : ''}
      ${lib.duplicates ? `<p class="tiny mt-sm">Одинаковых файлов перенесено в «_duplicates»: ${fmt.num(lib.duplicates)}</p>` : ''}
    </div>`;
  }

  function listedText(iso) {
    const d = new Date(iso.replace(' ', 'T') + (iso.endsWith('Z') ? '' : 'Z'));
    if (isNaN(d)) return iso;
    return d.toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
  }

  function banners(s, offline) {
    if (offline || phase(s) !== 'ready') return '';
    const pipe = s.pipeline || {};
    let out = '';
    if ((pipe.state === 'needs_action' || pipe.state === 'error') && pipe.error) {
      out += App.errorBanner('Скачивание остановилось', pipe.error.text, App.errorActions(pipe.error));
    } else if (pipe.state === 'needs_login') {
      out += App.errorBanner('Сессия Apple истекла', 'Войдите снова — скачивание продолжится с того же места. Скачанное не пропадёт.',
        `<button class="primary" data-action="go-login">${icon('key')}Войти снова</button>`);
    }
    out += App.spaceBanner(s);
    return out;
  }

  App.screens.home = {
    mount(el) {
      el.innerHTML = `<div id="home-banner"></div>
        <section class="card hero" id="home-hero" aria-labelledby="home-headline">
          <div class="hero-top"><span id="home-pill"></span><span id="home-beat"></span></div>
          <div class="hero-body">
            <div id="home-visual"></div>
            <div>
              <h1 id="home-headline">Загружаю…</h1>
              <p class="sub" id="home-subline"></p>
              <div class="row actions" id="home-actions"></div>
              <div id="home-current"></div>
            </div>
          </div>
          <div id="home-stats"></div>
        </section>
        <div class="mt" id="home-safety"></div>
        <div class="grid-2 mt" id="home-grid">
          <div class="stack"><div id="home-library"></div><div id="home-feed"></div></div>
          <div class="stack"><div id="home-folder"></div><div id="home-recent"></div></div>
        </div>`;
    },
    update(s) {
      const offline = App.state.offline;
      const ph = phase(s);
      const pipe = s.pipeline || {};
      App.html(byId('home-banner'), banners(s, offline));
      byId('home-hero').className = `card hero state-${offline ? 'needs_action' : pipe.state || 'idle'}`;
      App.html(byId('home-pill'), pill(s, offline));
      App.html(byId('home-beat'), heartbeat(s, offline));
      App.html(byId('home-visual'), visual(s, offline));
      const [h1, sub] = headline(s, offline);
      App.text(byId('home-headline'), h1);
      App.text(byId('home-subline'), sub);
      App.html(byId('home-actions'), actions(s, offline));
      App.html(byId('home-current'), offline ? '' : currentFile(s));
      App.html(byId('home-stats'), stats(s, offline));
      const lib = s.library || {};
      const showSafety = ph === 'ready' && !offline && lib.total && (lib.downloaded || 0) < lib.total;
      App.html(byId('home-safety'), showSafety ? App.safetyNote() : '');
      byId('home-grid').classList.toggle('hidden', ph !== 'ready' || offline);
      if (ph === 'ready' && !offline) {
        const library = libraryCard(s);
        const feed = pipe.started_at ? App.feedCard(pipe.events) : '';
        App.html(byId('home-library'), library);
        App.html(byId('home-feed'), feed);
        App.html(byId('home-folder'), App.folderCard(s));
        App.html(byId('home-recent'), App.recentCard(s.recent, s.now));
        byId('home-grid').classList.toggle('single', !library && !feed);
      }
      // Скачивание только что закончилось целиком — показываем итоги
      if (pipe.state === 'done' && pipe.mode === 'download' && pipe.finished_at && pipe.finished_at !== lastDoneAt) {
        lastDoneAt = pipe.finished_at;
        if (!lib.failed && (lib.downloaded || 0) >= (lib.total || 0) && App.state.route === 'home') App.go('#/done');
      }
    },
  };
})();
