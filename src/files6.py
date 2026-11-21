import ipaddress
import usb_backup
import os,shutil,stat,time,uuid,threading,zipfile,tarfile,hashlib,html,re,secrets,urllib.parse,subprocess,http.client,json,mimetypes
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
ROOTS={'home':Path('/home/sm0k3'),'media':Path('/media'),'mnt':Path('/mnt'),'knowledge':Path('/home/sm0k3/Knowledge')}
uploads={};lock=threading.Lock();share_guard=threading.Lock();shared={'server':None,'process':None,'url':'','token':'','path':None,'expires':0,'log':None}
BUILTIN_MCP_NAME = 'MCP — Узлы Pi.md'
BUILTIN_MCP = '''# MCP — Узлы Pi

## Подключение

- Транспорт: Streamable HTTP. Endpoint текущей установки: `https://<tailscale-ip>:8794/mcp`.
- Авторизация: HTTP-заголовок `Authorization: Bearer <token>`. Токен хранится на основном Pi в `/etc/pi-node-mcp/token`. Читайте его в административной сессии и передавайте в хранилище секретов своего клиента.
- Для HTTPS клиенту нужно доверять публичному сертификату `/etc/pi-node-mcp/cert.pem`. Для Node.js можно указать путь к доверенному сертификату через NODE_EXTRA_CA_CERTS. Для Python настройте TLS-контекст клиента или SSL_CERT_FILE.
- В DeepSeek Harness, Agent Zero или другом MCP-клиенте создайте подключение Streamable HTTP, укажите URL, заголовок авторизации и TLS-доверие. Для проверки вызовите node_list, затем node_health с полученным node_id.
- Доступ к текущему сервису идёт по Tailscale. Имя сервиса systemd: `pi-node-mcp.service`.

## Инструменты

- node_list — список узлов, явно разрешённых этому экземпляру MCP.
- node_health — состояние узла, системные показатели и статусы контейнеров.
- node_links — сетевые интерфейсы, IPv4/IPv6, таблица маршрутов и кэш Wi-Fi.
- node_inventory — известные соседи и TCP/UDP-порты самого Raspberry Pi.
- node_bluetooth — список известных Bluetooth-устройств из BlueZ.
- Все инструменты, кроме node_list, принимают `{"node_id":"<ID>"}`. Доступ предназначен для чтения диагностики.

## Настройка и проверка

1. `sudo systemctl status pi-node-mcp.service` показывает состояние сервиса. `sudo journalctl -u pi-node-mcp.service -n 30` показывает журнал.
2. 401 означает, что клиенту нужен действующий Bearer-токен. Ошибка сертификата означает, что нужно сверить адрес и добавить публичный сертификат в доверенные.
3. Если узел отсутствует в node_list, проверьте его подключение в «Узлы Pi» и параметр --node в сервисе. Несколько --node разрешают несколько узлов.
4. Связь с удалённым узлом использует отдельный диагностический ключ и проверку его TLS-отпечатка. На основном Pi карта узлов хранится в `/etc/pi-node-mcp/peers.json`, на лёгком узле — SHA-256 своего ключа в `/opt/pi-node/mcp-diagnostics.sha256`.
5. Для разных агентов можно поднять отдельные экземпляры на разных портах с разными токенами и списками узлов. При смене токена обновите настройки клиентов и перезапустите экземпляр MCP.
6. Локальный транспорт stdio: `python3 /opt/pi-dashboard/pi-node-mcp.py --node <ID> --peer-credentials /etc/pi-node-mcp/peers.json`. Процессу требуется доступ к папке установки и указанному файлу. Для отдельного контейнера используйте HTTPS.
'''

def mcp_knowledge():
 try:
  value=subprocess.check_output(['tailscale','ip','-4'],text=True,timeout=3).strip()
  address=ipaddress.IPv4Address(value)
  if address in ipaddress.IPv4Network('100.64.0.0/10'):
   return BUILTIN_MCP.replace('<tailscale-ip>',str(address))
 except (OSError,ValueError,subprocess.SubprocessError):
  pass
 return BUILTIN_MCP


