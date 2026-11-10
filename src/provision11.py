import os,io,json,time,uuid,secrets,hashlib,threading,shlex,subprocess,socket,ssl
from pathlib import Path
import nodeops10,vault,cluster5,ssh_nodes7
ROOT=Path('/opt/pi-dashboard');guard=threading.Lock();jobs={}
BOOTSTRAP=r'''import os,sys,json,hashlib,subprocess,shutil,ipaddress,socket
from pathlib import Path
cfg=json.loads(sys.stdin.readline());stage=Path(cfg['stage']);root=Path('/opt/pi-node');unit=Path('/etc/systemd/system/pi-control-node.service')
if os.geteuid()!=0:raise SystemExit('Root privileges are required')
if root.exists() or unit.exists():raise SystemExit('Node installation already exists; refusing to overwrite')
if not Path('/etc/debian_version').exists():raise SystemExit('Only Debian/Raspberry Pi OS is supported')
if shutil.disk_usage('/').free<2*1024**3:raise SystemExit('At least 2 GiB of free space required')
mem=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:')))
if mem<450000:raise SystemExit('At least 450 MiB RAM required')
if not shutil.which('docker'):
 print('Installing Docker packages',flush=True)
 subprocess.run(['apt-get','update'],check=True,timeout=400,stdout=subprocess.DEVNULL)
 subprocess.run(['apt-get','install','-y','--no-install-recommends','docker.io','docker-cli'],check=True,timeout=900,env={**os.environ,'DEBIAN_FRONTEND':'noninteractive'},stdout=subprocess.DEVNULL)
if not shutil.which('openssl'):raise SystemExit('openssl is required')
probe=socket.socket()
try:probe.bind(('0.0.0.0',8484))
except OSError:raise SystemExit('Port 8484 is already in use')
finally:probe.close()
root.mkdir(mode=0o700);shutil.copyfile(stage/'pi_node_agent.py',root/'agent.py');os.chmod(root/'agent.py',0o644)
password=cfg['password'];salt=os.urandom(16);digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex();config={'salt':salt.hex(),'password_hash':digest,'port':8484}
(root/'config.json').write_text(json.dumps(config));os.chmod(root/'config.json',0o600)
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem'),'-days','825','-subj','/CN=Pi Control Node'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30);os.chmod(root/'key.pem',0o600)
unit.write_text('[Unit]\nDescription=Pi Control lightweight node\nAfter=network-online.target docker.service\nWants=network-online.target\n[Service]\nType=simple\nUser=root\nUMask=0077\nExecStart=/usr/bin/python3 /opt/pi-node/agent.py\nRestart=on-failure\nRestartSec=5\nMemoryMax=128M\nNoNewPrivileges=yes\n[Install]\nWantedBy=multi-user.target\n')
subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15);subprocess.run(['systemctl','enable','--now','pi-control-node.service'],check=True,timeout=30)
print('NODE_INSTALL_OK',flush=True)
'''
def snapshot(job):
 with guard:return dict(jobs.get(job,{'state':'missing'}))
def create(d):
 node=next((x for x in ssh_nodes7.list_nodes() if x['id']==str(d.get('id',''))),None)
 if not node:raise ValueError('SSH-узел не найден.')
 if any(x.get('host')==node['host'] and x.get('port')==8484 for x in cluster5.get()):raise ValueError('Панель этого узла уже добавлена.')
 master=str(d.get('master',''));panel_password=str(d.get('panel_password',''))
 if not master and not 12<=len(panel_password)<=128:raise ValueError('Откройте KDBX или укажите отдельный пароль панели от 12 символов.')
 with guard:
  if any(x.get('node')==node['id'] and x.get('state')=='running' for x in jobs.values()):raise ValueError('Установка уже выполняется.')
  jid=uuid.uuid4().hex;jobs[jid]={'id':jid,'node':node['id'],'state':'running','message':'Проверяем доступ и ресурсы.'}
 # Copy request into the worker only; secrets are never written to job status or logs.
 args={'id':node['id'],'master':master,'secret':str(d.get('secret','')),'panel_password':panel_password}
 threading.Thread(target=worker,args=(jid,node,args),daemon=True).start();return {'job':jid,'message':'Проверка и установка запущены.'}
def change(jid,**fields):
 with guard:jobs[jid].update(fields)
