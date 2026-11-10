import os,pty,fcntl,select,subprocess,threading,time,secrets,signal,re,termios,struct,codecs
lock=threading.Lock();sessions={};LIMIT=3;IDLE=900

def _cleanup():
 now=time.monotonic()
 for ident,s in list(sessions.items()):
  if 'channel' in s:
   if s['channel'].closed or now-s['last']>IDLE:
    s['channel'].close();s['client'].close();sessions.pop(ident,None)
   continue
  if s['proc'].poll() is not None or now-s['last']>IDLE:
   try:os.killpg(s['proc'].pid,signal.SIGTERM)
   except (ProcessLookupError,PermissionError):pass
   try:os.close(s['fd'])
   except OSError:pass
   sessions.pop(ident,None)
def start():
 with lock:
  _cleanup()
  if len(sessions)>=LIMIT:raise ValueError('Достигнут лимит терминальных сессий.')
  master,slave=pty.openpty()
  try:
   fcntl.fcntl(master,fcntl.F_SETFL,fcntl.fcntl(master,fcntl.F_GETFL)|os.O_NONBLOCK)
   proc=subprocess.Popen(['/usr/bin/setsid','--ctty','/usr/sbin/runuser','-u','sm0k3','--','/bin/bash','-i'],stdin=slave,stdout=slave,stderr=slave,env={'TERM':'xterm-256color','PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/home/sm0k3','USER':'sm0k3'},close_fds=True)
  except Exception:os.close(master);raise
  finally:os.close(slave)
  ident=secrets.token_urlsafe(24);sessions[ident]={'fd':master,'proc':proc,'last':time.monotonic(),'decoder':codecs.getincrementaldecoder('utf-8')('replace')};return {'id':ident,'message':'Терминал пользователя sm0k3 запущен.'}
def session(d,op):
 ident=str(d.get('id',''))
 if not re.fullmatch(r'[A-Za-z0-9_-]{20,60}',ident):raise ValueError('Некорректная сессия.')
 with lock:
  s=sessions.get(ident)
  if not s:raise ValueError('Сессия завершена. Создайте новую.')
  if op=='resize':
   try:cols=int(d.get('cols'));rows=int(d.get('rows'))
   except (TypeError,ValueError):raise ValueError('Некорректный размер терминала.')
   if not 20<=cols<=500 or not 5<=rows<=200:raise ValueError('Некорректный размер терминала.')
   if 'channel' in s:s['channel'].resize_pty(width=cols,height=rows)
   else:fcntl.ioctl(s['fd'],termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
   return {'ok':True}
  if 'channel' in s:
   ch=s['channel']
   if time.monotonic()-s['last']>IDLE:
    _cleanup();raise ValueError('Сессия завершена после бездействия.')
   if op=='write':
    data=d.get('data','')
    if not isinstance(data,str) or len(data.encode())>8192:raise ValueError('Слишком длинный ввод.')
    s['last']=time.monotonic()
    try:ch.sendall(data.encode())
    except Exception:raise ValueError('SSH-сессия завершилась.')
    return {'ok':True}
   if op=='stop':
    ch.close();s['client'].close();sessions.pop(ident,None);return {'message':'Сессия завершена.'}
   if op=='read':
    data=ch.recv(65536) if ch.recv_ready() else b''
    return {'data':s['decoder'].decode(data),'running':not ch.closed}
   raise ValueError('Неизвестная операция.')
  proc=s['proc']
  if time.monotonic()-s['last']>IDLE:
   _cleanup();raise ValueError('Сессия завершена после бездействия.')
  if op=='write':
   s['last']=time.monotonic()
   data=d.get('data','')
   if not isinstance(data,str) or len(data.encode())>8192:raise ValueError('Слишком длинный ввод.')
   try:os.write(s['fd'],data.encode())
   except OSError:raise ValueError('Терминал завершился.')
   return {'ok':True}
  if op=='stop':
   try:os.killpg(proc.pid,signal.SIGTERM)
   except (ProcessLookupError,PermissionError):pass
   try:os.close(s['fd'])
   except OSError:pass
   sessions.pop(ident,None);return {'message':'Сессия завершена.'}
  if op=='read':
   chunks=[];size=0
   while size<65536:
    try:
     if not select.select([s['fd']],[],[],0)[0]:break
     b=os.read(s['fd'],min(8192,65536-size))
     if not b:break
     chunks.append(b);size+=len(b)
    except (BlockingIOError,OSError):break
   return {'data':s['decoder'].decode(b''.join(chunks)),'running':proc.poll() is None}
 raise ValueError('Неизвестная операция.')
def reap():
 while True:
  time.sleep(60)
  with lock:_cleanup()

def remote_start(d):
 import base64,hashlib,hmac,io,paramiko,ssh_nodes7,vault
 node=next((x for x in ssh_nodes7.list_nodes() if x['id']==str(d.get('node_id',''))),None)
 if not node:raise ValueError('SSH-узел не найден.')
 secret=str(d.get('secret','')) or (vault.dispatch('reveal',{'master':str(d.get('master','')),'id':node['entry']}).get('password','') if node.get('entry') else '')
 if not secret:raise ValueError('Введите SSH-секрет для узла или откройте KDBX.')
 class Pin(paramiko.MissingHostKeyPolicy):
  def missing_host_key(self,client,hostname,key):
   value='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
   if not hmac.compare_digest(value,node['fingerprint']):raise paramiko.SSHException('Host key mismatch')
 options={}
 if node.get('auth','password')=='password':options['password']=secret
 else:
  for cls in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
   try:options['pkey']=cls.from_private_key(io.StringIO(secret));break
   except (paramiko.SSHException,ValueError):continue
  if 'pkey' not in options:raise ValueError('Не удалось открыть приватный ключ.')
 with lock:
  _cleanup()
  if len(sessions)>=LIMIT:raise ValueError('Достигнут лимит терминальных сессий.')
  client=paramiko.SSHClient();client.set_missing_host_key_policy(Pin())
  try:
   client.connect(node['host'],port=node['port'],username=node['username'],look_for_keys=False,allow_agent=False,timeout=8,banner_timeout=8,auth_timeout=8,**options)
   channel=client.invoke_shell(term='xterm-256color',width=100,height=30);channel.setblocking(0)
  except Exception:
   client.close();raise ValueError('Не удалось подключиться к SSH-узлу или проверить его ключ.')
  ident=secrets.token_urlsafe(24);sessions[ident]={'client':client,'channel':channel,'last':time.monotonic(),'decoder':codecs.getincrementaldecoder('utf-8')('replace')};return {'id':ident,'message':'SSH-терминал подключён: '+node['name']}