SERVICE_GUIDE = """# Pi Control Service and Feature Reference

## Getting started

Use the controller's private HTTPS address on your LAN or Tailscale. The dashboard is a privileged control plane. Configure the panel credentials and trust the administrator-confirmed TLS certificate before entering any secrets. Check the System and Services pages after installation. Current container names, service URLs and limits come from the deployed configuration.

## Services

| Service | Purpose |
| --- | --- |
| Agent Zero | Optional AI agent workspace. Review available tools and permissions before allowing actions. |
| DeepSeek Harness | Optional local AI workspace and agent integration when installed on the controller. |
| Portainer | Container inventory, logs and lifecycle management on the controller. |
| PostgreSQL | Database service with SQL-consistent backup when configured. Optional Supabase workflows are described in Settings. |
| Ollama | Local models for the Knowledge area and AI chat. Review memory limits before loading models. |
| Nextcloud | File collaboration and sharing. |
| ONLYOFFICE | Document editing, optionally connected to Nextcloud. |
| n8n | Automation and workflow scheduling. |
| OWASP ZAP and ZAP API | Optional tools for authorized security reviews. |
| Pi-hole | On-demand DNS filtering and local DNS dashboard. |
| Gluetun | Route selected containers through an administrator-configured VPN. |
| Tor | Optional configured proxy. Availability and controls depend on the installation. |
| Tailscale | Private connectivity, approved subnet routes and node access. |
| Cloudflare Tunnel | Published file links or connected service routes when configured. |
| Local Desktop, VNC and noVNC | Pi desktop access and an optional browser viewer. Remote Mac, VNC and RDP profiles appear under System. |

Use Start and Stop on the Services page. On the configured controller and current lightweight nodes, startup preferences follow panel actions. Running containers use a restart policy, and the controller boot unit restores the saved preference. The controller installation requires the service-state hook and its boot unit.

## Dashboard features

- **Overview and System:** node health, memory and disk usage, history where configured, power controls, remote desktops and the local desktop.
- **Files:** two panes for approved roots, USB discovery, browser upload, ZIP archives, file sharing, and scoped remote node access. Supported transfer operations depend on the selected endpoint.
- **Terminal:** interactive local and SSH tabs. Selecting a host with an existing tab brings it forward. Close individual sessions when finished.
- **Pi nodes:** discover lightweight agents advertised over mDNS and Raspberry-named Tailscale peers. Unknown devices can be entered manually. Compare the SHA-256 TLS fingerprint with a trusted source before enrollment. Verify node logins and results. The SSH setup flow checks the SSH host key and administrator authorization before installation.
- **Lightweight nodes:** health and container controls, diagnostics, local network information, Bluetooth and Wi-Fi discovery, files, USB reading, backup downloads, and Tailscale subnet route settings. Node access may also be provided to selected diagnostic MCP tools.
- **Network:** network status and explicitly authorized discovery or diagnostics. Device visibility depends on routing, filtering, adapter capabilities and network policy.
- **VPN and tunnels:** settings for Gluetun, Tailscale, other available proxies and permitted file publishing. An approved subnet route can reach a node LAN without an exit node.
- **AI:** local Ollama and supported remote providers. Scope any service secret to the relevant application before using it.
- **Passwords and secrets:** KDBX list, search, import/export, grouping, password generation, separately copied usernames and passwords, floating widget and service scopes. Optional Bitwarden and Vaultwarden account access uses the official CLI, a personal API key and a short-lived unlock session. The integration supports search and field copy.
- **Knowledge:** file-based collection of Markdown and text notes with folder navigation, search and preview. The separate MCP guide covers the active endpoint and authorization.
- **Settings:** display options, notifications, platform integration, storage and backups, safe update and rollback. Telegram delivery requires an administrator-configured bot and destination.
- **MCP:** the optional authenticated endpoint exposes read-only node inventory, health, interfaces, known neighbors and Bluetooth records for approved nodes. See the dedicated MCP note.

## Backup and recovery

- **USB:** encrypted age backups, integrity checks, scheduled or insertion-driven operation when configured, and extraction into a separate recovery directory. Keep the recovery identity outside the device.
- **Pi node destination:** select a paired lightweight node. The controller creates encrypted archives, streams them over certificate-pinned HTTPS, and the receiver publishes the set after checking size and SHA-256. Use an existing scoped node credential or provide its panel password. Lightweight nodes expose completed sets for authenticated download. The manifest contains recovery metadata.
- **Restore remote copy:** download all files from the same backup set. Check them against the manifest, then use the recovery process and the off-device age identity. The remote receiver does not require the private key.
- **Updates:** apply the verified GitHub server and UI packages from Settings. Complementary system integration scripts and lightweight agents are maintained separately.
- **Physical protection:** plan LUKS2 migration on a spare SD card and verify recovery before switching. The dashboard sign-in controls application access.

Keep this guide alongside the installation and operations documentation in the repository. The default note is created when the Knowledge workspace is opened.
"""


