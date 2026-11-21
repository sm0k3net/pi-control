"""HTTP endpoints and scheduled platform health sampling."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import cloud_postgres
import node_features
import remote_desktop
import platform_events
import usb_backup

ASSETS = {'platform-ui.js': 'application/javascript', 'platform-ui.css': 'text/css',
          'cloud-usb-ui.js': 'application/javascript'}
GET = {'/api/platform/backup', '/api/platform/events', '/api/platform/usb', '/api/platform/postgres',
       '/api/platform/node-tools', '/api/platform/desktop', '/api/platform/vault-tools', '/api/platform/bitwarden', '/api/platform/node-backup'}
POST = GET.copy()
GET.update(remote_desktop.MAC_GET)
ROOT = Path(__file__).resolve().parent
SAMPLE = Path('/run/pi-platform-cpu.json')


def get(handler):
    path = urlsplit(handler.path).path
    if path in remote_desktop.MAC_GET:
        return remote_desktop.mac_get(handler)
    name = path.removeprefix('/')
    if name in ASSETS:
        return handler.respond(200, (ROOT / name).read_bytes(), ASSETS[name] + '; charset=utf-8')
    if not handler.authenticated():
        return handler.respond(401, {'error': 'Требуется вход'})
    try:
        data = {'/api/platform/backup': usb_backup.status,
                '/api/platform/events': platform_events.list_events,
                '/api/platform/usb': usb_backup.file_roots,
                '/api/platform/node-tools': node_features.status,
                '/api/platform/desktop': remote_desktop.status,
                '/api/platform/vault-tools': node_features.secret_catalog,
                '/api/platform/bitwarden': node_features.bw_status,
                '/api/platform/node-backup': node_features.node_backup_status,
                '/api/platform/postgres': cloud_postgres.status}[path]()
        return handler.respond(200, data)
    except (OSError, ValueError, subprocess.SubprocessError):
        return handler.respond(503, {'error': 'Состояние пока недоступно.'})


def post(handler, data):
    if not handler.authenticated():
        return handler.respond(401, {'error': 'Требуется вход'})
    try:
        result = {'/api/platform/backup': usb_backup.action,
                  '/api/platform/events': platform_events.action,
                  '/api/platform/node-tools': node_features.action,
                  '/api/platform/desktop': remote_desktop.action,
                  '/api/platform/vault-tools': node_features.enroll_service_secret,
                  '/api/platform/bitwarden': lambda d: node_features.bw_action(
                      hashlib.sha256(handler.headers.get('Cookie','').encode()).digest(),d),
                  '/api/platform/node-backup': node_features.node_backup_action,
                  '/api/platform/postgres': cloud_postgres.action,
                  '/api/platform/usb': lambda _: usb_backup.request_rescan()}[handler.path](data)
        return handler.respond(200, result)
    except ValueError as exc:
        return handler.respond(400, {'error': str(exc)})
    except (OSError, subprocess.SubprocessError):
        return handler.respond(503, {'error': 'Операция временно недоступна.'})


def sample():
    cpu = None
    fields = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    total, idle = sum(fields), sum(fields[3:5])
    previous = usb_backup.load(SAMPLE)
    usb_backup.save(SAMPLE, [total, idle])
    if previous and total > previous[0]:
        cpu = min(100, max(0, 100 * (1 - (idle - previous[1]) / (total - previous[0]))))
    try:
        temperature = int(Path('/sys/class/thermal/thermal_zone0/temp').read_text().strip()) / 1000
    except (OSError, ValueError):
        temperature = None
    disk = shutil.disk_usage('/')
    vpn_config = usb_backup.load(ROOT / 'vpn-routing.json', {}) or {}
    vpn_expected = bool(vpn_config.get('host') or vpn_config.get('containers'))
    vpn_down = False
    try:
        p = subprocess.run(['docker', 'inspect', '-f',
                            '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}',
                            'pi-extra-gluetun-1'], capture_output=True, text=True, timeout=10)
        state = p.stdout.strip().split()
        vpn_down = (vpn_expected or state[:1] == ['true']) and state != ['true', 'healthy']
    except (OSError, subprocess.TimeoutExpired):
        vpn_down = True if vpn_expected else None
    platform_events.monitor(cpu, temperature, round(disk.used * 100 / disk.total), vpn_down)




BOOT_STATE = ROOT / 'boot-service-preferences.json'
NATIVE_SERVICES = {'desktop', 'vnc', 'novnc', 'harness'}


def runtime_command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=180)
    if result.returncode:
        raise ValueError('Не удалось применить настройку запуска сервиса.')
    return result.stdout.strip()


def record_service_state(key, verb, services):
    if verb not in ('start','stop') or key not in services and key not in NATIVE_SERVICES:
        return
    import fcntl
    with (ROOT/'boot-service-preferences.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        container=services.get(key,{}).get('container')
        state=usb_backup.load(BOOT_STATE,{}) or {}
        state[key]=verb=='start'
        usb_backup.save(BOOT_STATE,state)
        if container:
            policy='unless-stopped' if verb=='start' else 'no'
            runtime_command(['docker','update','--restart='+policy,container])
        elif key=='harness':
            runtime_command(['systemctl','enable' if verb=='start' else 'disable','dsh-web.service'])


def runtime_service_enabled(key, service):
    container=service.get('container')
    if container:
        try:
            return runtime_command(['docker','inspect','-f','{{.State.Running}}',container])=='true'
        except ValueError:
            return None
    units={'desktop':['systemctl','is-active','lightdm.service'],
           'novnc':['systemctl','--user','-M','sm0k3@','is-active','novnc-websockify.service'],
           'vnc':['runuser','-u','sm0k3','--','/usr/local/bin/pi-vnc','status'],
           'harness':['systemctl','is-active','dsh-web.service'],
           'tor':['systemctl','is-active','tor@default.service']}
    if key not in units:
        return None
    result=subprocess.run(units[key],capture_output=True,text=True,timeout=10)
    if key=='vnc':
        return 'Xtigervnc' in result.stdout
    return result.returncode==0


def service_boot(bootstrap=False):
    import server
    commands=server.COMMANDS
    services=server.SERVICES
    current=usb_backup.load(BOOT_STATE,{}) or {}
    issues=[]
    if bootstrap:
        keys=set(services)|NATIVE_SERVICES
        for key in sorted(keys):
            if key in current or key+':start' not in commands:
                continue
            try:
                result=runtime_service_enabled(key,services.get(key,{}))
                if result is not None:
                    record_service_state(key,'start' if result else 'stop',services)
            except (ValueError,OSError,subprocess.SubprocessError):
                issues.append(key)
    else:
        for key,enabled in sorted(current.items(),key=lambda item:(item[0]!='vpn', item[0])):
            verb='start' if enabled is True else 'stop'
            command=commands.get(key+':'+verb)
            if type(enabled) is not bool or not command:
                continue
            try:
                state=runtime_service_enabled(key,services.get(key,{}))
                if state is enabled or (state is None and not enabled):
                    continue
                runtime_command(command)
                record_service_state(key,verb,services)
            except (ValueError,OSError,subprocess.SubprocessError):
                issues.append(key)
    if issues:
        raise SystemExit('Could not restore settings for '+', '.join(issues))


if __name__ == '__main__':
    import sys
    if sys.argv[1:] in (['--restore-services'],['--initialize-services']):
        service_boot(bootstrap='--initialize-services' in sys.argv)
    else:
        sample()
