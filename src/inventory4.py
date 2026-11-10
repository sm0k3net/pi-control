import os,json,time,html,hashlib,ipaddress,subprocess,re
from pathlib import Path
import extras
ROOT=Path('/opt/pi-dashboard');DIR=ROOT/'network-history';DIR.mkdir(mode=0o700,exist_ok=True);KNOWN=ROOT/'known-networks.json'
def known():
 try:return json.loads(KNOWN.read_text())
 except Exception:return {}
def current():
 from control3 import network_info,run
 network=network_info().get('network','');ssid=run(['nmcli','-g','GENERAL.CONNECTION','device','show','wlan0']);uid=run(['nmcli','-g','GENERAL.CON-UUID','device','show','wlan0']);link=run(['iw','dev','wlan0','link']);m=re.search(r'Connected to ([0-9a-f:]{17})',link,re.I);bssid=m.group(1).lower() if m else ''
 return {'network':network,'name':ssid,'uuid':uid,'bssid':bssid,'id':hashlib.sha256((uid+'|'+bssid+'|'+network).encode()).hexdigest()[:16]}
def save_scan(scan):
 try:info=scan.get('network_info') or current()
 except Exception:info={'network':scan.get('network',''),'name':'','id':'unknown'}
 scan['network_info']=info;scan['id']=str(int(time.time()*1000))+'-'+os.urandom(3).hex();extras.atomic(DIR/(scan['id']+'.json'),scan)
 items=known();idx=info['id'];old=items.get(idx,{});items[idx]={'id':idx,'name':old.get('name') or info.get('name') or scan.get('network',''),'network':info.get('network',''),'seen':int(time.time()),'scans':old.get('scans',0)+1,'description':old.get('description',''),'auto_printers':bool(old.get('auto_printers'))};extras.atomic(KNOWN,items)
 try:same_network=current()['id']==idx
 except Exception:same_network=False
 if items[idx].get('auto_printers') and same_network:
  for h in scan.get('hosts',[]):
   if any(p.get('port')==631 and p.get('protocol')=='tcp' for p in h.get('ports',[])):
    try:add_printer({'ip':h['ip'],'authorized':True},scan)
    except Exception:pass
 return scan
def list_reports():
 result=[]
 for p in sorted(DIR.glob('*.json'),reverse=True)[:100]:
  try:
   d=json.loads(p.read_text());result.append({'id':d['id'],'time':d['time'],'network':d.get('network',''),'name':d.get('network_info',{}).get('name',''),'hosts':len(d.get('hosts',[])),'mode':d.get('mode','')})
  except Exception:continue
 return {'reports':result,'networks':list(known().values())}
def load_report(id):
 if not re.fullmatch(r'\d{10,16}-[a-f0-9]{6}',id):raise ValueError('Неверный идентификатор отчёта.')
 try:return json.loads((DIR/(id+'.json')).read_text())
 except Exception:raise ValueError('Отчёт не найден.')
def update_network(d):
 x=known();id=str(d.get('id',''))
 if id not in x:raise ValueError('Сеть не найдена.')
 name=str(d.get('name','')).strip();desc=str(d.get('description','')).strip()
 if not name or len(name)>100 or len(desc)>4000:raise ValueError('Проверьте название и описание сети.')
 if d.get('auto_printers') and not d.get('authorized'):raise ValueError('Подтвердите разрешение на добавление принтеров этой сети.')
 x[id].update(name=name,description=desc,auto_printers=bool(d.get('auto_printers')));extras.atomic(KNOWN,x);return {'message':'Сеть сохранена.'}
def knowledge(id):
 item=known().get(id)
 if not item:raise ValueError('Сеть не найдена.')
 title='Сеть: '+item['name'];text='Подсеть: '+item['network']+'\nОписание: '+item.get('description','')+'\nСканирований: '+str(item.get('scans',0))
 for p in sorted(DIR.glob('*.json'),reverse=True):
  try:
   scan=json.loads(p.read_text())
   if scan.get('network_info',{}).get('id')==id:
    for h in scan.get('hosts',[])[:40]:text+='\n'+h.get('ip','')+' · '+h.get('kind','')+' · '+h.get('name','')+' · '+', '.join(str(x.get('port'))+'/'+x.get('service','') for x in h.get('ports',[])[:15])
    break
  except Exception:continue
 import hashlib
 extras.atomic(ROOT/'knowledge'/(hashlib.sha256(title.encode()).hexdigest()+'.json'),{'title':title,'text':text[:6000]});return {'message':'Сеть добавлена в базу знаний.'}
