import json,time,threading,subprocess,ipaddress
from pathlib import Path
import extras,discovery5
ROOT=Path('/opt/pi-dashboard');DB=ROOT/'wifi-observations.json';DISK_FILE=ROOT/'disk-usage.json';lock=threading.Lock();disk_lock=threading.Lock();disk_result={'running':False,'rows':[],'error':'','time':0}
try:
 cached=json.loads(DISK_FILE.read_text())
 if isinstance(cached,dict) and isinstance(cached.get('rows'),list):disk_result.update(rows=cached['rows'],time=cached.get('time',0),error=cached.get('error',''))
except (OSError,ValueError):pass
def run(args,timeout=10):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
  return p.stdout.strip() if p.returncode==0 else ''
 except (OSError,subprocess.TimeoutExpired):return ''
def read():
 try:return json.loads(DB.read_text())
 except Exception:return {'profiles':{},'events':[],'current':''}
def wifi_sample(check_external=False):
 uid=run(['nmcli','-g','GENERAL.CON-UUID','device','show','wlan0']);state=run(['nmcli','-g','GENERAL.STATE','device','show','wlan0']);ipv4=run(['nmcli','-g','IP4.ADDRESS','device','show','wlan0']).splitlines();ip=ipv4[0].split('/')[0] if ipv4 else ''
 if not uid or uid=='--':uid=''
 signal='';raw=run(['nmcli','-t','--escape','yes','-f','IN-USE,SIGNAL','device','wifi','list','ifname','wlan0','--rescan','no'])
 for line in raw.splitlines():
  fields=discovery5.fields(line)
  if len(fields)==2 and fields[0]=='*':signal=fields[1];break
 with lock:
  d=read();old=d.get('current','');events=d.setdefault('events',[]);p=d.setdefault('profiles',{})
  if old!=uid:
   events.insert(0,{'time':int(time.time()),'kind':'connected' if uid else 'disconnected','uuid':uid or old,'message':'Подключение изменилось' if uid else 'Wi-Fi отключился'});d['current']=uid
  rec=p.setdefault(uid,{}) if uid else {}
  if uid:
   if ip:rec['internal_ip']=ip;rec['last_seen']=int(time.time())
   rec['signal']=signal;rec['state']=state
  need=bool(uid and ip and (check_external or not rec.get('external_ip')))
  d['events']=events[:200];extras.atomic(DB,d)
 if need:
  out=run(['curl','-4','--interface','wlan0','--connect-timeout','4','--max-time','7','-fsS','https://ifconfig.io/ip'],9)
  try:out=str(ipaddress.ip_address(out))
  except ValueError:out=''
  if out:
   with lock:
    d=read();rec=d.setdefault('profiles',{}).setdefault(uid,{});rec['external_ip']=out;rec['external_checked']=int(time.time());extras.atomic(DB,d)
 return {'active_uuid':uid,'state':state,'internal_ip':ip,'signal':signal}
def wifi_info():
 x=wifi_sample();d=read();return {**x,'observations':d.get('profiles',{}),'events':d.get('events',[])[:100]}
def wifi_background():
 while True:
  try:wifi_sample()
  except Exception:pass
  time.sleep(60)
def wifi_journal():
 text=run(['journalctl','-u','NetworkManager','--since','-2 hours','--no-pager','-n','400','-o','short-iso'],8)
 lines=[l[:350] for l in text.splitlines() if ('wlan0' in l.lower() or 'wifi' in l.lower() or 'wi-fi' in l.lower()) and not any(w in l.lower() for w in ('password','psk=','secret','private key'))]
 return {'lines':lines[-80:]}
def disk_start():
 with disk_lock:
  if disk_result['running']:return dict(disk_result)
  disk_result.update(running=True,error='')
 def work():
  try:
   # -x keeps scans on the root filesystem; -B1 produces bytes and no traversal of mounted media.
   p=subprocess.run(['du','-x','-B1','--max-depth=1','/'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=180)
   rows=[]
   for line in p.stdout.splitlines():
    parts=line.split('\t',1)
    if len(parts)==2 and parts[0].isdigit() and parts[1]!='/':rows.append({'path':parts[1],'bytes':int(parts[0])})
   if not rows:raise ValueError('Не удалось прочитать размеры каталогов.')
   with disk_lock:
    disk_result.update(running=False,rows=sorted(rows,key=lambda a:-a['bytes']),time=int(time.time()),error='' if p.returncode==0 else 'Некоторые каталоги не удалось прочитать.')
    extras.atomic(DISK_FILE,dict(disk_result))
  except Exception:
   with disk_lock:disk_result.update(running=False,error='Не удалось измерить каталоги за отведённое время.')
 threading.Thread(target=work,daemon=True).start();return dict(disk_result)
def disk_status():
 with disk_lock:return dict(disk_result)
