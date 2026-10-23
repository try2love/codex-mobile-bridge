"""Explicit WSL registration. Native host IDs keep their original meaning."""
import hashlib
import json
import threading
import os
import re
from pathlib import Path, PureWindowsPath

from bridge.app.lifecycle import private_json
from bridge.platforms.windows import wsl
from bridge.clients.codex.remote import RemoteUnavailable


def entry_id(distribution, user, home):
    key = json.dumps([distribution, user, home], ensure_ascii=False).encode('utf-8')
    return 'wsl:' + hashlib.sha256(key).hexdigest()[:32]


class Entries:
    def __init__(self, directory, desktop_home=None):
        self.directory = Path(directory) / 'codex-entries'
        self.path = self.directory / 'entries.json'
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.bridges = {}
        self.candidates = {}
        self.desktop_home = Path(desktop_home).resolve() if desktop_home else None
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('version') != 1 or not isinstance(value.get('entries'), list):
                raise ValueError('Codex 入口配置无法识别')
            self.entries = value['entries']
            for row in self.entries:
                if row.get('id') != entry_id(row['distribution'], row['user'], row['home']):
                    raise ValueError('Codex 入口身份不匹配')
        else:
            self.entries = []

    @staticmethod
    def public_row(row, enabled=False, running=False):
        return {'id': row['id'], 'label': row['label'], 'kind': 'wsl-cli',
                'distribution': row['distribution'], 'enabled': enabled, 'running': running,
                'accessManagement': 'external'}

    def public(self):
        with self.lock:
            running = set(wsl.running_distributions())
            values = {r['id']: self.public_row(r, r.get('enabled', True), r['distribution'] in running)
                      for r in self.entries}
            for key, row in self.candidates.items():
                values.setdefault(key, self.public_row(row, False, row['distribution'] in running))
            return {'wslSupported': os.name == 'nt',
                    'entries': [{'id': 'desktop', 'label': '桌面 Codex', 'kind': 'desktop',
                                 'enabled': True, 'running': True}, *values.values()]}

    def scan(self):
        # Explicit action only. No startup/periodic automatic distribution probing.
        candidates, errors = {}, []
        for distribution in wsl.running_distributions():
            try:
                info = wsl.probe(distribution)
                for item in info.get('homes', []):
                    identifier = entry_id(distribution, info['user'], item['home'])
                    candidates[identifier] = {'id': identifier, 'distribution': distribution,
                        'user': info['user'], 'home': item['home'],
                        'windowsHome': item.get('windowsHome'),
                        'label': 'WSL · ' + distribution + ' · ' + info['user'] +
                                 ('' if item['home'] == info.get('userHome', '') + '/.codex' else ' · ' + item['home'])}
            except (OSError, ValueError, TimeoutError):
                errors.append({'distribution': distribution, 'error': '未找到可读取的 Codex 环境或 Python'})
        with self.lock:
            self.candidates = candidates
        return {**self.public(), 'errors': errors}

    def enabled(self, identifier):
        with self.lock:
            return any(row['id'] == identifier and row.get('enabled', True) for row in self.entries)

    def applications(self):
        """Display inventory only; never connect or probe a daemon while polling."""
        rows = []
        for row in self.public()['entries']:
            if row['kind'] != 'wsl-cli':
                continue
            with self.lock:
                bridge = self.bridges.get(row['id'])
                server = bridge.server if bridge else None
                connected = bool(server and not server.closed and row['enabled'])
            rows.append({'id': row['id'], 'name': 'WSL Codex · ' + row['label'].removeprefix('WSL · '),
                         'kind': 'wsl-cli', 'icon': 'codex', 'installed': True, 'configured': True,
                         'enabled': row['enabled'], 'selectable': True, 'connected': connected,
                         'running': True if connected else None if row['running'] else False,
                         'distributionRunning': row['running'],
                         'setupStatus': 'ready', 'canQuitDesktop': False, 'canForceQuit': False,
                         'reason': ('可从手机新建、恢复 WSL 会话；账号与模型接入由原工具管理。'
                                    if row['running'] else '打开此应用时可启动已登记的 WSL 环境和原生共享服务。')})
        return rows

    def configure(self, identifier, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('入口开关无效')
        if not isinstance(identifier, str) or not re.fullmatch(r'wsl:[0-9a-f]{32}', identifier):
            raise ValueError('WSL 入口标识无效，请先扫描')
        with self.lock:
            row = next((r for r in self.entries if r['id'] == identifier), None)
            if row is None:
                candidate = self.candidates.get(identifier)
                if candidate is None or not enabled:
                    raise ValueError('请先扫描并选择已有 WSL 入口')
                if self.desktop_home is not None and candidate.get('windowsHome'):
                    # Linux probe canonicalizes home before wslpath conversion.
                    # Never stat a WSL UNC path: that can hang or wake a distro.
                    def normalized(path):
                        return str(PureWindowsPath(path)).casefold().rstrip('\\').replace('\\\\wsl$\\', '\\\\wsl.localhost\\', 1)
                    if normalized(candidate['windowsHome']) == normalized(self.desktop_home):
                        raise ValueError('此 WSL 入口与桌面共用同一 Codex 目录，不能重复接入')
                if len(self.entries) >= 16:
                    raise ValueError('最多登记 16 个 WSL 入口')
                row = dict(candidate)
                self.entries.append(row)
            row['enabled'] = enabled
            self.directory.mkdir(parents=True, exist_ok=True)
            private_json(self.path, {'version': 1, 'entries': self.entries})
            old = self.bridges.pop(identifier, None) if not enabled else None
        if old is not None:
            old.close()
        return self.public()

    def get(self, identifier):
        with self.lock:
            if self.closed.is_set():
                raise KeyError('网关正在停止')
            row = next((r for r in self.entries if r['id'] == identifier and r.get('enabled', True)), None)
            if row is None:
                raise KeyError('WSL 入口未登记或已关闭')
            if identifier not in self.bridges:
                from bridge.features.endpoints.wsl import WslEntry
                self.bridges[identifier] = WslEntry(row, self.directory / identifier[4:])
            return self.bridges[identifier]

    def lists(self, **options):
        with self.lock:
            identifiers = [r['id'] for r in self.entries if r.get('enabled', True)]
        groups, errors = [], []
        for identifier in identifiers:
            try:
                groups.extend(self.get(identifier).list(**options))
            except (OSError, ValueError, RemoteUnavailable) as exc:
                errors.append({'host': identifier, 'label': 'WSL', 'error': str(exc)})
        return groups, errors

    def close(self):
        self.closed.set()
        with self.lock:
            values = list(self.bridges.values())
            self.bridges.clear()
        for bridge in values:
            bridge.close()
