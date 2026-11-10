import base64,binascii,json,os,secrets,tempfile,threading,time,shutil
from pathlib import Path
from pykeepass import PyKeePass,create_database
from pykeepass.exceptions import CredentialsError
ROOT=Path('/opt/pi-dashboard');FILE=ROOT/'vault.kdbx';guard=threading.RLock();failed=[]
def check_password(value):
 if not isinstance(value,str) or not 12<=len(value)<=256:raise ValueError('Мастер-пароль должен содержать от 12 до 256 символов.')
 return value
def opened(password):
 now=time.monotonic();failed[:]=[t for t in failed if now-t<60]
 if len(failed)>=6:raise ValueError('Слишком много попыток. Повторите через минуту.')
 if not FILE.exists():raise ValueError('Сначала создайте или импортируйте хранилище.')
 try:k=PyKeePass(str(FILE),password=str(password))
 except CredentialsError:failed.append(now);raise ValueError('Неверный мастер-пароль или файл повреждён.')
 except Exception:raise ValueError('Не удалось открыть KDBX. Проверьте файл и способ защиты.')
 failed.clear();return k
def save(k):
 tmp=ROOT/('.vault-'+secrets.token_hex(8)+'.kdbx')
 try:
  k.save(str(tmp));os.chmod(tmp,0o600);os.replace(tmp,FILE)
 finally:
  try:tmp.unlink()
  except FileNotFoundError:pass
def info():return {'exists':FILE.exists()}
def create(d):
 password=check_password(d.get('master'))
 with guard:
  if FILE.exists():raise ValueError('Хранилище уже существует.')
  tmp=ROOT/('.vault-'+secrets.token_hex(8)+'.kdbx')
  try:
   k=create_database(str(tmp),password=password);k.save(str(tmp));os.chmod(tmp,0o600);os.replace(tmp,FILE)
  finally:
   try:tmp.unlink()
   except FileNotFoundError:pass
 return {'message':'Хранилище создано.'}
def entries(k):return [{'id':str(e.uuid),'title':e.title or '', 'username':e.username or '', 'url':e.url or '', 'group':' / '.join(e.path[:-1]) if e.path else ''} for e in k.entries]
def get_entry(k,id):
 e=next((e for e in k.entries if str(e.uuid)==id),None)
 if e is None:raise ValueError('Запись не найдена.')
 return e
def dispatch(op,d):
 password=str(d.get('master',''))
 with guard:
  k=opened(password)
  if op=='list':return {'entries':entries(k)}
  if op=='reveal':
   e=get_entry(k,str(d.get('id','')));return {'title':e.title or '', 'username':e.username or '', 'password':e.password or '', 'url':e.url or '', 'notes':e.notes or ''}
  if op=='save':
   title=str(d.get('title','')).strip();username=str(d.get('username',''));secret=str(d.get('password',''));url=str(d.get('url',''));notes=str(d.get('notes',''));id=str(d.get('id',''))
   if not title or len(title)>200 or any(len(v)>12000 for v in (username,secret,url,notes)):raise ValueError('Проверьте название и размеры полей.')
   if id:
    e=get_entry(k,id);e.title=title;e.username=username;e.password=secret;e.url=url;e.notes=notes
   else:k.add_entry(k.root_group,title,username,secret,url=url,notes=notes)
   save(k);return {'message':'Запись сохранена.'}
  if op=='delete':
   e=get_entry(k,str(d.get('id','')));k.delete_entry(e);save(k);return {'message':'Запись удалена.'}
  if op=='import-services':
   source=Path('/root/pi-passwords.txt')
   if not source.exists():raise ValueError('Файл паролей не найден.')
   group=k.find_groups(name='Pi services',first=True) or k.add_group(k.root_group,'Pi services');count=0
   for line in source.read_text().splitlines():
    if not line or line.startswith('#') or '=' not in line:continue
    name,value=line.split('=',1)
    if not value:continue
    existing=k.find_entries(group=group,title=name,first=True)
    if existing:existing.password=value
    else:k.add_entry(group,name,'',value)
    count+=1
   save(k);return {'message':f'Перенесено записей: {count}. Исходный файл сохранён.'}
  if op=='export':return {'data':base64.b64encode(FILE.read_bytes()).decode()}
  if op=='import':
   raw=str(d.get('data',''));inpw=str(d.get('import_master',''))
   if len(raw)>2500000:raise ValueError('Файл больше допустимого размера (около 1.8 МБ).')
   try:blob=base64.b64decode(raw,validate=True)
   except (ValueError,binascii.Error):raise ValueError('Некорректный файл.')
   tmp=ROOT/('.import-'+secrets.token_hex(8)+'.kdbx')
   try:
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(blob)
    imported=PyKeePass(str(tmp),password=inpw);group=k.add_group(k.root_group,'Import '+time.strftime('%Y-%m-%d %H:%M:%S'));count=0
    for e in imported.entries:
     k.add_entry(group,e.title or '(без названия)',e.username or '',e.password or '',url=e.url or '',notes=e.notes or '');count+=1
    save(k);return {'message':f'Импортировано записей: {count}. Основные поля скопированы в отдельную группу.'}
   except CredentialsError:raise ValueError('Неверный пароль импортируемой базы.')
   except ValueError:raise
   except Exception:raise ValueError('Не удалось импортировать базу. Возможно, нужен файл-ключ или другая версия KDBX.')
   finally:
    try:tmp.unlink()
    except FileNotFoundError:pass
  raise ValueError('Неизвестная операция.')
