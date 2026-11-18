'use strict';
(() => {
  const byId = id => document.getElementById(id);
  const el = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  async function call(name, body) {
    const response = await fetch('/api/platform/' + name, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Операция недоступна.');
    return data;
  }
  const show = (element, message) => { element.textContent = message; };
  const bytes = n => typeof n === 'number' ? (n / 2 ** 30).toLocaleString(undefined, {maximumFractionDigits:2}) + ' GiB' : '—';
  const when = t => t ? new Date(t * 1000).toLocaleString() : '—';
  const health = byId('page-alerts');
  const toolbar = document.querySelector('.topbar');
  const active = () => !byId('app')?.classList.contains('hidden') && !document.hidden;

  if (toolbar && health) {
    const bell = el('button', '🔔', 'platform-bell');
    bell.type = 'button';
    bell.setAttribute('aria-label','Уведомления');
    bell.setAttribute('aria-haspopup','dialog');
    const badge = el('span', '', 'platform-unread');
    badge.hidden = true;
    bell.append(badge);
    const anchor = byId('refresh');
    if (anchor && toolbar.contains(anchor)) anchor.before(bell);
    else toolbar.append(bell);
    const dialog = el('dialog', '', 'platform-dialog polished-dialog panel');
    dialog.setAttribute('aria-label','Уведомления');
    dialog.innerHTML = '<header><h2>Уведомления</h2><button type="button" aria-label="Закрыть окно" class="button small-btn" data-close>×</button></header><div data-events></div><div class="form-actions"><button type="button" class="button secondary" data-read>Прочитать все</button><button type="button" class="button primary" data-all>Все уведомления</button></div>';
    document.body.append(dialog);
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.querySelector('[data-all]').onclick = () => { dialog.close(); go('alerts'); };

    const panel = el('section', '', 'panel spaced platform-inbox');
    panel.innerHTML = '<div class="panel-title"><div><h2>Входящие</h2><p class="fine-print">События устройства, VPN и резервных копий.</p></div><button type="button" class="button secondary small-btn" data-read>Прочитать все</button></div><label class="platform-filter">Показать <select data-filter><option value="all">Все события</option><option value="unread">Непрочитанные</option><option value="backup">Резервные копии</option><option value="warnings">Предупреждения и ошибки</option></select></label><div data-events></div><p data-error role="status"></p>';
    const alertSettings = el('section', '', 'panel spaced platform-notification-settings');
    alertSettings.innerHTML = '<h2>Уведомления в Pi Control</h2><form><div class="platform-form-grid"><label>Порог CPU (%)<input required type="number" name="cpu_percent" min="10" max="100"></label><label>Длительность нагрузки (мин)<input required type="number" name="cpu_minutes" min="2" max="60"></label><label>Температура (°C)<input required type="number" name="temperature" min="40" max="100"></label><label>Заполнение диска (%)<input required type="number" name="disk_percent" min="50" max="100"></label></div><div class="platform-options"><label><input type="checkbox" name="vpn"> Состояние Gluetun</label><label><input type="checkbox" name="backup"> Результаты резервного копирования</label></div><button type="submit" class="button primary">Сохранить</button><p role="status"></p></form>';
    const intro = health.querySelector('.page-intro');
    if (intro) intro.after(panel, alertSettings);
    else health.prepend(panel, alertSettings);
    let data, initialized = false, refreshing = false;

    function renderList(container, items) {
      container.replaceChildren();
      if (!items.length) {
        container.append(el('p','Уведомлений пока нет.','fine-print'));
        return;
      }
      for (const item of items) {
        const row = el('article', '', 'platform-event');
        row.dataset.level = item.level;
        row.dataset.unread = String(!item.seen);
        const group = el('div');
        group.append(el('strong',item.message),el('small',when(item.created)));
        row.append(group);
        if (!item.seen) {
          const mark = el('button','Прочитано','button secondary small-btn');
          mark.type = 'button';
          mark.onclick = () => act({op:'read',id:item.id});
          row.append(mark);
        }
        container.append(row);
      }
    }
    function draw() {
      if (!data) return;
      badge.hidden = data.unread === 0;
      badge.textContent = data.unread > 99 ? '99+' : data.unread;
      bell.setAttribute('aria-label','Уведомления, непрочитанных: ' + data.unread);
      const filter = panel.querySelector('[data-filter]').value;
      if (dialog.open) renderList(dialog.querySelector('[data-events]'),data.items.slice(0,8));
      if (health.classList.contains('active')) renderList(panel.querySelector('[data-events]'), data.items.filter(item =>
        filter === 'all' || filter === 'unread' && !item.seen || filter === 'backup' && item.kind === 'backup' ||
        filter === 'warnings' && ['warning','error'].includes(item.level)));
      if (!initialized) {
        const form = alertSettings.querySelector('form');
        for (const [key,value] of Object.entries(data.settings)) {
          const field = form.elements.namedItem(key);
          if (field.type === 'checkbox') field.checked = value;
          else field.value = value;
        }
        initialized = true;
      }
    }
    async function refresh() {
      if (!active() || refreshing) return;
      refreshing = true;
      try { data = await call('events'); draw(); }
      catch(error) { show(panel.querySelector('[data-error]'), error.message); }
      finally { refreshing = false; }
    }
    async function act(payload) {
      try { data = await call('events',payload); draw(); }
      catch(error) { show(panel.querySelector('[data-error]'),error.message); }
    }
    bell.onclick = () => { dialog.showModal(); draw(); refresh(); };
    dialog.querySelector('[data-read]').onclick = panel.querySelector('[data-read]').onclick = () => act({op:'read-all'});
    panel.querySelector('[data-filter]').onchange = draw;
    alertSettings.querySelector('form').onsubmit = async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const payload = {op:'settings'};
      for (const field of form.elements) {
        if (field.name) payload[field.name] = field.type === 'checkbox' ? field.checked : Number(field.value);
      }
      try { data = await call('events',payload); draw(); show(form.querySelector('[role=status]'),'Настройки сохранены.'); }
      catch(error) { show(form.querySelector('[role=status]'),error.message); }
    };
    new MutationObserver(() => { if (health.classList.contains('active')) { draw(); refresh(); } })
      .observe(health,{attributes:true,attributeFilter:['class']});
    new MutationObserver(refresh).observe(byId('app'),{attributes:true,attributeFilter:['class']});
    setInterval(refresh,30000);
    refresh();
  }

  const old = byId('backup-info');
  const backupPanel = old?.closest('.panel');
  if (!backupPanel) return;
  const legacy = el('details','','platform-legacy');
  legacy.append(el('summary','Резервное копирование на другие носители'));
  const lastNodes = [...backupPanel.childNodes].filter(node => node.nodeType === 1 && node.tagName !== 'H2');
  for (const item of lastNodes) legacy.append(item);
  backupPanel.append(legacy);
  const usb = el('div','','platform-usb');
  usb.innerHTML = '<h3>USB-накопитель</h3><p class="fine-print">Архивы шифруются age. Для восстановления храните закрытый ключ отдельно.</p><label>Накопитель <select data-drive></select></label><p data-usb-status role="status"></p><div class="form-actions"><button type="button" class="button primary" data-operation="prepare">Подготовить и создать первую копию</button><button type="button" class="button primary" data-operation="start">Создать копию сейчас</button><button type="button" class="button secondary" data-operation="verify">Проверить последнюю копию</button></div><form><h3>Автоматизация</h3><div class="platform-form-grid"><label>Расписание<select name="schedule"><option value="off">Вручную</option><option value="daily">Каждый день</option><option value="weekly">По понедельникам</option></select></label><label>Время (Минск)<input type="time" name="clock" required value="03:00"></label><label>Минимальный интервал при подключении (ч)<input type="number" name="insert_hours" min="1" max="168" required value="24"></label></div><div class="platform-options"><label><input type="checkbox" name="on_insert"> Проверять и копировать при подключении выбранного USB</label></div><button type="submit" class="button primary" data-config>Сохранить сценарии</button></form><p data-result role="status"></p><p data-message role="status"></p><p class="fine-print">Во время согласованного копирования некоторые сервисы приостанавливаются и затем запускаются снова. Проверка SHA‑256 подтверждает целостность файлов; для проверки восстановления нужен закрытый ключ.</p>';
  backupPanel.insertBefore(usb,legacy);
  const select = usb.querySelector('[data-drive]');
  const form = usb.querySelector('form');
  const settingsPage = byId('page-settings');
  const buttons = [...usb.querySelectorAll('[data-operation]')];
  const submit = usb.querySelector('[data-config]');
  let data, init = false, submitting = false, refreshing = false;

  function state() {
    if (!data) return;
    const drive = data.devices.find(item => item.uuid === select.value);
    const trusted = !!drive && drive.uuid === data.settings.uuid;
    const busy = submitting || data.last?.state === 'running';
    buttons.forEach(button => {
      const operation = button.dataset.operation;
      button.hidden = operation === 'prepare' ? trusted && data.last?.state !== 'failed' : !trusted;
      if (operation === 'prepare') show(button,trusted ? 'Подготовить повторно' : 'Подготовить и создать первую копию');
      button.disabled = busy || !drive?.supported || operation !== 'verify' && !data.recipient_ready;
    });
    submit.disabled = busy || !data.settings.uuid;
    show(usb.querySelector('[data-usb-status]'), !data.recipient_ready ? 'Публичный ключ age отсутствует.' :
      !drive ? 'Подключите USB-накопитель. Список обновится автоматически.' :
      !drive.supported ? 'Эта файловая система не поддерживается или UUID повторяется.' :
      trusted ? 'Накопитель подготовлен. Можно настроить автоматизацию.' :
      'Подготовка сохранит первую зашифрованную копию. Накопитель форматироваться не будет.');
    const item = data.last;
    show(usb.querySelector('[data-result]'), item?.message ? [item.message,
      item.saved ? when(item.saved) : '', item.bytes ? bytes(item.bytes) : '',
      item.app_version ? 'Pi Control ' + item.app_version : ''].filter(Boolean).join(' · ') : '');
  }
  async function refreshUSB() {
    if (!active() || refreshing || !settingsPage?.classList.contains('active')) return;
    refreshing = true;
    try {
      data = await call('backup');
      const selected = select.value;
      const before = [...select.options].map(o=>o.textContent + o.value).join('|');
      const options = data.devices.map(item=>new Option([item.label || item.path,item.format||'?',bytes(item.size)].join(' · '),item.uuid));
      if (!options.length) options.push(new Option('Накопитель не обнаружен',''));
      if (before !== options.map(o=>o.textContent+o.value).join('|')) {
        select.replaceChildren(...options);
        if (data.devices.some(item=>item.uuid===selected)) select.value = selected;
        else if (data.devices.some(item=>item.uuid===data.settings.uuid)) select.value = data.settings.uuid;
      }
      if (!init) {
        form.elements.schedule.value = data.settings.schedule || 'off';
        form.elements.clock.value = data.settings.clock || '03:00';
        form.elements.insert_hours.value = data.settings.insert_hours || 24;
        form.elements.on_insert.checked = !!data.settings.on_insert;
        init = true;
      }
      state();
    } catch(error) { show(usb.querySelector('[data-message]'),error.message); }
    finally { refreshing = false; }
  }
  async function send(payload) {
    submitting = true;
    state();
    try {
      const result = await call('backup',payload);
      show(usb.querySelector('[data-message]'),result.message);
      if (payload.op === 'settings') init = false;
      await refreshUSB();
    } catch(error) { show(usb.querySelector('[data-message]'),error.message); }
    finally { submitting = false; state(); }
  }
  for (const button of buttons) button.onclick = () => send({op:button.dataset.operation,uuid:select.value});
  select.onchange = state;
  form.onsubmit = event => {
    event.preventDefault();
    send({op:'settings',schedule:form.elements.schedule.value,clock:form.elements.clock.value,
          insert_hours:Number(form.elements.insert_hours.value),on_insert:form.elements.on_insert.checked});
  };
  new MutationObserver(refreshUSB).observe(settingsPage,{attributes:true,attributeFilter:['class']});
  setInterval(refreshUSB,5000);
  const footer = document.querySelector('#app footer');
  if (footer) {
    const shortcut = el('button','Уведомления ↑','button secondary small-btn');
    shortcut.type = 'button';
    shortcut.onclick = () => { go('alerts'); window.scrollTo({top:0,behavior:'smooth'}); };
    const label = el('div');
    label.append(el('strong','Pi Control'),el('small','Raspberry Pi · локальная панель'));
    footer.replaceChildren(label,shortcut);
  }
})();