def ensure_builtin_knowledge():
 base=ROOTS['knowledge']
 if base.is_symlink():return
 try:
  try:base.mkdir()
  except FileExistsError:pass
  else:owner(base,base.parent)
  for name, content in ((BUILTIN_MCP_NAME,mcp_knowledge),
                        ('Pi Control — Service and Feature Reference.md',lambda:SERVICE_GUIDE)):
   path=base/name
   if path.exists() or path.is_symlink():continue
   try:
    document=content()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as file:
     file.write(document)
     file.flush()
     os.fsync(file.fileno())
    owner(path,base)
   except OSError:
    pass
 except OSError:
  pass

def validname(name):
 if not isinstance(name,str) or not name or name in ('.','..') or '/' in name or '\\' in name or '\x00' in name or any(ord(c)<32 for c in name) or len(name.encode())>240:raise ValueError('Недопустимое имя файла.')
 return name
def root_path(root):
 if isinstance(root,str) and root.startswith('usb:'):
  return usb_backup.file_root(root)
 return ROOTS.get(root)
def resolve(root,relative='',must=True):
 base=root_path(root)
 if not base:raise ValueError('Неизвестный раздел.')
 if not isinstance(relative,str) or len(relative)>4096 or '\x00' in relative:raise ValueError('Некорректный путь.')
 p=Path(relative)
 if p.is_absolute() or any(c in ('..','.') for c in p.parts):raise ValueError('Путь выходит за пределы раздела.')
 current=base
 for part in p.parts:
  validname(part);current=current/part
  if current.is_symlink():raise ValueError('Символические ссылки не поддерживаются.')
  if root.startswith('usb:') and current.exists():
   mode=current.stat().st_mode
   if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)) or current.stat().st_dev!=base.stat().st_dev:
    raise ValueError('Специальные файлы и вложенные точки монтирования USB недоступны.')
 if must and not current.exists():raise ValueError('Файл или папка не найдены.')
 return current
def child(parent,name):
 validname(name);p=parent/name
 if p.exists() or p.is_symlink():raise ValueError('Файл или папка с таким именем уже существуют.')
 return p
def owner(p,parent):
 try:
  s=parent.stat()
  if s.st_uid!=0:os.chown(p,s.st_uid,s.st_gid,follow_symlinks=False)
 except PermissionError:pass
def items(d):
 root=d.get('root','home');rel=d.get('path','')
 if root=='knowledge':ensure_builtin_knowledge()
 p=resolve(root,rel)
 if not p.is_dir():raise ValueError('Это не папка.')
 try:allitems=list(os.scandir(p))
 except OSError:raise ValueError('Не удалось открыть папку.')
 if len(allitems)>10000:raise ValueError('В папке более 10000 элементов; откройте другую папку.')
 rows=[]
 for x in allitems:
  if x.name.startswith('.pi-upload-'):continue
  try:
   st=x.stat(follow_symlinks=False);symlink=x.is_symlink();isdir=x.is_dir(follow_symlinks=False)
   rows.append({'name':x.name,'directory':isdir,'symlink':symlink,'size':st.st_size,'modified':int(st.st_mtime),'mode':stat.filemode(st.st_mode),'owner':st.st_uid,'group':st.st_gid})
  except OSError:continue
 rows.sort(key=lambda x:(not x['directory'],x['name'].casefold()))
 return {'root':root,'path':str(Path(rel)) if rel else '','items':rows,'roots':[{'id':k,'name':{'home':'Домашняя папка','media':'Носители /media','mnt':'Носители /mnt','knowledge':'База знаний'}[k]} for k in ROOTS]+usb_backup.file_roots()}
