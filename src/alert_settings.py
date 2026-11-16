"""Validation and classification of Telegram alert settings."""
DEFAULTS = {'temperature': 80, 'disk_percent': 85, 'memory_percent': 10,
            'cpu_percent': 90, 'cpu_enabled': False, 'repeat_minutes': 30,
            'power_enabled': True, 'services_enabled': True}
LIMITS = {'temperature': (30, 120), 'disk_percent': (1, 100),
          'memory_percent': (1, 99), 'cpu_percent': (1, 100), 'repeat_minutes': (1, 1440)}
BOOLS = ('cpu_enabled', 'power_enabled', 'services_enabled')

def validate_settings(value):
    if not isinstance(value, dict):
        raise ValueError('Некорректные настройки оповещений.')
    out = dict(DEFAULTS)
    for name, (low, high) in LIMITS.items():
        raw = value.get(name, out[name])
        if isinstance(raw, bool):
            raise ValueError('Некорректное значение: ' + name)
        try:
            number = int(raw)
        except (ValueError, TypeError):
            raise ValueError('Некорректное значение: ' + name)
        if not low <= number <= high:
            raise ValueError('Недопустимое значение: ' + name)
        out[name] = number
    for name in BOOLS:
        raw = value.get(name, out[name])
        if not isinstance(raw, bool):
            raise ValueError('Некорректное значение: ' + name)
        out[name] = raw
    return out

def topic_id(value):
    value = str(value or '').strip()
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 2**31 - 1:
        raise ValueError('ID темы должен быть положительным целым числом.')
    return int(value)

def evaluate(system, services, config, desired):
    a = {}; t = system.get('temperature'); disk_total = system.get('disk_total', 0)
    memory_total = system.get('memory_total', 0); cpu = system.get('cpu')
    if t is not None and t >= config['temperature']: a['temp'] = f'Температура {t} °C'
    if disk_total > 0 and system['disk_used'] / disk_total * 100 >= config['disk_percent']:
        a['disk'] = f"Диск заполнен на {round(100 * system['disk_used'] / disk_total)}%"
    if memory_total > 0 and system['memory_available'] / memory_total * 100 < config['memory_percent']:
        a['memory'] = f"Доступно менее {config['memory_percent']}% оперативной памяти"
    if config['cpu_enabled'] and cpu is not None and cpu >= config['cpu_percent']:
        a['cpu'] = f'Загрузка CPU {cpu}%'
    if config['power_enabled'] and system.get('undervoltage_now'): a['power'] = 'Обнаружено пониженное напряжение питания'
    if config['services_enabled']:
        for item in services:
            if item['id'] in ('agent', 'portainer', 'postgres') and desired(item['id']) and item['state'] != 'running':
                a['service_' + item['id']] = item['name'] + ': сервис не работает'
    return a
