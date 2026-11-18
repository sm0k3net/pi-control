'use strict';
(() => {
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
})();

(() => {
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

  const system=document.getElementById('page-system');
  if(system){
    const parent=document.getElementById('desktop-status')?.closest('.panel');
    const root=makePanel(system,'Удалённые рабочие столы','Remote desktops',
      'Подключайтесь к Mac по VNC и Windows по RDP через Tailscale. Первый пароль открывает рабочий стол Pi. Затем в клиенте введите данные удалённой системы.',
      'Reach Mac over VNC and Windows over RDP via Tailscale. First unlock the Pi desktop. Then enter the remote system credentials in its client.',parent);
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
        open.onclick=()=>{
          const tab=window.open('about:blank','_blank');
          if(tab)tab.opener=null;
          run(open,status,async()=>{
            try{
              const result=await request(desktopPath,{op:'open',id:target.id});
              const url=new URL('http://'+result.address);url.port=result.port;
              url.pathname='/vnc.html';url.search='?autoconnect=true&resize=scale';url.hash='';
              if(tab)tab.location.replace(url.href);
              else {
                const link=node('a',label('Открыть noVNC','Open noVNC'));
                link.href=url.href;link.target='_blank';link.rel='noopener';item.append(link);
              }
            }catch(error){tab?.close();throw error;}
          });
        };
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
})();
