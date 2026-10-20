/* Обработка: формат фото (HEIC в JPG), одинаковые файлы, раскладка по годам и месяцам. */
(function () {
  'use strict';
  const App = window.App;
  const { esc, fmt, byId } = App;
  const icon = App.icon;

  const FORMATS = [
    ['originals', 'Оригиналы', 'Файлы HEIC остаются как есть, как в iCloud. Лучшее качество и самый маленький размер.'],
    ['jpg', 'JPG-копии', 'Рядом появляется JPG, который открывается везде, а HEIC переезжает в подпапку «_heic_originals». Ничего не удаляется.'],
    ['both', 'Оба', 'JPG рядом с HEIC: оригинал на месте, копия для просмотра. Места нужно примерно вдвое больше.'],
  ];

  function paintFormat(s) {
    const cfg = s.config || {};
    const html = FORMATS.map(([k, title, text]) => `<label class="check"><input type="radio" name="photo-format" value="${k}" data-field="photo-format" ${
      cfg.photo_format === k ? 'checked' : ''}><span><b>${title}</b><span class="tiny">${text}</span></span></label>`).join('');
    const box = byId('proc-format');
    if (!box.querySelector('input') || box.dataset.fmt !== cfg.photo_format) {
      App.html(box, `<fieldset class="bare"><legend class="sr-only">Формат фото</legend><div class="checks">${html}</div></fieldset>`);
      box.dataset.fmt = cfg.photo_format;
    }
  }

  App.screens.process = {
    mount(el) {
      el.innerHTML = `<div class="page-head"><h1>Обработка</h1>
          <p>Всё по желанию и после скачивания. Ничего не удаляется: лишнее переезжает в подпапки, оригиналы остаются.</p></div>
        <div id="proc-job"></div>
        <div class="stack">
          <div class="card"><div class="card-head"><span class="icon-box">${icon('image')}</span><div class="grow"><h2>Формат фото</h2>
              <span class="tiny">Айфон снимает в HEIC — на Windows такие файлы открываются не везде</span></div></div>
            <div id="proc-format"></div>
            <div class="row mt-md"><button class="primary" data-action="process-start" data-kind="heic" id="proc-heic-btn" aria-describedby="proc-heic-note">${icon('sparkles')}Сделать JPG-копии сейчас</button>
              <span class="tiny" id="proc-heic-note"></span></div>
            <p class="tiny mt-sm">JPG сохраняет данные снимка: дату, место съёмки и цветовой профиль. Видеочасть Live Photo не трогается.</p></div>
          <div class="card"><div class="card-head"><span class="icon-box">${icon('copy')}</span><div class="grow"><h2>Одинаковые файлы</h2>
              <span class="tiny">Точные копии по содержимому — например, фото, сохранённое дважды</span></div></div>
            <p class="lead" id="proc-dup-text"></p>
            <div class="row"><button data-action="process-start" data-kind="dedupe" aria-describedby="proc-dup-note">${icon('grid')}Найти и перенести в «_duplicates»</button>
              <button class="ghost" data-action="open-folder" data-which="duplicates">${icon('folder')}Открыть «_duplicates»</button>
              <span class="tiny" id="proc-dup-note"></span></div></div>
          <div class="card"><div class="card-head"><span class="icon-box">${icon('folder')}</span><div class="grow"><h2>Раскладка по годам и месяцам</h2>
              <span class="tiny">2023 → 2023-07 → IMG_0001.HEIC</span></div></div>
            <div class="switch"><div><b>Раскладывать по датам съёмки</b>
                <p>Включено — файлы ложатся в папки по годам и месяцам прямо при скачивании. Выключено — все файлы в одну папку.</p></div>
              <input type="checkbox" data-field="by-month" data-action="layout-toggle" aria-label="Раскладывать по датам"></div>
            <div class="row mt-xs"><button data-action="process-start" data-kind="layout" aria-describedby="proc-layout-note">${icon('refresh')}Переложить уже скачанное</button>
              <span class="tiny" id="proc-layout-note">Если меняли переключатель после скачивания — разложит файлы заново по датам</span></div></div>
        </div>`;
      byId('proc-format').addEventListener('change', (e) => {
        if (e.target.dataset.field !== 'photo-format') return;
        App.run(null, () => App.api.post('/api/settings', { photo_format: e.target.value }), { okToast: false }).then(() => App.toast('Сохранено', 'ok'));
      });
    },
    update(s) {
      const cfg = s.config || {};
      const lib = s.library || {};
      const busy = (s.pipeline || {}).running || (s.job || {}).running;
      paintFormat(s);
      App.html(byId('proc-job'), App.jobCard(s.job, ['heic', 'dedupe', 'layout']));
      byId('proc-heic-btn').disabled = busy || cfg.photo_format === 'originals' || !lib.downloaded;
      App.text(byId('proc-heic-note'), cfg.photo_format === 'originals' ? 'Выберите «JPG-копии» или «Оба», чтобы конвертировать'
        : !lib.downloaded ? 'Сначала скачайте фото' : busy ? 'Дождитесь окончания скачивания или другой задачи' : '');
      App.text(byId('proc-dup-text'), lib.duplicates ? `Уже перенесено в «_duplicates»: ${fmt.files(lib.duplicates)}. Можно запустить ещё раз — найдутся новые.`
        : 'Программа сравнит скачанные файлы по контрольной сумме и перенесёт повторы в папку «_duplicates». Оставляется самый ранний по дате съёмки.');
      for (const btn of byId('screen-process').querySelectorAll('[data-kind="dedupe"],[data-kind="layout"]')) btn.disabled = busy || !lib.downloaded;
      // Почему кнопка выключена — словами рядом с ней
      const why = !lib.downloaded ? 'Сначала скачайте фото' : busy ? 'Дождитесь окончания скачивания или другой задачи' : '';
      App.text(byId('proc-dup-note'), why);
      App.text(byId('proc-layout-note'), why || 'Если меняли переключатель после скачивания — разложит файлы заново по датам');
      const sw = byId('screen-process').querySelector('[data-field="by-month"]');
      if (document.activeElement !== sw) { sw.checked = !!cfg.by_month; sw.disabled = !!(s.pipeline || {}).running; }
    },
  };

  App.action('layout-toggle', (input) => {
    setTimeout(() => App.run(null, () => App.api.post('/api/settings', { by_month: input.checked }), { okToast: false })
      .then((res) => { if (res && res.ok) App.toast(input.checked ? 'Новые файлы лягут по годам и месяцам' : 'Новые файлы лягут в одну папку', 'ok'); }), 0);
  });
})();
