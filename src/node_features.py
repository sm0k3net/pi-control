"""Read-only task templates and USB browsing for enrolled Pi nodes."""
import hashlib
import hmac
import http.client
import ipaddress
import subprocess
import json
import ssl
import threading
import uuid
from contextlib import contextmanager
import time
from urllib.parse import urlencode

import cluster5

TASKS = {'host', 'links', 'observed', 'bluetooth', 'usb'}
MAX = 12 * 1024 * 1024


def status():
    return {'nodes': [{k: node[k] for k in ('id', 'name', 'host')} for node in cluster5.get()]}


def secret(node, data):
    password = data.get('password')
    if password and data.get('id') == node['id']:
        return str(password)
    entry = node.get('entry')
    if not entry:
        return ''
    try:
        import service_secrets
    except ImportError:
        if not data.get('master') or data.get('id') != node['id']:
            return ''
        import vault
        return vault.dispatch('reveal', {'id': entry, 'master': data['master']})['password']
    return service_secrets.resolve({'id': entry, 'master': data.get('master', '')}, 'nodes')['password']


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, node, timeout):
        self.fingerprint = node['fingerprint'].upper()
        if not cluster5.addr_ok(node['host']):
            raise ValueError('Адрес узла недопустим.')
        super().__init__(node['host'], node['port'], context=ssl._create_unverified_context(),
                         timeout=timeout)

    def connect(self):
        super().connect()
        fingerprint = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest().upper()
        if not hmac.compare_digest(fingerprint, self.fingerprint):
            self.close()
            raise ValueError('Отпечаток сертификата не совпадает.')


@contextmanager
def peer_session(node, password, timeout=8):
    conn = PinnedConnection(node, timeout)
    headers = {'Origin': f"https://{node['host']}:{node['port']}", 'Content-Type': 'application/json'}
    try:
        conn.request('POST', '/api/login', body=json.dumps({'password': password}), headers=headers)
        login = conn.getresponse()
        cookie = login.getheader('Set-Cookie', '').split(';', 1)[0]
        login.read(8192)
        if login.status != 200 or not cookie.startswith('pi_session='):
            raise ValueError('Узел отклонил вход.')
        headers['Cookie'] = cookie
        yield conn, headers
    finally:
        conn.close()
        if headers.get('Cookie'):
            try:
                conn.request('POST', '/api/logout', body='{}', headers=headers)
                conn.getresponse().read(8192)
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                conn.close()


def get_peer(node, password, route):
    with peer_session(node, password) as (conn, headers):
        conn.request('GET', route, headers=headers)
        response = conn.getresponse()
        payload = response.read(MAX + 1)
        if response.status == 404:
            raise ValueError('Обновите лёгкую панель этого узла.')
        if response.status != 200 or len(payload) > MAX:
            raise ValueError('Узел не смог выполнить запрос.')
        return json.loads(payload)


def mcp_snapshot(node_id, task, token):
    if task not in {'host', 'links', 'observed', 'bluetooth'}:
        raise ValueError('Unsupported diagnostic task.')
    node = next((item for item in cluster5.get() if item['id'] == node_id), None)
    if not node:
        raise ValueError('Enrolled node unavailable.')
    conn = PinnedConnection(node, 16)
    try:
        path = '/api/mcp-diagnostics?' + urlencode({'task':task})
        conn.request('GET', path, headers={'Authorization':'Bearer '+token})
        reply = conn.getresponse()
        if reply.status != 200:
            raise ValueError('The remote node rejected its diagnostic credential.')
        data = reply.read(MAX + 1)
        if len(data) > MAX:
            raise ValueError('Diagnostic response is too large.')
        return {'node':node['name'], 'id':node_id, 'time':int(time.time()), 'result':json.loads(data)}
    finally:
        conn.close()


TRANSFERS = {}
TRANSFER_GUARD = threading.Lock()
TRANSFER_LIMIT = 128 * 1024**3


