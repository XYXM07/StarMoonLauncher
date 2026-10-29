"""Saved Java servers, bounded status requests and per-launch join arguments."""
import io
import ipaddress
import json
import random
import re
import socket
import struct
import time
import uuid

from .core import LauncherError, atomic_json, read_json


def address(value):
    value = str(value).strip()
    if not value or len(value) > 255 or re.search(r'[\s/\\@?#\x00-\x1f]', value):
        raise LauncherError('请输入有效的服务器地址，例如 play.example.com:25565')
    explicit, port = False, 25565
    if value.startswith('['):
        match = re.fullmatch(r'\[([^\]]+)\](?::([0-9]+))?', value)
        if not match: raise LauncherError('IPv6 地址请使用 [地址]:端口 格式')
        host, number = match.groups()
        try: host = str(ipaddress.IPv6Address(host))
        except ValueError: raise LauncherError('无效的 IPv6 地址') from None
        if number: port, explicit = int(number), True
    elif value.count(':') > 1:
        try: host = str(ipaddress.IPv6Address(value))
        except ValueError: raise LauncherError('无效的 IPv6 地址') from None
    else:
        host, sep, number = value.partition(':')
        if sep:
            if not number.isdigit(): raise LauncherError('服务器端口必须为 1–65535')
            port, explicit = int(number), True
        try: host = host.rstrip('.').encode('idna').decode('ascii').lower()
        except UnicodeError: raise LauncherError('无效的服务器域名') from None
        if not host or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.')):
            raise LauncherError('无效的服务器域名')
    if not 1 <= port <= 65535: raise LauncherError('服务器端口必须为 1–65535')
    display = '[' + host + ']' if ':' in host else host
    return {'host': host, 'port': port, 'explicit_port': explicit,
            'address': display + (':' + str(port) if explicit else '')}


class ServerList:
    def __init__(self, store):
        self.store, self.path = store, store.root / 'servers.json'

    def list(self):
        with self.store.lock:
            items = read_json(self.path, [])
            if not isinstance(items, list): raise LauncherError('服务器列表文件格式错误')
            return items

    def save(self, data):
        location = address(data.get('address', ''))['address']
        name = str(data.get('name', '')).strip()[:80] or location
        instance = str(data.get('instance', ''))
        if instance: self.store.instance(instance)
        with self.store.lock:
            items = self.list()
            identifier = str(data.get('id') or uuid.uuid4().hex)
            if data.get('id') and not any(i['id'] == identifier for i in items): raise LauncherError('服务器记录不存在')
            if any(i['address'] == location and i['id'] != identifier for i in items): raise LauncherError('此服务器已在列表中')
            item = {'id': identifier, 'name': name, 'address': location, 'instance': instance, 'notes': str(data.get('notes', ''))[:500]}
            items = [item if i['id'] == identifier else i for i in items] if data.get('id') else items + [item]
            atomic_json(self.path, items)
            return item

    def remove(self, identifier):
        with self.store.lock:
            atomic_json(self.path, [i for i in self.list() if i['id'] != identifier])
        return {}


def srv_target(parsed, timeout):
    host, port = parsed['host'], parsed['port']
    if parsed['explicit_port']: return host, port
    try: ipaddress.ip_address(host); return host, port
    except ValueError: pass
    import dns.exception
    import dns.resolver
    try:
        answers = list(dns.resolver.resolve('_minecraft._tcp.' + host, 'SRV', lifetime=timeout))
        priority = min(record.priority for record in answers)
        choices = [record for record in answers if record.priority == priority]
        selected = random.choices(choices, weights=[record.weight or 1 for record in choices])[0]
        target = str(selected.target).rstrip('.')
        if not target: raise LauncherError('该域名未提供 Minecraft 服务')
        return target, selected.port
    except (dns.exception.DNSException, ValueError):
        return host, port


def varint(value):
    value &= 0xffffffff
    result = bytearray()
    while True:
        byte, value = value & 0x7f, value >> 7
        result.append(byte | (0x80 if value else 0))
        if not value: return bytes(result)


def read_varint(read):
    result = 0
    for shift in range(0, 35, 7):
        chunk = read(1)
        if not chunk: raise ValueError('Unexpected end of packet')
        value = chunk[0]
        result |= (value & 127) << shift
        if not value & 128: return result
    raise ValueError('Invalid VarInt')


def plain_description(value, depth=0):
    if depth > 8: return ''
    if isinstance(value, str): return re.sub(r'§.', '', value)[:2000]
    if isinstance(value, list): return ''.join(plain_description(v, depth + 1) for v in value[:100])[:2000]
    if isinstance(value, dict):
        return (plain_description(value.get('text', value.get('translate', '')), depth + 1) +
                plain_description(value.get('extra', []), depth + 1))[:2000]
    return ''


def status(value, timeout=4):
    parsed = address(value)
    started = time.monotonic()
    deadline = started + timeout
    try:
        target = srv_target(parsed, min(2, timeout))
        with socket.create_connection(target, timeout=max(.1, deadline-time.monotonic())) as connection:
            def read(count):
                data = bytearray()
                while len(data) < count:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise TimeoutError()
                    connection.settimeout(remaining)
                    chunk = connection.recv(count-len(data))
                    if not chunk: raise ValueError('Unexpected end of packet')
                    data.extend(chunk)
                return bytes(data)
            def send(packet): connection.sendall(varint(len(packet)) + packet)
            host = parsed['host'].encode('utf-8')
            send(b'\0' + varint(-1) + varint(len(host)) + host + struct.pack('>H', parsed['port']) + b'\1')
            ping_started = time.monotonic()
            send(b'\0')
            size = read_varint(read)
            if not 0 < size <= 1024 * 1024: raise ValueError('Oversized status packet')
            packet = io.BytesIO(read(size))
            if read_varint(packet.read) != 0: raise ValueError('Wrong response')
            length = read_varint(packet.read)
            if length > size: raise ValueError('Invalid string length')
            data = json.loads(packet.read(length).decode('utf-8'))
            players, version = data.get('players', {}), data.get('version', {})
            return {'online': True, 'latency': round((time.monotonic()-ping_started)*1000),
                    'players': max(0, int(players.get('online', 0))), 'max_players': max(0, int(players.get('max', 0))),
                    'version': str(version.get('name', ''))[:120], 'motd': plain_description(data.get('description', ''))}
    except (OSError, ValueError, TypeError, AttributeError, LauncherError, RecursionError):
        return {'online': False, 'error': '无法查询状态：服务器离线、超时或未开放状态查询'}


def join_arguments(profile, target):
    parsed = address(target)
    # Match actual profile capability, including snapshots and inherited modded profiles.
    modern = 'quickPlayMultiplayer' in json.dumps(profile.get('arguments', {}))
    if not modern:
        match = re.match(r'^(\d+)\.(\d+)', str(profile.get('id', '')))
        modern = bool(match and tuple(map(int, match.groups())) >= (1, 20))
    if modern: return ['--quickPlayMultiplayer', parsed['address']]
    return ['--server', parsed['host'], '--port', str(parsed['port'])]
