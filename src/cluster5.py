import json,ipaddress,re,ssl,hashlib,hmac,http.client,threading,uuid
from pathlib import Path
import extras
ROOT=Path('/opt/pi-dashboard');FILE=ROOT/'cluster-nodes.json';lock=threading.Lock()
def get():
 try:return json.loads(FILE.read_text())
 except Exception:return []
def cert_fingerprint():
 pem=(ROOT/'cert.pem').read_text();der=ssl.PEM_cert_to_DER_cert(pem);return hashlib.sha256(der).hexdigest().upper()
def addr_ok(addr):
 try:ip=ipaddress.ip_address(addr)
 except ValueError:return False
 return ip.version==4 and any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10'))
def create(d):
 name=str(d.get('name','')).strip();host=str(d.get('host','')).strip();fingerprint=re.sub(r'[^0-9a-fA-F]','',str(d.get('fingerprint',''))).upper();entry=str(d.get('entry','')).strip()
 try:
  port=int(d.get('port',8484))
  if entry:uuid.UUID(entry)
  elif not d.get('manual'):raise ValueError()
 except Exception:raise ValueError('Укажите порт и запись KDBX или ручной пароль.')
 if not name or len(name)>80 or not addr_ok(host) or port<1 or port>65535 or len(fingerprint)!=64:raise ValueError('Укажите имя, локальный или Tailscale IPv4, порт и SHA-256 отпечаток сертификата.')
 with lock:
  arr=get()
  if len(arr)>=20:raise ValueError('Достигнут лимит 20 узлов.')
  if any(x['host']==host and x['port']==port for x in arr):raise ValueError('Узел с таким адресом уже добавлен.')
  arr.append({'id':uuid.uuid4().hex,'name':name,'host':host,'port':port,'fingerprint':fingerprint,'entry':entry});extras.atomic(FILE,arr)
 return {'message':'Узел добавлен. Для подключения откройте хранилище.'}
def remove(id):
 with lock:
  arr=get();new=[x for x in arr if x['id']!=id]
  if len(arr)==len(new):raise ValueError('Узел не найден.')
  extras.atomic(FILE,new)
 return {'message':'Узел удалён из панели.'}
def peer(node,password,service=None,action=None):
 if not addr_ok(node.get('host','')):raise ValueError('Адрес узла недопустим.')
 conn=http.client.HTTPSConnection(node['host'],node['port'],context=ssl._create_unverified_context(),timeout=25);cookie='';headers={}
 try:
  conn.connect();cert=conn.sock.getpeercert(binary_form=True);actual=hashlib.sha256(cert).hexdigest().upper()
  if not hmac.compare_digest(actual,node['fingerprint']):raise ValueError('Отпечаток сертификата узла не совпадает; пароль не отправлен.')
  origin='https://'+node['host']+':'+str(node['port']);headers={'Content-Type':'application/json','Origin':origin}
  conn.request('POST','/api/login',body=json.dumps({'password':password}).encode(),headers=headers);res=conn.getresponse();cookie=res.getheader('Set-Cookie','').split(';',1)[0];res.read(65536)
  if res.status!=200 or not cookie.startswith('pi_session='):raise ValueError('Узел отклонил пароль панели.')
  headers['Cookie']=cookie
  if service is None:
   conn.request('GET','/api/status',headers={'Cookie':cookie});r=conn.getresponse();data=r.read(1024*1024);result=json.loads(data)
   if r.status!=200:raise ValueError('Узел не вернул статус.')
   return {'system':result.get('system',{}),'services':[{'id':x.get('id'),'name':x.get('name'),'state':x.get('state'),'status':x.get('status')} for x in result.get('services',[])]}
  if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',str(service)) or action not in ('start','stop'):raise ValueError('Операция недоступна.')
  conn.request('POST','/api/action',body=json.dumps({'service':service,'action':action}).encode(),headers=headers);r=conn.getresponse();data=r.read(65536);result=json.loads(data)
  if r.status not in (200,202):raise ValueError(result.get('message','Операция не выполнена.'))
  return {'message':result.get('message','Операция запущена.')}
 except ValueError:raise
 except Exception:raise ValueError('Не удалось связаться с узлом. Проверьте сеть и доступность панели.')
 finally:
  if cookie:
   try:conn.request('POST','/api/logout',body=b'{}',headers=headers);conn.getresponse().read(65536)
   except Exception:pass
  conn.close()
def action(d):
 n=next((x for x in get() if x['id']==str(d.get('id',''))),None)
 if not n:raise ValueError('Узел не найден.')
 import vault
 secret=str(d.get('password','')) if not n.get('entry') else vault.dispatch('reveal',{'master':str(d.get('master','')),'id':n['entry']})['password']
 if not secret:raise ValueError('В записи хранилища отсутствует пароль панели узла.')
 op=d.get('op','status')
 if op=='status':return peer(n,secret)
 if op=='service':return peer(n,secret,str(d.get('service','')),str(d.get('action','')))
 raise ValueError('Неизвестная операция.')
