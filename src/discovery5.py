import subprocess,re,threading,time
from pathlib import Path
wifi_lock=threading.Lock();bt_lock=threading.Lock()
def run(args,timeout=15):
 try:p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 except subprocess.TimeoutExpired:raise ValueError('Поиск занял слишком много времени.')
 except FileNotFoundError:raise ValueError('Необходимая системная утилита не установлена.')
 if p.returncode:raise ValueError('Не удалось выполнить поиск. Проверьте состояние адаптера.')
 return p.stdout
def fields(row):
 out=[];part='';escaped=False
 for c in row:
  if escaped:part+=c;escaped=False
  elif c=='\\':escaped=True
  elif c==':':out.append(part);part=''
  else:part+=c
 out.append(part);return out
def wifi_scan(d):
 target=str(d.get('hidden_ssid','')).strip()
 if target and (len(target.encode())>32 or any(ord(c)<32 for c in target)):raise ValueError('Укажите корректный SSID скрытой сети.')
 with wifi_lock:
  args=['nmcli','device','wifi','rescan','ifname','wlan0']
  if target:args+=['ssid',target]
  rescan=True
  try:run(args,timeout=15)
  except ValueError:rescan=False
  # Rescan is asynchronous; request NetworkManager's current cache after a short bounded wait.
  time.sleep(3)
  result=run(['nmcli','-t','--escape','yes','-f','IN-USE,SSID,BSSID,CHAN,SIGNAL,SECURITY','device','wifi','list','ifname','wlan0','--rescan','no'],timeout=20)
  rows=[]
  for ln in result.splitlines():
   cols=fields(ln)
   if len(cols)!=6:continue
   active,ssid,bssid,channel,signal,security=cols
   try:signal=max(0,min(100,int(signal)))
   except ValueError:signal=0
   rows.append({'ssid':'' if ssid in ('','--') else ssid,'bssid':bssid,'channel':channel,'signal':signal,'security':security or 'Открытая','active':active=='*','hidden':ssid in ('','--')})
  return {'networks':sorted(rows,key=lambda x:(not x['active'],-x['signal'])),'directed':bool(target),'rescan':rescan,'message':('Поиск завершён.' if rescan else 'Новый поиск недоступен; показаны кэшированные результаты.')+' Скрытые имена не определяются, если точка их не раскрывает.'}
def bluetooth_scan():
 with bt_lock:
  info=run(['bluetoothctl','show'],10)
  if 'Powered: yes' not in info:raise ValueError('Bluetooth-адаптер выключен или недоступен.')
  try:scan=subprocess.run(['bluetoothctl','--timeout','8','scan','on'],capture_output=True,text=True,timeout=13)
  except subprocess.TimeoutExpired:raise ValueError('Не удалось завершить поиск Bluetooth.')
  # An independent command guarantees discovery is not left active after timeout.
  subprocess.run(['bluetoothctl','scan','off'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
  runout=run(['bluetoothctl','devices'],8);seen=set(re.findall(r'(?:NEW|CHG)\] Device ([0-9A-Fa-f:]{17})',scan.stdout));items=[]
  for line in runout.splitlines():
   m=re.match(r'Device ([0-9A-Fa-f:]{17})\s*(.*)',line)
   if m:items.append({'address':m.group(1),'name':m.group(2).strip() or 'Без имени','seen':m.group(1) in seen})
  return {'devices':items[:150],'message':'Устройства из текущего поиска и кэша Bluetooth. Запись в кэше не гарантирует, что устройство сейчас рядом.'}