def add_printer(data,scan=None):
 if not data.get('authorized'):raise ValueError('Подтвердите, что принтер разрешено добавить.')
 try:ip=str(ipaddress.IPv4Address(str(data.get('ip',''))))
 except Exception:raise ValueError('Укажите IPv4-адрес принтера.')
 if scan is None:
  from control3 import inventory
  scan=inventory()
 try:
  if scan.get('network_info',{}).get('id')!=current()['id']:raise ValueError('Сеть изменилась; повторите сканирование перед добавлением принтера.')
 except ValueError:raise
 except Exception:raise ValueError('Не удалось определить текущую сеть.')
 if not any(h.get('ip')==ip and any(p.get('port')==631 and p.get('protocol')=='tcp' for p in h.get('ports',[])) for h in scan.get('hosts',[])):raise ValueError('IPP-принтер не обнаружен в последнем сканировании.')
 device='pi-'+scan['network_info']['id'][:6]+'-'+ip.replace('.','-');uri='ipp://'+ip+'/ipp/print'
 existing=subprocess.run(['lpstat','-p',device],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
 if existing.returncode==0:return {'message':'Принтер уже добавлен: '+device}
 p=subprocess.run(['ipptool','-q','-T','5',uri,'/usr/share/cups/ipptool/get-printer-attributes.test'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
 if p.returncode:raise ValueError('Принтер не ответил по IPP Everywhere на /ipp/print; проверьте адрес и совместимость.')
 p=subprocess.run(['lpadmin','-p',device,'-E','-v',uri,'-m','everywhere'],capture_output=True,text=True,timeout=45)
 if p.returncode:raise ValueError('Не удалось добавить принтер через CUPS. Проверьте совместимость IPP Everywhere.')
 return {'message':'Принтер добавлен: '+device}
def report_html(id):
 d=load_report(id);e=lambda x:html.escape(str(x or ''),quote=True);hosts=d.get('hosts',[]);ports=sum(len(h.get('ports',[])) for h in hosts);when=time.strftime('%d.%m.%Y %H:%M',time.localtime(d.get('time',0)))
 cards=''.join('<article><b>'+e(h.get('kind'))+'</b><h3>'+e(h.get('name') or h.get('ip'))+'</h3><small>'+e(h.get('ip'))+' · '+e(h.get('vendor') or 'Производитель не определён')+'</small><div class="tags">'+''.join('<span>'+e(p.get('port'))+'/'+e(p.get('protocol'))+' '+e(p.get('service'))+'</span>' for p in h.get('ports',[])[:14])+'</div></article>' for h in hosts)
 rows=''.join('<tr><td><b>'+e(h.get('ip'))+'</b><br><small>'+e(h.get('name'))+'</small></td><td>'+e(h.get('kind'))+'<br><small>'+e(h.get('vendor'))+'</small></td><td>'+e(h.get('mac'))+'</td><td>'+('<br>'.join(e(p.get('port'))+'/'+e(p.get('protocol'))+' · '+e(p.get('service'))+' '+e(p.get('product')) for p in h.get('ports',[])) or 'Не обнаружены')+'</td></tr>' for h in hosts)
 return ('''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Инвентаризация сети</title><style>''' + '''*{box-sizing:border-box}body{margin:0;background:#f4f7f5;color:#20342b;font:15px system-ui,sans-serif}.wrap{max-width:1120px;margin:auto;padding:42px 24px}.hero{background:#184d3b;color:#fff;border-radius:22px;padding:35px}.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;opacity:.8}h1{font-size:36px;margin:10px 0}h2{margin:30px 0 12px}.meta{opacity:.8}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:22px 0}.stat,article{background:white;border:1px solid #e1eae4;border-radius:14px;padding:18px}.stat b{display:block;font-size:26px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}article h3{margin:12px 0 4px;font-size:17px}small{color:#788a80}.tags{margin-top:12px}.tags span{display:inline-block;background:#edf5f0;color:#226147;padding:4px 7px;border-radius:6px;margin:2px;font-size:11px}table{border-collapse:collapse;background:white;width:100%;border-radius:12px;overflow:hidden}td,th{padding:13px;text-align:left;vertical-align:top;border-bottom:1px solid #edf1ee}th{font-size:11px;color:#7b8b81;text-transform:uppercase}.note{color:#74867c;line-height:1.6;font-size:13px}@media(max-width:650px){.table{overflow:auto}table{min-width:700px}h1{font-size:28px}}@media print{body{background:white}.wrap{padding:0}.hero{color:#163f31;background:#eaf5ee}article,table{break-inside:avoid}}''' + '''</style><div class="wrap"><div class="hero"><div class="eyebrow">Pi Control · Network inventory</div><h1>Инвентаризация сети</h1><div class="meta">'''+e(d.get('network'))+' · '+e(when)+'''</div></div><div class="grid"><div class="stat"><small>Устройства</small><b>'''+str(len(hosts))+'''</b></div><div class="stat"><small>Открытые TCP-порты</small><b>'''+str(ports)+'''</b></div><div class="stat"><small>Режим</small><b>'''+('Полный' if d.get('mode')=='full' else 'Быстрый')+'''</b></div></div><h2>Карта устройств</h2><p class="note">Логическое представление результатов; физические соединения не определялись.</p><div class="cards">'''+cards+'''</div><h2>Инвентаризация и сервисы</h2><div class="table"><table><thead><tr><th>Адрес и имя</th><th>Тип и производитель</th><th>MAC</th><th>Обнаруженные сервисы</th></tr></thead><tbody>'''+rows+'''</tbody></table></div><p class="note">Тип устройства определяется по косвенным признакам и может быть ошибочным. Закрытые и не ответившие устройства, UDP-порты и физическая топология в этот отчёт не входят. Сканирование могло быть прервано по тайм-ауту.</p></div></html>''').encode()
