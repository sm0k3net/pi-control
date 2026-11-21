'use strict';
function piUiReady(init){
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
}
piUiReady(() => {
  const element = (tag,text,cls) => {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    if (cls) item.className = cls;
    return item;
  };
  const settings = document.getElementById('page-settings');
  const backup = document.getElementById('backup-info')?.closest('.panel');
  if (!settings || !backup) return;

  async function rpc(op, data={}) {
    return api('/api/maintenance',{method:'POST',body:JSON.stringify({op,...data})});
  }

  function confirmAction(title, description) {
    return new Promise(resolve => {
      const dialog = element('dialog','','panel maintenance-dialog');
      const form = element('form');
      form.method = 'dialog';
      const heading = element('h2',title);
      heading.id = 'maintenance-dialog-title';
      dialog.setAttribute('aria-labelledby',heading.id);
      form.append(heading,element('p',description));
      const actions = element('div','','form-actions');
      const back = element('button','Отмена','button secondary');
      back.value = 'cancel';
      back.autofocus = true;
      const proceed = element('button','Продолжить','button primary');
      proceed.value = 'confirm';
      actions.append(back,proceed);
      form.append(actions);
      dialog.append(form);
      dialog.addEventListener('close',() => {
        resolve(dialog.returnValue === 'confirm');
        dialog.remove();
      },{once:true});
      document.body.append(dialog);
      dialog.showModal();
    });
  }

  settings.classList.add('has-managed-updates');
  const panel=element('section','','panel maintenance-panel');
  panel.innerHTML='<header class="maintenance-head"><div class="maintenance-icon" aria-hidden="true">↻</div><div><h2>Обновление Pi Control</h2><p>Один релиз для сервера и интерфейса, с проверкой после перезапуска и откатом.</p></div><span data-version class="maintenance-tag">—</span></header><div class="form-actions"><button data-check type="button" class="button secondary">Проверить релиз</button><button data-install type="button" class="button primary" hidden>Обновить панель</button><button data-undo type="button" class="button secondary" disabled>Откатить</button></div><details data-notes hidden><summary>Изменения в релизе</summary><pre></pre></details><p data-update-status role="status" class="maintenance-status"></p>';
  const settingsBody=document.getElementById('settings-organized-content')||settings;
  const hub=settings.querySelector('.settings-hub');
  settingsBody.prepend(panel);
  function syncPanel(){
    if(!hub)return;
    const query=hub.querySelector('input[type=search]')?.value.trim().toLowerCase();
    panel.hidden=query ? !'pi control обновление интерфейс сервер откат upgrade update rollback'.includes(query) :
      hub.querySelector('[aria-selected=true]')?.dataset.category!=='system';
    if (!panel.hidden) {
      const empty=settingsBody.querySelector('.settings-empty');
      if(empty)empty.hidden=true;
    }
  }
  hub?.addEventListener('click',syncPanel);
  hub?.querySelector('input[type=search]')?.addEventListener('input',syncPanel);
  syncPanel();
  const install=panel.querySelector('[data-install]');
  const undo=panel.querySelector('[data-undo]');
  const updateMessage=panel.querySelector('[data-update-status]');
  let candidate;
  const checkButton=panel.querySelector('[data-check]');
  let checked=false;
  async function check() {
    if(checkButton.disabled)return;
    checked=true;
    const button=checkButton;
    button.disabled=true;
    updateMessage.textContent='Проверяем GitHub…';
    try {
      candidate=await rpc('check');
      install.hidden=!candidate.available;
      undo.disabled=!candidate.rollback;
      panel.querySelector('[data-version]').textContent=candidate.current+(candidate.available?' → '+candidate.tag:'');
      panel.querySelector('[data-notes]').hidden=!candidate.notes;
      panel.querySelector('pre').textContent=candidate.notes||'';
      updateMessage.textContent=candidate.available ? 'Найдено серверное обновление.' :
        candidate.digest ? 'Более новой серверной версии нет.' : 'Для этого релиза серверный пакет отсутствует.';
    } catch(error){ updateMessage.textContent=error.message; }
    finally{button.disabled=false;}
  }
  checkButton.onclick=check;
  new IntersectionObserver(entries=>{
    if(!checked && entries.some(entry=>entry.isIntersecting))check();
  }).observe(panel);

  const wizard=element('details','','maintenance-wizard');
  wizard.innerHTML='<summary><strong>Мастер восстановления USB</strong><small>Проверка и извлечение в отдельный каталог</small></summary><form><div class="maintenance-grid"><label>Накопитель<select name="usb" required></select></label><label>Резервная копия<select name="backup" required></select></label><label>Закрытый ключ age<input name="key" type="password" autocomplete="off" required></label></div><fieldset><legend>Что восстановить</legend><label><input type="checkbox" name="home" checked> Домашний каталог</label><label><input type="checkbox" name="postgres"> SQL-дамп PostgreSQL</label><label><input type="checkbox" name="services"> Данные и конфигурации сервисов</label></fieldset><button class="button primary" type="submit">Проверить и извлечь</button></form><p data-restore-status role="status" class="maintenance-status"></p>';
  backup.append(wizard);
  const form=wizard.querySelector('form');
  const restoreMessage=wizard.querySelector('[data-restore-status]');
  const disks=form.elements.usb;
  const backups=form.elements.backup;

  async function loadBackups(){
    backups.replaceChildren(new Option('Проверяем накопитель…',''));
    if (!disks.value) {
      backups.replaceChildren(new Option('Сначала выберите USB',''));
      return;
    }
    const selected=disks.value;
    try{
      const rows=await rpc('scan',{uuid:selected});
      if(disks.value!==selected)return;
      backups.replaceChildren(...rows.map(item=>new Option(item.name,item.name)));
      if(!rows.length)backups.append(new Option('Копии не найдены',''));
    }catch(error){restoreMessage.textContent=error.message;}
  }
  disks.onchange=loadBackups;
  wizard.addEventListener('toggle',async ()=>{
    if(!wizard.open)return;
    try{
      const data=await api('/api/platform/backup');
      disks.replaceChildren(...data.devices.filter(d=>d.supported).map(d=>new Option(d.label||d.path,d.uuid)));
      if(!disks.options.length)disks.append(new Option('Подключите USB',''));
      if (data.devices.some(d=>d.uuid===data.settings.uuid && d.supported))disks.value=data.settings.uuid;
      loadBackups();
    }catch(error){restoreMessage.textContent=error.message;}
  });

  let activeControl=null, busy=false, observerBusy=false, lastJob=null;
  async function refresh(){
    if(observerBusy||document.hidden||!settings.classList.contains('active'))return;
    observerBusy=true;
    try{
      const data=await api('/api/maintenance');
      if(data.server_version){
        const next=candidate?.available && candidate.current===data.server_version?' → '+candidate.tag:'';
        const ui=data.ui_version?' · UI '+data.ui_version:'';
        panel.querySelector('[data-version]').textContent='Server '+data.server_version+ui+next;
      }
      busy=data.job?.phase==='running';
      undo.disabled=busy||!data.rollback;
      install.disabled=busy;
      if(data.job?.message){
        const target=activeControl || (data.job.path ? restoreMessage : updateMessage);
        const path=data.job.path ? ' Каталог: '+data.job.path+' · файлов: '+data.job.files+' · пропущено: '+data.job.skipped+'.' : '';
        target.textContent=data.job.message+path;
        target.dataset.phase=data.job.phase;
        if(lastJob!==data.job.time && data.job.phase!=='running' && activeControl===updateMessage){
          install.hidden=true;
          candidate=null;
          activeControl=null;
        }
        lastJob=data.job.time;
      }
    }catch(error){if(activeControl)activeControl.textContent='Панель перезапускается или служба временно недоступна.';}
    finally{observerBusy=false;}
  }

  async function begin(operation, data, target){
    if(busy)return;
    activeControl=target;
    try{
      await rpc(operation,data);
      target.textContent='Операция принята.';
      busy=true;
      install.disabled=true;
      undo.disabled=true;
      refresh();
    }catch(error){target.textContent=error.message;}
  }
  install.onclick=async()=>{
    if(candidate?.available && await confirmAction('Установить '+candidate.tag+'?',
      'Панель и USB-монитор перезапустятся. При сбое вернутся предыдущие версии сервера и интерфейса.'))
      begin('update',{tag:candidate.tag},updateMessage);
  };
  undo.onclick=async()=>{
    if(await confirmAction('Вернуть предыдущую версию?',
      'Будут восстановлены управляемые файлы сервера и версия интерфейса. Панель и USB-монитор перезапустятся.'))
      begin('rollback',{},updateMessage);
  };
  form.onsubmit=async event=>{
    event.preventDefault();
    const parts=['home','postgres','services'].filter(name=>form.elements[name].checked);
    if(!parts.length){restoreMessage.textContent='Выберите хотя бы одну часть.';return;}
    if(!await confirmAction('Извлечь резервную копию?',
      'Будет создан новый каталог восстановления. Действующие файлы и базы останутся доступны.'))return;
    const key=form.elements.key.value.trim();
    form.elements.key.value='';
    begin('restore',{uuid:disks.value,backup:backups.value,parts,key},restoreMessage);
  };
  new MutationObserver(refresh).observe(settings,{attributes:true,attributeFilter:['class']});
  setInterval(refresh,4500);
});

