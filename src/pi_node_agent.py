#!/usr/bin/env python3
"""Small, independently authenticated Pi node panel; no external Python packages."""
import os,sys,time,json,ssl,hmac,hashlib,secrets,re,subprocess,shutil,threading,ipaddress,socket
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,quote
from concurrent.futures import ThreadPoolExecutor
import base64
import stat
from contextlib import contextmanager
ROOT=Path(os.environ.get('PI_NODE_ROOT','/opt/pi-node'));CFG=json.loads((ROOT/'config.json').read_text());sessions={};attempts=[];guard=threading.Lock()
def command(args,timeout=6):
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
  return r.stdout.strip() if r.returncode==0 else ''
 except (OSError,subprocess.TimeoutExpired):return ''
def status():
 mem={}
 for line in Path('/proc/meminfo').read_text().splitlines():
  p=line.split()
  if p[0].rstrip(':') in ('MemTotal','MemAvailable'):mem[p[0].rstrip(':')]=int(p[1])*1024
 disk=shutil.disk_usage('/');rows=[]
 try:temperature=round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text().strip())/1000,1)
 except (OSError,ValueError):temperature=None
 for line in command(['docker','ps','-a','--format','{{json .}}'],12).splitlines():
  try:
   x=json.loads(line);name=x['Names']
   if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',name):rows.append({'id':name,'name':name,'state':x.get('State','unknown'),'status':x.get('Status','')})
  except (ValueError,KeyError):pass
 return {'system':{'memory_total':mem.get('MemTotal',0),'memory_available':mem.get('MemAvailable',0),'memory_used':mem.get('MemTotal',0)-mem.get('MemAvailable',0),'disk_total':disk.total,'disk_free':disk.free,'disk_used':disk.used,'temperature':temperature,'load':os.getloadavg()[0],'uptime':int(float(Path('/proc/uptime').read_text().split()[0]))},'services':rows[:150]}
def network_status():
 def parsed(args,default):
  try:return json.loads(command(args,8))
  except Exception:return default
 interfaces=[]
 for item in parsed(['ip','-j','address','show'],[]):
  interfaces.append({'name':item.get('ifname',''), 'state':item.get('operstate',''), 'addresses':[a.get('local','')+'/'+str(a.get('prefixlen','')) for a in item.get('addr_info',[]) if a.get('family') in ('inet','inet6')]})
 routes=parsed(['ip','-j','-4','route','show'],[])
 try:
  with open('/proc/sys/net/ipv4/ip_forward') as f:forward=f.read().strip()=='1'
 except OSError:forward=False
 return {'interfaces':interfaces,'routes':[{'dst':x.get('dst',''), 'gateway':x.get('gateway',''), 'dev':x.get('dev','')} for x in routes], 'forwarding':forward,'attached_networks':attached_networks()}
def tunnel_status():
 try:
  p=json.loads(command(['tailscale','debug','prefs'],8))
  return {'available':True,'advertised_routes':p.get('AdvertiseRoutes') or [],'accept_routes':bool(p.get('RouteAll'))}
 except Exception:return {'available':False,'advertised_routes':[],'accept_routes':False}
def home_path(relative):
 if '\x00' in relative:raise ValueError('Invalid path')
 base=Path('/home').resolve(); target=(base/relative.lstrip('/')).resolve(strict=True)
 if target!=base and base not in target.parents:raise ValueError('Path outside /home')
 return target
def list_home(relative):
 p=home_path(relative)
 if not p.is_dir():raise ValueError('Not a directory')
 entries=[]
 for item in sorted(p.iterdir(),key=lambda x:x.name.casefold())[:1000]:
  try:
   if item.is_symlink():continue
   t=item.stat();entries.append({'name':item.name,'directory':item.is_dir(),'size':t.st_size,'modified':int(t.st_mtime)})
  except OSError:continue
 return {'path':str(p.relative_to(Path('/home'))).replace('.', '', 1) if p==Path('/home') else str(p.relative_to(Path('/home'))),'items':entries}
USB_BASE = ROOT / 'usb'
USB_LOCK = threading.RLock()
USB_TYPES = {'vfat', 'exfat', 'ntfs', 'ext2', 'ext3', 'ext4'}
USB_LIMIT = 8 * 1024 * 1024


def json_command(args):
    try:
        return json.loads(command(args, 5))
    except ValueError:
        return {}


def usb_devices():
    rows = json_command(['lsblk', '--tree', '--json', '-o',
                         'PATH,TYPE,TRAN,UUID,FSTYPE,LABEL,MAJ:MIN']).get('blockdevices', [])
    devices = {}

    def visit(row, usb):
        usb = usb or row.get('tran') == 'usb'
        uuid = row.get('uuid') or ''
        if (usb and row.get('type') in ('disk', 'part') and row.get('fstype') in USB_TYPES
                and re.fullmatch(r'[A-Za-z0-9-]{1,80}', uuid)):
            devices.setdefault(uuid, []).append(row)
        for child in row.get('children', []):
            visit(child, usb)

    for row in rows:
        visit(row, False)
    return {key: rows[0] for key, rows in devices.items() if len(rows) == 1}


def usb_info():
    return [{'uuid': uuid, 'label': info.get('label') or uuid, 'filesystem': info['fstype']}
            for uuid, info in usb_devices().items()]


