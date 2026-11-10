import os,json,time,re,subprocess,shutil,sqlite3,urllib.request,urllib.error,threading
from pathlib import Path
import extras
ROOT=Path('/opt/pi-dashboard'); CONFIG=ROOT/'ai-providers.json'; DB=ROOT/'chats.sqlite3'; LOCK=threading.Lock()
def cmd(a,timeout=15):
 p=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
 if p.returncode:raise ValueError('Команда не выполнена. Проверьте параметры и состояние устройства.')
 return p.stdout.strip()
def profiles():
 rows=[]
 for uuid in cmd(['nmcli','-g','UUID','connection','show']).splitlines():
  try:
   typ=cmd(['nmcli','-g','connection.type','connection','show','uuid',uuid])
   if typ not in ('802-11-wireless','wifi'):continue
   def get(f):return cmd(['nmcli','-g',f,'connection','show','uuid',uuid])
   rows.append({'uuid':uuid,'name':get('connection.id'),'ssid':get('802-11-wireless.ssid'),'active':bool(get('GENERAL.DEVICES')!='--' and get('GENERAL.DEVICES')),'hidden':get('802-11-wireless.hidden')=='yes','autoconnect':get('connection.autoconnect')=='yes'})
  except Exception:continue
 return {'profiles':rows}