def transfer_action(data):
    operation = data['op']
    with TRANSFER_GUARD:
        if operation == 'transfer-status':
            item = TRANSFERS.get(data.get('job'))
            if not item:
                raise ValueError('Передача не найдена.')
            return dict(item)
        active = sum(item['phase'] == 'running' for item in TRANSFERS.values())
        if active >= 2:
            raise ValueError('Дождитесь завершения одной из текущих передач.')
        for key, item in list(TRANSFERS.items()):
            if item['phase'] != 'running' and time.time() - item['updated'] > 1800:
                del TRANSFERS[key]
        if len(TRANSFERS) >= 64:
            raise ValueError('Журнал передач заполнен. Повторите позже.')
        nodes = {node['id']: node for node in cluster5.get()}
        source = nodes.get(data.get('source'))
        destination = nodes.get(data.get('destination'))
        if not source or not destination or source['id'] == destination['id']:
            raise ValueError('Выберите два разных подключённых узла.')
        credentials = []
        for node, key in ((source, 'source_password'), (destination, 'destination_password')):
            password = secret(node, {'id':node['id'], 'password':data.get(key), 'master':data.get('master')})
            if not password:
                raise ValueError('Укажите пароль панели или настройте секреты обоих узлов.')
            credentials.append(password)
        source_path = str(data.get('source_path', ''))
        target_path = str(data.get('target_path', ''))
        if not source_path or not target_path or max(len(source_path),len(target_path)) > 2048:
            raise ValueError('Укажите пути в папках обмена обоих узлов.')
        job = uuid.uuid4().hex
        TRANSFERS[job] = {'phase':'running','bytes':0,'total':None,'updated':time.time()}
        threading.Thread(target=copy_file, args=(job, source, destination, credentials,
                                               source_path, target_path), daemon=True).start()
        return {'job':job}


def transfer_progress(job, **changes):
    with TRANSFER_GUARD:
        TRANSFERS[job].update(changes, updated=time.time())


def copy_file(job, source, destination, credentials, source_path, target_path):
    try:
        with peer_session(source, credentials[0], 45) as (reader, auth):
            reader.request('GET','/api/share-download?'+urlencode({'path':source_path}),headers=auth)
            response = reader.getresponse()
            if response.status != 200:
                raise ValueError('Исходный файл недоступен в папке обмена.')
            size = int(response.getheader('Content-Length', '-1'))
            if not 0 <= size <= TRANSFER_LIMIT:
                raise ValueError('Некорректный размер файла.')
            transfer_progress(job, total=size)
            with peer_session(destination, credentials[1], 45) as (writer, auth):
                writer.putrequest('POST','/api/share-upload?'+urlencode({'path':target_path}))
                writer.putheader('Content-Length',str(size))
                for key, value in auth.items():
                    if key != 'Content-Type':
                        writer.putheader(key,value)
                writer.endheaders()
                sent = 0
                while sent < size:
                    chunk = response.read(min(256*1024, size-sent))
                    if not chunk:
                        raise ValueError('Чтение исходного файла прервано.')
                    writer.send(chunk)
                    sent += len(chunk)
                    transfer_progress(job, bytes=sent)
                reply = writer.getresponse()
                if reply.status != 201:
                    raise ValueError('Целевой узел отклонил файл. Проверьте путь, свободное место и имя.')
                reply.read(8192)
        transfer_progress(job, phase='complete')
    except (OSError, ValueError, http.client.HTTPException) as error:
        message = str(error) if isinstance(error, ValueError) else 'Соединение прервалось во время передачи.'
        transfer_progress(job, phase='error', error=message)