piUiReady(() => {
  const lang = () => document.documentElement.lang === 'en';
  const label = (ru,en) => lang() ? en : ru;
  const node = (tag,value='',cls='') => {
    const el=document.createElement(tag);
    el.textContent=value;
    if(cls)el.className=cls;
    return el;
  };
  const button = (title,primary=false) => {
    const el=node('button',title,'button '+(primary?'primary':'secondary'));
    el.type='button';
    return el;
  };
  const request = (path,data) => data ? api(path,{method:'POST',body:JSON.stringify(data)}) : api(path);
  const nodesPath='/api/platform/node-tools';
  const desktopPath='/api/platform/desktop';
  const templates=[
    ['host','Состояние узла','Host health'],['links','Wi-Fi и Ethernet','Wi-Fi and Ethernet'],
    ['observed','Известные соседи и локальные порты','Known neighbors and local listeners'],
    ['bluetooth','Bluetooth','Bluetooth'],['usb','Подключённые USB','Attached USB']
  ];
  function makePanel(page,ru,en,description,descriptionEn,anchor=null) {
    const root=node('section','','panel maintenance-panel linked-devices');
    root.append(node('h2',label(ru,en)),node('p',label(description,descriptionEn),'linked-lead'));
    if(anchor)anchor.after(root);else page.append(root);
    return root;
  }
  async function run(button,status,work) {
    button.disabled=true;
    status.textContent=label('Выполняется…','Working…');
    try { await work(); status.textContent=''; }
    catch(error) { status.textContent=error.message; }
    finally { button.disabled=false; }
  }
  function auth(id,password='') {
    return {id,password,master: typeof vaultMaster==='string'?vaultMaster:''};
  }
  function field(title,input) {
    const wrap=node('label',title);
    wrap.append(input);return wrap;
  }
  function passwordField() {
    const input=node('input');
    input.type='password';input.autocomplete='off';
    input.placeholder=label('Если не настроен секрет узла','If no node secret is configured');
    return input;
  }
  function selectNodes(all=false) {
    const select=node('select');
    async function refresh() {
      const last=select.value;
      const data=await request(nodesPath);
      select.replaceChildren(new Option(label('Выберите узел','Choose a node'),''));
      if(all)select.add(new Option(label('Авто · узел с сохранённым секретом','Auto · node with a stored secret'),'auto'));
      for(const item of data.nodes)select.add(new Option(item.name+' · '+item.host,item.id));
      if([...select.options].some(o=>o.value===last))select.value=last;
    }
    return [select,refresh];
  }
  function refreshWhenVisible(page,refresh,status) {
    const callback=()=>{if(page.classList.contains('active'))refresh().catch(e=>{ status.textContent=e.message; });};
    new MutationObserver(callback).observe(page,{attributes:true,attributeFilter:['class']});
    callback();
  }
  function renderData(output,data) {
    output.replaceChildren();
    const heading=node('h3',data.node);
    const stamp=node('small',new Date(data.time*1000).toLocaleString());
    output.append(heading,stamp);
    for(const [key,value] of Object.entries(data.result)){
      const keys={system:['Система','System'],services:['Сервисы','Services'],
        interfaces:['Интерфейсы','Interfaces'],routes:['Маршруты','Routes'],wifi:['Wi-Fi','Wi-Fi'],
        wifi_networks:['Сети Wi-Fi из кэша','Cached Wi-Fi networks'],forwarding:['IP-переадресация','IP forwarding'],neighbors:['Известные соседи','Known neighbors'],
        local_listeners:['Локальные TCP/UDP-порты','Local TCP/UDP listeners'],
        adapters:['Адаптеры','Adapters'],devices:['Устройства','Devices']};
      const group=node('details','','linked-result');group.open=true;
      group.append(node('summary',keys[key]?label(...keys[key]):key));
      const rows=Array.isArray(value)?value:[value];
      if(!rows.length)group.append(node('p',label('Записей нет','No records')));
      for(const row of rows) {
        if(row && typeof row==='object'){
          const fields=node('dl','','linked-data');
          for(const [name,item] of Object.entries(row)){
            const display=Array.isArray(item)?item.join(', '):String(item??'—');
            fields.append(node('dt',name),node('dd',display));
          }
          group.append(fields);
        }else group.append(node('p',String(row)));
      }
      output.append(group);
    }
  }

  const cluster=document.getElementById('page-cluster');
  if(cluster){
    const root=makePanel(cluster,'Шаблоны задач','Task templates',
      'Выполняются на выбранном Raspberry Pi. Автовыбор использует сохранённые секреты подключённых узлов.',
      'Runs on the selected Raspberry Pi. Automatic selection uses stored secrets for enrolled nodes.',
      cluster.querySelector('.page-intro'));
    const [select,refresh]=selectNodes(true),pwd=passwordField(),task=node('select');
    for(const [id,ru,en] of templates)task.add(new Option(label(ru,en),id));
    const rows=node('div','','maintenance-grid');
    rows.append(field(label('Исполнитель','Run on'),select),field(label('Шаблон','Template'),task),
      field(label('Пароль лёгкой панели','Node panel password'),pwd));
    const submit=button(label('Делегировать задачу','Delegate task'),true);
    const reload=button(label('Обновить узлы','Refresh nodes'));
    const status=node('p','','maintenance-status'),output=node('div');
    status.setAttribute('role','status');
    const actions=node('div','','form-actions');actions.append(submit,reload);
    root.append(rows,actions,status,output);
    select.onchange=()=>{pwd.disabled=select.value==='auto';};
    reload.onclick=()=>run(reload,status,refresh);
    submit.onclick=()=>run(submit,status,async()=>{
      const data=auth(select.value,pwd.value);pwd.value='';output.replaceChildren();
      if(!data.id)throw Error(label('Выберите исполнителя.','Choose a node.'));
      const result=await request(nodesPath,{...data,op:'task',task:task.value});
      renderData(output,result);
    });
    refreshWhenVisible(cluster,refresh,status);
  }

  const files=document.getElementById('page-files');
  if(files){
    const root=makePanel(files,'USB на удалённом узле','Remote node USB',
      'Просмотр каталогов и скачивание файлов до 8 МиБ. Накопитель открывается для чтения.',
      'Browse folders and download files up to 8 MiB. Drives are opened read-only.');
    const [select,refresh]=selectNodes(),pwd=passwordField(),disk=node('select');
    const grid=node('div','','maintenance-grid');
    grid.append(field(label('Узел','Node'),select),field(label('Пароль лёгкой панели','Node panel password'),pwd),
      field(label('Накопитель','Drive'),disk));
    const scan=button(label('Найти USB','Find USB'),true),status=node('p','','maintenance-status');
    const content=node('div');status.setAttribute('role','status');
    root.append(grid,scan,status,content);
    const reset=()=>{disk.replaceChildren();content.replaceChildren();pwd.value='';};
    select.onchange=reset;
    async function browse(path) {
      const id=select.value,uuid=disk.value;
      const data=await request(nodesPath,{...auth(id,pwd.value),op:'usb-list',uuid,path});
      if(select.value!==id||disk.value!==uuid)return;
      content.replaceChildren(node('h3',data.node+' · /'+data.result.path));
      if(path){
        const up=button(label('↑ Выше','↑ Up'));
        up.onclick=()=>run(up,status,()=>browse(path.split('/').slice(0,-1).join('/')));
        content.append(up);
      }
      if(!data.result.items.length)content.append(node('p',label('Папка пуста','Folder is empty')));
      for(const item of data.result.items){
        const line=node('div','','linked-file'),next=[path,item.name].filter(Boolean).join('/');
        const open=button(item.directory?label('Открыть','Open'):label('Скачать','Download'));
        line.append(node('span',(item.directory?'▸ ':'')+item.name),open);content.append(line);
        open.disabled=!item.directory && item.size>8388608;
        if(open.disabled)open.title=label('Файл больше 8 МиБ','File exceeds 8 MiB');
        open.onclick=()=>run(open,status,async()=>{
          if(item.directory){await browse(next);return;}
          const {result}=await request(nodesPath,{...auth(id,pwd.value),op:'usb-download',uuid,path:next});
          const bytes=Uint8Array.from(atob(result.base64),char=>char.charCodeAt(0));
          const url=URL.createObjectURL(new Blob([bytes])), a=node('a');
          a.href=url;a.download=result.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
        });
      }
    }
    scan.onclick=()=>run(scan,status,async()=>{
      if(!select.value)throw Error(label('Выберите узел.','Choose a node.'));
      const id=select.value,{result}=await request(nodesPath,{...auth(id,pwd.value),op:'task',task:'usb'});
      if(select.value!==id)return;
      disk.replaceChildren(...result.devices.map(item=>new Option(item.label+' · '+item.filesystem,item.uuid)));
      content.replaceChildren();
      if(disk.value)await browse('');
      else content.append(node('p',label('Накопители не обнаружены.','No drives detected.')));
    });
    disk.onchange=()=>run(scan,status,()=>browse(''));
    refreshWhenVisible(files,refresh,status);
    new MutationObserver(()=>{if(!files.classList.contains('active'))pwd.value='';})
      .observe(files,{attributes:true,attributeFilter:['class']});
  }

  if(files){
    const root=makePanel(files,'Передача между узлами','Node-to-node transfer',
      'Скопируйте файл между папками обмена. Данные передаются потоком через основной Pi.',
      'Copy a file between node transfer folders. Data streams through the controller.');
    const [source,refreshSource]=selectNodes(),[destination,refreshDestination]=selectNodes();
    const from=node('input'),to=node('input'),fromPass=passwordField(),toPass=passwordField();
    from.placeholder=to.placeholder='folder/file';
    const grid=node('div','','maintenance-grid'),status=node('p','','maintenance-status');
    status.setAttribute('role','status');
    const progress=node('progress');progress.max=100;progress.hidden=true;
    progress.setAttribute('aria-label',label('Прогресс передачи','Transfer progress'));
    const start=button(label('Начать передачу','Start transfer'),true);
    grid.append(field(label('Исходный узел','Source node'),source),field(label('Файл в папке обмена','Source share path'),from),
      field(label('Пароль исходного узла','Source password'),fromPass),
      field(label('Целевой узел','Destination node'),destination),field(label('Целевой путь','Destination path'),to),
      field(label('Пароль целевого узла','Destination password'),toPass));
    root.append(grid,start,progress,status);
    refreshWhenVisible(files,()=>Promise.all([refreshSource(),refreshDestination()]),status);
    start.onclick=async()=>{
      start.disabled=true;progress.hidden=false;progress.removeAttribute('value');
      status.textContent=label('Подключаемся к узлам…','Connecting to nodes…');
      try{
        const payload={op:'transfer-start',source:source.value,destination:destination.value,
          source_path:from.value,target_path:to.value,source_password:fromPass.value,
          destination_password:toPass.value,master:typeof vaultMaster==='string'?vaultMaster:''};
        fromPass.value=toPass.value='';
        const {job}=await request(nodesPath,payload);
        while(true){
          await new Promise(resolve=>setTimeout(resolve,1200));
          const result=await request(nodesPath,{op:'transfer-status',job});
          if(result.total)progress.value=Math.floor(result.bytes*100/result.total);
          status.textContent=result.bytes.toLocaleString()+' '+label('байт','bytes');
          if(result.phase==='error')throw new Error(result.error);
          if(result.phase==='complete'){
            progress.value=100;
            status.textContent=label('Файл передан.','Transfer complete.');
            break;
          }
        }
      }catch(error){status.textContent=error.message;progress.hidden=true;}
      finally{start.disabled=false;}
    };
  }

  const tunnels=document.getElementById('page-tunnels');
  if(tunnels){
    const root=makePanel(tunnels,'Локальные сети узлов Pi','Pi node LANs',
      'Одобренные LAN-маршруты Tailscale. Приём маршрутов включает все разрешённые subnet routes на этом Pi.',
      'Approved Tailscale LAN routes. The receive switch applies to all allowed subnet routes on this Pi.',
      tunnels.querySelector('.page-intro'));
    const rows=node('div'),status=node('p','','maintenance-status');
    status.setAttribute('role','status');
    const controls=node('div','','form-actions');
    const reload=button(label('Обновить','Refresh'));
    const toggle=button(label('Приём маршрутов','Receive routes'),true);
    toggle.disabled=true;controls.append(reload,toggle);root.append(controls,status,rows);
    let info;
    const render=data=>{
      info=data;
      toggle.textContent=data.accept_routes?label('Выключить приём маршрутов','Disable route acceptance'):
        label('Включить приём маршрутов','Enable route acceptance');
      toggle.disabled=!data.accept_routes&&!data.routes.some(r=>r.online&&!r.overlap);
      rows.replaceChildren();
      if(!data.routes.length)rows.append(node('p',label(
        'Одобренных маршрутов пока нет. Объявите локальную сеть в лёгкой панели и одобрите её в Tailscale.',
        'No approved routes yet. Advertise an attached subnet from the node panel and approve it in Tailscale.')));
      for(const route of data.routes){
        const line=node('div','','linked-file');
        const state=route.overlap?label('Пересекается с локальной сетью','Overlaps the local LAN'):
          !route.online?label('Узел не в сети','Node offline'):
          route.installed?label('Маршрут установлен','Route installed'):label('Одобрен, приём выключен','Approved, not installed');
        line.append(node('strong',route.node+' · '+route.cidr),node('span',state));rows.append(line);
      }
    };
    const refresh=async()=>render(await request(nodesPath,{op:'remote-lans'}));
    reload.onclick=()=>run(reload,status,refresh);
    toggle.onclick=async()=>{
      if(!info)return;
      if(!info.accept_routes&&!confirm(label('Включить на этом Pi приём всех разрешённых маршрутов Tailscale?',
        'Accept all allowed Tailscale routes on this Pi?')))return;
      await run(toggle,status,async()=>{
        render(await request(nodesPath,{op:'remote-lans-accept',enabled:!info.accept_routes}));
      });
      if(info)toggle.disabled=!info.accept_routes&&!info.routes.some(r=>r.online&&!r.overlap);
    };
    refreshWhenVisible(tunnels,refresh,status);
  }

  const system=document.getElementById('page-system');
  if(system){
    const parent=document.getElementById('desktop-status')?.closest('.panel');
    const root=makePanel(system,'Удалённые рабочие столы','Remote desktops',
      'Подключайтесь к Mac по VNC и Windows по RDP через Tailscale. Mac открывается напрямую в браузере. Для обычного VNC и RDP сначала откройте рабочий стол Pi.',
      'Reach Mac over VNC and Windows over RDP via Tailscale. Mac opens directly in your browser. Standard VNC and RDP start on the Pi desktop.',parent);
    const add=node('details','','linked-result');
    add.append(node('summary',label('Добавить устройство','Add a device')));
    const form=node('form','','linked-form'),grid=node('div','','maintenance-grid');
    const protocol=node('select');
    protocol.add(new Option('Mac / Apple Screen Sharing','mac'));
    protocol.add(new Option('Windows / RDP','rdp'));
    protocol.add(new Option(label('Обычный VNC (пароль)','Standard VNC (password)'),'vnc'));
    const name=node('input'),address=node('input'),username=node('input');
    name.required=true;name.maxLength=80;address.required=true;address.placeholder='100.x.x.x';username.maxLength=128;
    const user=field(label('Имя пользователя (необязательно)','Username (optional)'),username);
    protocol.onchange=()=>{user.hidden=protocol.value==='vnc';};
    let editing='';
    grid.append(field(label('Название','Name'),name),field(label('IPv4 Tailscale','Tailscale IPv4'),address),
      field(label('Протокол','Protocol'),protocol),user);
    const save=button(label('Сохранить','Save'),true);save.type='submit';
    const status=node('p','','maintenance-status'),listing=node('div');
    status.setAttribute('role','status');
    form.append(grid,save);add.append(form);root.append(add,status,listing);
    function render(data){
      listing.replaceChildren();
      if(!data.targets.length)listing.append(node('p',label('Рабочих столов пока нет.','No desktops saved.')));
      for(const target of data.targets){
        const item=node('div','','linked-file'),detail=node('div'),actions=node('div','','form-actions');
        detail.append(node('strong',target.name),node('p',target.protocol.toUpperCase()+' · '+target.address+(target.username?' · '+target.username:'')));
        const open=button(label('Подключиться','Connect'),true),remove=button(label('Удалить','Remove'));
        open.disabled=!data.viewers[target.protocol];
        if(open.disabled)open.title=label('Клиент не установлен на Pi','Viewer not installed on the Pi');
        const edit=button(label('Изменить','Edit'));
        edit.onclick=()=>{
          editing=target.id;name.value=target.name;address.value=target.address;
          protocol.value=target.protocol;username.value=target.username||'';protocol.onchange();
          add.open=true;name.focus();
        };
        actions.append(open,edit,remove);item.append(detail,actions);listing.append(item);
        function openDesktop(control,legacy=false){
          const tab=window.open('about:blank','_blank');
          if(tab)tab.opener=null;
          run(control,status,async()=>{
            try{
              const result=await request(desktopPath,{op:'open',id:target.id,legacy});
              const url=result.url?new URL(result.url,location.origin):new URL('http://'+result.address);
              if(!result.url){
                url.port=result.port;url.pathname='/vnc.html';url.search='?autoconnect=true&resize=scale';
              }
              if(tab)tab.location.replace(url.href);
              else {
                const link=node('a',label('Открыть noVNC','Open noVNC'));
                link.href=url.href;link.target='_blank';link.rel='noopener';item.append(link);
              }
            }catch(error){tab?.close();throw error;}
          });
         }
        open.onclick=()=>openDesktop(open);
        if(target.protocol==='mac'){
          const fallback=button(label('Обычный режим','Standard fallback'));
          fallback.disabled=open.disabled;
          fallback.onclick=()=>openDesktop(fallback,true);
          actions.insertBefore(fallback,remove);
        }
        remove.onclick=()=>run(remove,status,async()=>render(await request(desktopPath,{op:'remove',id:target.id})));
      }
    }
    form.onsubmit=event=>{
      event.preventDefault();
      run(save,status,async()=>{
        render(await request(desktopPath,{op:editing?'edit':'add',id:editing,name:name.value,address:address.value,
          protocol:protocol.value,username:username.value}));
        form.reset();editing='';protocol.onchange();add.open=false;
      });
    };
    refreshWhenVisible(system,async()=>render(await request(desktopPath)),status);
  }
});

