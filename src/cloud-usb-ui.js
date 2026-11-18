'use strict';
(() => {
  const $id = id => document.getElementById(id);
  const files = $id('page-files');
  let usbChecking = false;
  const drives = document.createElement('div');
  drives.className = 'fm-drive-bar';
  const driveList = document.createElement('div');
  driveList.className = 'fm-drive-list';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'button secondary small-btn';
  retry.textContent = 'Проверить USB';
  retry.onclick = async () => {
    retry.disabled = true;
    try {
      const result = await api('/api/platform/usb',{method:'POST',body:'{}'});
      fmMsg(result.message);
      await refreshUSBFiles();
    } catch (error) { fmMsg(error.message); }
    finally { retry.disabled = false; }
  };
  drives.append(driveList,retry);
  files?.querySelector('.fm-panes')?.before(drives);
  let lastDrives = '';
  function drawDrives(devices) {
    const snapshot = JSON.stringify(devices);
    if (snapshot === lastDrives) return;
    lastDrives = snapshot;
    driveList.replaceChildren();
    for (const disk of devices) {
      const item = document.createElement(disk.ready ? 'button' : 'span');
      item.className = disk.ready ? 'button secondary small-btn' : 'fm-drive-state';
      item.textContent = disk.name + (disk.ready ? ' ↗' : disk.error ? ' · ошибка монтирования' : ' · ожидает монтирования');
      if (disk.ready) {
        item.type = 'button';
        item.onclick = () => {
          const select = $id('fm-root-'+fm.active);
          if (select.hidden && fm.active === 'right') $id('fm-remote')?.click();
          select.value = disk.id;
          select.dispatchEvent(new Event('change'));
        };
      } else if (disk.error) {
        item.title = disk.error;
        item.setAttribute('aria-label',disk.error);
      }
      driveList.append(item);
    }
    if (!devices.length) driveList.textContent = 'USB-накопители не подключены';
  }
  async function refreshUSBFiles() {
    if (!files?.classList.contains('active') || usbChecking) return;
    usbChecking = true;
    try {
      const devices = await api('/api/platform/usb');
      drawDrives(devices);
      const label = d => d.name + (d.ready ? '' : d.error ? ' · ошибка монтирования' : ' · ожидает монтирования');
      for (const side of ['left','right']) {
        const select = $id('fm-root-'+side);
        if (!select) continue;
        const selected = select.value;
        const old = [...select.options].filter(o => o.value.startsWith('usb:'));
        const previous = old.map(o => [o.value,o.textContent,o.disabled].join('|')).join();
        const current = devices.map(d => [d.id,label(d),!d.ready].join('|')).join();
        if (current === previous) continue;
        old.forEach(o => o.remove());
        for (const disk of devices) {
          const option = new Option(label(disk),disk.id);
          option.disabled = !disk.ready;
          select.append(option);
        }
        if ([...select.options].some(o => o.value === selected && !o.disabled)) select.value = selected;
        else if (selected.startsWith('usb:')) {
          select.value = 'home';
          select.dispatchEvent(new Event('change'));
          if (typeof fmMsg === 'function') fmMsg('USB-накопитель отключён.');
        }
      }
    } catch (_) {
      driveList.textContent = 'Не удалось проверить USB. Повторите проверку.';
      lastDrives = '';
    } finally { usbChecking = false; }
  }
  if (files) {
    new MutationObserver(refreshUSBFiles).observe(files,{attributes:true,attributeFilter:['class']});
    setInterval(refreshUSBFiles,4500);
  }

  if (files && typeof fmRender === 'function') {
    const render = fmRender;
    fmRender = function(side) {
      render(side);
      const pane = fm[side];
      const path = $id('fm-path-'+side);
      if (!path || pane.kind === 'local') return;
      const parts = pane.path.split('/').filter(Boolean);
      path.replaceChildren();
      const root = $id('fm-root-'+side).selectedOptions[0]?.textContent || '/';
      [root,...parts].forEach((name,index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'fm-crumb';
        button.textContent = name;
        button.disabled = index === parts.length;
        button.onclick = () => {
          pane.path = parts.slice(0,index).join('/');
          fmLoad(side);
        };
        path.append(button);
      });
    };
  }

  const anchor = $id('backup-info')?.closest('.panel');
  if (!anchor) return;
  const panel = document.createElement('details');
  panel.className = 'cloud-backup-panel platform-legacy';
  panel.innerHTML = '<summary>PostgreSQL · Supabase</summary><p class="fine-print">Реквизиты используются только для текущего запроса. Для архива используется настроенный публичный ключ age. Восстановление создаёт новую локальную БД.</p><div class="platform-form-grid"><label>Новая локальная БД<input name="target" placeholder="restored_app" autocomplete="off"></label></div><details><summary>Архивы в Supabase Storage</summary><p class="fine-print">В Supabase создайте приватный bucket и получите S3-реквизиты в Storage → Settings. При переносе устройства используйте те же bucket и ключ расшифровки age.</p><div class="platform-form-grid"><label>Project Ref<input name="project" autocomplete="off"></label><label>Приватный bucket<input name="bucket" autocomplete="off"></label><label>Регион S3<input name="region" placeholder="eu-central-1"></label><label>Access Key ID<input name="access" autocomplete="off"></label><label>Secret Access Key<input name="storage_secret" type="password" autocomplete="new-password"></label><label>Локальная база<select name="database"></select></label><label>Архив<select name="object"></select></label><label>Закрытый ключ age<input name="age_key" type="password" autocomplete="new-password"></label></div><div class="form-actions"><button type="button" class="button secondary" data-cloud="storage-test">Проверить Storage</button><button type="button" class="button primary" data-cloud="upload">Скопировать БД в облако</button><button type="button" class="button secondary" data-cloud="list">Обновить архивы</button><button type="button" class="button secondary" data-cloud="download">Восстановить архив</button></div></details><details><summary>Скопировать схему из облачной PostgreSQL</summary><p class="fine-print">В Supabase откройте Connect, выберите Session pooler или Direct и перенесите хост и пользователя. Используется порт 5432 и TLS. Служебные схемы и права Supabase не переносятся. Это разовый снимок схемы.</p><div class="platform-form-grid"><label>Хост<input name="host" placeholder="aws-0-eu-central-1.pooler.supabase.com" autocomplete="off"></label><label>Пользователь<input name="user" placeholder="postgres.&lt;project-ref&gt;" autocomplete="off"></label><label>Пароль PostgreSQL<input name="db_password" type="password" autocomplete="new-password"></label><label>Схема<input name="schema" value="public"></label></div><div class="form-actions"><button type="button" class="button secondary" data-cloud="db-test">Проверить соединение</button><button type="button" class="button primary" data-cloud="pull">Скопировать схему в новую БД</button></div></details><p data-cloud-message role="status"></p>';
  anchor.append(panel);
  const text = panel.querySelector('[data-cloud-message]');
  const fields = key => panel.querySelector('[name="'+key+'"]');
  const payload = keys => Object.fromEntries(keys.map(key => [key, fields(key).value.trim()]));
  const storageKeys = ['project','bucket','region','access','storage_secret'];
  const postgresKeys = ['host','user','db_password','schema'];
  const post = data => api('/api/platform/postgres',{method:'POST',body:JSON.stringify(data)});
  let busy = false;
  async function refresh() {
    if (!panel.open || !$id('page-settings')?.classList.contains('active') || busy) return;
    try {
      const status = await api('/api/platform/postgres');
      if (status.message) text.textContent = status.message;
      else if (!status.client_ready || !status.storage_ready || !status.age_ready) text.textContent = 'Проверьте зависимости PostgreSQL, rclone и ключ age.';
      for (const button of panel.querySelectorAll('[data-cloud]')) button.disabled = status.phase === 'running';
    } catch (error) { text.textContent = error.message; }
  }
  panel.addEventListener('toggle',async () => {
    if (!panel.open) return;
    try {
      const result = await post({op:'databases'});
      const selected = fields('database').value;
      fields('database').replaceChildren(...result.databases.map(item=>new Option(item,item)));
      if (result.databases.includes(selected)) fields('database').value = selected;
    } catch (error) { text.textContent = error.message; }
    refresh();
  });
  panel.addEventListener('click',async e => {
    const button = e.target.closest('[data-cloud]');
    if (!button || busy) return;
    const op = button.dataset.cloud;
    const data = {op,...payload(['pull','db-test'].includes(op) ? postgresKeys : storageKeys)};
    if ('db_password' in data) data.db_password = fields('db_password').value;
    if (op === 'upload') data.database = fields('database').value;
    if (op === 'download') Object.assign(data,payload(['object','age_key','target']));
    if (op === 'pull') Object.assign(data,payload(['target']));
    busy = true;
    button.disabled = true;
    text.textContent = 'Выполняется…';
    try {
      const result = await post(data);
      if (op === 'list') fields('object').replaceChildren(...result.objects.map(item=>new Option(item.Name,item.Name)));
      text.textContent = result.message || result.result || 'Архивов найдено: '+result.objects.length;
      if (op === 'download') fields('age_key').value = '';
      if (op === 'pull') fields('db_password').value = '';
    } catch (error) { text.textContent = error.message; }
    finally { busy = false; button.disabled = false; }
  });
  setInterval(refresh,5000);
})();