def diagnostics(task):
    if task == 'host':
        return status()
    if task == 'links':
        return {**network_status(), 'wifi': command(['iw', 'dev']).splitlines()[:80],
                'wifi_networks': command(['nmcli', '-t', '-f', 'IN-USE,SSID,CHAN,SIGNAL,SECURITY',
                                          'device', 'wifi', 'list', '--rescan', 'no']).splitlines()[:100]}
    if task == 'observed':
        neighbors = json_command(['ip', '-j', 'neigh', 'show'])
        return {'neighbors': [row for row in neighbors if row.get('dev', '').startswith(('eth', 'en', 'wl'))][:250],
                'local_listeners': command(['ss', '-H', '-lntu']).splitlines()[:150]}
    if task == 'bluetooth':
        return {'adapters': command(['bluetoothctl', 'list']).splitlines()[:32],
                'devices': command(['bluetoothctl', 'devices']).splitlines()[:128]}
    if task == 'usb':
        return {'devices': usb_info()}
    raise ValueError('Unknown task')


def mount_usb(uuid):
    with USB_LOCK:
        device = usb_devices().get(uuid)
        if not device:
            raise ValueError('USB not found')
        USB_BASE.mkdir(mode=0o700, exist_ok=True)
        target = USB_BASE / uuid
        target.mkdir(mode=0o700, exist_ok=True)
        current = command(['findmnt', '-n', '--mountpoint', str(target), '-o', 'MAJ:MIN'])
        if current != device['maj:min']:
            if current:
                subprocess.run(['umount', str(target)], timeout=6, check=True, capture_output=True)
            fs = device['fstype']
            options = 'ro,nosuid,nodev,noexec'
            if fs in {'ext3', 'ext4'}:
                options += ',noload'
            subprocess.run(['mount', '-t', 'ntfs3' if fs == 'ntfs' else fs,
                            '-o', options, device['path'], str(target)],
                           timeout=10, check=True, capture_output=True)
        info = command(['findmnt', '-n', '--mountpoint', str(target), '-o', 'MAJ:MIN,OPTIONS'])
        fields = info.split()
        if len(fields) != 2 or fields[0] != device['maj:min'] or 'ro' not in fields[1].split(','):
            raise ValueError('USB mount verification failed')
        return target


@contextmanager
def usb_entry(uuid, path, directory):
    pieces = path.split('/') if path else []
    if len(path) > 2048 or (not directory and not pieces) or any(piece in ('', '.', '..') or '\x00' in piece for piece in pieces):
        raise ValueError('Invalid USB path')
    base = mount_usb(uuid)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptors = [os.open(base, flags | os.O_DIRECTORY)]
    try:
        root_device = os.fstat(descriptors[0]).st_dev
        for i, name in enumerate(pieces):
            require_dir = directory or i < len(pieces) - 1
            descriptors.append(os.open(name, flags | (os.O_DIRECTORY if require_dir else 0),
                                       dir_fd=descriptors[-1]))
            if os.fstat(descriptors[-1]).st_dev != root_device:
                raise ValueError('Cross-device paths are unavailable')
        yield descriptors[-1]
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def usb_list(uuid, path):
    items = []
    with usb_entry(uuid, path, True) as fd:
        with os.scandir(fd) as entries:
            for entry in entries:
                if len(items) >= 500:
                    break
                try:
                    entry.name.encode('utf-8')
                    info = entry.stat(follow_symlinks=False)
                except (UnicodeError, FileNotFoundError):
                    continue
                if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
                    items.append({'name': entry.name, 'directory': stat.S_ISDIR(info.st_mode),
                                  'size': info.st_size})
    return {'path': path, 'items': sorted(items, key=lambda item: (not item['directory'], item['name'].casefold()))}


def usb_file(uuid, path):
    with usb_entry(uuid, path, False) as fd:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > USB_LIMIT:
            raise ValueError('File too large or unsupported (8 MiB maximum)')
        with os.fdopen(os.dup(fd), 'rb') as handle:
            data = handle.read(USB_LIMIT + 1)
        if len(data) > USB_LIMIT:
            raise ValueError('File too large')
    return {'name': path.rsplit('/', 1)[-1], 'base64': base64.b64encode(data).decode('ascii')}


SHARE = ROOT / 'transfers'
FILE_LIMIT = 128 * 1024**3
TRANSFER_LOCK = threading.BoundedSemaphore(3)
CHUNK = 256 * 1024


@contextmanager
def share_parent(path, directory=False):
    parts = path.split('/') if path else []
    if ((not parts and not directory) or len(path) > 2048 or
            any(not p or p.startswith('.') or len(p.encode()) > 255 or '\x00' in p for p in parts)):
        raise ValueError('Недопустимый путь.')
    SHARE.mkdir(mode=0o700, exist_ok=True)
    dirs = []
    try:
        fd = os.open(SHARE, os.O_DIRECTORY | os.O_NOFOLLOW)
        dirs.append(fd)
        for part in (parts if directory else parts[:-1]):
            fd = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            dirs.append(fd)
        yield fd, parts[-1] if parts else ''
    finally:
        for fd in reversed(dirs):
            os.close(fd)


def share_list(path):
    with share_parent(path, True) as (root, _), os.scandir(root) as scan:
        rows = []
        for entry in scan:
            if entry.name.startswith('.'):
                continue
            try:
                entry.name.encode()
                info = entry.stat(follow_symlinks=False)
            except (OSError, UnicodeError):
                continue
            directory = stat.S_ISDIR(info.st_mode)
            if directory or stat.S_ISREG(info.st_mode):
                rows.append({'name': entry.name, 'directory': directory, 'size': info.st_size})
            if len(rows) == 1000:
                break
    return {'path': path, 'items': sorted(rows, key=lambda row: (not row['directory'], row['name'].casefold()))}


