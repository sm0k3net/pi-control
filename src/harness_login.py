"""Retrieve the bootstrap token only for the current systemd invocation."""
import ipaddress
import re
import subprocess
from urllib.parse import parse_qs, urlencode, urlsplit


def run(args, timeout=8):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if p.returncode:
        raise ValueError('Не удалось проверить состояние DeepSeek Harness.')
    return p.stdout.strip()


def current_invocation():
    values = {}
    for line in run(['systemctl', 'show', 'dsh-web.service', '--property=ActiveState', '--property=InvocationID', '--property=MainPID']).splitlines():
        key, _, value = line.partition('=')
        values[key] = value
    ident = values.get('InvocationID', '')
    if values.get('ActiveState') != 'active' or not re.fullmatch(r'[0-9a-f]{32}', ident) or values.get('MainPID', '0') == '0':
        raise ValueError('DeepSeek Harness не запущен или ещё не готов.')
    return ident


def token_for_invocation(log):
    for line in reversed(log.splitlines()):
        match = re.search(r'https?://[^\s]+[?&]token=[A-Za-z0-9_-]+', line)
        if not match:
            continue
        parsed = urlsplit(match.group(0)); values = parse_qs(parsed.query).get('token', [])
        if len(values) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{20,256}', values[0]):
            return values[0]
    raise ValueError('Токен текущего запуска не найден. Проверьте журнал службы DeepSeek Harness.')


def login_url():
    ident = current_invocation()
    log = run(['journalctl', '-b', '--no-pager', '--output=cat', '_SYSTEMD_INVOCATION_ID='+ident], timeout=12)
    token = token_for_invocation(log)
    if current_invocation() != ident:
        raise ValueError('Служба перезапустилась. Повторите открытие.')
    addresses = run(['tailscale', 'ip', '-4']).splitlines()
    try:
        addr = ipaddress.ip_address(addresses[0])
        if addr.version != 4 or addr not in ipaddress.ip_network('100.64.0.0/10'):
            raise ValueError()
    except (IndexError, ValueError):
        raise ValueError('Адрес Tailscale не найден.')
    return {'url': 'http://' + str(addr) + ':3080/?' + urlencode({'token': token})}
