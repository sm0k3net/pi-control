"""Bounded, operator-initiated Wi-Fi audit jobs for authorized networks."""
import base64,csv,io,json,os,re,shutil,signal,subprocess,threading,time,uuid
from pathlib import Path
import wifi_adapters
ROOT=Path('/opt/pi-dashboard/wifi-audits')
lock=threading.RLock(); job=None
MAC=re.compile(r'^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$')

def _mac(v):
 v=str(v or '').upper().strip()
 if not MAC.fullmatch(v) or v in ('FF:FF:FF:FF:FF:FF','00:00:00:00:00:00') or int(v[:2],16)&1:raise ValueError('Укажите корректный индивидуальный MAC-адрес.')
 return v

def _current():
 if not job:raise ValueError('Сначала запустите Wi-Fi аудит.')
 return job

def _read_csv(path):
 try:
  text=path.read_text(encoding='utf-8',errors='replace')[:2_000_000]
 except (OSError,ValueError):return [],[]
 aps=[];stations=[];mode=''
 for row in csv.reader(io.StringIO(text)):
  row=[x.strip() for x in row]
  if not row or not row[0]:continue
  if row[0]=='BSSID':mode='ap';continue
  if row[0]=='Station MAC':mode='station';continue
  if not MAC.fullmatch(row[0].upper()):continue
  if mode=='ap' and len(row)>=14:
   aps.append({'bssid':row[0].upper(),'first':row[1],'last':row[2],'channel':row[3],'power':row[8],'beacons':row[9],'security':' / '.join(x for x in row[5:8] if x),'ssid':row[13][:32]})
  elif mode=='station' and len(row)>=6:
   stations.append({'station':row[0].upper(),'first':row[1],'last':row[2],'power':row[3],'packets':row[4],'bssid':row[5].upper()})
 return aps[:500],stations[:1000]

def status():
 with lock:
  if not job:return {'running':False,'session':None,'aps':[],'stations':[],'tools':tools()}
  j=job;running=j['process'].poll() is None;aps,stations=_read_csv(j['dir']/'audit-01.csv')
  cap=j['dir']/'audit-01.cap';hashes=j['dir']/'capture.22000'
  return {'running':running,'session':j['id'],'device':j['device'],'bssid':j['bssid'],'channel':j['channel'],'deadline':j['deadline'],'aps':aps,'stations':stations,'cap_bytes':cap.stat().st_size if cap.exists() else 0,'hash_bytes':hashes.stat().st_size if hashes.exists() else 0,'tools':tools()}

def tools():return {x:bool(shutil.which(x)) for x in ('airmon-ng','airodump-ng','aireplay-ng','aircrack-ng','hcxpcapngtool')}

def _stop_process(proc):
 if proc.poll() is None:
  try:os.killpg(proc.pid,signal.SIGINT);proc.wait(timeout=5)
  except (ProcessLookupError,subprocess.TimeoutExpired):
   if proc.poll() is None:os.killpg(proc.pid,signal.SIGTERM)

def stop():
 with lock:
  j=_current();_stop_process(j['process'])
  return {'message':'Захват остановлен.',**status()}

def start(d):
 global job
 if d.get('authorized') is not True:raise ValueError('Подтвердите разрешение владельца сети.')
 device=wifi_adapters.validate(str(d.get('device','')));kind=str(d.get('kind','survey'))
 if kind not in ('survey','capture'):raise ValueError('Неизвестный тип наблюдения.')
 try:duration=int(d.get('duration',60))
 except (TypeError,ValueError):raise ValueError('Некорректная длительность.')
 if not 15<=duration<=300:raise ValueError('Длительность: 15–300 секунд.')
 with lock:
  if job and job['process'].poll() is None:raise ValueError('Сначала остановите текущий захват.')
  adapter=next((x for x in wifi_adapters.adapters()['adapters'] if x['device']==device),None)
  if not adapter or adapter['mode']!='monitor':raise ValueError('Сначала переведите выбранный адаптер в режим мониторинга.')
  if adapter['primary']:raise ValueError('Нельзя использовать основной интернет-канал для захвата.')
  bssid='';channel=''
  if kind=='capture':
   bssid=_mac(d.get('bssid'));channel=str(d.get('channel','')).strip()
   if not re.fullmatch(r'\d{1,3}',channel) or not 1<=int(channel)<=196:raise ValueError('Укажите корректный канал.')
   previous=_current();aps,_=_read_csv(previous['dir']/'audit-01.csv')
   if not any(x['bssid']==bssid and x['channel']==channel for x in aps):raise ValueError('Сначала обнаружьте эту точку и канал при наблюдении.')
  if not shutil.which('airodump-ng') or not shutil.which('prlimit'):raise ValueError('Не установлены airodump-ng или prlimit.')
  ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
  if len([p for p in ROOT.iterdir() if p.is_dir()])>=20:raise ValueError('Достигнут предел 20 сохранённых аудитов; освободите место вручную.')
  ident=uuid.uuid4().hex;folder=ROOT/ident;folder.mkdir(mode=0o700)
  args=['prlimit','--fsize=67108864','--','airodump-ng','--write',str(folder/'audit'),'--write-interval','2','--output-format','csv' if kind=='survey' else 'pcap,csv']
  if bssid:args+=['--bssid',bssid,'--channel',channel]
  args.append(device)
  try:proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,umask=0o077)
  except OSError:folder.rmdir();raise ValueError('Не удалось запустить airodump-ng.')
  job={'id':ident,'dir':folder,'device':device,'process':proc,'bssid':bssid,'channel':channel,'deadline':time.time()+duration,'last_deauth':0}
  timer=threading.Timer(duration,_expire,args=(ident,));timer.daemon=True;timer.start()
  return {'message':'Наблюдение запущено; оно завершится автоматически.','session':ident}