def upload_file(handler, path, length):
    if not 0 <= length <= FILE_LIMIT:
        raise ValueError('Размер файла превышает 128 ГиБ.')
    with share_parent(path) as (root, name):
        disk = os.fstatvfs(root)
        if length > disk.f_bavail * disk.f_frsize:
            raise ValueError('Недостаточно места на принимающем узле.')
        temporary = '.upload-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root)
        try:
            with os.fdopen(fd, 'wb') as target:
                remaining = length
                while remaining:
                    chunk = handler.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError('Загрузка прервана.')
                    target.write(chunk)
                    remaining -= len(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.link(temporary, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=root)
    return {'bytes': length}


def download_file(handler, path):
    with share_parent(path) as (root, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Недоступный файл.')
        size = count = info.st_size
        code, start = 200, 0
        interval = handler.headers.get('Range')
        if interval:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', interval)
            if not match or not size or int(match[1]) >= size or (match[2] and int(match[2]) < int(match[1])):
                handler.send_response(416)
                handler.send_header('Content-Range', f'bytes */{size}')
                handler.send_header('Content-Length', '0')
                handler.end_headers()
                return
            start = int(match[1])
            count = min(size - 1, int(match[2]) if match[2] else size - 1) - start + 1
            code = 206
            source.seek(start)
        handler.send_response(code)
        handler.send_header('Content-Type', 'application/octet-stream')
        handler.send_header('Content-Length', str(count))
        handler.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(name, safe=''))
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Accept-Ranges', 'bytes')
        handler.send_header('Cache-Control', 'no-store')
        if code == 206:
            handler.send_header('Content-Range', f'bytes {start}-{start + count - 1}/{size}')
        handler.end_headers()
        handler.close_connection = True
        while count:
            chunk = source.read(min(CHUNK, count))
            if not chunk:
                return
            try:
                handler.wfile.write(chunk)
            except (OSError, TimeoutError):
                return
            count -= len(chunk)


DISCOVERY_LOCK = threading.Lock()
INVENTORY_PORTS = (22, 53, 80, 139, 443, 445, 515, 631, 2049, 3389, 5432, 5900, 8000, 8080, 8443, 9100)


def attached_networks():
    allowed = {}
    rows = json_command(['ip', '-j', '-4', 'address', 'show', 'up'])
    for row in rows:
        if not row.get('ifname', '').startswith(('wl', 'eth', 'en')):
            continue
        for entry in row.get('addr_info', []):
            if entry.get('scope') != 'global':
                continue
            address = ipaddress.IPv4Interface(f"{entry['local']}/{entry['prefixlen']}")
            if any(address.network.subnet_of(ipaddress.IPv4Network(space)) for space in
                   ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
                allowed[str(address.network)] = row['ifname']
    return allowed


def port_inventory(cidr):
    subnet = ipaddress.IPv4Network(cidr, strict=True)
    ranges = [ipaddress.IPv4Network(key) for key in attached_networks()]
    if subnet.num_addresses > 64 or not any(subnet.subnet_of(network) for network in ranges):
        raise ValueError('Выберите до 64 адресов в сети, подключённой напрямую к Ethernet или Wi-Fi.')

    def inventory(address):
        opened = []
        route = json_command(['ip', '-j', 'route', 'get', str(address)])
        if not route or route[0].get('gateway') or route[0].get('dev') not in attached_interfaces:
            return None
        for port in INVENTORY_PORTS:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.18)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                route[0]['dev'].encode()+b'\0')
                try:
                    if sock.connect_ex((str(address), port)) == 0:
                        opened.append(port)
                except OSError:
                    continue
        return {'ip': str(address), 'open_tcp': opened} if opened else None

    attached_interfaces = set(attached_networks().values())
    with ThreadPoolExecutor(max_workers=8) as workers:
        rows = [row for row in workers.map(inventory, subnet.hosts()) if row]
    neighbors = json_command(['ip', '-j', 'neigh', 'show'])
    for row in neighbors:
        if row.get('dev') not in attached_interfaces or not row.get('lladdr'):
            continue
        try:
            if ipaddress.IPv4Address(row.get('dst')) not in subnet:
                continue
        except ValueError:
            continue
        known = next((item for item in rows if item['ip'] == row['dst']), None)
        if known is None:
            known = {'ip': row['dst'], 'open_tcp': []}
            rows.append(known)
        known['mac'] = row['lladdr']
    return {'subnet': str(subnet), 'ports': list(INVENTORY_PORTS),
            'devices': sorted(rows, key=lambda row: ipaddress.IPv4Address(row['ip']))}


def discovery(data):
    if data.get('confirmed') is not True:
        raise ValueError('Подтвердите запуск обнаружения.')
    if not DISCOVERY_LOCK.acquire(blocking=False):
        raise ValueError('На узле уже выполняется обнаружение.')
    try:
        mode = data.get('mode')
        if mode == 'wifi':
            if not shutil.which('nmcli'):
                raise ValueError('Для поиска Wi-Fi требуется NetworkManager.')
            scan = subprocess.run(['nmcli', '-t', '-f', 'SSID,BSSID,CHAN,SIGNAL,SECURITY',
                                   'device', 'wifi', 'list', '--rescan', 'yes'],
                                  timeout=20, capture_output=True, text=True)
            if scan.returncode:
                raise ValueError('Адаптер Wi-Fi недоступен или сканирование занято.')
            return {'networks': scan.stdout.splitlines()[:150]}
        if mode == 'bluetooth':
            if not command(['bluetoothctl', 'list']):
                raise ValueError('Bluetooth-адаптер недоступен.')
            scan = subprocess.run(['bluetoothctl', '--timeout', '7', 'scan', 'on'],
                                  capture_output=True, text=True, timeout=10)
            if 'Failed to start discovery' in scan.stdout or 'No default controller' in scan.stdout:
                raise ValueError('Адаптер Bluetooth не запустил обнаружение.')
            discovered = re.findall(r'Device ((?:[\dA-Fa-f]{2}:){5}[\dA-Fa-f]{2})', scan.stdout)
            return {'seen': sorted(set(discovered)), 'devices': diagnostics('bluetooth')['devices']}
        if mode == 'lan':
            return port_inventory(str(data.get('cidr', '')))
        raise ValueError('Неизвестный режим обнаружения.')
    finally:
        DISCOVERY_LOCK.release()