PRIVATE_RANGES = tuple(ipaddress.IPv4Network(value) for value in
                       ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def local_cmd(args):
    result = subprocess.run(args,capture_output=True,text=True,timeout=10,check=True)
    return json.loads(result.stdout)


def lan_prefix(value):
    try:
        parsed = ipaddress.IPv4Network(value)
    except (ValueError,TypeError):
        return None
    return parsed if any(parsed.subnet_of(parent) for parent in PRIVATE_RANGES) else None


def remote_lans():
    state = local_cmd(['tailscale','status','--json'])
    prefs = local_cmd(['tailscale','debug','prefs'])
    routes = local_cmd(['ip','-j','-4','route','show','table','all'])
    installed = {row.get('dst') for row in routes if row.get('dev') == 'tailscale0'
                 and str(row.get('table')) == '52'}
    connected = [route for row in routes if row.get('scope') == 'link'
                 and row.get('dev','').startswith(('eth','en','wl'))
                 if (route := lan_prefix(row.get('dst'))) is not None]
    nodes = {node['host']:node for node in cluster5.get()}
    output = []
    for peer in (state.get('Peer') or {}).values():
        node = next((nodes[ip] for ip in peer.get('TailscaleIPs',[]) if ip in nodes),None)
        if not node:
            continue
        for raw in peer.get('PrimaryRoutes') or []:
            if (prefix := lan_prefix(raw)) is None:
                continue
            overlap = any(prefix.overlaps(route) for route in connected)
            output.append({'node':node['name'],'cidr':str(prefix),'online':bool(peer.get('Online')),
                           'installed':str(prefix) in installed,'overlap':overlap})
    return {'accept_routes':bool(prefs.get('RouteAll')),'routes':output}


def accept_remote_lans(data):
    enable = data.get('enabled')
    if not isinstance(enable,bool):
        raise ValueError('Укажите новое состояние приёма маршрутов.')
    current = remote_lans()
    if enable:
        if not any(row['online'] and not row['overlap'] for row in current['routes']):
            raise ValueError('Нет одобренных доступных маршрутов к узлам Pi.')
        if any(row['overlap'] for row in current['routes']):
            raise ValueError('Маршрут удалённого узла пересекается с локальной сетью.')
    subprocess.run(['tailscale','set','--accept-routes='+str(enable).lower()],
                   capture_output=True,timeout=15,check=True)
    return remote_lans()


def action(data):
    op = data.get('op')
    if op=='discover-pi':return discover_nodes()
    if op=='probe-pi':return probe_node(data)
    if op=='pair-pi':return pair_node(data)
    if op == 'remote-lans':
        return remote_lans()
    if op == 'remote-lans-accept':
        return accept_remote_lans(data)
    if op in ('transfer-start', 'transfer-status'):
        return transfer_action(data)
    if op == 'task':
        task = data.get('task')
        if not isinstance(task, str) or task not in TASKS:
            raise ValueError('Неизвестный шаблон.')
        route = '/api/diagnostics?' + urlencode({'task': task})
    elif op in ('usb-list', 'usb-download'):
        route = '/api/' + op + '?' + urlencode({'uuid': str(data.get('uuid', ''))[:80],
                                               'path': str(data.get('path', ''))[:2048]})
    else:
        raise ValueError('Неизвестное действие.')
    selected = data.get('id')
    nodes = cluster5.get()
    candidates = [node for node in nodes if node['id'] == selected]
    if op == 'task' and selected == 'auto':
        candidates = [node for node in nodes if node.get('entry')][:3]
        data = {'id': 'auto'}
    error = 'Добавьте узел и укажите пароль панели или привяжите секрет.'
    for node in candidates:
        try:
            password = secret(node, data)
            if password:
                result = get_peer(node, password, route)
                return {'node': node['name'], 'id': node['id'], 'time': int(time.time()), 'result': result}
        except (KeyError, TypeError, ValueError, OSError, http.client.HTTPException) as exc:
            error = str(exc) if isinstance(exc, ValueError) else 'Связь с узлом недоступна.'
    raise ValueError(error)


CREDENTIAL_SCOPES = frozenset(('nodes','ssh','ai'))


def secret_catalog():
    try:
        import service_secrets
    except ImportError:
        return {'available':False,'entries':[],'nodes':[]}
    nodes = cluster5.get()
    entries = service_secrets.listing()['entries']
    for entry in entries:
        entry['nodes'] = [node['name'] for node in nodes if node.get('entry')==entry['id']]
    return {'available':True,'entries':entries,
            'nodes':[{key:node[key] for key in ('id','name','host')} for node in nodes]}


def enroll_service_secret(data):
    try:
        import service_secrets
    except ImportError:
        raise ValueError('Хранилище сервисных секретов ещё не настроено.') from None
    import vault

    entry, master = data.get('entry'), data.get('master')
    scopes = data.get('scopes')
    node_id = data.get('node')
    if not isinstance(entry,str) or not isinstance(master,str) or not master:
        raise ValueError('Откройте KDBX и выберите сохранённую запись.')
    if (not isinstance(scopes,list) or not scopes or
            any(not isinstance(scope,str) or scope not in CREDENTIAL_SCOPES for scope in scopes)):
        raise ValueError('Выберите, где разрешено использовать секрет.')
    node = None
    if node_id:
        if 'nodes' not in scopes:
            raise ValueError('Разрешите использование в разделе «Узлы Pi».')
        node = next((item for item in cluster5.get() if item['id']==node_id),None)
        if not node:
            raise ValueError('Узел больше не подключён.')
    record = vault.dispatch('reveal',{'id':entry,'master':master})
    if node is not None:
        try:
            get_peer(node,record['password'],'/api/status')
        except (OSError,http.client.HTTPException):
            raise ValueError('Связь с узлом недоступна. Привязка не изменена.') from None
    with service_secrets.LOCK:
        known = next((item for item in service_secrets.listing()['entries'] if item['id']==entry),{})
        result = service_secrets.save({**record,'id':entry,
                                      'scopes':sorted(set(scopes)|set(known.get('scopes',[])))})
    if node is not None:
        with cluster5.lock:
            nodes=cluster5.get()
            current=next((item for item in nodes if item['id']==node_id),None)
            fields=('host','port','fingerprint','entry')
            if not current or any(current.get(k)!=node.get(k) for k in fields):
                return {'id':result['id'],'message':'Секрет создан. Узел изменился во время проверки, привязка не обновлена.'}
            current['entry']=entry
            cluster5.extras.atomic(cluster5.FILE,nodes)
        return {'id':result['id'],'message':'Секрет проверен и привязан к узлу. Он будет использоваться при закрытом KDBX.'}
    return {'id':result['id'],'message':result['message']}


BW_SESSIONS = {}
BW_LOCK = threading.RLock()
BW_LIFETIME = 300


def bw_executable():
    import shutil
    from pathlib import Path
    dedicated = '/opt/pi-control-tools/node_modules/.bin/bw'
    return shutil.which('bw') or (dedicated if Path(dedicated).is_file() else None)


def bw_clear(token):
    with BW_LOCK:
        session = BW_SESSIONS.pop(token, None)
        if session:
            session['directory'].cleanup()


def bw_command(directory, args, extra=None):
    import os
    command = bw_executable()
    if not command:
        raise ValueError('Установите официальный Bitwarden CLI (bw).')
    environment = {'PATH':os.environ['PATH'], 'HOME':directory,
                   'BITWARDENCLI_APPDATA_DIR':directory, **(extra or {})}
    try:
        result = subprocess.run([command,*args,'--nointeraction'],env=environment,
                                capture_output=True,text=True,timeout=40)
    except subprocess.TimeoutExpired:
        raise ValueError('Bitwarden не ответил вовремя.') from None
    if result.returncode or len(result.stdout) > 24*1024**2:
        raise ValueError('Bitwarden отклонил операцию. Проверьте сервер, ключ API и пароль.')
    return result.stdout.strip()


def bw_status():
    return {'available':bool(bw_executable())}


def bw_action(owner, data):
    import os
    import re
    import secrets
    import tempfile
    from pathlib import Path
    from urllib.parse import urlsplit

    with BW_LOCK:
        op = data.get('op')
        if op == 'connect':
            url, client, key, password = (data.get(field) for field in
                                         ('server','client_id','client_secret','password'))
            if any(not isinstance(v,str) or not 1 <= len(v) <= 1024
                   or '\x00' in v for v in (url,client,key,password)):
                raise ValueError('Укажите HTTPS-сервер, персональный ключ API и мастер-пароль.')
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment
                    or any(ord(ch)<=32 for ch in url)):
                raise ValueError('Нужен адрес HTTPS без параметров и учётных данных.')
            for token, entry in list(BW_SESSIONS.items()):
                if entry['owner']==owner or entry['expires']<=time.monotonic():
                    bw_clear(token)
            if len(BW_SESSIONS)>=4:
                raise ValueError('Слишком много сессий Bitwarden.')
            root = Path('/run/pi-control-bitwarden')
            root.mkdir(mode=0o700,exist_ok=True)
            directory = tempfile.TemporaryDirectory(dir=root)
            try:
                bw_command(directory.name,['config','server',url.rstrip('/')])
                bw_command(directory.name,['login','--apikey'],{'BW_CLIENTID':client,'BW_CLIENTSECRET':key})
                session = bw_command(directory.name,['unlock','--passwordenv','PI_BW_MASTER','--raw'],
                                     {'PI_BW_MASTER':password})
                if not session:
                    raise ValueError('Хранилище осталось закрыто.')
                bw_command(directory.name,['sync'],{'BW_SESSION':session})
            except Exception:
                directory.cleanup()
                raise
            token=secrets.token_urlsafe(32)
            BW_SESSIONS[token]={'owner':owner,'directory':directory,'session':session,
                                'expires':time.monotonic()+BW_LIFETIME}
            expiry=threading.Timer(BW_LIFETIME,bw_clear,args=(token,))
            expiry.daemon=True
            expiry.start()
            return {'session':token,'seconds':BW_LIFETIME}
        token=data.get('session')
        entry=BW_SESSIONS.get(token) if isinstance(token,str) else None
        if not entry or entry['owner']!=owner or entry['expires']<=time.monotonic():
            raise ValueError('Откройте хранилище Bitwarden заново.')
        if op=='lock':
            bw_clear(token)
            return {'locked':True}
        local=entry['directory'].name
        env={'BW_SESSION':entry['session']}
        if op=='list':
            query=data.get('query','')
            if not isinstance(query,str) or len(query)>120:
                raise ValueError('Слишком длинный запрос.')
            args=['list','items','--raw']
            if query:
                args.extend(('--search',query))
            items=json.loads(bw_command(local,args,env))
            return {'items':[{'id':item['id'],'name':item.get('name',''),
                              'username':(item.get('login') or {}).get('username') or ''}
                             for item in items if item.get('type')==1 and not item.get('deletedDate')][:150]}
        if op=='copy':
            ident, field = data.get('id'), data.get('field')
            if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-fA-F-]{36}',ident) or field not in ('username','password'):
                raise ValueError('Выберите запись и поле.')
            item=json.loads(bw_command(local,['get','item',ident,'--raw'],env))
            if item.get('type')!=1 or item.get('deletedDate'):
                raise ValueError('Доступны активные записи типа Login.')
            return {'value':(item.get('login') or {}).get(field) or ''}
        raise ValueError('Неизвестная операция Bitwarden.')