def make_dir(d):
 parent=resolve(d.get('root'),d.get('path',''));p=child(parent,d.get('name',''));p.mkdir();owner(p,parent);return {'message':'Папка создана.'}
def rename(d):
 p=resolve(d.get('root'),d.get('path',''))
 if p==root_path(d.get('root')):raise ValueError('Нельзя переименовать корень раздела.')
 dest=child(p.parent,d.get('name',''));p.rename(dest);return {'message':'Переименовано.'}
def check_tree(p):
 if p.is_symlink():raise ValueError('Операции с символическими ссылками не поддерживаются.')
 if p.is_dir():
  count=0
  for parent,dirs,files in os.walk(p,followlinks=False):
   for n in dirs+files:
    count+=1
    if count>10000:raise ValueError('Слишком много элементов в папке.')
    item=Path(parent)/n
    metadata=item.lstat()
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)) or metadata.st_dev!=p.stat().st_dev:
     raise ValueError('Папка содержит ссылку, специальный файл или другую точку монтирования.')
def transfer(d):
 p=resolve(d.get('root'),d.get('path',''));destdir=resolve(d.get('dest_root'),d.get('dest_path',''))
 if not destdir.is_dir() or p == root_path(d.get('root')):raise ValueError('Недопустимая операция.')
 dest=child(destdir,p.name);op=d.get('op')
 if p.is_dir() and (destdir==p or p in destdir.parents):raise ValueError('Нельзя скопировать папку внутрь самой себя.')
 check_tree(p)
 if op=='copy':
  if p.is_dir():shutil.copytree(p,dest,symlinks=False)
  else:shutil.copy2(p,dest)
  owner(dest,destdir)
  if dest.is_dir():
   for parent,dirs,files in os.walk(dest):
    for name in dirs+files:owner(Path(parent)/name,destdir)
 elif op=='move':shutil.move(str(p),str(dest))
 else:raise ValueError('Неизвестная операция.')
 return {'message':'Операция завершена.'}
def delete(d):
 p=resolve(d.get('root'),d.get('path',''))
 if p == root_path(d.get('root')):raise ValueError('Нельзя удалить корень раздела.')
 check_tree(p)
 if p.is_dir():shutil.rmtree(p)
 else:p.unlink()
 return {'message':'Удалено.'}
def chmod_item(d):
 p=resolve(d.get('root'),d.get('path',''));mode=str(d.get('mode',''))
 if not re.fullmatch(r'[0-7]{3}',mode):raise ValueError('Укажите права тремя цифрами, например 640 или 755.')
 if p == root_path(d.get('root')):raise ValueError('Нельзя изменять права корня раздела.')
 os.chmod(p,int(mode,8),follow_symlinks=False);return {'message':'Права изменены.'}
def make_file(d):
 parent=resolve(d.get('root'),d.get('path',''));p=child(parent,d.get('name',''));fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd);owner(p,parent);return {'message':'Файл создан.'}
def read_text(d):
 p=resolve(d.get('root'),d.get('path',''))
 if not p.is_file() or p.stat().st_size>1048576:raise ValueError('Для просмотра доступен текстовый файл размером до 1 МиБ.')
 try:text=p.read_text(encoding='utf-8')
 except UnicodeError:raise ValueError('Файл не является текстом UTF-8.')
 return {'text':text,'size':p.stat().st_size,'modified':p.stat().st_mtime_ns}
