"""Explicit client instances; registration never starts or authenticates an App."""
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from types import MappingProxyType

from bridge.clients.profiles import NativeProfile
from bridge.features.auth.auth import replace_session_file, session_file_lock
from bridge.features.endpoints.registry import local_entry_id, local_home


FAMILIES = ('codex', 'claude', 'deepseek', 'zcode', 'antigravity')
ENTRY = re.compile(r'([a-z][a-z0-9-]{0,31}):([0-9a-f]{32})\Z')
RECORD_FIELDS = {'id', 'family', 'kind', 'label', 'home', 'roots'}
PROOF_FIELDS = {'path', 'identity', 'anchor', 'anchorIdentity'}
MAX_ENTRIES = 16


def _path(value):
    if (not isinstance(value, (str, Path)) or not str(value).strip()
            or re.search(r'[\x00-\x1f\x7f]', str(value))):
        raise ValueError('请选择独立配置目录')
    text = str(value).strip()
    if text.startswith(('\\\\', '//')):
        raise ValueError('独立桌面接入不使用网络或 WSL 目录')
    result = Path(text).expanduser()
    if not result.is_absolute():
        raise ValueError('独立配置目录必须是绝对路径')
    return result.resolve()


def _directory_identity(path):
    value = path.stat()
    if not stat.S_ISDIR(value.st_mode):
        raise ValueError('独立配置路径必须是目录')
    return [value.st_dev, value.st_ino]


def _proof(value, *, required=False):
    path = _path(value)
    exists = path.exists()
    if required and not exists:
        raise ValueError('独立配置目录不存在，请先选择已有目录')
    identity = _directory_identity(path) if exists else None
    anchor = path
    while not anchor.exists():
        anchor = anchor.parent
    return {'path': str(path), 'identity': identity, 'anchor': str(anchor),
            'anchorIdentity': _directory_identity(anchor)}


def instance_id(family, home):
    """Keep Codex's existing path-derived identifier convention unchanged."""
    return family + ':' + hashlib.sha256(os.path.normcase(str(home)).encode()).hexdigest()[:32]


def _overlap(left, right):
    a, b = Path(os.path.normcase(left['path'])), Path(os.path.normcase(right['path']))
    return (a == b or a in b.parents or b in a.parents or
            left['identity'] is not None and left['identity'] == right['identity'])


def _label(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 80
            or re.search(r'[\x00-\x1f\x7f]', value)):
        raise ValueError('请输入 1–80 字的接入名称，不能包含控制字符')
    return value.strip()


@dataclass(frozen=True)
class ResolvedInstance:
    id: str
    family: str
    kind: str
    label: str
    enabled: bool
    profile: NativeProfile
    gateway_root: Path
    state_root: Path
    pending_roots: tuple = ()


