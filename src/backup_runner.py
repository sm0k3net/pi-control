import os,json,time,subprocess,hashlib
from pathlib import Path
import features,extras,server
ROOT=Path('/opt/pi-dashboard');last={'time':int(time.time()),'ok':False,'message':'Не удалось выполнить резервное копирование.'}
try:
 d=json.loads((ROOT/'backup-settings.json').read_text());path=features.backup_validate(d['path']);recipient=Path('/root/pi-backup.pub').read_text().strip()
 before=set(Path(path).glob('pi-backup-*.tar.gz.age'))
 p=subprocess.run(['/usr/local/bin/pi-backup',path,recipient],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=21600)
 if p.returncode:raise RuntimeError('Ошибка создания архива. Проверьте носитель и сервис PostgreSQL.')
 files=set(Path(path).glob('pi-backup-*.tar.gz.age'))-before
 if len(files)!=1:raise RuntimeError('Не удалось определить созданный архив.')
 f=next(iter(files));hashlocal=hashlib.md5()
 with f.open('rb') as source:
  for chunk in iter(lambda:source.read(1024*1024),b''):hashlocal.update(chunk)
 if d.get('remote'):
  target=d['remote'].rstrip('/')+'/'+f.name
  p=subprocess.run(['rclone','copyto','--checksum',str(f),target],capture_output=True,text=True,timeout=21600)
  if p.returncode:raise RuntimeError('Локальная копия создана, но выгрузить её на Google Drive не удалось.')
  p=subprocess.run(['rclone','md5sum',target],capture_output=True,text=True,timeout=300)
  if p.returncode or not p.stdout.split() or p.stdout.split()[0].lower()!=hashlocal.hexdigest():raise RuntimeError('Локальная копия создана, но контрольная сумма Google Drive не совпала или недоступна.')
 last={'time':int(time.time()),'ok':True,'message':'Зашифрованный архив создан'+(' и проверен на Google Drive.' if d.get('remote') else ' на внешнем носителе.'),'filename':f.name,'bytes':f.stat().st_size,'md5':hashlocal.hexdigest()}
except Exception as e:last['message']=str(e)[:300] or last['message']
extras.atomic(ROOT/'backup-last.json',last)
try:
 if server.read_telegram().get('enabled'):server.send_telegram('🍓 sm0k3-pi · резервное копирование\n'+last['message'])
except Exception:pass
if not last['ok']:raise SystemExit(1)
