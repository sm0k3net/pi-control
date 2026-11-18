"""HTTP endpoints and scheduled platform health sampling."""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import cloud_postgres
import platform_events
import usb_backup

ASSETS = {'platform-ui.js': 'application/javascript', 'platform-ui.css': 'text/css',
          'cloud-usb-ui.js': 'application/javascript'}
GET = {'/api/platform/backup', '/api/platform/events', '/api/platform/usb', '/api/platform/postgres'}
POST = GET
ROOT = Path(__file__).resolve().parent
SAMPLE = Path('/run/pi-platform-cpu.json')


def get(handler):
    path = urlsplit(handler.path).path
    name = path.removeprefix('/')
    if name in ASSETS:
        return handler.respond(200, (ROOT / name).read_bytes(), ASSETS[name] + '; charset=utf-8')
    if not handler.authenticated():
        return handler.respond(401, {'error': 'Требуется вход'})
    try:
        data = {'/api/platform/backup': usb_backup.status,
                '/api/platform/events': platform_events.list_events,
                '/api/platform/usb': usb_backup.file_roots,
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


if __name__ == '__main__':
    sample()
