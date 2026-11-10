import base64,hashlib,hmac,io,re,stat,shlex
import paramiko,ssh_nodes7,vault

def open_node(d):
 n=next((n for n in ssh_nodes7.list_nodes() if n['id']==str(d.get('id',''))),None)
 if not n:raise ValueError('Узел не найден.')
 secret=str(d.get('secret','')) or (vault.dispatch('reveal',{'master':str(d.get('master','')),'id':n['entry']}).get('password','') if n.get('entry') else '')
 if not secret:raise ValueError('Введите SSH-секрет для узла или откройте KDBX.')
 class Pin(paramiko.MissingHostKeyPolicy):
  def missing_host_key(self,client,hostname,key):
   actual='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
   if not hmac.compare_digest(actual,n['fingerprint']):raise paramiko.SSHException('Host key mismatch')
 opts={}
 if n.get('auth','password')=='password':opts['password']=secret
 else:
  for cls in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
   try:opts['pkey']=cls.from_private_key(io.StringIO(secret));break
   except (paramiko.SSHException,ValueError):continue
  if 'pkey' not in opts:raise ValueError('Не удалось прочитать SSH-ключ.')
 client=paramiko.SSHClient();client.set_missing_host_key_policy(Pin())
 try:client.connect(n['host'],port=n['port'],username=n['username'],look_for_keys=False,allow_agent=False,timeout=8,banner_timeout=8,auth_timeout=8,**opts)
 except Exception:client.close();raise ValueError('SSH недоступен, пароль неверный или отпечаток ключа изменился.')
 return n,client

def containers(d):
 n,c=open_node(d)
 try:
  command="if docker info >/dev/null 2>&1; then docker ps -a --format '{{.Names}}\\t{{.State}}\\t{{.Image}}'; elif sudo -n true 2>/dev/null; then sudo -n docker ps -a --format '{{.Names}}\\t{{.State}}\\t{{.Image}}'; else exit 1; fi"
  _,stdout,_=c.exec_command(command,timeout=20)
  rows=stdout.read(64000).decode('utf-8','replace').splitlines();status=stdout.channel.recv_exit_status()
  if status:raise ValueError('Docker недоступен пользователю SSH. Проверьте установку и права.')
  out=[]
  for row in rows[:150]:
   p=row.split('\t')
   if len(p)==3:out.append({'name':p[0],'state':p[1],'image':p[2]})
  return {'containers':out}
 finally:c.close()
def container_action(d):
 name=str(d.get('container',''));action=str(d.get('action',''))
 if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',name) or action not in ('start','stop','restart'):raise ValueError('Недопустимое действие.')
 n,c=open_node(d)
 try:
  command='if docker info >/dev/null 2>&1; then exec docker '+action+' '+shlex.quote(name)+'; elif sudo -n true 2>/dev/null; then exec sudo -n docker '+action+' '+shlex.quote(name)+'; else exit 1; fi'
  _,out,err=c.exec_command(command,timeout=45);status=out.channel.recv_exit_status()
  if status:raise ValueError('Docker отклонил команду: '+err.read(512).decode('utf-8','replace'))
  return {'message':'Команда Docker выполнена.'}
 finally:c.close()
def list_files(d):
 n,c=open_node(d);sftp=None
 try:
  sftp=c.open_sftp();base=sftp.normalize('.');rel=str(d.get('path','')).strip('/')
  if '\x00' in rel or any(p in ('.','..') for p in rel.split('/') if p):raise ValueError('Недопустимый путь.')
  path=base
  for part in filter(None,rel.split('/')):
   path=path.rstrip('/')+'/'+part
   if stat.S_ISLNK(sftp.lstat(path).st_mode):raise ValueError('Символические ссылки недоступны.')
  rows=[]
  for x in sftp.listdir_attr(path)[:2000]:
   if x.filename in ('.','..'):continue
   rows.append({'name':x.filename,'directory':stat.S_ISDIR(x.st_mode),'symlink':stat.S_ISLNK(x.st_mode),'size':x.st_size,'modified':x.st_mtime})
  rows.sort(key=lambda x:(not x['directory'],x['name'].casefold()))
  return {'items':rows,'path':rel,'base':base}
 except ValueError:raise
 except Exception:raise ValueError('Не удалось открыть каталог по SFTP.')
 finally:
  if sftp:sftp.close()
  c.close()

def download(d):
 n,c=open_node(d);sftp=None
 try:
  sftp=c.open_sftp();base=sftp.normalize('.');rel=str(d.get('path','')).strip('/')
  parts=rel.split('/')
  if not rel or '\x00' in rel or any(x in ('','.','..') for x in parts):raise ValueError('Недопустимый путь.')
  path=base
  for part in parts:
   path=path.rstrip('/')+'/'+part;attrs=sftp.lstat(path)
   if stat.S_ISLNK(attrs.st_mode):raise ValueError('Символические ссылки недоступны.')
  if not stat.S_ISREG(attrs.st_mode):raise ValueError('Выберите файл.')
  if attrs.st_size>8*1024*1024:raise ValueError('Пока доступны файлы до 8 МиБ; для больших используйте SFTP-клиент.')
  with sftp.open(path,'rb') as f:data=f.read(8*1024*1024+1)
  if len(data)>8*1024*1024:raise ValueError('Файл превысил лимит 8 МиБ.')
  return {'name':parts[-1],'data':base64.b64encode(data).decode('ascii')}
 except ValueError:raise
 except Exception:raise ValueError('Не удалось скачать файл по SFTP.')
 finally:
  if sftp:sftp.close()
  c.close()

def upload(d):
 import binascii
 n,c=open_node(d);sftp=None;created=False;target=''
 try:
  sftp=c.open_sftp();base=sftp.normalize('.');rel=str(d.get('path','')).strip('/');name=str(d.get('name',''))
  if not name or name in ('.','..') or '/' in name or '\\' in name or '\x00' in name or len(name.encode())>240:raise ValueError('Недопустимое имя файла.')
  if '\x00' in rel or any(x in ('.','..') for x in rel.split('/') if x):raise ValueError('Недопустимый путь.')
  path=base
  for part in filter(None,rel.split('/')):
   path=path.rstrip('/')+'/'+part;attrs=sftp.lstat(path)
   if stat.S_ISLNK(attrs.st_mode) or not stat.S_ISDIR(attrs.st_mode):raise ValueError('Недоступный каталог.')
  raw=str(d.get('data',''))
  if len(raw)>1400000:raise ValueError('Пока поддерживаются файлы до 1 МиБ.')
  try:data=base64.b64decode(raw,validate=True)
  except (ValueError,binascii.Error):raise ValueError('Некорректные данные файла.')
  if len(data)>1048576:raise ValueError('Пока поддерживаются файлы до 1 МиБ.')
  target=path.rstrip('/')+'/'+name
  with sftp.open(target,'wx') as f:
   created=True;f.write(data)
  return {'message':'Файл загружен на удалённый узел.'}
 except ValueError:raise
 except Exception:
  if sftp and created:
   try:sftp.remove(target)
   except Exception:pass
  raise ValueError('Не удалось загрузить файл; возможно, файл уже существует.')
 finally:
  if sftp:sftp.close()
  c.close()