class Instances:
    """Own metadata only; native path semantics are supplied by trusted resolvers.

    ``defaults`` returns NativeProfile declarations for all reserved default
    roots. Codex metadata is delegated to the existing Entries object; resolving
    it here does not call Entries.get() or construct a native execution owner.
    """

    def __init__(self, gateway_root, *, resolvers=None, defaults=None, codex=None):
        self.gateway_root = Path(gateway_root).resolve()
        self.directory = self.gateway_root/'client-instances'
        self.path = self.directory/'entries.json'
        self.resolvers = dict(resolvers or {})
        self.defaults = defaults or (lambda: ())
        self.codex = codex
        self.lock = threading.RLock()

    def _family(self, value):
        if not isinstance(value, str) or value not in set(FAMILIES) | {key[0] for key in self.resolvers}:
            raise ValueError('未知客户端平台')

    def _describe(self, profile, *, family=None, kind=None, home=None, required=True):
        if not isinstance(profile, NativeProfile):
            raise ValueError('原生目录解析器未提供有效的配置声明')
        if (family is not None and profile.family != family or kind is not None and profile.kind != kind
                or not isinstance(profile.roots, dict) and not hasattr(profile.roots, 'items')):
            raise ValueError('原生配置声明与所选平台或接入方式不匹配')
        native_home = _path(profile.home)
        if home is not None and native_home != _path(home):
            raise ValueError('原生目录解析器改变了所选配置目录')
        if not profile.roots or any(not isinstance(key, str) or not re.fullmatch(r'[a-z][A-Za-z0-9]{0,39}', key)
                                    or key == 'home' for key in profile.roots):
            raise ValueError('原生配置目录声明无效')
        roots = {'home': _proof(native_home, required=required)}
        roots.update((name, _proof(path)) for name, path in profile.roots.items())
        normalized = NativeProfile(profile.family, profile.kind, native_home,
                                   MappingProxyType({name: Path(proof['path']) for name, proof in roots.items() if name != 'home'}))
        return normalized, roots

    def _native(self, family, kind, home):
        if not isinstance(kind, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', kind):
            raise ValueError('独立配置接入方式无效')
        resolver = self.resolvers.get((family, kind))
        if resolver is None:
            raise ValueError('此平台尚未提供已验证的独立配置方式')
        selected = _path(home)
        return self._describe(resolver(selected), family=family, kind=kind, home=selected)

    def _load(self):
        if any(path.is_symlink() or path.resolve() != path for path in (self.directory, self.path)):
            raise ValueError('接入登记文件不能使用符号链接')
        if not self.path.exists():
            return []
        if self.path.stat().st_size > 1024*1024:
            raise ValueError('接入登记文件过大')
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or set(value) != {'version', 'entries'}
                or type(value['version']) is not int or value['version'] != 1
                or not isinstance(value['entries'], list) or len(value['entries']) > MAX_ENTRIES):
            raise ValueError('接入登记格式无法识别')
        seen = set()
        for row in value['entries']:
            if (not isinstance(row, dict) or set(row) != RECORD_FIELDS or row['family'] in ('codex', 'zcode')
                    or not isinstance(row['id'], str) or not ENTRY.fullmatch(row['id'])
                    or row['id'].split(':')[0] != row['family'] or row['id'] in seen
                    or not isinstance(row['kind'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', row['kind'])
                    or not isinstance(row['home'], str)
                    or row['id'] != instance_id(row['family'], Path(row['home']))
                    or not isinstance(row['roots'], dict) or 'home' not in row['roots']):
                raise ValueError('接入登记身份无效')
            _label(row['label'])
            for name, proof in row['roots'].items():
                if (not isinstance(name, str) or not re.fullmatch(r'[a-z][A-Za-z0-9]{0,39}', name)
                        or not isinstance(proof, dict) or set(proof) != PROOF_FIELDS
                        or any(not isinstance(proof[field], str) or not Path(proof[field]).is_absolute() for field in ('path', 'anchor'))
                        or any(not isinstance(proof[field], list) or len(proof[field]) != 2
                               or any(type(item) is not int or item < 0 for item in proof[field])
                               for field in ('anchorIdentity',))
                        or proof['identity'] is not None and (not isinstance(proof['identity'], list)
                            or len(proof['identity']) != 2 or any(type(item) is not int or item < 0 for item in proof['identity']))):
                    raise ValueError('接入目录身份记录无效')
                if Path(proof['anchor']) not in (Path(proof['path']), *Path(proof['path']).parents):
                    raise ValueError('接入目录的父目录身份记录无效')
            if row['roots']['home']['path'] != row['home'] or row['roots']['home']['identity'] is None:
                raise ValueError('接入主目录身份记录无效')
            seen.add(row['id'])
        return value['entries']

    def _codex_rows(self):
        return self.codex.local_profiles()['profiles'] if self.codex is not None else []

    def _state_root(self, entry):
        return self.directory/entry.replace(':', '-')

    def _settings_path(self, entry):
        root = self._state_root(entry)
        path = root/'desktop-sessions/settings.json'
        if any(value.is_symlink() or value.resolve() != value for value in (self.directory, root, path.parent, path)):
            raise ValueError('实例状态目录不能使用符号链接')
        return path

    def _settings(self, entry):
        path = self._settings_path(entry)
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict) or not isinstance(value.get('enabled', {}), dict):
            raise ValueError('实例启用配置格式无效')
        return value

    def _enabled(self, row):
        value = self._settings(row['id']).get('enabled', {}).get(row['family'], False)
        if type(value) is not bool:
            raise ValueError('实例启用开关格式无效')
        return value

    def _public(self, row):
        return {**{key: row[key] for key in ('id', 'family', 'kind', 'label', 'home')},
                'enabled': self._enabled(row)}

    def _default_claims(self):
        for profile in self.defaults():
            yield self._describe(profile, required=False)[1]
        if self.codex is not None and self.codex.desktop_home is not None:
            yield {'home': _proof(self.codex.desktop_home)}

    def _check_conflicts(self, roots, rows, *, excluding=None):
        gateway = _proof(self.gateway_root)
        if any(_overlap(root, gateway) for root in roots.values()):
            raise ValueError('原生配置目录不能与网关资料目录重叠')
        for defaults in self._default_claims():
            if any(_overlap(a, b) for a in roots.values() for b in defaults.values()):
                raise ValueError('所选目录与默认接入重叠，不能重复添加或共享账号目录')
        for row in rows:
            if row['id'] == excluding:
                continue
            # Another unavailable native directory must not block this instance.
            # Its reserved paths and pinned inodes are sufficient: its own
            # resolve/pin must verify any newly created directories before use.
            if any(_overlap(a, b) for a in roots.values() for b in row['roots'].values()):
                raise ValueError('所选目录与已登记接入重叠，请使用已有入口')
        for row in self._codex_rows():
            if row['id'] == excluding:
                continue
            if any(_overlap(a, _proof(row['home'])) for a in roots.values()):
                raise ValueError('所选目录与已登记 Codex 接入重叠')

    def _write(self, rows):
        replace_session_file(self.path, json.dumps({'version': 1, 'entries': rows}, ensure_ascii=False).encode('utf-8'))

    @contextmanager
    def _registration_lock(self):
        with self.lock:
            if self.directory.is_symlink() or self.directory.resolve() != self.directory:
                raise ValueError('接入登记目录不能使用符号链接')
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with session_file_lock(self.path):
                yield

    def register(self, value):
        if not isinstance(value, dict) or set(value) != {'family', 'kind', 'label', 'home'}:
            raise ValueError('新增接入请求包含未知或缺失字段')
        family, kind = value['family'], value['kind']
        self._family(family)
        label = _label(value['label'])
        if family == 'zcode':
            raise ValueError('ZCode 仅使用默认接入，不新增第二套配置')
        if family == 'codex':
            if kind != 'codex-local' or self.codex is None:
                raise ValueError('请使用已有 Codex 独立配置接入方式')
            home = local_home(value['home'])
            with self._registration_lock():
                self._check_conflicts({'home': _proof(home, required=True)}, self._load())
                result = self.codex.save_local(str(home), label)
            return next(row for row in self.list('codex') if row['id'] == result['entry'])
        profile, roots = self._native(family, kind, value['home'])
        row = {'id': instance_id(family, profile.home), 'family': family, 'kind': kind,
               'label': label, 'home': str(profile.home), 'roots': roots}
        with self._registration_lock():
            rows = self._load()
            if len(rows) >= MAX_ENTRIES:
                raise ValueError('最多登记 16 个独立客户端配置')
            self._check_conflicts(roots, rows)
            self._write([*rows, row])
        return self._public(row)

    def list(self, family=None):
        if family is not None:
            self._family(family)
        with self.lock:
            rows = [self._public(row) for row in self._load() if family is None or row['family'] == family]
            if family in (None, 'codex'):
                rows.extend({'id': row['id'], 'family': 'codex', 'kind': 'codex-local',
                             'label': row['label'], 'home': row['home'], 'enabled': row.get('enabled', True)}
                            for row in self._codex_rows())
            return rows

    def remove(self, family, entry):
        """Forget an explicit registration, never its native or private files."""
        self._family(family)
        if not isinstance(entry, str) or not ENTRY.fullmatch(entry) or entry.split(':')[0] != family:
            raise ValueError('只能移除已添加的独立客户端')
        with self._registration_lock():
            if family == 'codex' and self.codex is not None:
                self.codex.remove_local(entry)
                return
            rows = self._load()
            row = next((row for row in rows if row['id'] == entry), None)
            if row is None:
                raise ValueError('接入实例未登记')
            if self._enabled(row):
                raise ValueError('请先停用此客户端的手机接入，再移除配置')
            self._write([row for row in rows if row['id'] != entry])

    def _checked(self, row):
        profile, roots = self._native(row['family'], row['kind'], row['home'])
        if instance_id(row['family'], profile.home) != row['id'] or set(roots) != set(row['roots']):
            raise ValueError('原生配置目录声明已变化，请重新配置')
        pending = []
        for name, saved in row['roots'].items():
            current = roots[name]
            if (current['path'] != saved['path'] or
                    _directory_identity(Path(saved['anchor'])) != saved['anchorIdentity'] or
                    saved['identity'] is not None and current['identity'] != saved['identity']):
                raise ValueError('独立配置目录身份已变化，请重新配置')
            if saved['identity'] is None and current['identity'] is not None:
                pending.append(name)
        return profile, roots, tuple(pending)

    def resolve(self, family, entry=None, *, require_enabled=True):
        self._family(family)
        if entry is None or entry == family or family == 'codex' and entry == 'desktop':
            return None
        if not isinstance(entry, str) or not ENTRY.fullmatch(entry) or entry.split(':')[0] != family:
            raise ValueError('接入实例与平台不匹配')
        if family == 'codex':
            row = next((r for r in self._codex_rows() if r['id'] == entry), None)
            if row is None:
                raise ValueError('Codex 接入实例未登记')
            if require_enabled and not row.get('enabled', True):
                raise ValueError('此接入实例已关闭')
            home = local_home(row['home'])
            if local_entry_id(home) != entry or _directory_identity(home) != row.get('directoryIdentity'):
                raise ValueError('Codex 配置目录身份已变化，请重新配置')
            self._check_conflicts({'home': _proof(home, required=True)}, self._load(), excluding=entry)
            profile = NativeProfile('codex', 'codex-local', home, MappingProxyType({'config': home}))
            return ResolvedInstance(entry, family, 'codex-local', row['label'], row.get('enabled', True),
                                    profile, self.gateway_root, self.codex.directory/entry.replace(':', '-'))
        with self.lock:
            rows = self._load()
            row = next((r for r in rows if r['id'] == entry), None)
            if row is None:
                raise ValueError('接入实例未登记')
            enabled = self._enabled(row)
            if require_enabled and not enabled:
                raise ValueError('此接入实例已关闭')
            profile, roots, pending = self._checked(row)
            self._check_conflicts(roots, rows, excluding=entry)
            return ResolvedInstance(entry, family, row['kind'], row['label'], enabled,
                                    profile, self.gateway_root, self._state_root(entry), pending)

    def configure(self, entry, enabled):
        if not isinstance(entry, str) or not ENTRY.fullmatch(entry) or type(enabled) is not bool:
            raise ValueError('独立接入开关无效')
        family = entry.split(':')[0]
        if family == 'codex':
            if self.codex is None:
                raise ValueError('Codex 接入实例未登记')
            if enabled:
                self.resolve(family, entry, require_enabled=False)
            self.codex.configure(entry, enabled)
            return next(row for row in self.list('codex') if row['id'] == entry)
        with self.lock:
            row = next((r for r in self._load() if r['id'] == entry), None)
            if row is None:
                raise ValueError('接入实例未登记')
            if enabled:
                self.resolve(family, entry, require_enabled=False)
            path = self._settings_path(entry)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with session_file_lock(path):
                if enabled:
                    self.resolve(family, entry, require_enabled=False)
                settings = self._settings(entry)
                settings['enabled'] = {**settings.get('enabled', {}), family: enabled}
                replace_session_file(path, json.dumps(settings, ensure_ascii=False).encode('utf-8'))
        return next(row for row in self.list(family) if row['id'] == entry)

    def pin_initialized_roots(self, entry):
        """Record newly created native directories; this is not runtime proof."""
        if not isinstance(entry, str) or not ENTRY.fullmatch(entry):
            raise ValueError('独立接入标识无效')
        family = entry.split(':')[0]
        if family in ('codex', 'zcode'):
            raise ValueError('此接入不使用原生目录初始化绑定')
        with self.lock, session_file_lock(self.path):
            rows = self._load()
            row = next((r for r in rows if r['id'] == entry), None)
            if row is None:
                raise ValueError('接入实例未登记')
            _, roots, pending = self._checked(row)
            self._check_conflicts(roots, rows, excluding=entry)
            if pending:
                changed = {**row, 'roots': {name: roots[name] if name in pending else proof
                                          for name, proof in row['roots'].items()}}
                self._write([changed if r['id'] == entry else r for r in rows])
        return self.resolve(family, entry, require_enabled=False)