MCP_TASKS = {'host', 'links', 'observed', 'bluetooth'}


def mcp_diagnostic(handler):
    route = urlsplit(handler.path)
    task = parse_qs(route.query).get('task', [''])[0]
    if task not in MCP_TASKS:
        return handler.respond(404, {'error':'Unknown diagnostic task'})
    token = handler.headers.get('Authorization', '')
    if not token.startswith('Bearer ') or len(token) != 71:
        return handler.respond(401, {'error':'Invalid node credential'})
    try:
        expected = (ROOT / 'mcp-diagnostics.sha256').read_text().strip()
    except OSError:
        return handler.respond(401, {'error':'Node credential not configured'})
    if not hmac.compare_digest(hashlib.sha256(token[7:].encode()).hexdigest(), expected):
        return handler.respond(401, {'error':'Invalid node credential'})
    return handler.respond(200, diagnostics(task))


def authorised(handler):
 try:
  c=SimpleCookie();c.load(handler.headers.get('Cookie',''));token=c['pi_session'].value
  with guard:return sessions.get(token,0)>time.monotonic()
 except Exception:return False
class Handler(BaseHTTPRequestHandler):
 server_version='PiNode';sys_version=''
 def log_message(self,*args):pass
 def respond(self,code,data,typ='application/json; charset=utf-8',extra=None):
  raw=json.dumps(data,ensure_ascii=False).encode() if typ.startswith('application/json') else data.encode();self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
  for k,v in (extra or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path=='/':return self.respond(200,HTML,'text/html; charset=utf-8')
  if self.path=='/app.js':return self.respond(200,JS,'application/javascript; charset=utf-8')
  if self.path=='/style.css':return self.respond(200,CSS,'text/css; charset=utf-8')
  if urlsplit(self.path).path=='/api/mcp-diagnostics':return mcp_diagnostic(self)
  if not authorised(self):return self.respond(401,{'error':'Sign in required'})
  if self.path=='/api/status':return self.respond(200,status())
  if self.path=='/api/network':return self.respond(200,network_status())
  if self.path=='/api/tunnels':return self.respond(200,tunnel_status())
  route=urlsplit(self.path)
  if route.path in ('/api/share', '/api/share-download'):
   path=parse_qs(route.query).get('path',[''])[0]
   try:
    if route.path=='/api/share':return self.respond(200,share_list(path))
    if not TRANSFER_LOCK.acquire(blocking=False):return self.respond(503,{'error':'Заняты все слоты передачи.'})
    try:
     self.connection.settimeout(40)
     return download_file(self,path)
    finally:TRANSFER_LOCK.release()
   except (OSError,ValueError):return self.respond(400,{'error':'Файл или каталог недоступен.'})
  if route.path in ('/api/diagnostics','/api/usb-list','/api/usb-download'):
   query = {key: values[0] for key, values in parse_qs(route.query).items()}
   try:
    if route.path == '/api/diagnostics':
     result = diagnostics(query.get('task'))
    else:
     browse = usb_list if route.path == '/api/usb-list' else usb_file
     result = browse(query.get('uuid', ''), query.get('path', ''))
    return self.respond(200, result)
   except (OSError, ValueError, subprocess.SubprocessError):
    return self.respond(400, {'error':'Данные недоступны. Проверьте носитель и его файловую систему.'})
  if route.path=='/api/files':
   try:return self.respond(200,list_home(parse_qs(route.query).get('path',[''])[0]))
   except (OSError,ValueError):return self.respond(400,{'error':'Недоступный каталог'})
  if route.path=='/api/file':
   try:
    p=home_path(parse_qs(route.query).get('path',[''])[0]);f=p.open('rb')
    if not p.is_file():f.close();raise ValueError()
    size=os.fstat(f.fileno()).st_size
    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="download"');self.send_header('Content-Length',str(size));self.send_header('Cache-Control','no-store');self.end_headers()
    with f:shutil.copyfileobj(f,self.wfile,128*1024)
    return
   except (OSError,ValueError):return self.respond(400,{'error':'Недоступный файл'})
  return self.respond(404,{'error':'Not found'})
 def do_POST(self):
  origin=self.headers.get('Origin','');host=self.headers.get('Host','')
  if origin!='https://'+host:return self.respond(403,{'error':'Invalid origin'})
  route=urlsplit(self.path)
  if route.path=='/api/share-upload':
   if not authorised(self):return self.respond(401,{'error':'Sign in required'})
   if not TRANSFER_LOCK.acquire(blocking=False):return self.respond(503,{'error':'Заняты все слоты передачи.'})
   try:
    self.connection.settimeout(40)
    if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') is None:
     raise ValueError('Требуется Content-Length.')
    path=parse_qs(route.query).get('path',[''])[0]
    return self.respond(201,upload_file(self,path,int(self.headers['Content-Length'])))
   except FileExistsError:return self.respond(409,{'error':'Файл с таким именем уже существует.'})
   except (OSError,ValueError) as error:return self.respond(400,{'error':str(error) if isinstance(error,ValueError) else 'Ошибка записи файла.'})
   finally:
    self.close_connection=True
    TRANSFER_LOCK.release()
  try:
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=16384:raise ValueError()
   data=json.loads(self.rfile.read(length));assert isinstance(data,dict)
  except Exception:return self.respond(400,{'error':'Invalid request'})
  if self.path=='/api/login':
   now=time.monotonic()
   with guard:
    attempts[:]=[x for x in attempts if now-x<60]
    if len(attempts)>=8:return self.respond(429,{'error':'Try again later'})
    candidate=hashlib.scrypt(str(data.get('password','')).encode(),salt=bytes.fromhex(CFG['salt']),n=16384,r=8,p=1).hex()
    if not hmac.compare_digest(candidate,CFG['password_hash']):attempts.append(now);return self.respond(401,{'error':'Invalid password'})
    token=secrets.token_urlsafe(32);sessions[token]=now+43200
   return self.respond(200,{'ok':True},extra={'Set-Cookie':'pi_session='+token+'; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=43200'})
  if not authorised(self):return self.respond(401,{'error':'Sign in required'})
  if self.path=='/api/logout':
   try:
    c=SimpleCookie();c.load(self.headers.get('Cookie',''))
    with guard:sessions.pop(c['pi_session'].value,None)
   except Exception:pass
   return self.respond(200,{'ok':True},extra={'Set-Cookie':'pi_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'})
  if self.path=='/api/discovery':
   try:return self.respond(200,discovery(data))
   except (ValueError,OSError,subprocess.SubprocessError) as error:
    return self.respond(400,{'error':str(error) if isinstance(error,ValueError) else 'Обнаружение недоступно.'})
  if self.path=='/api/share-folder':
   try:
    with share_parent(str(data.get('path',''))) as (root,name):os.mkdir(name,0o700,dir_fd=root)
    return self.respond(201,{'ok':True})
   except (ValueError,OSError):return self.respond(400,{'error':'Не удалось создать каталог.'})
  if self.path=='/api/action':
   name=str(data.get('service',''));action=str(data.get('action',''))
   if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',name) or action not in ('start','stop','restart'):return self.respond(400,{'message':'Invalid operation'})
   try:p=subprocess.run(['docker',action,name],capture_output=True,text=True,timeout=60)
   except Exception:return self.respond(503,{'message':'Docker unavailable'})
   return self.respond(200 if p.returncode==0 else 409,{'message':'Done' if p.returncode==0 else 'Docker action failed'})
  return self.respond(404,{'error':'Not found'})
HTML='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pi Control Node</title><link rel="stylesheet" href="/style.css"><script defer src="/app.js"></script></head><body><main><header><span>● PI CONTROL / NODE</span><h1>Узел Raspberry Pi<span class="dot">.</span></h1><p>Лёгкая панель удалённого устройства</p></header><section id="login" class="login-card" aria-labelledby="login-title"><div class="login-cover"><strong>Pi Control</strong><h2>Удалённый узел</h2><p>Состояние Raspberry Pi, подключённые устройства и файлы в одной панели.</p></div><div class="login-body"><h2 id="login-title">Войти в панель</h2><form id="form"><label for="password">Пароль узла</label><div class="login-password"><input id="password" type="password" autocomplete="current-password" required><button id="toggle-password" class="secondary" type="button" aria-pressed="false">Показать</button></div><button id="login-submit">Войти</button></form><p id="error" role="alert"></p></div></section><div id="view" hidden><nav aria-label="Разделы"><button data-tab="overview">Обзор</button><button data-tab="monitor">Мониторинг</button><button data-tab="diagnostics">Диагностика</button><button data-tab="network">Сеть</button><button data-tab="tunnels">Туннели</button><button data-tab="files">Файлы</button><button data-tab="settings">Настройки</button></nav><section><div class="line"><h2 id="heading">Обзор</h2><button id="refresh" class="secondary">Обновить</button></div><div id="content"></div></section><button id="logout" class="secondary">Выйти</button></div></main></body></html>'''
CSS='''*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:#f3f7f4;color:#193027;font:15px system-ui}main{max-width:1050px;margin:4vh auto;padding:20px}header span{color:#b81f49;font-size:12px;letter-spacing:.15em;font-weight:700}header .dot{font-size:inherit}h1{font-size:clamp(30px,5vw,46px);margin:12px 0}header p,section p{color:#6e8578}section{background:#fff;border:1px solid #e1ebe4;border-radius:18px;padding:24px;margin:20px 0;box-shadow:0 8px 30px #1c46300c}input,button{font:inherit;border:1px solid #d7e5db;border-radius:9px;padding:10px}input{max-width:100%}button{background:#186441;color:#fff;cursor:pointer}button.secondary,nav button{background:#fff;color:#245841}nav{display:flex;gap:7px;flex-wrap:wrap}nav button.active{background:#186441;color:#fff}.line,.row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.row{border-top:1px solid #edf3ee;padding:12px 0}.metrics{display:flex;gap:12px;flex-wrap:wrap}.metric{background:#f4f9f5;border-radius:10px;padding:15px;flex:1;min-width:140px}.muted{color:#6e8578;font-size:13px}.name{overflow-wrap:anywhere}.error{color:#ad2445}a{color:#186441}.diagnostic-output{margin-top:1rem}.diagnostic-output pre{overflow:auto;max-height:28rem;background:#f4f9f5;padding:1rem;border-radius:10px}.diagnostic-output button{margin:3px}body:has(#view[hidden]) main>header{display:none}body:has(#view[hidden]) main{min-height:90vh;display:grid;place-items:center}.login-card{display:grid;grid-template-columns:1fr 1fr;overflow:hidden;padding:0;width:min(100%,860px);box-shadow:0 18px 60px #243a3420}.login-cover,.login-body{padding:clamp(22px,5vw,50px)}.login-cover{background:#a62953;color:white}.login-cover strong{font-size:1.2rem}.login-cover h2{font-size:clamp(26px,4vw,44px);margin:32px 0 8px}.login-cover p{color:#fff0f5}.login-body form{display:grid;gap:12px}.login-password{display:flex;gap:5px}.login-password input{flex:1;width:0}.login-body #error{color:#9c2247}.tool-grid{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}.share-board,.discover-card{margin-top:16px}.discover-card label{display:block;margin-top:10px}.share-board progress{width:min(160px,100%)}@media(max-width:620px){.login-card{grid-template-columns:1fr}.login-cover h2{margin-top:12px}}button:focus-visible,a:focus-visible{outline:3px solid #bc4a6a;outline-offset:2px}'''
JS='''"use strict";const $=x=>document.querySelector(x);let tab='overview',dir='';const labels={overview:'Обзор',monitor:'Мониторинг',network:'Сеть',diagnostics:'Диагностика и USB',tunnels:'Туннели',files:'Файлы',settings:'Настройки'};async function api(url,data){let r=await fetch(url,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,credentials:'same-origin'});let x=await r.json();if(!r.ok)throw Error(x.error||x.message||'Ошибка');return x}function text(tag,value,cls){let e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e}function row(parent,name,value){let r=text('div','','row');r.append(text('span',name,'name'),text('span',String(value),'muted'));parent.append(r)}function metrics(root,s){let box=text('div','','metrics');for(let [a,b] of [['ОЗУ занято',(s.memory_used/1073741824).toFixed(2)+' ГиБ'],['Диск свободен',(s.disk_free/1073741824).toFixed(2)+' ГиБ'],['Нагрузка CPU',Number(s.load).toFixed(2)],['Время работы',Math.floor(s.uptime/3600)+' ч']])box.append(text('div',a+': '+b,'metric'));root.append(box)}async function load(){try{let d=await api('/api/status');$('#login').hidden=true;$('#view').hidden=false;$('#heading').textContent=labels[tab];document.querySelectorAll('[data-tab]').forEach(e=>e.classList.toggle('active',e.dataset.tab===tab));let root=$('#content');root.replaceChildren();if(tab==='overview'||tab==='monitor'){metrics(root,d.system);if(tab==='monitor'){row(root,'ОЗУ всего',(d.system.memory_total/1073741824).toFixed(2)+' ГиБ');row(root,'Диск всего',(d.system.disk_total/1073741824).toFixed(2)+' ГиБ');row(root,'Диск занято',(d.system.disk_used/1073741824).toFixed(2)+' ГиБ');row(root,'Температура',d.system.temperature===null?'нет данных':d.system.temperature+' °C')}else{root.append(text('h3','Контейнеры'));for(let c of d.services){let r=text('div','','row');r.append(text('span',c.name+' · '+c.state,'name'));let b=text('button',c.state==='running'?'Остановить':'Запустить');b.onclick=async()=>{try{await api('/api/action',{service:c.id,action:c.state==='running'?'stop':'start'});load()}catch(e){alert(e.message)}};r.append(b);root.append(r)}if(!d.services.length)root.append(text('p','Контейнеров нет.'))}}else if(tab==='diagnostics'){diagnosticView(root)}else if(tab==='network'){let n=await api('/api/network');root.append(text('h3','Интерфейсы'));for(let i of n.interfaces)row(root,i.name+' · '+i.state,i.addresses.join(', ')||'нет адреса');root.append(text('h3','Маршруты IPv4'));for(let r of n.routes)row(root,r.dst||'default',[r.gateway,r.dev].filter(Boolean).join(' · '));row(root,'Пересылка IPv4',n.forwarding?'включена':'выключена');discoveryView(root,n.attached_networks||{})}else if(tab==='tunnels'){let t=await api('/api/tunnels');root.append(text('p','Состояние Tailscale и объявленных маршрутов. Изменение маршрутов выполняется администратором.'));row(root,'Tailscale',t.available?'доступен':'недоступен');row(root,'Приём маршрутов',t.accept_routes?'включён':'выключен');for(let r of t.advertised_routes)row(root,'Объявленный маршрут',r);if(!t.advertised_routes.length)root.append(text('p','Объявленных маршрутов нет.'))}else if(tab==='files'){shareBrowser(root)}else if(tab==='settings'){root.append(text('p','Облегчённая панель работает с отдельным паролем и HTTPS-сертификатом. Доступны прямые подключения по локальной сети и Tailscale.'));row(root,'Режим','Управляемый узел');row(root,'Порт панели',location.port||'443');row(root,'Безопасность','HTTPS · отдельная сессия');root.append(text('p','Пароль и сетевые параметры панели в этом разделе пока изменить нельзя.','muted'))}}catch(e){if(/Sign in/.test(e.message)){$('#login').hidden=false;$('#view').hidden=true}else{$('#content').replaceChildren(text('p',e.message,'error'))}}}document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;load()});$('#form').onsubmit=async e=>{e.preventDefault();try{await api('/api/login',{password:$('#password').value});$('#password').value='';$('#error').textContent='';load()}catch(e){$('#error').textContent=e.message}};$('#refresh').onclick=load;$('#logout').onclick=async()=>{await api('/api/logout',{});location.reload()};load();
$('#toggle-password').onclick=()=>{
 const input=$('#password'),toggle=$('#toggle-password'),visible=input.type==='password';
 input.type=visible?'text':'password';toggle.setAttribute('aria-pressed',String(visible));
 toggle.textContent=visible?'Скрыть':'Показать';
};
let sharedPath='';
function shareBrowser(root){
 const tools=text('div','','tool-grid'), board=text('div','','share-board');
 const transfer=text('button','Обмен файлами'), home=text('button','Домашний каталог','secondary');
 tools.append(transfer,home);root.append(tools,board);
 transfer.onclick=()=>browseShare(board,sharedPath);
 home.onclick=()=>browseHome(board,'');
 browseShare(board,sharedPath);
}
async function browseHome(board,path){
 board.replaceChildren(text('p','Домашний каталог доступен для просмотра.'));
 try{
  const data=await api('/api/files?path='+encodeURIComponent(path));
  if(path){
   const up=text('button','↑ Выше','secondary');
   up.onclick=()=>browseHome(board,path.split('/').slice(0,-1).join('/'));board.append(up);
  }
  for(const item of data.items){
   const line=text('div','','row'), name=[path,item.name].filter(Boolean).join('/');
   line.append(text('span',item.name,'name'));
   if(item.directory){
    const open=text('button','Открыть','secondary');
    open.onclick=()=>browseHome(board,name);line.append(open);
   }else{
    const link=text('a','Скачать');
    link.href='/api/file?path='+encodeURIComponent(name);link.download=item.name;line.append(link);
   }
   board.append(line);
  }
 }catch(e){board.append(text('p',e.message,'error'))}
}
async function browseShare(board,path){
 sharedPath=path;
 const status=text('p','Загружаем каталог…','muted');status.setAttribute('role','status');
 board.replaceChildren(text('h3','Обмен файлами / '+path),status);
 try{
  const result=await api('/api/share?path='+encodeURIComponent(path));
  if(!status.isConnected)return;
  status.textContent='';
  const buttons=text('div','','tool-grid'), upload=text('input','');
  upload.type='file';upload.setAttribute('aria-label','Выберите файл для загрузки');
  const folder=text('button','Новая папка','secondary');
  buttons.append(upload,folder);board.append(buttons);
  const child=name=>[path,name].filter(Boolean).join('/');
  if(path){
   const up=text('button','↑ Выше','secondary');
   up.onclick=()=>browseShare(board,path.split('/').slice(0,-1).join('/'));buttons.prepend(up);
  }
  folder.onclick=async()=>{
   const name=prompt('Имя новой папки');
   if(!name||name.includes('/'))return;
   try{await api('/api/share-folder',{path:child(name)});browseShare(board,path)}
   catch(e){status.textContent=e.message}
  };
  upload.onchange=()=>{
   const file=upload.files[0];if(!file)return;
   const request=new XMLHttpRequest(), progress=text('progress',''), abort=text('button','Отменить','secondary');
   request.open('POST','/api/share-upload?path='+encodeURIComponent(child(file.name)));
   progress.max=100;progress.value=0;progress.setAttribute('aria-label','Прогресс загрузки');
   abort.onclick=()=>request.abort();
   buttons.append(progress,abort);
   upload.disabled=true;
   request.upload.onprogress=e=>{if(e.lengthComputable)progress.value=Math.round(100*e.loaded/e.total)};
   request.onload=()=>{
    if(request.status===201 && status.isConnected){browseShare(board,path);return}
    try{status.textContent=JSON.parse(request.responseText).error||'Ошибка загрузки'}
    catch{status.textContent='Ошибка загрузки'}
   };
   request.onerror=()=>{status.textContent='Соединение прервано.'};
   request.onabort=()=>{status.textContent='Загрузка отменена.'};
   request.onloadend=()=>{upload.disabled=false;abort.remove();progress.remove()};
   request.send(file);
  };
  for(const item of result.items){
   const line=text('div','','row'), name=child(item.name);
   line.append(text('span',item.name,'name'));
   if(item.directory){
    const open=text('button','Открыть','secondary');
    open.onclick=()=>browseShare(board,name);line.append(open);
   }else{
    const link=text('a','Скачать · '+new Intl.NumberFormat('ru').format(item.size)+' Б');
    link.href='/api/share-download?path='+encodeURIComponent(name);link.download=item.name;line.append(link);
   }
   board.append(line);
  }
  if(!result.items.length)board.append(text('p','В этой папке пока нет файлов.','muted'));
 }catch(e){status.textContent=e.message}
}
function discoveryView(root,networks){
 const section=text('div','','discover-card');
 section.append(text('h3','Обнаружение устройств'),text('p','Поиск выполняется на этом Raspberry Pi. Инвентаризация проверяет распространённые TCP-порты, до 64 адресов за запуск.'));
 const links=text('ul','');
 for(const [network,iface] of Object.entries(networks))links.append(text('li',iface+' · '+network));
 if(links.children.length)section.append(links);
 const subnet=text('input','');subnet.placeholder='192.168.1.0/26';subnet.setAttribute('aria-label','Подсеть для инвентаризации');
 const consent=text('input','');consent.type='checkbox';
 const check=text('label',' Разрешено обнаружение устройств');check.prepend(consent);
 const buttons=text('div','','tool-grid'), output=text('div','','diagnostic-output');
 section.append(subnet,check,buttons,output);root.append(section);
 for(const [mode,label] of [['wifi','Искать Wi-Fi'],['bluetooth','Искать Bluetooth'],['lan','Инвентаризация IPv4']]){
  const run=text('button',label,'secondary');
  run.onclick=async()=>{
   if(!consent.checked){output.replaceChildren(text('p','Подтвердите разрешение.','error'));return}
   const all=[...buttons.children];all.forEach(item=>item.disabled=true);
   output.replaceChildren(text('p','Ищем устройства…','muted'));
   try{
    const data=await api('/api/discovery',{mode,confirmed:true,cidr:subnet.value.trim()});
    output.replaceChildren();
    if(mode==='lan'){
     output.append(text('h4',data.subnet+' · '+data.devices.length+' устройств'));
     for(const device of data.devices)row(output,device.ip,[device.mac,device.open_tcp.length?'TCP '+device.open_tcp.join(', '):'Открытые TCP-порты не обнаружены'].filter(Boolean).join(' · '));
    }else{
     const entries=mode==='wifi'?data.networks:data.devices;
     if(mode==='bluetooth')output.append(text('p','Во время поиска обнаружено: '+data.seen.length));
     for(const entry of entries)output.append(text('p',entry));
     if(!entries.length)output.append(text('p','Устройства не найдены.'));
    }
   }catch(e){output.replaceChildren(text('p',e.message,'error'))}
   finally{all.forEach(item=>item.disabled=false)}
  };
  buttons.append(run);
 }
}

function diagnosticView(root){
 const actions=text('div','','metrics'), output=text('div','','diagnostic-output');
 root.append(actions,output);
 const templates=[['links','Интерфейсы'],['observed','Известные соседи'],['bluetooth','Bluetooth'],['usb','USB']];
 for(const [task,label] of templates){
  const button=text('button',label,'secondary'); actions.append(button);
  button.onclick=async()=>{
   button.disabled=true; output.replaceChildren(text('p','Читаем данные…'));
   try{
    const data=await api('/api/diagnostics?task='+task);
    if(task==='usb'){
     output.replaceChildren(text('p',data.devices.length?'Выберите накопитель для чтения.':'USB не обнаружен.'));
     for(const item of data.devices){
      const open=text('button',item.label,'secondary');
      open.onclick=()=>usbView(output,item,''); output.append(open);
     }
    }else output.replaceChildren(text('pre',JSON.stringify(data,null,2)));
   }catch(e){output.replaceChildren(text('p',e.message,'error'))}
   finally{button.disabled=false}
  }
 }
}
async function usbView(root,device,path){
 const query=name=>'?uuid='+encodeURIComponent(device.uuid)+'&path='+encodeURIComponent(name);
 try{
  const data=await api('/api/usb-list'+query(path));
  root.replaceChildren(text('p',device.label+' / '+path));
  const back=text('button',path?'↑ Выше':'Накопители','secondary');
  back.onclick=()=>path?usbView(root,device,path.split('/').slice(0,-1).join('/')):load(); root.append(back);
  for(const item of data.items){
   const next=[path,item.name].filter(Boolean).join('/');
   const line=text('div','','row'),button=text('button',item.directory?'Открыть':'Скачать','secondary');
   line.append(text('span',item.name,'name'),button);root.append(line);
   button.disabled=!item.directory && item.size>8388608;
   button.onclick=async()=>{
    if(item.directory){usbView(root,device,next);return}
    button.disabled=true;
    try{
     const file=await api('/api/usb-download'+query(next));
     const blob=new Blob([Uint8Array.from(atob(file.base64),c=>c.charCodeAt(0))]);
     const url=URL.createObjectURL(blob),a=text('a','');
     a.href=url;a.download=file.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
    }catch(e){line.append(text('small',e.message,'error'))}
    finally{button.disabled=false}
   };
  }
 }catch(e){root.append(text('p',e.message,'error'))}
}
'''
def private_addresses():
 out=['127.0.0.1']
 try:rows=json.loads(command(['ip','-j','-4','addr','show']))
 except Exception:return out
 for r in rows:
  name=r.get('ifname','')
  if not (name.startswith(('eth','en','wl')) or name=='tailscale0'):continue
  for x in r.get('addr_info',[]):
   try:
    addr=ipaddress.ip_address(x['local'])
    if addr.version==4 and (addr.is_private or addr in ipaddress.ip_network('100.64.0.0/10')) and not addr.is_loopback:out.append(str(addr))
   except Exception:pass
 return sorted(set(out))
if __name__=='__main__':
 ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2;ctx.load_cert_chain(str(ROOT/'cert.pem'),str(ROOT/'key.pem'))
 # Reconcile private and Tailscale addresses as interfaces move between networks.
 port=int(CFG.get('port',8484));servers={}
 while True:
  addresses=set(private_addresses())
  for addr in addresses-set(servers):
   try:
    srv=ThreadingHTTPServer((addr,port),Handler);srv.socket=ctx.wrap_socket(srv.socket,server_side=True);servers[addr]=srv;threading.Thread(target=srv.serve_forever,daemon=True).start()
   except OSError:continue
  for addr in set(servers)-addresses:
   srv=servers.pop(addr);srv.shutdown();srv.server_close()
  time.sleep(20)