def wifi(d):
 op=d.get('op'); uid=str(d.get('uuid',''));known={x['uuid']:x for x in profiles()['profiles']}
 if op=='confirm':
  subprocess.run(['systemctl','stop','pi-wifi-rollback.timer'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);return {'message':'Переключение подтверждено; автоматический возврат отменён.'}
 if op in ('connect','delete','edit') and uid not in known:raise ValueError('Подключение не найдено.')
 if op=='delete':
  if known[uid]['active']:raise ValueError('Нельзя удалить активное подключение. Сначала переключитесь на другое.')
  cmd(['nmcli','connection','delete','uuid',uid]);return {'message':'Подключение удалено.'}
 if op=='connect':
  active=next((x['uuid'] for x in known.values() if x['active']),None)
  if not active:raise ValueError('Нет активного Wi-Fi для автоматического возврата; переключите сеть локально.')
  if active==uid:return {'message':'Эта сеть уже активна.'}
  subprocess.run(['systemctl','stop','pi-wifi-rollback.timer'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  cmd(['systemd-run','--unit=pi-wifi-rollback','--on-active=120s','/usr/bin/nmcli','connection','up','uuid',active],20)
  def connect():subprocess.run(['nmcli','--wait','45','connection','up','uuid',uid,'ifname','wlan0'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
  threading.Thread(target=connect,daemon=True).start();return {'message':'Переключаем сеть. Зайдите по новому адресу и подтвердите подключение в течение 2 минут.'}
 if op in ('add','edit'):
  ssid=str(d.get('ssid','')).strip();name=str(d.get('name','')).strip() or ssid;pw=str(d.get('password',''));hidden=bool(d.get('hidden'));auto=bool(d.get('autoconnect',True))
  if not ssid or len(ssid.encode())>32 or len(name)>80 or '\n' in ssid+name:raise ValueError('Укажите корректное имя и SSID сети.')
  if pw and not (8<=len(pw)<=63 or re.fullmatch('[0-9A-Fa-f]{64}',pw)):raise ValueError('Пароль WPA должен содержать 8–63 символа или 64 шестнадцатеричных знака.')
  if op=='add':
   before=set(known);cmd(['nmcli','connection','add','type','wifi','ifname','wlan0','con-name',name,'ssid',ssid]);created=[x['uuid'] for x in profiles()['profiles'] if x['uuid'] not in before];uid=created[0] if len(created)==1 else None
   if not uid:raise ValueError('Профиль создан, но не найден; проверьте NetworkManager.')
  args=['nmcli','connection','modify','uuid',uid,'connection.id',name,'802-11-wireless.ssid',ssid,'802-11-wireless.hidden','yes' if hidden else 'no','connection.autoconnect','yes' if auto else 'no']
  if pw:args+=['wifi-sec.key-mgmt','wpa-psk','wifi-sec.psk',pw]
  cmd(args);return {'message':'Профиль сохранён.'}
 raise ValueError('Неизвестная операция')
def ollama_op(d):
 op=d.get('op');name=str(d.get('name','')).strip()
 if not re.fullmatch(r'[\w.:/-]{1,180}',name):raise ValueError('Укажите корректное имя модели Ollama.')
 if op=='unload':return extras.ollama('/api/generate',{'model':name,'keep_alive':0},timeout=30)
 if op=='delete':
  ip=extras.get_ip()
  if not ip:raise ValueError('Сначала запустите Ollama.')
  req=urllib.request.Request('http://'+ip+':11434/api/delete',data=json.dumps({'model':name}).encode(),headers={'Content-Type':'application/json'},method='DELETE')
  try:
   with urllib.request.urlopen(req,timeout=45):pass
  except Exception:raise ValueError('Не удалось удалить модель.')
  return {'message':'Модель удалена.'}
 if op=='pull':
  def pull():
   try:extras.ollama('/api/pull',{'model':name,'stream':False},timeout=7200)
   except Exception:pass
  threading.Thread(target=pull,daemon=True).start();return {'message':'Загрузка модели началась в фоне. Обновите список позже.'}
 if op=='load':
  extras.ollama('/api/generate',{'model':name,'prompt':'','stream':False,'keep_alive':'10m'},timeout=240);return {'message':'Модель загружена в память на 10 минут.'}
 raise ValueError('Неизвестная операция')
def get_config():
 try:return json.loads(CONFIG.read_text())
 except Exception:return {}
def save_config(d):
 old=get_config()
 for provider in ('groq','openrouter'):
  key=str(d.get(provider,'')).strip()
  if len(key)>500:raise ValueError('Слишком длинный API ключ.')
  if key:old[provider]=key
 extras.atomic(CONFIG,old);return {'message':'Ключи сохранены на Raspberry Pi.'}
def db():
 c=sqlite3.connect(DB,timeout=10);c.execute('create table if not exists chats (id integer primary key, title text, created integer)');c.execute('create table if not exists messages (id integer primary key, chat_id integer, role text, content text, created integer)');return c
def get_chats():
 with db() as c:return {'chats':[{'id':r[0],'title':r[1]} for r in c.execute('select id,title from chats order by id desc limit 100')]}
def get_chat(id):
 with db() as c:return {'messages':[{'role':r[0],'content':r[1]} for r in c.execute('select role,content from messages where chat_id=? order by id',(int(id),))]}
def chat_manage(d):
 try: cid=int(d.get('chat_id',0))
 except (ValueError,TypeError): raise ValueError('Неверный идентификатор чата.')
 op=d.get('op')
 with db() as c:
  if not c.execute('select 1 from chats where id=?',(cid,)).fetchone():raise ValueError('Чат не найден.')
  if op=='rename':
   title=str(d.get('title','')).strip()
   if not title or len(title)>100:raise ValueError('Введите название длиной до 100 символов.')
   c.execute('update chats set title=? where id=?',(title,cid));return {'message':'Название сохранено.'}
  if op=='delete':
   c.execute('delete from messages where chat_id=?',(cid,));c.execute('delete from chats where id=?',(cid,));return {'message':'Диалог удалён.'}
 raise ValueError('Неизвестная операция.')
def ai_chat(d,api_override=None):
 q=str(d.get('message','')).strip();provider=str(d.get('provider','ollama'));model=str(d.get('model','')).strip();cid=d.get('chat_id')
 if not q or len(q)>8000 or not re.fullmatch(r'[\w.:/-]{1,180}',model) or provider not in ('ollama','groq','openrouter'):raise ValueError('Укажите сообщение, провайдера и модель.')
 with db() as c:
  if cid:
   cid=int(cid)
   if not c.execute('select id from chats where id=?',(cid,)).fetchone():raise ValueError('Чат не найден.')
  else:cid=c.execute('insert into chats(title,created) values(?,?)',(q[:70],int(time.time()))).lastrowid
  history=[{'role':r[0],'content':r[1]} for r in c.execute('select role,content from (select id,role,content from messages where chat_id=? order by id desc limit 12) order by id',(cid,))]
  c.execute('insert into messages(chat_id,role,content,created) values(?,?,?,?)',(cid,'user',q,int(time.time())))
 system='Ты полезный ИИ-помощник. Отвечай на русском. Не заявляй, что выполнил действия, если это не подтверждено.'
 if d.get('system_context'):
  system+='\nПо запросу пользователя предоставлены локальные диагностические данные (только чтение): '+json.dumps({'disk':shutil.disk_usage('/')._asdict(),'uptime':Path('/proc/uptime').read_text().split()[0]},ensure_ascii=False)
 messages=[{'role':'system','content':system}]+history+[{'role':'user','content':q}]
 tools=[{'type':'function','function':{'name':'system_status','description':'Получить состояние Raspberry Pi: память, время работы, диск','parameters':{'type':'object','properties':{}}}},{'type':'function','function':{'name':'list_services','description':'Посмотреть состояние контейнеров Docker','parameters':{'type':'object','properties':{}}}},{'type':'function','function':{'name':'network_status','description':'Показать локальные сетевые адреса и текущую сеть','parameters':{'type':'object','properties':{}}}}]
 def invoke(current):
  if provider=='ollama':
   payload={'model':model,'messages':current,'stream':False,'think':False,'options':{'num_ctx':4096,'num_predict':400}}
   if d.get('system_context'):payload['tools']=tools
   return extras.ollama('/api/chat',payload,timeout=240).get('message',{})
  key=api_override or get_config().get(provider)
  if not key:raise ValueError('Сначала сохраните API-ключ провайдера в настройках чата.')
  url={'groq':'https://api.groq.com/openai/v1/chat/completions','openrouter':'https://openrouter.ai/api/v1/chat/completions'}[provider]
  payload={'model':model,'messages':current,'max_tokens':700}
  if d.get('system_context'):payload['tools']=tools
  request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
  try:
   with urllib.request.urlopen(request,timeout=120) as f:a=json.load(f)
   return a['choices'][0]['message']
  except Exception:raise ValueError('Провайдер недоступен. Проверьте модель, API-ключ и интернет.')
 for round in range(3):
  msg=invoke(messages);calls=msg.get('tool_calls') or []
  if not calls:break
  if not d.get('system_context') or round==2:raise ValueError('Превышено число вызовов инструментов.')
  messages.append(msg)
  for call in calls[:3]:
   name=call.get('function',{}).get('name','')
   try:
    if name=='system_status':result={'disk':shutil.disk_usage('/')._asdict(),'uptime_seconds':Path('/proc/uptime').read_text().split()[0],'memory':Path('/proc/meminfo').read_text().splitlines()[:3]}
    elif name=='list_services':result=cmd(['docker','ps','-a','--format','{{.Names}}: {{.Status}}'],12)
    elif name=='network_status':result={'addresses':cmd(['ip','-brief','-4','address'],10),'wifi':cmd(['nmcli','-g','GENERAL.CONNECTION','device','show','wlan0'],10)}
    else:result={'error':'Инструмент недоступен'}
   except Exception:result={'error':'Не удалось получить данные'}
   message={'role':'tool','content':json.dumps(result,ensure_ascii=False),'tool_name':name}
   if call.get('id'):message['tool_call_id']=call['id']
   if provider!='ollama':message.pop('tool_name',None)
   messages.append(message)
 answer=msg.get('content') or msg.get('thinking') or ''
 if not answer:raise ValueError('Модель не вернула текст ответа. Попробуйте другую модель.')
 with db() as c:c.execute('insert into messages(chat_id,role,content,created) values(?,?,?,?)',(cid,'assistant',answer,int(time.time())))
 return {'chat_id':cid,'answer':answer}
BACKUP=ROOT/'backup-settings.json'
def backup_status():
 try:d=json.loads(BACKUP.read_text())
 except Exception:d={}
 try:remotes=cmd(['rclone','listremotes']).splitlines()
 except Exception:remotes=[]
 try:
  data=json.loads(cmd(['lsblk','-J','-o','NAME,SIZE,TYPE,MOUNTPOINTS,RM']));devices=[]
  def walk(xs):
   for x in xs:
    for m in (x.get('mountpoints') or []):
     if m and m.startswith('/') and m not in ('/','/boot/firmware','/boot') and x.get('type') in ('part','disk'):devices.append({'mount':m,'size':x.get('size')})
    walk(x.get('children') or [])
  walk(data.get('blockdevices',[]))
 except Exception:devices=[]
 try:last=json.loads((ROOT/'backup-last.json').read_text())
 except Exception:last={}
 return {'configured':bool(d.get('path')),'path':d.get('path',''),'remote':d.get('remote',''),'schedule':d.get('schedule','off'),'remotes':remotes,'devices':devices,'last':last}
def backup_validate(path):
 p=Path(path)
 if not p.is_absolute() or not p.is_dir():raise ValueError('Укажите существующую папку на подключённой флешке.')
 p=p.resolve();rootdev=cmd(['findmnt','-n','-o','SOURCE','-T','/']);dev=cmd(['findmnt','-n','-o','SOURCE','-T',str(p)])
 if rootdev==dev:raise ValueError('Папка находится на системной SD-карте. Выберите внешнюю флешку.')
 try:
  rootparent=cmd(['lsblk','-n','-o','PKNAME',rootdev]).strip();destparent=cmd(['lsblk','-n','-o','PKNAME',dev]).strip()
  if rootparent and destparent and rootparent==destparent:raise ValueError('Папка находится на системной SD-карте. Выберите внешнюю флешку.')
 except ValueError as e:
  if 'SD-карте' in str(e):raise
 if not os.access(p,os.W_OK):raise ValueError('Нет прав на запись в папку.')
 return str(p)
def backup_config(d):
 path=backup_validate(str(d.get('path','')));remote=str(d.get('remote','')).strip();schedule=d.get('schedule','off')
 if schedule not in ('off','daily','weekly'):raise ValueError('Неизвестное расписание.')
 if remote:
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}:[A-Za-z0-9_./ -]{0,180}',remote) or '..' in remote.split('/'):raise ValueError('Укажите путь rclone в формате remote:папка.')
  if remote.split(':')[0]+':' not in cmd(['rclone','listremotes']).splitlines():raise ValueError('Такое подключение rclone не настроено.')
 extras.atomic(BACKUP,{'path':path,'remote':remote,'schedule':schedule})
 service=Path('/etc/systemd/system/pi-backup-dashboard.service');timer=Path('/etc/systemd/system/pi-backup-dashboard.timer')
 service.write_text('[Unit]\nDescription=Pi Control encrypted backup\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /opt/pi-dashboard/backup_runner.py\n')
 timer.write_text('[Unit]\nDescription=Pi Control backup schedule\n[Timer]\nOnCalendar='+('weekly' if schedule=='weekly' else 'daily')+'\nPersistent=true\n[Install]\nWantedBy=timers.target\n')
 cmd(['systemctl','daemon-reload']);subprocess.run(['systemctl','disable','--now','pi-backup-dashboard.timer'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 if schedule!='off':cmd(['systemctl','enable','--now','pi-backup-dashboard.timer'])
 return {'message':'Настройки резервного копирования сохранены.'}
def backup_start():
 if not BACKUP.exists():raise ValueError('Сначала настройте внешний носитель.')
 d=json.loads(BACKUP.read_text());backup_validate(d.get('path',''))
 if subprocess.run(['systemctl','is-active','--quiet','pi-backup-dashboard.service']).returncode==0:raise ValueError('Резервное копирование уже выполняется.')
 cmd(['systemctl','start','--no-block','pi-backup-dashboard.service']);return {'message':'Резервное копирование запущено. Сервисы временно остановятся на время копирования.'}
