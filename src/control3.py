import os,json,time,re,secrets,subprocess,threading,ipaddress,xml.etree.ElementTree as ET
from pathlib import Path
import extras
ROOT=Path('/opt/pi-dashboard');LIMITS=ROOT/'resource-limits.json';SCAN=ROOT/'network-inventory.json'; env=ROOT/'pihole.env'; running={'active':False,'error':''};guard=threading.Lock()
def run(args,timeout=30):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
 if p.returncode:raise ValueError('Операция не выполнена: '+p.stderr.strip()[-350:])
 return p.stdout.strip()
def limits_read():
 try:return json.loads(LIMITS.read_text())
 except Exception:return {}
def inspect(containers):
 names=[s['container'] for s in containers.values()];result={}
 p=subprocess.run(['docker','inspect',*names],capture_output=True,text=True,timeout=12)
 try:
  for x in json.loads(p.stdout):
   h=x['HostConfig'];result[x['Name'].lstrip('/')]={'memory':h.get('Memory',0),'cpus':round(h.get('NanoCpus',0)/1000000000,2)}
 except Exception:pass
 return result
def apply_limits(name,d):
 args=['docker','update','--memory',str(d['memory']),'--memory-swap',str(d['memory']*2),'--cpus',str(d['cpus']),name]
 return run(args,45)
def limits_set(d,services):
 key=str(d.get('service',''));service=services.get(key)
 if not service:raise ValueError('Сервис не найден.')
 try:mem=int(d.get('memory_mib'));cpus=float(d.get('cpus'))
 except Exception:raise ValueError('Укажите память в МиБ и число ядер.')
 if not 128<=mem<=7168 or not 0.25<=cpus<=4:raise ValueError('Память: 128–7168 МиБ, CPU: 0.25–4 ядра.')
 settings={'memory':mem*1048576,'cpus':round(cpus,2)};apply_limits(service['container'],settings)
 obj=limits_read();obj[key]=settings;extras.atomic(LIMITS,obj)
 return {'message':'Лимиты применены и сохранены.'}
def restore_limits(services):
 rules=limits_read()
 if not rules:return
 current=inspect(services)
 for key,d in rules.items():
  info=services.get(key)
  if not info or info['container'] not in current:continue
  old=current[info['container']]
  if old['memory']!=d.get('memory') or abs(old['cpus']-d.get('cpus',0))>.01:
   try:apply_limits(info['container'],d)
   except Exception:pass