def _expire(ident):
 with lock:
  if job and job['id']==ident:_stop_process(job['process'])

def deauth(d):
 if d.get('authorized') is not True:raise ValueError('Подтвердите разрешение и воздействие на клиента.')
 bssid=_mac(d.get('bssid'));station=_mac(d.get('station'))
 try:count=int(d.get('count',1))
 except (TypeError,ValueError):raise ValueError('Некорректное количество.')
 if not 1<=count<=3:raise ValueError('Допустимо от 1 до 3 запросов.')
 with lock:
  j=_current();_,stations=_read_csv(j['dir']/'audit-01.csv')
  if j['process'].poll() is not None or not j['bssid'] or j['bssid']!=bssid:raise ValueError('Нужен активный захват выбранной сети.')
  if not any(x['station']==station and x['bssid']==bssid for x in stations):raise ValueError('Клиент не обнаружен в выбранной сети.')
  if time.monotonic()-j['last_deauth']<30:raise ValueError('Повтор возможен через 30 секунд.')
  j['last_deauth']=time.monotonic()
  try:p=subprocess.run(['aireplay-ng','--deauth',str(count),'-a',bssid,'-c',station,j['device']],capture_output=True,text=True,timeout=25)
  except (OSError,subprocess.TimeoutExpired):raise ValueError('Не удалось выполнить ограниченный запрос.')
  if p.returncode:raise ValueError('Адаптер или драйвер отклонил запрос; проверьте канал и поддержку injection.')
  return {'message':'Команда завершена. Получение handshake или отключение клиента не гарантировано.'}

def convert():
 with lock:
  j=_current()
  if j['process'].poll() is None:raise ValueError('Сначала остановите захват.')
  cap=j['dir']/'audit-01.cap';output=j['dir']/'capture.22000'
  if not cap.is_file():raise ValueError('Файл захвата отсутствует.')
  if not shutil.which('hcxpcapngtool'):raise ValueError('Конвертер hcxpcapngtool не установлен.')
  if output.exists():raise ValueError('Конвертация этой сессии уже выполнялась.')
  try:p=subprocess.run(['hcxpcapngtool','-o',str(output),str(cap)],capture_output=True,timeout=60)
  except (OSError,subprocess.TimeoutExpired):raise ValueError('Конвертация не завершена.')
  if p.returncode or not output.exists() or not output.stat().st_size:raise ValueError('Подходящих WPA-данных не найдено; наличие handshake не гарантировано.')
  return {'message':'Данные конвертированы в формат 22000. Храните файл как секрет.'}

def export(d):
 kind=str(d.get('kind',''))
 with lock:
  j=_current();aps,stations=_read_csv(j['dir']/'audit-01.csv')
  if kind in ('csv','txt'):
   out=io.StringIO(newline='');writer=csv.writer(out,lineterminator='\n')
   writer.writerow(['type','address','bssid','ssid','channel','signal','security','last_seen'])
   def safe(v):
    v=str(v or '');return "'"+v if v.startswith(('=','+','-','@','\t','\r')) else v
   for x in aps:writer.writerow([safe(v) for v in ('AP',x['bssid'],x['bssid'],x['ssid'],x['channel'],x['power'],x['security'],x['last'])])
   for x in stations:writer.writerow([safe(v) for v in ('CLIENT',x['station'],x['bssid'],'','',x['power'],'',x['last'])])
   data=out.getvalue() if kind=='csv' else '[Access points]\n'+'\n'.join(' | '.join(safe(v) for v in (x['ssid'] or '(hidden)',x['bssid'],x['channel'],x['power'],x['security'])) for x in aps)+'\n[Clients]\n'+'\n'.join(' | '.join(safe(v) for v in (x['station'],x['bssid'],x['power'])) for x in stations)+'\n'
   return {'name':'wifi-audit.'+kind,'data':base64.b64encode(data.encode()).decode()}
  if kind not in ('cap','22000'):raise ValueError('Неизвестный формат.')
  if j['process'].poll() is None:raise ValueError('Сначала остановите захват.')
  f=j['dir']/('audit-01.cap' if kind=='cap' else 'capture.22000')
  if not f.is_file() or f.is_symlink() or f.stat().st_size>8*1024*1024:raise ValueError('Файл отсутствует или превышает 8 МиБ.')
  return {'name':'wifi-audit.'+kind,'data':base64.b64encode(f.read_bytes()).decode()}
