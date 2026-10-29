"""Build-time publisher settings; private keys never belong in public sources."""
import json
import os
import sys
import uuid
from functools import lru_cache
from pathlib import Path


def _read(path):
    if not path.is_file(): return {}
    settings = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(settings, dict): raise ValueError('Publisher settings must be an object')
    return settings


def build_settings(root, environ=None):
    root = Path(root)
    environ = os.environ if environ is None else environ
    public, private = _read(root / 'publisher.json'), _read(root / 'publisher.local.json')
    client_id = str(environ.get('BLOCKCRAFT_MS_CLIENT_ID') or public.get('microsoft_client_id') or '').strip()
    key = str(environ.get('STARMOON_CURSEFORGE_API_KEY') or private.get('curseforge_api_key') or '').strip()
    if any(ord(char) < 32 or ord(char) > 126 for char in key) or len(key) > 4000:
        raise ValueError('Invalid CurseForge publisher key format')
    result = {'microsoft_client_id': str(uuid.UUID(client_id)) if client_id else ''}
    if key: result['curseforge_api_key'] = key
    return result


@lru_cache(maxsize=1)
def _runtime_settings():
    if getattr(sys, 'frozen', False):
        return _read(Path(sys._MEIPASS) / 'publisher.json')
    return build_settings(Path(__file__).resolve().parents[1])


def curseforge_key():
    return str(_runtime_settings().get('curseforge_api_key', '')).strip()