def pihole(action):
 if action=='stop':run(['docker','stop','-t','30','pi-pihole'],45);return
 if action!='start':raise ValueError('Операция недоступна.')
 if not env.exists():
  password=secrets.token_urlsafe(24); fd=os.open(env,os.O_CREAT|os.O_WRONLY|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:f.write('TZ=Europe/Minsk\nFTLCONF_webserver_api_password='+password+'\nFTLCONF_dns_listeningMode=all\nFTLCONF_dns_upstreams=1.1.1.1;8.8.8.8\n')
  p=Path('/root/pi-passwords.txt');lines=p.read_text().splitlines() if p.exists() else [];lines=[s for s in lines if not s.startswith('PIHOLE_PASSWORD=')];lines.append('PIHOLE_PASSWORD='+password)
  tmp=p.with_name('.pihole-passwords.tmp');fd=os.open(tmp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
  with os.fdopen(fd,'w') as f:f.write('\n'.join(lines)+'\n')
  os.replace(tmp,p)
 p=subprocess.run(['docker','inspect','pi-pihole'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 if p.returncode==0:run(['docker','start','pi-pihole'],60);return
 run(['docker','run','-d','--name','pi-pihole','--restart','no','--memory','512m','--env-file',str(env),'-p','53:53/tcp','-p','53:53/udp','-p','8088:80/tcp','-v','pi-pihole-data:/etc/pihole','pihole/pihole:latest'],120)
def pwd_change(current,new,password):
 import hmac
 if not hmac.compare_digest(current.encode(),password.encode()):raise ValueError('Текущий пароль неверен.')
 if len(new)<12 or len(new)>128 or new!=new.strip().strip('\"\'') or any(c in new for c in '\r\n\x00'):raise ValueError('Новый пароль должен содержать от 12 до 128 символов без перевода строки.')
 envfile=ROOT/'dashboard.env';p=Path('/root/pi-passwords.txt')
 oldenv=envfile.read_text();lines=oldenv.splitlines();lines=[('DASH_PASSWORD='+new) if s.startswith('DASH_PASSWORD=') else s for s in lines]
 # Password file supports one line per entry; keep both files root-only.
 lines2=p.read_text().splitlines() if p.exists() else [];lines2=[s for s in lines2 if not s.startswith('DASHBOARD_PASSWORD=')];lines2.append('DASHBOARD_PASSWORD='+new)
 for file,text in [(p,'\n'.join(lines2)+'\n'),(envfile,'\n'.join(lines)+'\n')]:
  temp=file.with_name('.'+file.name+'.tmp');fd=os.open(temp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
  with os.fdopen(fd,'w') as f:f.write(text)
  os.replace(temp,file)
 return new

def network_info():
 try:
  j=json.loads(run(['ip','-j','-4','addr','show','dev','wlan0']));a=next(x for r in j for x in r['addr_info'] if x.get('scope')=='global');net=str(ipaddress.ip_network(a['local']+'/'+str(a['prefixlen']),strict=False));return {'ip':a['local'],'network':net}
 except Exception:return {'ip':'','network':''}
def inventory():
 try:return json.loads(SCAN.read_text())
 except Exception:return {'hosts':[],'message':'Сканирование ещё не выполнялось'}
def scan_start(data):
 scope=str(data.get('network',''));mode=str(data.get('mode','quick'));ctx=network_info()
 try:network=ipaddress.ip_network(scope,strict=False);connected=ipaddress.ip_network(ctx['network'])
 except Exception:raise ValueError('Подключитесь к Wi-Fi и укажите корректную локальную подсеть.')
 if network.version!=4 or not network.is_private or network.prefixlen<24 or not network.subnet_of(connected):raise ValueError('Разрешена только текущая локальная IPv4-подсеть размером не более /24.')
 if mode not in ('quick','full'):raise ValueError('Неизвестный режим сканирования.')
 if not data.get('authorized'):raise ValueError('Подтвердите разрешение на сканирование сети.')
 import inventory4
 start_identity=inventory4.current()
 with guard:
  if running['active']:raise ValueError('Сканирование уже выполняется.')
  running.update(active=True,error='')
 def worker():
  try:
   # TCP connect scan. Explicitly selected LAN scope; no credential guessing or intrusive NSE scripts.
   args=['nmap','-sT','-sV','--version-light','--max-retries','1','--host-timeout',('900s' if mode=='full' else '90s'),'--max-rate','150','-oX','-']
   args+=['-p-'] if mode=='full' else ['--top-ports','100'];args.append(str(network))
   p=subprocess.run(args,capture_output=True,text=True,timeout=7200)
   if p.returncode:raise Exception('Сканер завершился с ошибкой')
   root=ET.fromstring(p.stdout);hosts=[]
   for host in root.findall('host'):
    if host.find('status') is None or host.find('status').get('state')!='up':continue
    ip=next((x.get('addr') for x in host.findall('address') if x.get('addrtype')=='ipv4'),'');mac=next((x.get('addr') for x in host.findall('address') if x.get('addrtype')=='mac'),'');vendor=next((x.get('vendor','') for x in host.findall('address') if x.get('addrtype')=='mac'),'')
    names=[x.get('name') for x in host.findall('./hostnames/hostname')];ports=[]
    for port in host.findall('./ports/port'):
     state=port.find('state');service=port.find('service')
     if state is not None and state.get('state')=='open':ports.append({'port':int(port.get('portid')),'protocol':port.get('protocol'),'service':service.get('name','') if service is not None else '', 'product':service.get('product','') if service is not None else ''})
    ports_ids={x['port'] for x in ports};terms=(vendor+' '+' '.join(names)+' '+' '.join(x['product'] for x in ports)).lower()
    kind='Принтер' if ports_ids&{631,9100} else 'Камера / потоковое устройство' if 554 in ports_ids or 'camera' in terms else 'Телевизор / медиаплеер' if ports_ids&{8008,8009,8060,8001} else 'Файловый сервер / NAS' if ports_ids&{445,139,2049} else 'Устройство сети'
    hosts.append({'ip':ip,'mac':mac,'vendor':vendor,'name':', '.join(names),'ports':ports,'kind':kind})
   result={'time':int(time.time()),'network':str(network),'mode':mode,'hosts':hosts,'message':'Готово','network_info':start_identity}
   import inventory4
   result=inventory4.save_scan(result);extras.atomic(SCAN,result)
  except Exception as e:running['error']=str(e)[:180]
  finally:running['active']=False
 threading.Thread(target=worker,daemon=True).start();return {'message':'Сканирование запущено. Результаты появятся после завершения.'}
AUTO=ROOT/'scan-auto.json'
def auto_config():
 try:return json.loads(AUTO.read_text())
 except Exception:return {'enabled':False,'network':''}
def set_auto(d):
 if not d.get('enabled'):extras.atomic(AUTO,{'enabled':False,'network':''});return {'message':'Автосканирование выключено.'}
 if not d.get('authorized'):raise ValueError('Подтвердите разрешение владельца сети.')
 net=network_info().get('network','')
 try:x=ipaddress.ip_network(net)
 except Exception:raise ValueError('Сначала подключитесь к Wi-Fi.')
 if not x.is_private or x.prefixlen<24:raise ValueError('Поддерживается частная подсеть не более /24.')
 uid=run(['nmcli','-g','GENERAL.CON-UUID','device','show','wlan0']);
 if not uid or uid=='--':raise ValueError('Не удалось определить Wi-Fi профиль.')
 link=run(['iw','dev','wlan0','link']);match=re.search(r'Connected to ([0-9a-f:]{17})',link,re.I)
 if not match:raise ValueError('Не удалось определить точку доступа Wi-Fi.')
 extras.atomic(AUTO,{'enabled':True,'network':net,'uuid':uid,'bssid':match.group(1).lower()});return {'message':'Автосканирование включено только для текущей подсети.'}
def background(services):
 last=''
 while True:
  try:
   restore_limits(services)
   cfg=auto_config();cur=network_info().get('network','');uid=run(['nmcli','-g','GENERAL.CON-UUID','device','show','wlan0']);link=run(['iw','dev','wlan0','link']);m=re.search(r'Connected to ([0-9a-f:]{17})',link,re.I);bssid=m.group(1).lower() if m else ''
   if not cur or cur!=cfg.get('network') or uid!=cfg.get('uuid') or bssid!=cfg.get('bssid') or not cfg.get('enabled'):last=''
   elif cur!=last and not running['active']:
    scan_start({'network':cur,'mode':'quick','authorized':True});last=cur
  except Exception:pass
  time.sleep(30)
if __name__=='__main__':
 import sys
 try:pihole(sys.argv[1])
 except Exception as e:print(str(e),file=sys.stderr);sys.exit(1)