BACKUP_GUARD = threading.Lock()
BACKUP_JOB = {'state':'idle'}


def node_backup_status():
    import usb_backup
    return {'nodes':status()['nodes'],'job':dict(BACKUP_JOB),
            'recipient_ready':usb_backup.RECIPIENT.is_file()}


def node_backup_action(data):
    import usb_backup
    node = next((node for node in cluster5.get() if node['id']==data.get('id')),None)
    if not node:
        raise ValueError('Выберите подключённый узел Pi.')
    password = secret(node, data)
    if not password:
        raise ValueError('Выберите сервисный секрет или укажите пароль панели узла.')
    operation = data.get('op')
    if operation == 'catalog':
        return get_peer(node,password,'/api/backup-catalog')
    if operation not in ('start','verify'):
        raise ValueError('Неизвестная операция.')
    if operation=='start' and not usb_backup.RECIPIENT.is_file():
        raise ValueError('Сначала настройте публичный ключ age для резервных копий.')
    if not BACKUP_GUARD.acquire(blocking=False):
        raise ValueError('Удалённое задание уже выполняется.')
    BACKUP_JOB.clear()
    BACKUP_JOB.update(state='running',node=node['name'],message='Проверка связи с узлом…')
    threading.Thread(target=node_backup_worker,args=(node,password,operation,
                     str(data.get('set',''))),daemon=True).start()
    return dict(BACKUP_JOB)