def worker(jid,node,args):
 client=None;sftp=None;stage='';password=args.get('panel_password') or secrets.token_urlsafe(30)
 try:
  _,client=nodeops10.open_node(args)
  cmd="if [ \"$(id -u)\" = 0 ] || sudo -n true 2>/dev/null; then echo ROOT_OK; fi; test ! -e /opt/pi-node && echo NODE_ABSENT; test ! -e /etc/systemd/system/pi-control-node.service && echo UNIT_ABSENT; uname -m; tr '\\000' ' ' </proc/device-tree/model 2>/dev/null; printf '\\n'"
  _,out,_=client.exec_command(cmd,timeout=15);r=out.read(4096).decode('utf-8','replace')
  if not all(x in r for x in ('ROOT_OK','NODE_ABSENT','UNIT_ABSENT','Raspberry Pi')):raise ValueError('Нет root-доступа, устройство не Raspberry Pi или установка уже существует; существующие файлы не изменены.')
  entry_id=''
  if args['master']:
   import shutil
   backup=ROOT/('vault-before-node-'+jid+'.kdbx');shutil.copy2(vault.FILE,backup);os.chmod(backup,0o600)
   with vault.guard:
    db=vault.opened(args['master']);title='Pi node '+node['name']+' '+jid[:8];entry=db.add_entry(db.root_group,title,'admin',password,url='https://'+node['host']+':8484/');entry_id=str(entry.uuid);vault.save(db)
  stage='.pi-control-install-'+jid[:12];sftp=client.open_sftp();sftp.mkdir(stage,mode=0o700)
  for name,data in [('pi_node_agent.py',(ROOT/'pi_node_agent.py').read_bytes()),('bootstrap.py',BOOTSTRAP.encode())]:
   with sftp.open(stage+'/'+name,'wb') as f:f.write(data)
   sftp.chmod(stage+'/'+name,0o600)
  absstage=sftp.normalize(stage);change(jid,message='Устанавливаем лёгкую панель; существующие данные не заменяются.')
  run='sudo -n /usr/bin/python3 '+shlex.quote(absstage+'/bootstrap.py')
  stdin,out,err=client.exec_command(run,timeout=1000);stdin.write(json.dumps({'stage':absstage,'password':password})+'\n');stdin.flush();stdin.channel.shutdown_write()
  output=out.read(12000).decode('utf-8','replace');error=err.read(4000).decode('utf-8','replace');exitcode=out.channel.recv_exit_status()
  if exitcode or 'NODE_INSTALL_OK' not in output:raise ValueError('Установка завершилась ошибкой. На узле мог остаться каталог /opt/pi-node; проверьте его перед повтором. '+error[-250:])
  # Obtain the certificate fingerprint over the already pinned SSH channel.
  _,out,_=client.exec_command('sudo -n openssl x509 -in /opt/pi-node/cert.pem -outform DER | sha256sum',timeout=15);fp=out.read(256).decode().split()[0].upper()
  if len(fp)!=64:raise ValueError('Панель установлена, но не удалось проверить сертификат.')
  info={'id':'temporary','name':node['name'],'host':node['host'],'port':8484,'fingerprint':fp,'entry':entry_id}
  # End-to-end HTTPS login and status test precedes adding the node to the controller.
  change(jid,message='Ожидаем доступности HTTPS и проверяем пароль панели.')
  failure=None
  for _ in range(20):
   try:status=cluster5.peer(info,password);failure=None;break
   except ValueError as e:failure=e;time.sleep(2)
  if failure:raise ValueError('Панель установлена, но пока недоступна по HTTPS: '+str(failure))
  cluster5.create({'name':node['name'],'host':node['host'],'port':8484,'fingerprint':fp,'entry':entry_id,'manual':not bool(entry_id)})
  change(jid,state='done',message='Панель установлена, HTTPS проверен, узел добавлен в подключённые.',resources=status.get('system',{}))
 except Exception as e:
  change(jid,state='error',message=str(e)[:400] or 'Не удалось выполнить установку.')
 finally:
  if sftp and stage:
   for name in ('pi_node_agent.py','bootstrap.py'):
    try:sftp.remove(stage+'/'+name)
    except Exception:pass
   try:sftp.rmdir(stage)
   except Exception:pass
  if sftp:sftp.close()
  if client:client.close()
  args.clear();password=''
