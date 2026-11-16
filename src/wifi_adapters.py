"""Wi-Fi interface discovery and explicit monitor-mode control."""
import json,re,subprocess,threading
from pathlib import Path
lock=threading.Lock()
def run(args,timeout=15,check=True):
 try:p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 except (OSError,subprocess.TimeoutExpired):raise ValueError('Системная утилита недоступна или не ответила.')
 if check and p.returncode:raise ValueError('Не удалось изменить состояние адаптера. Проверьте драйвер и NetworkManager.')
 return p.stdout.strip()
def validate(name):
 if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}',str(name)):raise ValueError('Некорректное имя интерфейса.')
 if not (Path('/sys/class/net')/name/'phy80211').exists():raise ValueError('Wi-Fi адаптер не найден.')
 return name
def adapters():
 try:routes=json.loads(run(['ip','-j','-4','route','show','default']) or '[]')
 except (ValueError,json.JSONDecodeError):routes=[]
 primary=min(routes,key=lambda x:x.get('metric',0)).get('dev','') if routes else ''
 result=[]
 for entry in sorted(Path('/sys/class/net').iterdir()):
  name=entry.name
  if not (entry/'phy80211').exists():continue
  try:
   info=run(['iw','dev',name,'info']); match=re.search(r'(?m)^\s*wiphy\s+(\d+)',info);phy='phy'+match.group(1) if match else ''
   kind=re.search(r'(?m)^\s*type\s+(\S+)',info);mode=kind.group(1) if kind else 'unknown'
   detail=run(['iw','phy',phy,'info'],timeout=10) if phy else ''
   section=detail.split('Supported interface modes:',1)[1].split('Band 1:',1)[0] if 'Supported interface modes:' in detail else ''
   driver=(entry/'device'/'driver').resolve().name if (entry/'device'/'driver').exists() else ''
   usb='/usb' in str((entry/'device').resolve()) if (entry/'device').exists() else False
   result.append({'device':name,'phy':phy,'driver':driver,'mode':mode,'monitor_supported':bool(re.search(r'(?m)^\s*\*\s*monitor\s*$',section)),'primary':name==primary,'usb':usb})
  except ValueError:continue
 return {'adapters':result,'tools':{'airmon':bool(__import__('shutil').which('airmon-ng')),'airodump':bool(__import__('shutil').which('airodump-ng'))}}
def set_mode(d):
 name=validate(str(d.get('device','')));wanted=str(d.get('mode',''))
 if wanted not in ('monitor','managed'):raise ValueError('Неизвестный режим адаптера.')
 with lock:
  data=adapters();a=next((x for x in data['adapters'] if x['device']==name),None)
  if not a:raise ValueError('Wi-Fi адаптер недоступен.')
  if a['mode']==wanted:return {'message':'Режим уже установлен.'}
  if wanted=='monitor' and a['primary']:raise ValueError('Нельзя перевести основной интернет-канал в мониторинг. Сначала выберите другой основной маршрут.')
  if wanted=='monitor' and not a['monitor_supported']:raise ValueError('Драйвер не сообщает поддержку monitor mode.')
  if wanted=='monitor' and d.get('confirmed') is not True:raise ValueError('Подтвердите отключение адаптера от текущей Wi-Fi сети.')
  try:
   run(['nmcli','device','set',name,'managed','no'])
   run(['ip','link','set','dev',name,'down'])
   run(['iw','dev',name,'set','type',wanted])
   run(['ip','link','set','dev',name,'up'])
   if wanted=='managed':run(['nmcli','device','set',name,'managed','yes'])
  except Exception:
   if wanted=='monitor':
    for cmd in (['ip','link','set','dev',name,'down'],['iw','dev',name,'set','type','managed'],['ip','link','set','dev',name,'up'],['nmcli','device','set',name,'managed','yes']):
     try:run(cmd,check=False)
     except ValueError:pass
   raise
  return {'message':'Режим изменён. '+('Подключение этого адаптера к Wi-Fi отключено.' if wanted=='monitor' else 'NetworkManager снова управляет адаптером; подключение может потребовать выбора сети.')}
