import json,subprocess,re
from pathlib import Path

def run(args,timeout=8):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
  return p.stdout.strip() if p.returncode==0 else ''
 except (OSError,subprocess.TimeoutExpired): return ''

def interfaces():
 try: routes=json.loads(run(['ip','-j','-4','route','show','default']) or '[]')
 except ValueError: routes=[]
 try: addrs=json.loads(run(['ip','-j','-4','address','show']) or '[]')
 except ValueError: addrs=[]
 output=[]
 for x in addrs:
  name=x.get('ifname','');dev=Path('/sys/class/net')/name/'device'
  if name!='wlan0' and not (dev.exists() and '/usb' in str(dev.resolve())):continue
  defaults=[r for r in routes if r.get('dev')==name]
  rs=sorted(defaults,key=lambda r:r.get('metric',0));route=rs[0] if rs else {}
  output.append({'device':name,'kind':'Wi-Fi' if name=='wlan0' else 'USB-сетевой адаптер','addresses':[a.get('local','')+'/'+str(a.get('prefixlen','')) for a in x.get('addr_info',[]) if a.get('family')=='inet'],'gateway':route.get('gateway',''),'metric':route.get('metric'),'state':x.get('operstate','UNKNOWN'),'usb_path':str(dev.resolve()) if dev.exists() and name!='wlan0' else ''})
 primary=min(routes,key=lambda r:r.get('metric',0)).get('dev','') if routes else ''
 for x in output:x['primary']=x['device']==primary
 return {'interfaces':output,'primary':primary,'message':'Одинаковый шлюз у двух интерфейсов может означать общий канал и не подтверждает независимое резервирование.'}

def bluetooth_info(data):
 addr=str(data.get('address','')).upper()
 if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',addr): raise ValueError('Некорректный Bluetooth-адрес.')
 raw=run(['bluetoothctl','info',addr],10)
 if not raw:raise ValueError('Устройство отсутствует в кэше Bluetooth или недоступно.')
 values={};uuids=[]
 for line in raw.splitlines():
  m=re.match(r'^\s*(Name|Alias|Class|Icon|Paired|Bonded|Trusted|Connected|RSSI|ManufacturerData Key|UUID):\s*(.*)',line)
  if m:
   if m.group(1)=='UUID':uuids.append(m.group(2)[:200])
   else:values[m.group(1)]=m.group(2)[:200]
 return {'address':addr,'details':values,'services':uuids[:40],'message':'Данные BlueZ могут быть кэшированными. Запрос не выполняет сопряжение или проверку паролей.'}

import threading
route_lock=threading.Lock()
def prefer(data):
 target=str(data.get('device',''))
 if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}',target): raise ValueError('Некорректный интерфейс.')
 with route_lock:
  state=interfaces();choices=[x for x in state['interfaces'] if x['gateway']]
  if target not in [x['device'] for x in choices]: raise ValueError('Выберите подключённый интерфейс с IPv4-шлюзом.')
  if len(choices)<2: raise ValueError('Для переключения необходимы два активных подключения со шлюзом.')
  previous=[]
  def call(args):
   p=subprocess.run(args,capture_output=True,text=True,timeout=20)
   if p.returncode:raise ValueError('NetworkManager не смог изменить маршрут; настройки возвращаются обратно.')
   return p.stdout.strip()
  try:
   for x in choices:
    dev=x['device'];conn=call(['nmcli','-g','GENERAL.CON-UUID','device','show',dev])
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',conn):raise ValueError('Подключение не управляется NetworkManager.')
    old=call(['nmcli','-g','ipv4.route-metric','connection','show','uuid',conn])
    if not re.fullmatch(r'-?\d+',old):raise ValueError('Не удалось проверить текущую метрику.')
    previous.append((dev,conn,old))
   # Apply the preferred route first, then lower priority of alternatives.
   previous.sort(key=lambda item:item[0]!=target)
   for dev,conn,old in previous:
    call(['nmcli','connection','modify','uuid',conn,'ipv4.route-metric','50' if dev==target else '600'])
    call(['nmcli','device','reapply',dev])
   current=interfaces()
   if current['primary']!=target:raise ValueError('Выбранный интерфейс не стал основным. Настройки возвращаются обратно.')
   return {'message':'Основной маршрут переключён. Второй интерфейс остаётся подключённым; доступность интернета нужно проверить.','interfaces':current['interfaces']}
  except Exception:
   for dev,conn,old in previous:
    subprocess.run(['nmcli','connection','modify','uuid',conn,'ipv4.route-metric',old],capture_output=True,timeout=20)
    subprocess.run(['nmcli','device','reapply',dev],capture_output=True,timeout=20)
   raise