def write_text(d):
 p=resolve(d.get('root'),d.get('path',''));text=d.get('text','')
 if not p.is_file() or not isinstance(text,str) or len(text.encode())>1048576:raise ValueError('Максимальный размер текста — 1 МиБ.')
 if p.stat().st_mtime_ns!=d.get('modified'):raise ValueError('Файл изменился с момента открытия. Откройте его заново.')
 tmp=child(p.parent,'.pi-upload-'+uuid.uuid4().hex);tmp.write_text(text,encoding='utf-8');os.chmod(tmp,stat.S_IMODE(p.stat().st_mode));owner(tmp,p.parent);os.replace(tmp,p);return {'message':'Файл сохранён.'}
def upload_begin(d):
 parent=resolve(d.get('root'),d.get('path',''));name=validname(d.get('name',''))
 if not parent.is_dir():raise ValueError('Выберите папку.')
 target=child(parent,name)
 try:size=int(d.get('size'))
 except Exception:raise ValueError('Неверный размер файла.')
 if size<0 or size>32*1024**3:raise ValueError('Максимальный размер загрузки — 32 ГиБ.')
 if size>shutil.disk_usage(parent).free-64*1024**2:raise ValueError('Недостаточно места на носителе.')
 with lock:
  for k,u in list(uploads.items()):
   if u['expires']<time.monotonic():
    try:discard_upload(u)
    except OSError:pass
    del uploads[k]
 token=secrets.token_urlsafe(24);tmp=child(parent,'.pi-upload-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);fingerprint=identity(os.fstat(fd));os.close(fd)
 with lock:uploads[token]={'tmp':tmp,'target':target,'parent':parent,'fingerprint':fingerprint,'size':size,'offset':0,'expires':time.monotonic()+3600}
 return {'upload_id':token,'chunk_size':4194304}
def identity(info):
 return info.st_dev,info.st_ino
def discard_upload(upload):
 try:
  if identity(upload['tmp'].lstat())==upload['fingerprint']:upload['tmp'].unlink()
 except FileNotFoundError:pass
def assert_upload(u):
 if identity(u['tmp'].lstat())!=u['fingerprint']:raise ValueError('Носитель для загрузки изменился.')
def upload_chunk(token,data):
 with lock:
  u=uploads.get(token)
  if not u or u['expires']<time.monotonic():raise ValueError('Загрузка не найдена или истекла.')
  if len(data)>4194304 or u['offset']+len(data)>u['size']:raise ValueError('Некорректный размер блока.')
  fd=os.open(u['tmp'],os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
  with os.fdopen(fd,'ab') as f:
   if identity(os.fstat(f.fileno()))!=u['fingerprint']:raise ValueError('Носитель для загрузки изменился.')
   f.write(data)
  u['offset']+=len(data);u['expires']=time.monotonic()+3600
  return {'offset':u['offset']}
def upload_end(d):
 token=str(d.get('upload_id',''))
 with lock:
  u=uploads.get(token)
  if not u:raise ValueError('Загрузка не найдена.')
  if u['offset']!=u['size']:raise ValueError('Файл загружен не полностью.')
  if u['target'].exists() or u['target'].is_symlink():raise ValueError('Файл назначения уже существует.')
  assert_upload(u)
  owner(u['tmp'],u['parent']);fd=os.open(u['target'],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
  try:os.replace(u['tmp'],u['target'])
  except Exception:
   u['target'].unlink();raise
  del uploads[token]
 return {'message':'Файл загружен.'}
def upload_cancel(d):
 with lock:
  u=uploads.pop(str(d.get('upload_id','')),None)
  if u:
   try:discard_upload(u)
   except OSError:pass
 return {'message':'Загрузка отменена.'}
def zip_create(d):
 p=resolve(d.get('root'),d.get('path',''));check_tree(p);dest=child(p.parent,validname(d.get('name','')))
 if not dest.name.lower().endswith('.zip'):raise ValueError('Имя архива должно оканчиваться на .zip')
 try:
  with zipfile.ZipFile(dest,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=4,allowZip64=True) as z:
   if p.is_dir():
    for parent,dirs,files in os.walk(p,followlinks=False):
     for name in files:
      f=Path(parent)/name;z.write(f,str(f.relative_to(p.parent)))
     for name in dirs:
      folder=Path(parent)/name
      if not any(folder.iterdir()):z.writestr(str(folder.relative_to(p.parent))+'/',b'')
   else:z.write(p,p.name)
  owner(dest,dest.parent)
 except Exception:
  if dest.exists():dest.unlink()
  raise
 return {'message':'ZIP-архив создан.'}
def extract(d):
 p=resolve(d.get('root'),d.get('path',''));dest=child(p.parent,validname(d.get('name','')));low=p.name.lower()
 if not p.is_file():raise ValueError('Выберите архив.')
 entries=[];total=0
 if low.endswith('.zip'):
  z=zipfile.ZipFile(p);members=z.infolist()
  for m in members:
   typ=(m.external_attr>>16)&0o170000
   if typ==stat.S_IFLNK:raise ValueError('Архив содержит ссылку.')
   entries.append((m.filename,m.file_size,m.is_dir(),m))
 elif low.endswith(('.tar','.tar.gz','.tgz')):
  z=tarfile.open(p,'r:*');members=z.getmembers()
  for m in members:
   if not (m.isfile() or m.isdir()):raise ValueError('Архив содержит неподдерживаемые ссылки или специальные файлы.')
   entries.append((m.name,m.size,m.isdir(),m))
 else:raise ValueError('Поддерживаются ZIP, TAR и TAR.GZ.')
 if len(entries)>10000:raise ValueError('В архиве слишком много элементов.')
 for name,size,isdir,m in entries:
  part=Path(name)
  if part.is_absolute() or not part.parts or any(c in ('..','.') for c in part.parts) or '\\' in name:raise ValueError('Архив содержит небезопасный путь.')
  total+=size
 if total>2*1024**3 or total>shutil.disk_usage(p.parent).free-64*1024**2:raise ValueError('Архив слишком большой или не хватает места.')
 dest.mkdir();owner(dest,dest.parent)
 try:
  for name,size,isdir,m in entries:
   target=dest.joinpath(*Path(name).parts)
   if isdir:target.mkdir(parents=True,exist_ok=True);owner(target,dest)
   else:
    target.parent.mkdir(parents=True,exist_ok=True);src=z.open(m) if low.endswith('.zip') else z.extractfile(m)
    with src,target.open('xb') as out:shutil.copyfileobj(src,out,1024*1024)
    owner(target,dest)
  for parent,dirs,files in os.walk(dest):
   for name in dirs+files:owner(Path(parent)/name,dest)
 except Exception:
  shutil.rmtree(dest);raise
 finally:z.close()
 return {'message':'Архив распакован.'}
def public_stop():
 with lock:
  p=shared.get('process');srv=shared.get('server');log=shared.get('log')
  if p and p.poll() is None:p.terminate()
  if srv:
   def close_server():
    srv.shutdown();srv.server_close()
   threading.Thread(target=close_server,daemon=True).start()
  if log:
   try:log.close()
   except Exception:pass
  shared.update(server=None,process=None,url='',token='',path=None,expires=0,log=None)
 return {'message':'Публикация остановлена.'}
def public_status():
 with lock:
  p=shared.get('process')
  dead=bool(p and p.poll() is not None)
  if shared['expires'] and time.time()>=shared['expires']:expired=True
  else:expired=False
  if p and p.poll() is None and shared.get('log'):
   try:
    shared['log'].flush();text=Path(shared['log'].name).read_text(errors='replace');m=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',text)
    if m:shared['url']=m.group(0)+'/'+shared['token']+'/'
    elif 'Registered tunnel connection' in text and Path('/opt/pi-dashboard/tunnel/files.env').exists():shared['url']='https://pi-files.sm0k3.net/'+shared['token']+'/'
   except OSError:pass
  result={'running':bool(p and p.poll() is None and not expired),'url':shared['url'] if not expired else '', 'expires':shared['expires'] if not expired else 0,'error':'Cloudflare не создал ссылку. Проверьте интернет и доступность api.trycloudflare.com.' if dead else ''}
 if expired or dead:public_stop()
 return result
def public_start(d):
 p=resolve(d.get('root'),d.get('path',''));check_tree(p)
 if p == root_path(d.get('root')):raise ValueError('Выберите конкретную папку или файл, а не корень раздела.')
 if public_status()['running']:raise ValueError('Сначала остановите текущую публикацию файлов.')
 minutes=d.get('minutes',15)
 if minutes not in (15,60,240):raise ValueError('Недопустимое время публикации.')
 token=secrets.token_urlsafe(24);expires=time.time()+minutes*60
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   if time.time()>=expires:self.send_error(410);return
   parts=urllib.parse.urlsplit(self.path);prefix='/'+token+'/'
   if not parts.path.startswith(prefix):self.send_error(404);return
   rel=urllib.parse.unquote(parts.path[len(prefix):]);x=Path(rel)
   if x.is_absolute() or any(c in ('..','.') for c in x.parts) or '\x00' in rel:self.send_error(404);return
   try:
    if p.is_symlink():raise ValueError()
    base=p if p.is_dir() else p.parent
    target=p if p.is_file() and not rel else base.joinpath(*x.parts)
    if p.is_file() and rel not in ('',p.name):raise ValueError()
    if target!=p and p.is_file():raise ValueError()
    q=base
    for c in target.relative_to(base).parts:
     q=q/c
     if q.is_symlink():raise ValueError()
    if not target.exists():raise ValueError()
    if target.is_dir():
     links=[]
     for item in sorted(target.iterdir(),key=lambda y:(not y.is_dir(),y.name.casefold()))[:5000]:
      if item.is_symlink():continue
      path=('/'.join(x.parts+(item.name,)))
      label=html.escape(item.name+('/' if item.is_dir() else ''));links.append('<li><a href="'+prefix+urllib.parse.quote(path)+'">'+label+'</a></li>')
     title=html.escape(target.name);raw=('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+title+'</title><h1>'+title+'</h1><p>Доступ только для чтения · ссылка временная</p><ul>'+''.join(links)+'</ul>').encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'");self.end_headers();self.wfile.write(raw);return
    if not target.is_file():raise ValueError()
    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(target.stat().st_size));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(target.name));self.end_headers()
    with target.open('rb') as f:shutil.copyfileobj(f,self.wfile,1024*1024)
   except (OSError,ValueError,BrokenPipeError):
    try:self.send_error(404)
    except Exception:pass
 server=ThreadingHTTPServer(('127.0.0.1',18849 if Path('/opt/pi-dashboard/tunnel/files.env').exists() else 0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 logpath=Path('/opt/pi-dashboard/fm-share.log');f=logpath.open('w');os.chmod(logpath,0o600)
 try:
  env=os.environ.copy();config=Path('/opt/pi-dashboard/tunnel/files.env')
  if config.exists():
   value=config.read_text().strip().split('=',1)[1];env['TUNNEL_TOKEN']=value
   args=['/usr/local/bin/cloudflared','tunnel','--no-autoupdate','--protocol','http2','run']
  else:args=['/usr/local/bin/cloudflared','tunnel','--no-autoupdate','--url','http://127.0.0.1:'+str(server.server_port)]
  proc=subprocess.Popen(args,env=env,stdout=f,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True,close_fds=True)
 except Exception:server.shutdown();f.close();raise ValueError('Не удалось запустить cloudflared.')
 with lock:shared.update(server=server,process=proc,url='',token=token,path=p,expires=expires,log=f)
 def finish():
  time.sleep(minutes*60)
  with lock:still=shared.get('token')==token
  if still:public_stop()
 threading.Thread(target=finish,daemon=True).start();return {'message':'Публикация запускается. Ссылка появится через несколько секунд.'}
def telegram(d):
 p=resolve(d.get('root'),d.get('path',''))
 if not p.is_file():raise ValueError('Telegram принимает выбранный файл; сначала создайте архив папки.')
 size=p.stat().st_size
 if size>49*1000*1000:raise ValueError('Файл слишком большой для отправки ботом: безопасный предел 49 МБ.')
 import server
 config=server.read_telegram();token=config.get('bot_token','');chat=str(config.get('chat_id',''))
 if not token or not chat:raise ValueError('Настройте Telegram-бота и chat ID в разделе уведомлений.')
 boundary='----PiControl'+secrets.token_hex(16);filename=p.name.replace('\"','_')
 head=('--'+boundary+'\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n'+chat+'\r\n--'+boundary+'\r\nContent-Disposition: form-data; name="document"; filename="'+filename+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode();tail=('\r\n--'+boundary+'--\r\n').encode()
 conn=http.client.HTTPSConnection('api.telegram.org',timeout=120)
 try:
  conn.putrequest('POST','/bot'+token+'/sendDocument');conn.putheader('Content-Type','multipart/form-data; boundary='+boundary);conn.putheader('Content-Length',str(len(head)+size+len(tail)));conn.endheaders();conn.send(head)
  with p.open('rb') as f:
   while True:
    buf=f.read(1024*1024)
    if not buf:break
    conn.send(buf)
  conn.send(tail);res=conn.getresponse();data=json.loads(res.read(65536))
  if not data.get('ok'):raise ValueError('Telegram отклонил отправку. Проверьте размер и настройки бота.')
 except ValueError:raise
 except Exception:raise ValueError('Не удалось отправить файл в Telegram.')
 finally:conn.close()
 return {'message':'Файл отправлен в настроенный чат Telegram.'}
def search_knowledge(d):
 query=d.get('query','')
 if not isinstance(query,str) or not 2<=len(query.strip())<=160:
  raise ValueError('Введите от 2 до 160 символов.')
 ensure_builtin_knowledge()
 base=resolve('knowledge')
 terms=query.strip().casefold().split()
 matches=[];checked=0;visited=0;budget=8*1024**2;partial=False
 for parent,dirs,files in os.walk(base,followlinks=False):
  relative=Path(parent).relative_to(base)
  dirs[:]=sorted(x for x in dirs if not x.startswith('.') and len(relative.parts)<10)
  visited+=1
  if visited>250 or checked>=400 or budget<0:
   partial=True;break
  for name in sorted(files):
   if checked>=400 or budget<0 or len(matches)>=40:
    partial=True;break
   if name.startswith('.') or Path(name).suffix.lower() not in ('.md','.markdown','.txt'):
    continue
   path=Path(parent)/name
   rel=(relative/name).as_posix()
   try:
    path=resolve('knowledge',rel)
    metadata=path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size>128*1024:continue
    with path.open(encoding='utf-8') as file:
     content=file.read(128*1024+1)
   except (OSError,UnicodeError,ValueError):
    continue
   checked+=1;budget-=metadata.st_size
   haystack=rel.casefold()+' '+content.casefold()
   if all(term in haystack for term in terms):
    snippet=next((line.strip() for line in content.splitlines() if any(
                  term in line.casefold() for term in terms)),'')
    matches.append({'path':rel,'snippet':snippet[:250]})
  if len(matches)>=40:break
 return {'items':matches,'checked':checked,'partial':partial}

def dispatch(op,d):
 f={'knowledge-search':search_knowledge,'list':items,'mkdir':make_dir,'touch':make_file,'chmod':chmod_item,'rename':rename,'transfer':transfer,'delete':delete,'read':read_text,'write':write_text,'upload-begin':upload_begin,'upload-end':upload_end,'upload-cancel':upload_cancel,'zip':zip_create,'extract':extract,'share-start':public_start,'share-stop':lambda x:public_stop(),'share-status':lambda x:public_status(),'telegram':telegram}.get(op)
 if not f:raise ValueError('Неизвестная операция.')
 if op=='share-start':
  with share_guard:return f(d)
 return f(d)
