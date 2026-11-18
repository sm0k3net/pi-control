'use strict';
(() => {
  const byId = id => document.getElementById(id);
  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value) element.textContent = value;
    return element;
  };
  const translations = new Map();
  const sourceText = new WeakMap();
  const attributes = ['placeholder', 'aria-label', 'title'];
  const sourceAttributes = new WeakMap();
  let language = localStorage.getItem('pi-language') || 'ru';
  let deviceDefault = 'ru';
  const languageControls = [];

  function label(ru, en) {
    translations.set(ru, en);
    return ru;
  }
  function translateText(text) {
    const source = sourceText.get(text) ?? text.data;
    const key = source.trim();
    const english = translations.get(key);
    if (!english) return;
    sourceText.set(text, source);
    const value = language === 'en' ? source.replace(key, english) : source;
    if (text.data !== value) text.data = value;
  }
  function translateElement(element) {
    if (element.closest('[data-literal],script,style,pre,code,textarea,[contenteditable],#vault-list,#ss-list,.terminal-output')) return;
    for (const attribute of attributes) {
      const values = sourceAttributes.get(element) || {};
      const source = values[attribute] || element.getAttribute(attribute);
      if (!translations.has(source)) continue;
      values[attribute] = source;
      sourceAttributes.set(element, values);
      const value = language === 'en' ? translations.get(source) : source;
      if (element.getAttribute(attribute) !== value) element.setAttribute(attribute, value);
    }
    for (const child of element.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) translateText(child);
      if (child.nodeType === Node.ELEMENT_NODE) translateElement(child);
    }
  }
  function setLanguage(value, remember = false) {
    language = value === 'en' ? 'en' : 'ru';
    if (remember) localStorage.setItem('pi-language', language);
    document.documentElement.lang = language;
    for (const wrapper of languageControls) {
      for (const button of wrapper.children) button.setAttribute('aria-pressed', String(button.value === language));
    }
    translateElement(document.body);
  }
  function switcher() {
    const wrapper = node('div', 'product-lang-switch');
    wrapper.setAttribute('aria-label', 'Language');
    for (const [code, name] of [['ru', 'RU'], ['en', 'ENG']]) {
      const button = node('button', '', name);
      button.type = 'button';
      button.value = code;
      button.onclick = () => setLanguage(code, true);
      wrapper.append(button);
    }
    languageControls.push(wrapper);
    return wrapper;
  }
  async function request(path, options = {}) {
    const result = await fetch(path, {
      credentials: 'same-origin', cache: 'no-store', ...options,
      headers: { ...(options.body ? {'Content-Type': 'application/json'} : {}), ...options.headers }
    });
    const data = await result.json();
    if (!result.ok) throw Error(data.error || 'Request failed.');
    return data;
  }
  const post = (path, value) => request(path, {method:'POST', body:JSON.stringify(value)});
  const notice = message => typeof toast === 'function' ? toast(message) : undefined;

  const login = byId('login');
  if (login) {
    const brand = node('div', 'product-login-brand');
    brand.innerHTML = '<div class="product-brand-inner"><span class="product-brand-name">Pi Control</span><h2></h2><p></p></div>';
    brand.querySelector('h2').textContent = label('Ваш Raspberry Pi', 'Your Raspberry Pi');
    brand.querySelector('p').textContent = label('Сервисы, файлы и состояние устройства в одной панели.', 'Services, files and device health in one place.');
    const card = login.querySelector('.login-card');
    login.prepend(brand);
    card.prepend(switcher());
    card.querySelector('h1').textContent = label('Вход в Pi Control', 'Sign in to Pi Control');
    card.querySelector('.eyebrow')?.remove();
    card.querySelector('p').textContent = label('Введите пароль для доступа к панели Raspberry Pi.', 'Enter your password to access the Raspberry Pi dashboard.');
    const form = byId('login-form');
    const password = byId('password');
    const toggle = node('button', 'product-password-toggle', label('Показать пароль', 'Show password'));
    toggle.type = 'button';
    toggle.setAttribute('aria-pressed', 'false');
    toggle.onclick = () => {
      const visible = password.type === 'password';
      password.type = visible ? 'text' : 'password';
      toggle.setAttribute('aria-pressed', String(visible));
      toggle.textContent = visible ? label('Скрыть пароль', 'Hide password') : label('Показать пароль', 'Show password');
    };
    password.after(toggle);
    form.addEventListener('submit', () => {
      password.type = 'password';
      toggle.setAttribute('aria-pressed', 'false');
      toggle.textContent = 'Показать пароль';
    }, true);
  }
  const top = byId('refresh');
  if (top) top.before(switcher());

  const settings = byId('page-settings');
  if (settings) {
    const languagePanel = node('section', 'panel product-settings');
    languagePanel.innerHTML = '<h2></h2><p class="fine-print"></p><form><label for="product-default-language"></label><select id="product-default-language"><option value="ru">Русский</option><option value="en">English</option></select><button class="button primary"></button></form><p role="status"></p>';
    languagePanel.querySelector('h2').textContent = label('Язык интерфейса', 'Interface language');
    languagePanel.querySelector('p').textContent = label('Язык по умолчанию применяется в новых браузерах.', 'The default language applies in new browsers.');
    languagePanel.querySelector('label').textContent = label('Язык по умолчанию', 'Default language');
    languagePanel.querySelector('button').textContent = label('Сохранить язык', 'Save language');
    const defaultSelect = languagePanel.querySelector('select');
    defaultSelect.value = deviceDefault;
    languagePanel.querySelector('form').onsubmit = async event => {
      event.preventDefault();
      const status = languagePanel.querySelector('[role=status]');
      try {
        const saved = await post('/api/product/preferences', {language:defaultSelect.value});
        deviceDefault = saved.language;
        setLanguage(deviceDefault, true);
        status.textContent = label('Язык сохранён.', 'Language saved.');
      } catch (error) { status.textContent = error.message; }
    };
    const releasePanel = node('section', 'panel product-settings');
    releasePanel.innerHTML = '<h2></h2><p class="fine-print"></p><div class="product-release-versions"></div><details><summary>Changelog</summary><pre data-literal></pre></details><div class="form-actions"><button class="button secondary product-check"></button><button class="button primary product-install" hidden></button><a class="button secondary" href="https://github.com/sm0k3net/pi-control/releases" target="_blank" rel="noopener noreferrer">GitHub ↗</a></div><p role="status"></p>';
    releasePanel.querySelector('h2').textContent = label('Обновление Pi Control', 'Pi Control updates');
    releasePanel.querySelector('p').textContent = label('Версии интерфейса и история изменений из официального репозитория.', 'UI versions and release notes from the official repository.');
    const check = releasePanel.querySelector('.product-check');
    const install = releasePanel.querySelector('.product-install');
    const releaseStatus = releasePanel.querySelector('[role=status]');
    check.textContent = label('Проверить обновления', 'Check for updates');
    install.textContent = label('Установить обновление', 'Install update');
    let release;
    async function checkRelease(force = false) {
      if (check.disabled) return;
      check.disabled = true;
      install.hidden = true;
      releaseStatus.textContent = label('Проверяем GitHub…', 'Checking GitHub…');
      try {
        release = await request('/api/product/update', { headers:force ? {'X-Pi-Refresh':'1'} : {} });
        releasePanel.querySelector('.product-release-versions').textContent = 'Pi Control UI ' + release.current + (release.latest ? ' → ' + release.latest : '');
        releasePanel.querySelector('details').hidden = !release.changelog;
        releasePanel.querySelector('pre').textContent = release.changelog || '';
        install.hidden = !release.available;
        releaseStatus.textContent = release.error || (release.available
          ? label('Доступна новая версия.', 'A new version is available.')
          : release.newer ? label('Для релиза нет проверяемого пакета обновления.', 'This release has no verifiable update bundle.')
          : label('Установлена актуальная версия.', 'You are up to date.'));
      } catch (error) { releaseStatus.textContent = error.message; }
      finally { check.disabled = false; }
    }
    check.onclick = () => checkRelease(true);
    install.onclick = async () => {
      install.disabled = true;
      check.disabled = true;
      try {
        await post('/api/product/update', {tag:release.tag});
        location.reload();
      } catch (error) { releaseStatus.textContent = error.message; }
      finally { install.disabled = false; check.disabled = false; }
    };
    const content = byId('settings-organized-content');
    const hub = settings.querySelector('.settings-hub');
    if (content && hub) {
      content.prepend(languagePanel, releasePanel);
      const search = hub.querySelector('input[type=search]');
      const updateVisibility = () => {
        const query = (search?.value || '').trim().toLowerCase();
        for (const [panel, category] of [[languagePanel,'appearance'],[releasePanel,'system']]) {
          panel.hidden = query
            ? !panel.querySelector('h2').textContent.toLowerCase().includes(query)
            : hub.querySelector('[aria-selected=true]')?.dataset.category !== category;
        }
        const empty = content.querySelector('.settings-empty');
        if (empty && (!languagePanel.hidden || !releasePanel.hidden)) empty.hidden = true;
      };
      search?.addEventListener('input', updateVisibility);
      hub.addEventListener('click', updateVisibility);
      updateVisibility();
    } else settings.append(languagePanel, releasePanel);
    new IntersectionObserver(records => {
      if (records.some(record => record.isIntersecting)) checkRelease();
    }).observe(releasePanel);
    request('/api/product/preferences').then(value => {
      deviceDefault = value.language;
      defaultSelect.value = deviceDefault;
      setLanguage(localStorage.getItem('pi-language') || deviceDefault);
    }).catch(() => {});
  }
  const vaultPage = byId('page-vault');
  if (vaultPage && byId('vault-list')) {
    const list = byId('vault-list');
    const search = byId('vault-search');
    const editor = byId('vault-id').closest('.panel');
    const status = node('p', 'product-vault-count');
    status.setAttribute('role', 'status');
    const sort = node('select', 'product-vault-sort');
    sort.setAttribute('aria-label', label('Сортировка записей', 'Sort entries'));
    sort.append(new Option(label('Порядок KDBX', 'KDBX order'), 'original'),
                new Option(label('По названию', 'By name'), 'title'),
                new Option(label('По группе', 'By group'), 'group'));
    const toolbar = node('div', 'product-vault-toolbar');
    search.before(toolbar);
    toolbar.append(search, sort);
    toolbar.after(status);
    const newEntry = node('button', 'button secondary', label('Добавить запись', 'Add entry'));
    newEntry.type = 'button';
    newEntry.onclick = () => {
      byId('vault-clear').click();
      byId('vault-title').focus();
    };
    toolbar.append(newEntry);
    const actions = node('details', 'product-vault-transfer');
    actions.append(node('summary', '', label('Импорт и резервная копия', 'Import and backup')));
    const unlocked = byId('vault-unlocked');
    const transfer = byId('vault-export')?.parentElement;
    if (transfer?.parentElement === unlocked) {
      transfer.before(actions);
      let next = actions.nextElementSibling;
      while (next) {
        const following = next.nextElementSibling;
        actions.append(next);
        next = following;
      }
    }

    const groupForm = node('div', 'field-actions');
    const groupLabel = node('label', '', label('Группа KDBX', 'KDBX group'));
    groupLabel.htmlFor = 'product-vault-group';
    const groupSelect = node('select');
    groupSelect.id = 'product-vault-group';
    const createGroup = node('button', 'button secondary small-btn', label('Новая группа', 'New group'));
    createGroup.type = 'button';
    groupForm.append(groupSelect, createGroup);
    const groupAnchor = editor.querySelector('label[for="vault-title"]');
    groupAnchor.before(groupLabel, groupForm);
    function showGroups(groups) {
      const selection = groupSelect.value;
      groupSelect.replaceChildren(...groups.map(group => new Option(group.name, group.id)));
      if (groups.some(group => group.id === selection)) groupSelect.value = selection;
    }
    let entryGroups = new Map();
    const callVault = vaultCall;
    vaultCall = async function(operation, payload = {}) {
      if (operation === 'save' && groupSelect.value) payload = {...payload, group_id:groupSelect.value};
      const result = await callVault(operation, payload);
      if (operation === 'list' && Array.isArray(result.groups)) {
        showGroups(result.groups);
        entryGroups = new Map(result.entries.map(entry => [entry.id, entry.group_id]));
      }
      return result;
    };
    const revealVaultEntry = vaultReveal;
    vaultReveal = async function(id) {
      await revealVaultEntry(id);
      if (byId('vault-id').value !== id) return;
      if (entryGroups.has(id)) groupSelect.value = entryGroups.get(id);
    };
    byId('vault-clear').addEventListener('click', () => { groupSelect.selectedIndex = 0; });
    createGroup.onclick = () => {
      const dialog = node('dialog', 'panel polished-dialog product-group-dialog');
      dialog.innerHTML = '<form><h2></h2><label for="product-vault-group-name"></label><input id="product-vault-group-name" required maxlength="120"><div class="form-actions"><button class="button primary"></button><button type="button" class="button secondary"></button></div><p role="status"></p></form>';
      dialog.querySelector('h2').textContent = label('Новая группа', 'New group');
      dialog.querySelector('label').textContent = label('Название группы', 'Group name');
      const buttons = dialog.querySelectorAll('form button');
      buttons[0].textContent = label('Создать', 'Create');
      buttons[1].textContent = label('Отменить', 'Cancel');
      buttons[1].onclick = () => dialog.close();
      dialog.querySelector('form').onsubmit = async event => {
        event.preventDefault();
        buttons[0].disabled = true;
        try {
          const group = await vaultCall('group-create', {name:dialog.querySelector('input').value});
          await vaultEntries();
          groupSelect.value = group.id;
          dialog.close();
        } catch (error) {
          dialog.querySelector('[role=status]').textContent = error.message;
        } finally { buttons[0].disabled = false; }
      };
      dialog.addEventListener('close', () => dialog.remove(), {once:true});
      document.body.append(dialog);
      dialog.showModal();
    };

    const restore = node('details', 'product-vault-transfer');
    restore.append(node('summary', '', label('Восстановить KDBX из копии', 'Restore KDBX from backup')));
    const restoreFile = node('input');
    restoreFile.type = 'file';
    restoreFile.accept = '.kdbx';
    restoreFile.setAttribute('aria-label', 'KDBX');
    const restorePassword = node('input');
    restorePassword.type = 'password';
    restorePassword.autocomplete = 'off';
    restorePassword.placeholder = label('Мастер-пароль файла', 'Backup master password');
    restorePassword.setAttribute('aria-label', restorePassword.placeholder);
    const restoreButton = node('button', 'button secondary', label('Восстановить хранилище', 'Restore vault'));
    restoreButton.type = 'button';
    const restoreStatus = node('p', 'fine-print');
    restoreStatus.setAttribute('role', 'status');
    restore.append(restoreFile, restorePassword, restoreButton, restoreStatus);
    byId('vault-state').after(restore);
    restoreButton.onclick = async () => {
      const file = restoreFile.files[0];
      if (!file || file.size > 1875000) {
        restoreStatus.textContent = label('Выберите файл KDBX до 1,8 МиБ.', 'Choose a KDBX file up to 1.8 MiB.');
        return;
      }
      restoreButton.disabled = true;
      try {
        const reader = new FileReader();
        const encoded = await new Promise((resolve,reject) => {
          reader.onload = () => resolve(reader.result.split(',')[1]);
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });
        await post('/api/product/vault-restore', {data:encoded,master:restorePassword.value});
        restorePassword.value = '';
        restoreFile.value = '';
        restore.open = false;
        await loadVaultInfo();
        notice(label('Хранилище восстановлено. Введите мастер-пароль, чтобы открыть его.', 'Vault restored. Enter its master password to unlock it.'));
      } catch (error) { restoreStatus.textContent = error.message; }
      finally { restoreButton.disabled = false; }
    };
    const availability = () => { restore.hidden = byId('vault-create').disabled; };
    new MutationObserver(availability).observe(byId('vault-create'), {attributes:true, attributeFilter:['disabled']});
    availability();

    function refreshVaultList() {
      const query = search.value.trim().toLowerCase();
      const rows = [...list.querySelectorAll('.vault-row')];
      for (const [index, row] of rows.entries()) row.dataset.order ??= index;
      const field = row => {
        if (sort.value === 'group') return row.querySelector('small')?.textContent.split(' · ').at(-1) || '';
        return row.querySelector('strong')?.textContent || '';
      };
      rows.sort((a,b) => sort.value === 'original'
        ? Number(a.dataset.order) - Number(b.dataset.order)
        : field(a).localeCompare(field(b), language));
      rows.forEach((row,index) => {
        row.hidden = !!query && !(row.dataset.search || row.textContent.toLowerCase()).includes(query);
        if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
      });
      const count = rows.filter(row => !row.hidden).length;
      status.textContent = label('Записей показано', 'Entries shown') + ' ' + count + ' / ' + rows.length;
      if (language === 'en') status.textContent = 'Entries shown ' + count + ' / ' + rows.length;
      editor.hidden = unlocked.classList.contains('hidden');
    }
    let scheduled;
    const queueRefresh = () => {
      if (scheduled) return;
      scheduled = requestAnimationFrame(() => { scheduled = 0; refreshVaultList(); });
    };
    search.addEventListener('input', queueRefresh);
    sort.onchange = queueRefresh;
    new MutationObserver(queueRefresh).observe(list, {childList:true});
    new MutationObserver(() => {
      if (unlocked.classList.contains('hidden')) {
        entryGroups.clear();
        list.replaceChildren();
        groupSelect.replaceChildren();
        search.value = '';
      }
      queueRefresh();
    }).observe(unlocked, {attributes:true, attributeFilter:['class']});
    refreshVaultList();
  }

  // Measure language changes against an explicit UI catalog. Avoid translating
  // filenames, passwords, note text, or entries supplied by the user.
  const excluded = '[data-literal],script,style,pre,code,textarea,[contenteditable],#vault-list,#ss-list,.terminal-output';
  new MutationObserver(records => {
    for (const record of records) {
      for (const added of record.addedNodes) {
        if (added.nodeType === Node.ELEMENT_NODE) translateElement(added);
        if (added.nodeType === Node.TEXT_NODE && !added.parentElement?.closest(excluded)) translateText(added);
      }
    }
  }).observe(document.body, {childList:true, subtree:true});
  setLanguage(language);
  request('/product-en.json').then(catalog => {
    for (const [ru,en] of Object.entries(catalog)) translations.set(ru,en);
    setLanguage(language);
  }).catch(() => {});
})();
