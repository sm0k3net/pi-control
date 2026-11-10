#!/usr/bin/env python3
"""Install the lightweight Pi Control node, not the main panel or its Docker stack."""
import argparse
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/opt/pi-node')
UNIT = Path('/etc/systemd/system/pi-control-node.service')
SOURCE = Path(__file__).resolve().parent.parent / 'src' / 'pi_node_agent.py'
PORT = 8484


def report():
    try:
        memory = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:'))) * 1024
    except (OSError, StopIteration, ValueError, IndexError):
        memory = 0
    try:
        model = Path('/proc/device-tree/model').read_bytes().rstrip(b'\0').decode('utf-8', 'replace')
    except OSError:
        model = 'unknown'
    try:
        disk_free = shutil.disk_usage('/').free
    except OSError:
        disk_free = 0
    return {'os': platform.system(), 'arch': platform.machine(), 'model': model,
            'ram_bytes': memory, 'disk_free_bytes': disk_free,
            'debian': Path('/etc/debian_version').exists(),
            'systemd': Path('/run/systemd/system').exists(),
            'docker': bool(shutil.which('docker')),
            'openssl': bool(shutil.which('openssl')),
            'source': SOURCE.is_file(),
            'existing_installation': ROOT.exists() or UNIT.exists()}


def errors(info, installing):
    issues = []
    if info['os'] != 'Linux' or not info['debian']:
        issues.append('Debian or Raspberry Pi OS is required.')
    if not info['systemd']:
        issues.append('A running systemd is required.')
    if not info['source']:
        issues.append('src/pi_node_agent.py is missing; use a complete checkout.')
    if info['ram_bytes'] < 450_000_000:
        issues.append('At least 450 MB RAM is required.')
    if info['disk_free_bytes'] < 2 * 1024**3:
        issues.append('At least 2 GiB of free storage is required.')
    if info['existing_installation']:
        issues.append('A node directory or unit exists; refusing to overwrite it.')
    if installing and os.geteuid() != 0:
        issues.append('Installation requires root; use sudo.')
    return issues


def run(cmd, **kwargs):
    subprocess.run(cmd, check=True, **kwargs)


def secure_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(data)


def install():
    info = report()
    issues = errors(info, True)
    if issues:
        raise RuntimeError('\n'.join(issues))
    # Guard the port before any package changes, and again at service start.
    with socket.socket() as probe:
        try:
            probe.bind(('0.0.0.0', PORT))
        except OSError as e:
            raise RuntimeError(f'Port {PORT} is unavailable: {e}') from e
    if not info['openssl'] or not info['docker']:
        print('Installing required distribution packages. Existing Docker data is not modified.', flush=True)
        packages = []
        if not info['openssl']:
            packages.append('openssl')
        if not info['docker']:
            packages.append('docker.io')
        run(['apt-get', 'update'], timeout=600)
        run(['apt-get', 'install', '-y', '--no-install-recommends', *packages], timeout=1200,
            env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
    # Never merge into an existing directory, even if another installer raced us.
    ROOT.mkdir(mode=0o700)
    password = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    shutil.copyfile(SOURCE, ROOT / 'agent.py')
    os.chmod(ROOT / 'agent.py', 0o644)
    secure_write(ROOT / 'config.json', json.dumps({'salt': salt.hex(), 'password_hash': digest, 'port': PORT}) + '\n')
    secure_write(ROOT / 'initial-password.txt', password + '\n')
    run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(ROOT / 'key.pem'),
         '-out', str(ROOT / 'cert.pem'), '-days', '825', '-subj', '/CN=Pi Control Node'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    os.chmod(ROOT / 'key.pem', 0o600)
    unit = ('[Unit]\nDescription=Pi Control lightweight node\nAfter=network-online.target docker.service\n'
            'Wants=network-online.target\n[Service]\nType=simple\nUser=root\nUMask=0077\n'
            'ExecStart=/usr/bin/python3 /opt/pi-node/agent.py\nRestart=on-failure\nRestartSec=5\n'
            'MemoryMax=128M\nNoNewPrivileges=yes\n[Install]\nWantedBy=multi-user.target\n')
    secure_write(UNIT, unit)
    os.chmod(UNIT, 0o644)
    run(['systemctl', 'daemon-reload'], timeout=30)
    run(['systemctl', 'enable', '--now', 'pi-control-node.service'], timeout=60)
    # The node binds discovered private addresses, so service activation is distinct from
    # verification of its network path and TLS fingerprint from a remote client.
    time.sleep(2)
    run(['systemctl', 'is-active', '--quiet', 'pi-control-node.service'], timeout=15)
    print('Lightweight node installed. Verify network access and the certificate fingerprint before login.')
    print('Password (root only): /opt/pi-node/initial-password.txt')
    print('Certificate fingerprint: sudo openssl x509 -in /opt/pi-node/cert.pem -noout -fingerprint -sha256')
    print('This installer does not install the main dashboard or application stack.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only system compatibility report')
    args = parser.parse_args()
    if args.check:
        info = report()
        info['issues'] = errors(info, False)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 1 if info['issues'] else 0
    try:
        install()
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        print(f'Installation failed: {e}', file=sys.stderr)
        print('If a partial /opt/pi-node or unit was created, inspect it before cleanup or retry.', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