def bluetooth_record(data):
 import time,extras
 address=str(data.get('address','')).upper()
 if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',address):raise ValueError('Некорректный Bluetooth-адрес.')
 name=str(data.get('name','')).strip()[:120];note=str(data.get('note','')).strip()[:500]
 path=Path('/opt/pi-dashboard/bluetooth-inventory.json')
 try: rows=json.loads(path.read_text())
 except Exception:rows=[]
 old=next((x for x in rows if x['address']==address),None);now=int(time.time())
 if old:old.update(name=name,note=note,last_saved=now)
 else:rows.append({'address':address,'name':name,'note':note,'first_saved':now,'last_saved':now})
 extras.atomic(path,rows[:1000]);return {'message':'Устройство сохранено в учёт Bluetooth.'}
def bluetooth_inventory():
 p=Path('/opt/pi-dashboard/bluetooth-inventory.json')
 try:return {'devices':json.loads(p.read_text())}
 except Exception:return {'devices':[]}

def bluetooth_audit(d):
 import time,extras
 addr=str(d.get('address','')).upper();mode=str(d.get('mode','info'))
 if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',addr):raise ValueError('Некорректный Bluetooth-адрес.')
 if mode not in ('info','ping','services'):raise ValueError('Неизвестная проверка.')
 if mode!='info' and d.get('authorized') is not True:raise ValueError('Подтвердите разрешение владельца устройства.')
 info=bluetooth_info({'address':addr});result=''
 if mode in ('ping','services'):
  command=['l2ping','-c','1','-t','3',addr] if mode=='ping' else ['sdptool','browse',addr]
  try:
   p=subprocess.run(command,capture_output=True,text=True,timeout=15);result=(p.stdout+'\n'+p.stderr).strip()[:12000]
   if p.returncode:result='Команда завершилась с кодом '+str(p.returncode)+'. '+result
  except subprocess.TimeoutExpired:result='Проверка не ответила за 15 секунд.'
  except FileNotFoundError:raise ValueError('Необходимая утилита BlueZ не установлена.')
 else:result=json.dumps(info,ensure_ascii=False)
 file=Path('/opt/pi-dashboard/bluetooth-audit.json')
 try:history=json.loads(file.read_text())
 except Exception:history=[]
 history.insert(0,{'address':addr,'mode':mode,'time':int(time.time()),'result':result});extras.atomic(file,history[:300])
 return {'message':'Проверка завершена. Отсутствие ответа не подтверждает уязвимость или выключенное устройство.','result':result}
def bluetooth_history():
 try:return {'history':json.loads(Path('/opt/pi-dashboard/bluetooth-audit.json').read_text())}
 except Exception:return {'history':[]}
def bluetooth_action(d):
 addr=str(d.get('address','')).upper();action=str(d.get('action',''))
 if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',addr) or action not in ('connect','disconnect','trust','untrust'):raise ValueError('Недопустимое действие.')
 if d.get('authorized') is not True:raise ValueError('Подтвердите разрешение владельца устройства.')
 info=bluetooth_info({'address':addr})
 if action in ('connect','trust') and info['details'].get('Paired')!='yes':raise ValueError('Сначала выполните сопряжение с подтверждением на устройствах.')
 try:p=subprocess.run(['bluetoothctl','--timeout','12',action,addr],capture_output=True,text=True,timeout=16)
 except subprocess.TimeoutExpired:raise ValueError('Операция не завершилась вовремя.')
 out=(p.stdout+'\n'+p.stderr).strip()
 if p.returncode or re.search(r'Failed|not available|not connected|not supported',out,re.I):raise ValueError('Действие не выполнено: '+out[:250])
 return {'message':'Команда выполнена. Проверьте состояние устройства.','result':out[:1500]}
def bluetooth_forget(d):
 import extras
 addr=str(d.get('address','')).upper();p=Path('/opt/pi-dashboard/bluetooth-inventory.json')
 if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',addr):raise ValueError('Некорректный Bluetooth-адрес.')
 rows=bluetooth_inventory()['devices'];extras.atomic(p,[x for x in rows if x.get('address')!=addr]);return {'message':'Запись удалена из учёта панели.'}
