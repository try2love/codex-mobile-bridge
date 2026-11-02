"""Explicit Codex entries; discovery never starts a native execution owner."""
import hashlib
import json
import threading
import sys
import os
import re
from pathlib import Path, PureWindowsPath

try:
    from tomllib import loads as load_toml
except ModuleNotFoundError:  # Python 3.9 and 3.10 gateway builds
    from bridge.vendor.wsl_tomli import loads as load_toml

from bridge.app.lifecycle import private_json
from bridge.clients.codex.remote import RemoteUnavailable


def codex_list_hosts(ssh_hosts, entries):
    """Default Codex aggregates SSH/WSL; independent homes own their own lists."""
    return {**ssh_hosts, **{key: row for key, row in entries.items()
                          if row.get('kind') != 'codex-local'}}


def entry_id(distribution, user, home):
    key = json.dumps([distribution, user, home], ensure_ascii=False).encode('utf-8')
    return 'wsl:' + hashlib.sha256(key).hexdigest()[:32]


def local_home(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('请选择 Codex 配置目录')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('Codex 配置目录必须是绝对路径')
    # UNC WSL homes belong to the WSL adapter; stat can wake a stopped distro.
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('请通过 WSL 扫描登记 WSL 目录；本机入口不使用网络目录')
    path = path.resolve(strict=True)
    if not path.is_dir() or not any((path/name).exists() for name in ('config.toml', 'auth.json', 'sessions', 'app-server-control')):
        raise ValueError('所选文件夹不是已有的 Codex 配置目录')
    config = path/'config.toml'
    if config.is_file():
        try:
            settings = load_toml(config.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise ValueError('无法读取所选目录的 config.toml') from exc
        sqlite = settings.get('sqlite_home')
        if sqlite and Path(sqlite).expanduser().resolve() != path:
            raise ValueError('此配置的 sqlite_home 位于其他目录，暂不支持独立接入')
    return path


def local_entry_id(home):
    return 'codex:' + hashlib.sha256(os.path.normcase(str(Path(home))).encode()).hexdigest()[:32]


class Entries:
    def __init__(self, directory, desktop_home=None, local_factory=None):
        self.directory = Path(directory) / 'codex-entries'
        self.path = self.directory / 'entries.json'
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.bridges = {}
        self.candidates = {}
        self.wsl = None
        if sys.platform == 'win32':
            from bridge.platforms.windows import wsl
            self.wsl = wsl
        self.local_factory = local_factory
        self.desktop_home = Path(desktop_home).resolve() if desktop_home else None
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('version') != 1 or not isinstance(value.get('entries'), list):
                raise ValueError('Codex 入口配置无法识别')
            self.entries = value['entries']
            for row in self.entries:
                expected = local_entry_id(row['home']) if row.get('kind') == 'codex-local' else entry_id(row['distribution'], row['user'], row['home'])
                if row.get('id') != expected:
                    raise ValueError('Codex 入口身份不匹配')
        else:
            self.entries = []

    @staticmethod
    def public_row(row, enabled=False, running=False):
        return {'id': row['id'], 'label': row['label'], 'kind': row.get('kind', 'wsl-cli'),
                'distribution': row.get('distribution'), 'enabled': enabled, 'running': running,
                'accessManagement': 'external'}

    def public(self):
        with self.lock:
            running = set(self.wsl.running_distributions()) if self.wsl else set()
            values = {r['id']: self.public_row(r, r.get('enabled', True), r.get('kind') == 'codex-local' or r.get('distribution') in running)
                      for r in self.entries}
            for key, row in self.candidates.items():
                values.setdefault(key, self.public_row(row, False, row.get('kind') == 'codex-local' or row.get('distribution') in running))
            return {'wslSupported': self.wsl is not None,
                    'entries': [{'id': 'desktop', 'label': '桌面 Codex', 'kind': 'desktop',
                                 'enabled': True, 'running': True}, *values.values()]}

    def scan(self):
        # Explicit action only. No startup/periodic automatic distribution probing.
        if not self.wsl:
            raise ValueError('当前电脑不支持 WSL 应用')
        candidates, errors = {}, []
        for distribution in self.wsl.running_distributions():
            try:
                info = self.wsl.probe(distribution)
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

    def local_profiles(self, scan=False):
        with self.lock:
            rows = [dict(row) for row in self.entries if row.get('kind') == 'codex-local']
        candidates, seen = [], {row['id'] for row in rows}
        if scan:
            # Explicit, shallow and bounded. Never recursively search disks or read credentials.
            for parent in {Path.home(), self.desktop_home.parent if self.desktop_home else Path.home()}:
                for candidate in sorted(parent.glob('.codex*'))[:256]:
                    try:
                        home = local_home(str(candidate)); identifier = local_entry_id(home)
                        if identifier in seen or (self.desktop_home and home.samefile(self.desktop_home)):
                            continue
                        seen.add(identifier)
                        candidates.append({'id': identifier, 'home': str(home), 'label': 'Codex · ' + home.name})
                    except (OSError, ValueError):
                        continue
        return {'profiles': rows, 'candidates': candidates}

    def save_local(self, home, label):
        home = local_home(home)
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80:
            raise ValueError('请输入 1–80 字的接入名称')
        identifier = local_entry_id(home)
        with self.lock:
            if self.desktop_home and home.samefile(self.desktop_home):
                raise ValueError('此目录已由默认 Codex 接入使用，不能重复添加')
            if any(row['id'] == identifier or (row.get('kind') == 'codex-local' and
                    Path(row['home']).exists() and home.samefile(row['home'])) for row in self.entries):
                raise ValueError('此 Codex 配置目录已登记，请使用已有入口')
            if len(self.entries) >= 16:
                raise ValueError('最多登记 16 个 Codex 入口')
            self.entries.append({'id': identifier, 'kind': 'codex-local', 'home': str(home),
                                 'label': label.strip(), 'enabled': True,
                                 'directoryIdentity': [home.stat().st_dev, home.stat().st_ino]})
            self.directory.mkdir(parents=True, exist_ok=True)
            private_json(self.path, {'version': 1, 'entries': self.entries})
        return {'entry': identifier, **self.local_profiles()}

    def enabled(self, identifier):
        with self.lock:
            return any(row['id'] == identifier and row.get('enabled', True) for row in self.entries)

    def hosts(self):
        with self.lock:
            return {row['id']: {'label': row['label'], 'kind': row.get('kind', 'wsl-cli')}
                    for row in self.entries if row.get('enabled', True)}

    def applications(self):
        """Display inventory only; never connect or probe a daemon while polling."""
        rows = []
        for row in self.public()['entries']:
            if row['kind'] == 'codex-local':
                with self.lock:
                    bridge = self.bridges.get(row['id'])
                    connected = bool(bridge and any(session.connected for session in list(bridge.live.values())))
                rows.append({'id': row['id'], 'name': row['label'], 'kind': 'codex-local', 'icon': 'codex',
                             'installed': True, 'configured': True, 'enabled': row['enabled'], 'selectable': True,
                             'connected': connected, 'running': True if connected else None, 'setupStatus': 'ready',
                             'canQuitDesktop': False, 'canForceQuit': False,
                             'description': '独立 Codex 配置 · VS Code / CLI',
                             'capabilities': {'create': True, 'accounts': True, 'notifications': True},
                             'reason': '独立管理账号、项目与会话。'})
                continue
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
                         'reason': ('可从手机新建、恢复 WSL 会话，并在网关管理 API 接入；官方登录仍在原 Codex CLI 完成。'
                                    if row['running'] else '打开此应用时可启动已登记的 WSL 环境和原生共享服务。')})
        return rows

    def configure(self, identifier, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('入口开关无效')
        if not isinstance(identifier, str) or not re.fullmatch(r'(?:wsl|codex):[0-9a-f]{32}', identifier):
            raise ValueError('Codex 入口标识无效，请先扫描或添加配置')
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

    def check_ipc_scope(self, identifier, thread_id, default_store):
        # Windows shares one native IPC pipe. A cloned UUID cannot identify a home.
        from bridge.features.sessions.store import SessionStore, StoreUnavailable
        from bridge.clients.codex.ipc import IPCError
        with self.lock:
            homes = [row['home'] for row in self.entries if row.get('kind') == 'codex-local' and row['id'] != identifier]
        for store in [default_store, *(SessionStore(home) for home in homes)]:
            try:
                if not any(store.home.glob('state_*.sqlite')):
                    continue
                store.get(thread_id)
            except KeyError:
                continue
            except (OSError, StoreUnavailable) as exc:
                raise IPCError('无法确认共享 IPC 中的配置归属，未连接其他执行器') from exc
            raise IPCError('其他 Codex 配置中存在同 ID 会话，无法确认原 owner 归属；未连接其他执行器')

    def get(self, identifier, *, management=False):
        with self.lock:
            if self.closed.is_set():
                raise KeyError('网关正在停止')
            row = next((r for r in self.entries if r['id'] == identifier and (r.get('enabled', True) or management and r.get('kind') == 'codex-local')), None)
            if row is None:
                raise KeyError('Codex 入口未登记或已关闭')
            if row.get('kind') == 'codex-local':
                home = local_home(row['home'])
                if (local_entry_id(home) != identifier or (self.desktop_home and home.samefile(self.desktop_home))
                        or row.get('directoryIdentity') != [home.stat().st_dev, home.stat().st_ino]):
                    raise ValueError('Codex 配置目录身份已变化，请重新配置')
            if identifier not in self.bridges:
                if row.get('kind') == 'codex-local':
                    if self.local_factory is None:
                        raise ValueError('本机配置接入不可用')
                    self.bridges[identifier] = self.local_factory(row, self.directory / identifier.replace(':', '-'))
                else:
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