def node_backup_worker(node, password, operation, selected):
    import os
    import tempfile
    from pathlib import Path

    import usb_backup

    def progress(state,message,**fields):
        BACKUP_JOB.update(state=state,message=message,**fields)

    def post(conn, headers, path, data):
        conn.request('POST',path,body=json.dumps(data),headers=headers)
        response=conn.getresponse()
        payload=json.loads(response.read(32769))
        if response.status not in (200,201):
            raise ValueError(payload.get('error','Узел отклонил резервную копию.'))
        return payload

    name = ''
    try:
        if operation=='verify':
            with peer_session(node,password,3600) as (conn,headers):
                result=post(conn,headers,'/api/backup-verify',{'set':selected})
            progress('complete','Размер и SHA-256 архивов подтверждены на узле.',name=selected,bytes=result['bytes'])
            return
        get_peer(node,password,'/api/backup-catalog')
        usb_backup.RUNTIME.mkdir(mode=0o700,exist_ok=True)
        spool=Path('/var/lib/pi-control-backup-spool')
        spool.mkdir(mode=0o700,exist_ok=True)
        with usb_backup.lock(usb_backup.RUNTIME/'worker.lock'), \
             tempfile.TemporaryDirectory(dir=spool) as working:
            directory=Path(working)
            with usb_backup.lock(Path('/run/pi-address-refresh.lock')):
                name,_=usb_backup.build(directory,{'uuid':node['id'],'remote':True},{'_progress':progress})
            folder=directory/name
            manifest=usb_backup.verify_set(folder)
            progress('running','Передача зашифрованных архивов…',name=name)
            with peer_session(node,password,3600) as (conn,headers):
                for filename in usb_backup.FILES:
                    encoded=urlencode({'set':name,'part':filename})
                    with (folder/filename).open('rb') as source:
                        conn.request('POST','/api/backup-upload?'+encoded,body=source,
                                     headers={**headers,'Content-Length':str(os.fstat(source.fileno()).st_size)})
                        response=conn.getresponse()
                        if response.status!=201:
                            response.read(32678)
                            raise ValueError('Узел отклонил архив. Проверьте свободное место.')
                        response.read()
                result=post(conn,headers,'/api/backup-commit',{'set':name,'manifest':manifest})
            progress('complete','Копия зашифрована и проверена на узле.',name=name,bytes=result['bytes'])
    except (OSError,ValueError,KeyError,http.client.HTTPException,subprocess.SubprocessError) as error:
        progress('error',str(error) if isinstance(error,ValueError) else 'Не удалось завершить удалённое задание.')
        if name:
            try:
                with peer_session(node,password,8) as (conn,headers):
                    post(conn,headers,'/api/backup-abort',{'set':name})
            except (OSError,ValueError,http.client.HTTPException):
                pass
    finally:
        BACKUP_GUARD.release()


