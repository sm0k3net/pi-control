"""Persistent, local notification inbox."""
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DB = Path(__file__).resolve().parent / 'platform-events.sqlite3'
DEFAULTS = {'cpu_percent': 90, 'cpu_minutes': 5, 'temperature': 80,
            'disk_percent': 90, 'vpn': True, 'backup': True}


def connect():
    conn = sqlite3.connect(DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, created INTEGER NOT NULL, kind TEXT NOT NULL,
            level TEXT NOT NULL, message TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS conditions (key TEXT PRIMARY KEY, since INTEGER NOT NULL, notified INTEGER NOT NULL);
    """)
    return conn


def settings(conn):
    values = dict(DEFAULTS)
    for key, value in conn.execute('SELECT name,value FROM settings'):
        if key in values:
            values[key] = bool(value) if isinstance(DEFAULTS[key], bool) else value
    return values


def insert(conn, kind, level, message, now):
    conn.execute('INSERT INTO events (created,kind,level,message) VALUES (?,?,?,?)',
                 (now, kind, level, message))
    conn.execute('DELETE FROM events WHERE id <= (SELECT max(id)-1000 FROM events)')


def push(kind, level, message):
    with closing(connect()) as conn, conn:
        if kind == 'backup' and not settings(conn)['backup']:
            return
        insert(conn, kind, level, message, int(time.time()))


def list_events():
    with closing(connect()) as conn:
        return {'items': [dict(row) for row in conn.execute(
            'SELECT * FROM events ORDER BY id DESC LIMIT 100')],
            'unread': conn.execute('SELECT count(*) FROM events WHERE seen=0').fetchone()[0],
            'settings': settings(conn)}


def action(data):
    with closing(connect()) as conn, conn:
        op = data.get('op')
        if op == 'read':
            ident = data.get('id')
            if type(ident) is not int or ident < 1:
                raise ValueError('Некорректный номер уведомления.')
            conn.execute('UPDATE events SET seen=1 WHERE id=?', (ident,))
        elif op == 'read-all':
            conn.execute('UPDATE events SET seen=1')
        elif op == 'settings':
            values = {}
            for key, (low, high) in {
                'cpu_percent': (10, 100), 'cpu_minutes': (2, 60),
                'temperature': (40, 100), 'disk_percent': (50, 100)
            }.items():
                value = data.get(key)
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f'Допустимый диапазон для {key}: {low}–{high}.')
                values[key] = value
            for key in ('vpn', 'backup'):
                values[key] = int(data.get(key) is True)
            conn.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', values.items())
        else:
            raise ValueError('Неизвестное действие.')
    return list_events()


def condition(conn, key, active, duration, message, now):
    row = conn.execute('SELECT since,notified FROM conditions WHERE key=?', (key,)).fetchone()
    if not active:
        if row and row['notified']:
            insert(conn, key, 'info', {
                'vpn': 'VPN восстановлен или выключен.',
                'cpu': 'Нагрузка CPU вернулась в норму.',
                'temperature': 'Температура вернулась в норму.',
                'disk': 'Использование диска ниже порога.',
            }[key], now)
        conn.execute('DELETE FROM conditions WHERE key=?', (key,))
        return
    since = row['since'] if row else now
    notified = bool(row and row['notified'])
    if not notified and now - since >= duration:
        insert(conn, key, 'warning', message, now)
        notified = True
    conn.execute('INSERT OR REPLACE INTO conditions VALUES (?,?,?)', (key, since, notified))


def monitor(cpu, temperature, disk, vpn_unavailable):
    now = int(time.time())
    with closing(connect()) as conn, conn:
        cfg = settings(conn)
        if cpu is not None:
            condition(conn, 'cpu', cpu >= cfg['cpu_percent'], cfg['cpu_minutes'] * 60,
                      f"Загрузка CPU держится выше порога {cfg['cpu_percent']}%.", now)
        if temperature is not None:
            condition(conn, 'temperature', temperature >= cfg['temperature'], 60,
                      f"Температура достигла {cfg['temperature']} °C.", now)
        condition(conn, 'disk', disk >= cfg['disk_percent'], 60,
                  f"Заполнение диска достигло {cfg['disk_percent']}%.", now)
        if vpn_unavailable is not None:
            condition(conn, 'vpn', cfg['vpn'] and vpn_unavailable, 120,
                      'Gluetun не готов, хотя VPN должен работать.', now)