piUiReady(() => {
  const find = id => document.getElementById(id);
  const el = (tag, text='', cls='') => {
    const item = document.createElement(tag);
    item.textContent = text;
    if (cls) item.className = cls;
    return item;
  };
  const btn = (text, cls='button secondary') => {
    const item=el('button',text,cls);item.type='button';return item;
  };
  const knowledge=find('page-knowledge');
  if(knowledge && typeof kb!=='undefined'){
    const layout=knowledge.querySelector('.kb-layout');
    if(layout){
      const field=find('kb-text'),preview=find('kb-render'),browse=find('kb-list');
      const save=find('kb-save'),previewToggle=find('kb-preview'),status=find('kb-status');
      const breadcrumb=el('nav','','kb-crumbs');
      breadcrumb.setAttribute('aria-label','Путь к папке');
      find('kb-path').hidden=true;find('kb-path').after(breadcrumb);
      const filter=find('kb-search'),count=el('p','','kb-count');
      const scope=btn('Искать во всей базе','button secondary kb-scope');
      const results=el('div','','kb-results');results.hidden=true;
      filter.after(scope,count);browse.after(results);
      const editorial=el('p','','kb-editor-meta');field.before(editorial);
      let original='', across=false, version=0, timer, saving=false;
      const dirty=()=> Boolean(kb.editing && field.value!==original);
      const mayLeave=()=>!dirty()||confirm('У заметки есть несохранённые изменения. Перейти дальше?');
      const editState=()=>{
        save.disabled=!dirty()||saving;
        previewToggle.disabled=!kb.editing;
        editorial.textContent=kb.editing?[
          dirty()?'Есть изменения':'Сохранено',
          (field.value.trim()?field.value.trim().split(/\s+/u).length:0)+' слов'
        ].join(' · '):'';
      };
      const renderBase=kbRender;
      kbRender=()=>{
        renderBase();
        breadcrumb.replaceChildren();
        let current='';
        for(const part of ['База знаний',...kb.path.split('/').filter(Boolean)]){
          const target=breadcrumb.childElementCount?[current,part].filter(Boolean).join('/'):'';
          current=target;
          const crumb=btn(part,'kb-crumb');
          if(target===kb.path)crumb.setAttribute('aria-current','page');
          crumb.onclick=()=>{if(!mayLeave())return;clearEditor();kb.path=target;kb.selected=null;kbLoad();};
          breadcrumb.append(crumb);
        }
        if(!across)count.textContent=kb.items.length+' в папке';
        editState();
      };
      const openBase=kbOpen;
      kbOpen=async name=>{
        await openBase(name);
        if(kb.editing){original=field.value;previewToggle.textContent='Просмотр';}
        editState();
      };
      function clearEditor(){
        kb.editing=null;original='';field.value='';field.hidden=false;
        preview.hidden=true;find('kb-current').textContent='Откройте заметку';
        editState();
      }
      browse.addEventListener('click',event=>{
        if(!event.target.closest('button'))return;
        if(!mayLeave()){event.stopImmediatePropagation();return;}
        clearEditor();
      },true);
      for(const id of ['kb-up','kb-new','kb-delete','kb-rename']){
        const action=find(id);
        action.addEventListener('click',event=>{
          if(!mayLeave()){
            event.preventDefault();event.stopImmediatePropagation();
          }else if(id==='kb-up')clearEditor();
        },true);
      }
      field.addEventListener('input',editState);
      save.onclick=async()=>{
        if(!dirty()||saving)return;
        const editing=kb.editing, draft=field.value;
        saving=true;editState();
        try{
          await fmOp('write',{root:'knowledge',...editing,text:draft});
          const current=await fmOp('read',{root:'knowledge',path:editing.path});
          if(kb.editing===editing){editing.modified=current.modified;original=draft;status.textContent='Сохранено · '+editing.path;}
          loadKnowledge();
        }catch(error){status.textContent=error.message;}
        finally{saving=false;editState();}
      };
      knowledge.addEventListener('keydown',event=>{
        if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save.click();}
      });
      window.addEventListener('beforeunload',event=>{if(dirty())event.preventDefault();});
      previewToggle.onclick=()=>{
        if(!kb.editing)return;
        if(!preview.hidden){
          preview.hidden=true;field.hidden=false;field.focus();previewToggle.textContent='Просмотр';
          return;
        }
        preview.replaceChildren();
        let block=null,list=null;
        for(const line of field.value.split('\n')){
          if(/^\s*```/.test(line)){
            if(block)block=null;
            else {block=el('code');const pre=el('pre');pre.append(block);preview.append(pre);}
            list=null;continue;
          }
          if(block){block.append(document.createTextNode(line+'\n'));continue;}
          const title=/^(#{1,6})\s+(.+)$/.exec(line);
          const bullet=/^\s*([-*+]|\d+\.)\s+(.+)$/.exec(line);
          if(bullet){
            const kind=/\d/.test(bullet[1])?'ol':'ul';
            if(!list||list.localName!==kind){list=el(kind);preview.append(list);}
            list.append(el('li',bullet[2]));continue;
          }
          list=null;
          if(title)preview.append(el('h'+Math.min(6,title[1].length+1),title[2]));
          else if(line.trim())preview.append(el('p',line));
        }
        preview.hidden=false;field.hidden=true;previewToggle.textContent='Редактировать';
      };
      async function search(query, sequence){
        try{
          const found=await fmOp('knowledge-search',{query});
          if(sequence!==version)return;
          results.replaceChildren();
          count.textContent=found.items.length+' совпадений · '+found.checked+' заметок'+(found.partial?' · поиск ограничен':'');
          for(const item of found.items){
            const hit=btn(item.path,'kb-hit');
            if(item.snippet)hit.append(el('small',item.snippet));
            hit.onclick=async()=>{
              if(!mayLeave())return;
              clearEditor();
              const pieces=item.path.split('/'),name=pieces.pop();
              kb.path=pieces.join('/');kb.selected=name;await kbLoad();await kbOpen(name);
            };
            results.append(hit);
          }
        }catch(error){if(sequence===version)count.textContent=error.message;}
      }
      filter.oninput=()=>{
        clearTimeout(timer);const current=++version;
        if(!across){kbRender();return;}
        const query=filter.value.trim();
        results.replaceChildren();
        count.textContent=query.length<2?'Введите не менее двух символов':'Поиск…';
        if(query.length>=2)timer=setTimeout(()=>search(query,current),260);
      };
      scope.onclick=()=>{
        across=!across; browse.hidden=across;results.hidden=!across;
        scope.textContent=across?'Искать в текущей папке':'Искать во всей базе';
        scope.setAttribute('aria-pressed',String(across));filter.oninput();filter.focus();
      };
      kbRender();
    }
  }

  const page=find('page-terminal');
  if(!page || typeof Terminal!=='function' || typeof FitAddon==='undefined')return;
  const stage=find('terminal-emulator'),target=find('terminal-target'),secret=find('terminal-secret');
  const start=find('terminal-start'),stop=find('terminal-stop'),state=find('terminal-state');
  if(!stage||!target||!secret||!start||!stop||!state)return;
  const tabs=el('div','','terminal-tabs');
  tabs.setAttribute('role','group');tabs.setAttribute('aria-label','Терминальные сессии');
  const tools=el('div','','terminal-tools');
  const empty=el('p','Выберите устройство и откройте терминал.','terminal-empty');
  stage.before(tabs,tools);stage.append(empty);stage.classList.add('terminal-workbench');
  const sessions=[];
  let selected=null,creating=false, font=Math.min(20,Math.max(11,Number(localStorage.getItem('pi-terminal-font'))||13));
  const call=(action,data)=>api('/api/feature/terminal-'+action,{method:'POST',body:JSON.stringify(data)});
  function render(){
    tabs.replaceChildren();
    for(const session of sessions){
      const tile=btn(session.label,'terminal-tab');
      tile.setAttribute('aria-pressed',String(session===selected));
      tile.append(el('small',session.running?'Открыт':'Завершён'));
      tile.onclick=()=>activate(session);
      tabs.append(tile);
    }
    empty.hidden=!!selected;
    start.disabled=creating;
    stop.disabled=!selected;
    stop.textContent=selected?.running?'Завершить сессию':'Закрыть вкладку';
    state.textContent=selected?.message || 'Терминал не открыт.';
  }
  function fit(session){
    if(session===selected && page.classList.contains('active'))session.fit.fit();
  }
  function activate(session){
    selected=session;
    for(const entry of sessions)entry.element.hidden=entry!==session;
    render();
    if(session)requestAnimationFrame(()=>{fit(session);session.terminal.focus();});
  }
  function send(session,data){
    if(!session?.running)return;
    const encoder=new TextEncoder();
    let chunk='',bytes=0;const chunks=[];
    for(const point of data){
      const size=encoder.encode(point).length;
      if(bytes+size>7000){chunks.push(chunk);chunk='';bytes=0;}
      chunk+=point;bytes+=size;
    }
    if(chunk)chunks.push(chunk);
    for(const part of chunks){
      session.queue=session.queue.then(()=>session.running&&call('write',{id:session.id,data:part}))
        .catch(error=>{session.message=error.message;if(selected===session)render();});
    }
  }
  async function poll(session){
    if(!session.running)return;
    try{
      const data=await call('read',{id:session.id});
      if(!session.running)return;
      if(data.data)session.terminal.write(data.data);
      if(!data.running){
        session.running=false;session.message='Сессия завершена.';render();return;
      }
      if(session.retry){session.retry=0;session.message='Подключено · '+session.label;if(selected===session)render();}
      session.timer=setTimeout(()=>poll(session),selected===session&&!document.hidden?300:1250);
    }catch(error){
      if(!session.running)return;
      session.retry=(session.retry||0)+1;
      session.message=error.message;
      if(/сессия завершена|сессия не найдена|требуется вход/i.test(error.message))session.running=false;
      if(session===selected||!session.running)render();
      if(session.running)session.timer=setTimeout(()=>poll(session),Math.min(8000,1500*session.retry));
    }
  }
  function showSecret(){
    const label=page.querySelector('[for="terminal-secret"]');
    if(label)label.hidden=!target.value;
    secret.hidden=!target.value;
  }
  target.onchange=()=>{
    showSecret();
    const running=sessions.find(item=>item.target===target.value&&item.running);
    if(running)activate(running);
  };
  start.onclick=async()=>{
    const host=target.value, label=target.selectedOptions[0]?.textContent||'Raspberry Pi';
    const existing=sessions.find(item=>item.target===host&&item.running);
    if(existing){activate(existing);return;}
    if(creating)return;
    creating=true;render();state.textContent='Подключаемся · '+label;
    try{
      const auth=host?{...sshAuth(sshNodeCache.find(n=>n.id===host),secret.value),node_id:host}:{};
      const data=await call('start',auth);
      secret.value='';
      const mount=el('div','','terminal-pane');stage.append(mount);
      const terminal=new Terminal({cursorBlink:true,scrollback:4000,fontSize:font,theme:{background:'#0e1c16',foreground:'#e5f5eb'}});
      const fitAddon=new FitAddon.FitAddon();
      terminal.loadAddon(fitAddon);
      const session={id:data.id,target:host,label,element:mount,terminal,fit:fitAddon,running:true,
                     message:data.message,timer:null,queue:Promise.resolve()};
      sessions.push(session);activate(session);terminal.open(mount);
      terminal.onData(value=>send(session,value));
      terminal.onResize(({rows,cols})=>{
        if(session.running&&cols>=20&&rows>=5)call('resize',{id:session.id,rows,cols}).catch(()=>{});
      });
      requestAnimationFrame(()=>fit(session));
      poll(session);
    }catch(error){state.textContent=error.message;toast(error.message);}
    finally{creating=false;start.disabled=false;}
  };
  stop.onclick=async()=>{
    const session=selected;if(!session)return;
    stop.disabled=true;
    try{
      if(session.running)await call('stop',{id:session.id});
      session.running=false;clearTimeout(session.timer);
      session.terminal.dispose();session.element.remove();
      sessions.splice(sessions.indexOf(session),1);
      activate(sessions.at(-1)||null);
    }catch(error){state.textContent=error.message;stop.disabled=false;}
  };
  find('terminal-form').onsubmit=event=>{
    event.preventDefault();const input=find('terminal-input');
    if(!selected?.running){state.textContent='Сначала откройте сессию.';return;}
    send(selected,input.value+'\r');input.value='';selected.terminal.focus();
  };
  for(const [name,data] of [['Ctrl+C','\x03'],['Tab','\t'],['Esc','\x1b']]){
    const key=btn(name);
    key.onclick=()=>{send(selected,data);selected?.terminal.focus();};
    tools.append(key);
  }
  const clear=btn('Очистить экран');clear.onclick=()=>selected?.terminal.clear();
  tools.append(clear);
  for(const [name,size] of [['A−',-1],['A+',1]]){
    const change=btn(name);
    change.onclick=()=>{
      font=Math.min(20,Math.max(11,font+size));localStorage.setItem('pi-terminal-font',font);
      for(const session of sessions)session.terminal.options.fontSize=font;
      if(selected)fit(selected);
    };
    tools.append(change);
  }
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>{if(selected)fit(selected);}).observe(stage);
  new MutationObserver(()=>{if(selected)fit(selected);}).observe(page,{attributes:true,attributeFilter:['class']});
  showSecret();render();
});

piUiReady(() => {
  const app=document.getElementById('app'), page=document.getElementById('page-vault');
  const vaultState=document.getElementById('vault-state');
  if(!app||!page||!vaultState||typeof vaultCall!=='function')return;
  const en=()=>document.documentElement.lang==='en';
  const t=(ru,eng)=>en()?eng:ru;
  const make=(tag,value='',cls='')=>{
    const el=document.createElement(tag);
    el.textContent=value;if(cls)el.className=cls;return el;
  };
  const button=(text,cls='button secondary')=>{
    const el=make('button',text,cls);el.type='button';return el;
  };
  const active=()=>!app.classList.contains('hidden');
  const key='pi-vault-widget';
  page.classList.add('vault-workspace-v2');
  const toolbar=make('section','','panel vault-workspace-bar');
  const description=make('div');
  description.append(make('strong',t('Хранилище KDBX','KDBX vault')),
    make('small',t('Пароли, логины и доступ сервисов','Passwords, usernames and service access')));
  const preference=make('button','','button secondary vault-widget-preference');
  preference.type='button';
  const toggle=make('input');toggle.type='checkbox';
  toggle.checked=localStorage.getItem(key)==='1';
  toggle.hidden=true;
  toolbar.append(description,preference,toggle);
  const intro=page.querySelector('.page-intro');
  if(intro)intro.after(toolbar);
  else page.prepend(toolbar);
  const secretView=make('section','','panel vault-service-view');
  secretView.hidden=true;
  const nav=make('div','','vault-workspace-nav');
  const passwords=button(t('Записи KDBX','KDBX entries'));
  const services=button(t('Сервисные секреты','Service secrets'));
  nav.append(passwords,services);toolbar.after(nav,secretView);
  const serviceHeading=make('h2',t('Сервисные секреты','Service secrets'));
  const serviceDescription=make('p',t('Разрешения и привязки продолжают работать при закрытом KDBX.',
    'Service grants and bindings stay active when KDBX is locked.'),'fine-print');
  const serviceRows=make('div');
  secretView.append(serviceHeading,serviceDescription,serviceRows);
  let view='entries';
  function switchView(value){
    view=value;
    secretView.hidden=value!=='services';
    for(const element of page.children){
      if([intro,toolbar,nav,secretView].includes(element))continue;
      element.classList.toggle('vault-view-hidden',value==='services');
    }
    passwords.setAttribute('aria-pressed',String(value==='entries'));
    services.setAttribute('aria-pressed',String(value==='services'));
    if(value==='services')loadServiceCatalog();
  }
  passwords.onclick=()=>switchView('entries');
  services.onclick=()=>switchView('services');
  async function loadServiceCatalog(){
    serviceRows.replaceChildren(make('p',t('Загружаем…','Loading…')));
    try{
      const data=await api('/api/platform/vault-tools');
      serviceRows.replaceChildren();
      if(!data.entries.length)serviceRows.append(make('p',t('Сервисных секретов пока нет. Создайте секрет из записи в KDBX.',
        'No service secrets yet. Create one from a KDBX entry.')));
      const scopes={nodes:t('Узлы Pi','Pi nodes'),ssh:'SSH',ai:t('ИИ-сервисы','AI services')};
      for(const item of data.entries){
        const row=make('div','','vault-service-row'),left=make('div'),right=make('div','','vault-scope-list');
        left.append(make('strong',item.title),make('small',[item.username,...(item.nodes||[])].filter(Boolean).join(' · ')));
        for(const scope of item.scopes)right.append(make('span',scopes[scope]||scope));
        row.append(left,right);serviceRows.append(row);
      }
    }catch(error){serviceRows.replaceChildren(make('p',error.message));}
  }
  passwords.setAttribute('aria-pressed','true');
  services.setAttribute('aria-pressed','false');
  const launcher=button(t('Пароли','Passwords'),'vault-launcher');
  launcher.setAttribute('aria-controls','pi-vault-widget');launcher.setAttribute('aria-expanded','false');
  const widget=make('section','','panel vault-widget');widget.id='pi-vault-widget';
  widget.setAttribute('aria-label',t('Быстрый доступ к паролям','Password quick access'));
  const head=make('header'),close=button('×','vault-widget-close');
  close.setAttribute('aria-label',t('Свернуть','Collapse'));
  head.append(make('strong',t('Пароли и секреты','Passwords and secrets')),close);
  const pane=make('div'),feedback=make('p','','fine-print');
  feedback.setAttribute('role','status');
  widget.append(head,pane,feedback);
  document.body.append(widget,launcher);
  widget.hidden=true;
  let generation=0,records=[],shownMaster=null;
  function isUnlocked(){return typeof vaultMaster==='string'&&!!vaultMaster;}
  function visibility(){
    launcher.hidden=!toggle.checked||!active();
    preference.textContent=toggle.checked?t('Выключить виджет паролей','Disable password widget'):
      t('Включить виджет паролей','Enable password widget');
    preference.setAttribute('aria-pressed',String(toggle.checked));
    if(launcher.hidden)hide(false);
  }
  function hide(focus=true){
    const open=!widget.hidden;
    ++generation;records=[];shownMaster=null;pane.replaceChildren();feedback.textContent='';
    widget.hidden=true;launcher.setAttribute('aria-expanded','false');
    if(open&&focus&&!launcher.hidden)launcher.focus();
  }
  function setEnabled(value){
    toggle.checked=value;localStorage.setItem(key,value?'1':'0');visibility();
  }
  preference.onclick=()=>setEnabled(!toggle.checked);
  window.addEventListener('storage',event=>{
    if(event.key===key){toggle.checked=event.newValue==='1';visibility();}
  });
  close.onclick=()=>hide();
  launcher.onclick=()=>{widget.hidden?open():hide();};
  widget.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();hide();}});
  const originalLock=lockVault;
  lockVault=function(...args){
    originalLock(...args);
    const wasOpen=!widget.hidden;
    ++generation;records=[];shownMaster=null;pane.replaceChildren();feedback.textContent='';
    if(wasOpen&&active())locked();
  };
  document.getElementById('vault-lock').onclick=()=>lockVault();
  new MutationObserver(visibility).observe(app,{attributes:true,attributeFilter:['class']});
  function locked(){
    pane.replaceChildren();
    const form=make('form','','vault-widget-unlock'),label=make('label',t('Мастер-пароль KDBX','KDBX master password'));
    const password=make('input');password.type='password';password.autocomplete='off';password.required=true;
    const submit=make('button',t('Открыть хранилище','Unlock vault'),'button primary');
    label.append(password);form.append(label,submit);pane.append(form);
    form.onsubmit=async event=>{
      event.preventDefault();const value=password.value;password.value='';submit.disabled=true;
      const request=++generation;
      try{
        const result=await api('/api/vault/list',{method:'POST',body:JSON.stringify({master:value})});
        if(request!==generation||widget.hidden||!active())return;
        vaultMaster=value;vaultTouch();
        const editor=document.getElementById('vault-unlocked');
        editor?.classList.remove('hidden');vaultState.textContent=t('Открыто','Unlocked');
        records=result.entries;shownMaster=value;renderList();
        vaultEntries().catch(()=>{});
      }catch(error){if(request===generation)feedback.textContent=error.message;}
      finally{submit.disabled=false;}
    };
  }
  async function open(){
    widget.hidden=false;launcher.setAttribute('aria-expanded','true');feedback.textContent='';
    if(!isUnlocked()){locked();pane.querySelector('input')?.focus();return;}
    const mine=++generation, master=vaultMaster;
    pane.replaceChildren(make('p',t('Читаем записи…','Loading entries…')));
    try{
      const result=await vaultCall('list');
      if(mine!==generation||vaultMaster!==master||widget.hidden)return;
      records=result.entries;shownMaster=master;renderList();
      pane.querySelector('input')?.focus();
    }catch(error){if(mine===generation)feedback.textContent=error.message;}
  }
  function copy(value,label){
    feedback.textContent='';
    if(!window.isSecureContext||!navigator.clipboard){
      feedback.textContent=t('Буфер обмена требует доверенного HTTPS.','Clipboard requires trusted HTTPS.');return;
    }
    const master=vaultMaster;
    let clipboard;
    if(typeof value==='function'){
      const pending=value().then(text=>{
        if(vaultMaster!==master||!text)throw Error(t('Хранилище закрыто или поле пустое.','Vault locked or field empty.'));
        return text;
      });
      pending.catch(()=>{});
      clipboard=window.ClipboardItem&&navigator.clipboard.write
        ?navigator.clipboard.write([new ClipboardItem({'text/plain':pending.then(text=>new Blob([text],{type:'text/plain'}))})])
        :pending.then(text=>navigator.clipboard.writeText(text));
    }else clipboard=navigator.clipboard.writeText(value);
    clipboard.then(()=>{feedback.textContent=label+' '+t('скопирован','copied');if(isUnlocked())vaultTouch();})
      .catch(error=>{feedback.textContent=error.message||t('Копирование не удалось.','Clipboard write failed.');});
  }
  function renderList(){
    if(!isUnlocked()||shownMaster!==vaultMaster){locked();return;}
    const search=make('input');search.type='search';
    search.placeholder=t('Поиск по паролям','Search passwords');search.setAttribute('aria-label',search.placeholder);
    const list=make('div','','vault-widget-list');
    const disable=button(t('Выключить виджет','Turn off widget'),'button secondary small-btn');
    disable.onclick=()=>setEnabled(false);
    pane.replaceChildren(search,list,disable);
    function render(){
      const q=search.value.trim().toLocaleLowerCase();
      const found=records.filter(item=>[item.title,item.username,item.group].join(' ').toLocaleLowerCase().includes(q));
      list.replaceChildren();
      if(!found.length)list.append(make('p',t('Записи не найдены.','No entries found.')));
      for(const item of found){
        const row=make('div','','vault-quick-row'), name=make('div');
        name.append(make('strong',item.title),make('small',[item.username,item.group].filter(Boolean).join(' · ')));
        const actions=make('div');
        const username=button(t('Логин','Username'),'button secondary small-btn');
        username.disabled=!item.username;username.onclick=()=>copy(item.username,t('Логин','Username'));
        const password=button(t('Пароль','Password'),'button secondary small-btn');
        password.onclick=()=>copy(()=>vaultCall('reveal',{id:item.id}).then(x=>x.password),t('Пароль','Password'));
        const use=button(t('В сервис','Use in service'),'button secondary small-btn');
        use.onclick=()=>serviceDialog(item.id,item.title);
        actions.append(username,password,use);row.append(name,actions);list.append(row);
      }
    }
    search.oninput=render;render();
  }
  let latestEntries=[];
  const beforeVaultCall=vaultCall;
  vaultCall=async function(op,data){
    const result=await beforeVaultCall(op,data);
    if(op==='list'&&Array.isArray(result.entries))latestEntries=result.entries;
    return result;
  };
  const beforeVaultEntries=vaultEntries;
  vaultEntries=async function(...args){
    await beforeVaultEntries(...args);
    const rows=document.querySelectorAll('#vault-list .vault-row');
    if(rows.length!==latestEntries.length)return;
    rows.forEach((row,index)=>{
      const item=latestEntries[index];
      if(!item||row.querySelector('strong')?.textContent!==item.title)return;
      const actions=row.querySelector('.vault-actions');
      if(!actions||actions.querySelector('.vault-convert'))return;
      const use=button(t('Секрет','Make secret'),'button secondary small-btn vault-convert');
      use.title=t('Создать сервисный секрет из этой записи','Create a service secret from this entry');
      use.onclick=()=>serviceDialog(item.id,item.title);
      actions.append(use);
    });
  };
  const editorId=document.getElementById('vault-id');
  const editorTitle=document.getElementById('vault-title');
  const save=document.getElementById('vault-save');
  if(save&&editorId){
    const use=button(t('Сделать сервисным секретом','Create service secret'));
    use.onclick=()=>{
      if(editorId.value)serviceDialog(editorId.value,editorTitle.value);
      else toast(t('Сначала откройте сохранённую запись.','Open a saved entry first.'));
    };
    save.after(use);
  }
  async function serviceDialog(entry,title){
    if(!isUnlocked())return toast(t('Откройте KDBX.','Unlock KDBX first.'));
    const dialog=make('dialog','','panel maintenance-dialog vault-service-dialog'),form=make('form');
    form.append(make('h2',t('Сервисный секрет','Service secret')),make('p',title));
    form.append(make('p',t('Выбранные инструменты будут использовать секрет с закрытым KDBX. Действующие разрешения сохраняются. Для автовыбора привяжите запись к узлу.',
                           'Allowed tools can use the secret while KDBX is locked. Existing grants stay active. Bind it to a node for automatic use.'),'fine-print'));
    const permissions=make('fieldset'),nodeLabel=make('label',t('Привязать к узлу Pi (необязательно)','Bind to a Pi node (optional)'));
    const select=make('select'),status=make('p','','fine-print'),actions=make('div','','form-actions');
    status.setAttribute('role','status');status.textContent=t('Читаем настройки…','Loading settings…');
    permissions.append(make('legend',t('Разрешить использование','Allow usage')));
    const scopes=new Map();
    for(const [id,ru,eng] of [['nodes','Узлы Pi','Pi nodes'],['ssh','Терминал и SSH','Terminal and SSH'],['ai','ИИ-сервисы','AI services']]){
      const option=make('input');option.type='checkbox';option.value=id;
      const label=make('label');label.append(option,make('span',t(ru,eng)));permissions.append(label);scopes.set(id,option);
    }
    nodeLabel.append(select);
    const done=make('button',t('Сохранить','Save'),'button primary');done.disabled=true;
    const cancel=button(t('Закрыть','Close'));cancel.onclick=()=>dialog.close();
    actions.append(done,cancel);form.append(permissions,nodeLabel,status,actions);dialog.append(form);
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    document.body.append(dialog);dialog.showModal();
    try{
      const settings=await api('/api/platform/vault-tools');
      if(!dialog.open)return;
      if(!settings.available)throw Error(t('Хранилище сервисных секретов недоступно.','Service secret store is unavailable.'));
      const current=settings.entries.find(x=>x.id===entry);
      for(const [key,option] of scopes)option.checked=current?current.scopes.includes(key):key==='nodes';
      for(const [key,option] of scopes)option.disabled=!!current?.scopes.includes(key);
      select.add(new Option(t('Без привязки','No binding'),''));
      for(const item of settings.nodes)select.add(new Option(item.name+' · '+item.host,item.id));
      select.onchange=()=>{if(select.value)scopes.get('nodes').checked=true;};
      status.textContent='';done.disabled=false;
    }catch(error){status.textContent=error.message;}
    form.onsubmit=async e=>{
      e.preventDefault();
      if(!isUnlocked()){status.textContent=t('Хранилище заблокировано.','Vault is locked.');return;}
      done.disabled=true;
      try{
        vaultTouch();
        const result=await api('/api/platform/vault-tools',{method:'POST',body:JSON.stringify({
          entry,master:vaultMaster,node:select.value,scopes:[...scopes].filter(([,option])=>option.checked).map(([id])=>id)
        })});
        status.textContent=result.message;
        if(view==='services')loadServiceCatalog();
      }catch(error){status.textContent=error.message;}
      finally{done.disabled=false;}
    };
  }
  visibility();
});

piUiReady(() => {
  const byId=id=>document.getElementById(id);
  const el=(tag,value='',cls='')=>{
    const item=document.createElement(tag);item.textContent=value;
    if(cls)item.className=cls;
    return item;
  };
  const button=(name,cls='button secondary')=>{
    const item=el('button',name,cls);item.type='button';return item;
  };
  const post=(path,data)=>api(path,{method:'POST',body:JSON.stringify(data)});
  const field=(title,input)=>{
    const label=el('label',title);label.append(input);return label;
  };
  const input=(type='text')=>{const item=el('input');item.type=type;return item;};

  const login=byId('login');
  const form=byId('login-form'),password=byId('password');
  if(login&&form&&password){
    const card=form.closest('.login-card');
    const toggle=form.querySelector('.product-password-toggle');
    if(card&&toggle){
      card.classList.add('login-access-card');
      const row=el('div','','login-access-input');
      password.before(row);row.append(password,toggle);
      form.querySelector('button[type=submit]')?.classList.add('login-access-submit');
      const error=byId('login-error');
      if(error)password.setAttribute('aria-describedby',error.id);
      form.addEventListener('submit',()=>{password.type='password';});
    }
  }

  const cluster=byId('page-cluster');
  if(cluster){
    const panel=el('section','','panel node-enrollment-workspace');
    panel.append(el('h2','Поиск и подключение Pi'),
      el('p','Найденные через mDNS и Tailscale устройства. Подключение подтверждается отпечатком TLS.','fine-print'));
    const actions=el('div','','form-actions'),discover=button('Найти узлы','button primary');
    const manual=button('Добавить вручную'),list=el('div','','node-found-list');
    const state=el('p','','fine-print');state.setAttribute('role','status');
    actions.append(discover,manual);panel.append(actions,state,list);
    cluster.querySelector('.page-intro')?.after(panel);
    function fill(item={}){
      byId('node-host').value=item.host||'';
      if(item.port)byId('node-port').value=item.port;
      byId('node-name').value=item.name||'';
      byId('node-fingerprint').value='';
      byId('node-host').focus();
    }
    manual.onclick=()=>fill();
    discover.onclick=async()=>{
      discover.disabled=true;
      state.textContent='Ищем опубликованные узлы…';
      try{
        const result=await post('/api/platform/node-tools',{op:'discover-pi'});
        list.replaceChildren();
        for(const item of result.items){
          const row=el('div','','linked-file'),meta=el('div');
          meta.append(el('strong',item.name),el('p',item.host+' · '+item.source));
          const use=button(item.enrolled?'Уже подключён':'Выбрать');
          use.disabled=item.enrolled;use.onclick=()=>fill(item);
          const choices=el('div','','form-actions');choices.append(use);
          const ssh=button('Подготовить SSH');
          ssh.onclick=()=>{
            if(byId('ssh-host')){
              byId('ssh-host').value=item.host;
              byId('ssh-name').value=item.name;
              byId('ssh-port').value=22;
              byId('ssh-key-fingerprint').value='';
              byId('ssh-host').focus();
            }
          };
          choices.append(ssh);row.append(meta,choices);list.append(row);
        }
        state.textContent=result.items.length?'Выберите узел из списка.':'Узлы не найдены. Можно добавить вручную.';
      }catch(error){state.textContent=error.message;}
      finally{discover.disabled=false;}
    };
    const add=byId('node-add'),fingerprint=byId('node-fingerprint');
    if(add&&fingerprint){
      const verify=button('Получить отпечаток');
      fingerprint.after(verify);
      verify.onclick=async()=>{
        verify.disabled=true;
        try{
          const data=await post('/api/platform/node-tools',{op:'probe-pi',host:byId('node-host').value.trim(),
            port:Number(byId('node-port').value)});
          fingerprint.value=data.fingerprint;
          state.textContent='Сравните SHA-256 с отпечатком на самом узле перед подключением.';
        }catch(error){state.textContent=error.message;}
        finally{verify.disabled=false;}
      };
      add.onclick=async()=>{
        if(!confirm('Отпечаток TLS сверен на самом узле?'))return;
        add.disabled=true;
        const manual=byId('node-manual-panel-password');
        try{
          const result=await post('/api/platform/node-tools',{op:'pair-pi',name:byId('node-name').value,
            host:byId('node-host').value.trim(),port:Number(byId('node-port').value),
            fingerprint:fingerprint.value,entry:byId('node-secret')?.value||'',
            password:manual?.value||'',master:typeof vaultMaster==='string'?vaultMaster:''});
          if(manual)manual.value='';
          state.textContent=result.message;
          if(typeof loadCluster==='function')loadCluster();
        }catch(error){state.textContent=error.message;toast(error.message);}
        finally{add.disabled=false;}
      };
    }
  }

  const backup=byId('backup-info')?.closest('.panel');
  if(backup){
    const group=el('details','','maintenance-wizard');
    group.append(el('summary','Копия на другом узле Pi'));
    const description=el('p','Архивы шифруются age на этом Pi и проверяются по SHA-256 на узле.');
    const select=el('select'),sets=el('select'),password=input('password');
    password.autocomplete='off';
    const options=el('div','','maintenance-grid');
    options.append(field('Подключённый узел',select),field('Пароль узла (если нет секрета)',password),field('Готовая копия',sets));
    const run=button('Создать копию','button primary'),verify=button('Проверить'),refresh=button('Обновить список');
    const actions=el('div','','form-actions'),message=el('p','','maintenance-status');
    message.setAttribute('role','status');actions.append(run,verify,refresh);
    group.append(description,options,actions,message);backup.append(group);
    const url='/api/platform/node-backup';
    const request=op=>post(url,{op,id:select.value,set:sets.value,password:password.value,
      master:typeof vaultMaster==='string'?vaultMaster:''});
    let timer;
    async function read(){
      const res=await api(url);
      if(!select.options.length)select.replaceChildren(...res.nodes.map(item=>new Option(item.name,item.id)));
      run.disabled=!res.recipient_ready||!select.value||res.job.state==='running';
      verify.disabled=!sets.value||res.job.state==='running';
      message.textContent=res.job.message||(!res.recipient_ready?'Настройте публичный ключ age.':'');
      if(group.open&&res.job.state==='running')timer=setTimeout(()=>read().catch(showError),2500);
    }
    const showError=error=>{message.textContent=error.message;};
    group.addEventListener('toggle',()=>{clearTimeout(timer);if(group.open)read().catch(showError);});
    select.onchange=()=>{sets.replaceChildren();verify.disabled=true;};
    refresh.onclick=async()=>{
      try{
        const res=await request('catalog');
        sets.replaceChildren(...res.items.map(value=>new Option(value,value)));
        verify.disabled=!sets.value;
        message.textContent=res.items.length?'Список обновлён.':'Копий на узле пока нет.';
      }catch(error){showError(error);}
    };
    async function runJob(op){
      try{await request(op);password.value='';await read();}
      catch(error){showError(error);}
    }
    run.onclick=()=>runJob('start');verify.onclick=()=>runJob('verify');
  }

  const serviceView=document.querySelector('#page-vault .vault-service-view');
  if(serviceView){
    const root=el('details','','bitwarden-panel');
    root.append(el('summary','Bitwarden / Vaultwarden'));
    root.append(el('p','Подключение по HTTPS и персональному API-ключу. Сессия закрывается через 5 минут.','fine-print'));
    const form=el('form','','maintenance-grid');
    const server=input('url'),id=input(),key=input('password'),master=input('password');
    server.placeholder='https://vault.bitwarden.com';
    for(const item of [server,id,key,master]){item.autocomplete='off';item.required=true;}
    form.append(field('HTTPS-сервер',server),field('Client ID',id),field('Client secret',key),field('Мастер-пароль',master));
    const submit=el('button','Подключиться','button primary');
    form.append(submit);root.append(form);
    const tools=el('div','','form-actions'),query=input('search'),search=button('Найти'),lock=button('Закрыть сессию');
    query.placeholder='Имя записи';query.setAttribute('aria-label','Поиск по Bitwarden');
    const listing=el('div'),status=el('p','','fine-print');status.setAttribute('role','status');
    tools.hidden=true;tools.append(query,search,lock);root.append(tools,listing,status);serviceView.append(root);
    const path='/api/platform/bitwarden';
    let session='',timer;
    async function load(){
      const data=await post(path,{op:'list',session,query:query.value});
      listing.replaceChildren();
      if(!data.items.length)listing.append(el('p','Записи не найдены.'));
      for(const record of data.items){
        const row=el('div','','linked-file'),name=el('div'),buttons=el('div');
        name.append(el('strong',record.name),el('p',record.username));
        for(const [field,title] of [['username','Логин'],['password','Пароль']]){
          const copy=button(title);
          copy.onclick=async()=>{
            try{
              const payload=post(path,{op:'copy',session,id:record.id,field});
              if(window.ClipboardItem&&navigator.clipboard?.write)
                await navigator.clipboard.write([new ClipboardItem({'text/plain':payload.then(
                  value=>new Blob([value.value],{type:'text/plain'}))})]);
              else await navigator.clipboard.writeText((await payload).value);
              status.textContent=title+' скопирован.';
            }catch(error){status.textContent=error.message;}
          };
          buttons.append(copy);
        }
        row.append(name,buttons);listing.append(row);
      }
    }
    function hide(){
      session='';tools.hidden=true;form.hidden=false;listing.replaceChildren();clearTimeout(timer);
    }
    submit.onclick=undefined;
    form.onsubmit=async event=>{
      event.preventDefault();submit.disabled=true;
      try{
        const payload={op:'connect',server:server.value,client_id:id.value,client_secret:key.value,password:master.value};
        key.value=master.value='';
        status.textContent='Открываем хранилище…';
        const result=await post(path,payload);
        session=result.session;tools.hidden=false;form.hidden=true;
        timer=setTimeout(()=>{hide();status.textContent='Срок сессии истёк.';},result.seconds*1000);
        status.textContent='Подключено.';await load();
      }catch(error){status.textContent=error.message;}
      finally{submit.disabled=false;}
    };
    search.onclick=()=>load().catch(error=>{status.textContent=error.message;});
    lock.onclick=async()=>{
      try{await post(path,{op:'lock',session});}catch{}
      hide();status.textContent='Сессия закрыта.';
    };
  }
});