def discover_nodes():
    import re
    import shutil

    discovered={}
    current={node['host'] for node in cluster5.get()}

    def known(host, name, source, port=None):
        if cluster5.addr_ok(host) and host not in discovered:
            discovered[host]={'host':host,'name':name[:80],'port':port,
                              'source':source,'enrolled':host in current}

    for node in cluster5.get():
        known(node['host'],node['name'],'Paired',node['port'])
    if shutil.which('avahi-browse'):
        try:
            listing=subprocess.run(['avahi-browse','-rtp','_pi-control._tcp'],
                                   capture_output=True,text=True,timeout=7).stdout
            for line in listing.splitlines():
                parts=line.split(';')
                if len(parts)>8 and parts[0]=='=' and parts[2]=='IPv4':
                    known(parts[7],parts[3],'mDNS',int(parts[8]))
        except (ValueError,OSError,subprocess.SubprocessError):
            pass
    try:
        peers=local_cmd(['tailscale','status','--json']).get('Peer') or {}
        for peer in peers.values():
            name=peer.get('HostName') or ''
            if peer.get('Online') and peer.get('OS')=='linux' and re.search(
                    r'raspberry|(?:^|[-_])pi[0-9]*($|[-_])',name,re.I):
                for host in peer.get('TailscaleIPs',[]):
                    known(host,name,'Tailscale',8484)
    except (OSError,ValueError,subprocess.SubprocessError):
        pass
    return {'items':list(discovered.values())[:60]}


def node_endpoint(data):
    host=data.get('host')
    port=data.get('port',8484)
    if not isinstance(host,str) or not cluster5.addr_ok(host) or type(port) is not int or not 1<=port<=65535:
        raise ValueError('Укажите частный IPv4 или Tailscale и допустимый порт.')
    return host,port


def probe_node(data):
    host,port=node_endpoint(data)
    conn=http.client.HTTPSConnection(host,port,timeout=5,context=ssl._create_unverified_context())
    try:
        conn.connect()
        fingerprint=hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest().upper()
        return {'fingerprint':fingerprint}
    except (OSError,ssl.SSLError):
        raise ValueError('Панель узла недоступна по указанному адресу.') from None
    finally:
        conn.close()


def pair_node(data):
    import re
    host,port=node_endpoint(data)
    fingerprint=re.sub(r'[: ]','',str(data.get('fingerprint',''))).upper()
    name=str(data.get('name','')).strip()
    entry=data.get('entry') or ''
    if not name or len(name)>80 or not re.fullmatch('[A-F0-9]{64}',fingerprint):
        raise ValueError('Сверьте отпечаток и укажите имя узла.')
    node={'name':name,'host':host,'port':port,'fingerprint':fingerprint,'entry':entry}
    password=data.get('password')
    if entry:
        node['id']='candidate'
        password=secret(node,{'master':data.get('master','')})
    if not isinstance(password,str) or not password or len(password)>1024:
        raise ValueError('Укажите пароль панели или выберите запись KDBX.')
    try:
        response=get_peer(node,password,'/api/status')
    except (OSError,http.client.HTTPException):
        raise ValueError('Не удалось проверить подключение по HTTPS.') from None
    if not isinstance(response.get('system'),dict) or not isinstance(response.get('services'),list):
        raise ValueError('Сервер не вернул статус Pi Control.')
    cluster5.create({**node,'manual':not entry})
    return {'message':'TLS и вход проверены. Узел добавлен.'}
