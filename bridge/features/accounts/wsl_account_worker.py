"""Standalone WSL account transaction. No bridge imports or CLI startup.

The gateway sends this source and a JSON request through private stdin. Public
results contain metadata and digests only; exceptions are deliberately generic.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
from urllib.parse import urlsplit


ERROR_MESSAGES = {
    'identity_changed': '账号身份已变化，请刷新后重新选择',
    'external_change': '原生配置已被外部修改，请使用原工具处理',
    'runtime_running': '此 WSL 配置仍有客户端或共享服务运行，无法保证重新载入认证；请先在原终端退出后切换',
    'runtime_unknown': '无法核验 WSL 进程状态，暂不能切换账号',
    'custom_routing': '原生配置包含其他共享认证提供商或不支持的路由，历史会话可能固定此路由，请使用原 CLI 管理',
    'static_capture_notice': '此接入包含附加认证；首次切换时将完整保留，之后可恢复。',
    'static_capture': '当前提供商使用静态 Bearer 或认证请求头，不能保存为 auth.json 登录；切换已保存 API 时会自动保留可完整恢复的原始快照',
    'profile_managed': '此接入包含外部管理、配置 profile 或系统凭据，请使用 WSL 原工具管理',
    'claude_managed': '此 Claude Code 接入由外部认证或组织策略管理',
    'native_exists': '已保存原始登录；请保留现有快照',
    'active_edit': '请先切换到其他接入，再编辑当前接入；名称可直接重命名',
    'key_origin': '修改 API 地址或认证方式后，请重新填写 API Key',
    'missing_account': '接入不存在',
    'toml_unavailable': '需要支持 TOML 核验的 WSL Python，或配置无法解析',
    'incomplete': '切换未完成；已保留原始快照，请刷新核验配置',
    'failed': 'WSL 账号操作未完成，请刷新并检查原生配置、进程及权限',
}


class AccountError(ValueError):
    pass


def _toml_loads(text):
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            if '_WSL_TOML_LOADS' not in globals():
                raise
            return _WSL_TOML_LOADS(text)
    return tomllib.loads(text)


def _bytes(path):
    if path.is_symlink():
        raise AccountError('配置文件不能是符号链接')
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 2 * 1024 * 1024:
            raise AccountError('配置文件权限或大小无法安全核验')
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write(path, value):
    if value is None:
        path.unlink(missing_ok=True)
        return
    fd, name = tempfile.mkstemp(prefix='.account-', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode()


def _account_identity(row):
    value = {key: value for key, value in (row or {}).items() if key != 'name'}
    return hashlib.sha256(_json(value)).hexdigest()


def _identity(files):
    return hashlib.sha256(_json({k: base64.b64encode(v).decode() if v is not None else None
                               for k, v in files.items()})).hexdigest()


def runtime_blockers(provider, directory, verified_updaters=()):
    """Conservative: any same-user native client blocks, including idle daemons.

    Never attempt to decide that an idle daemon will reload new auth. A failed
    inspection is uncertainty, rather than evidence that no work is running.
    """
    overrides = ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_API_KEY') if provider == 'codex' else (
        'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_OAUTH_TOKEN')
    if any(os.environ.get(key) for key in overrides):
        return ['WSL 环境变量覆盖认证，请先在原终端处理']
    for name in ('.profile', '.bash_profile', '.bashrc', '.zprofile', '.zshrc', '.zshenv'):
        try:
            text = (Path(os.path.expanduser('~'))/name).read_text()
            if any(re.search(r'(?m)^\s*(?:export\s+)?'+key+r'\s*=', text) for key in overrides):
                return ['WSL shell 配置包含认证覆盖，请使用原终端处理']
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            return ['WSL shell 认证配置无法核验']
    try:
        native_claude = set()
        native_names, native_paths = set(), set()
        if provider == 'claude':
            user_home = Path(os.path.expanduser('~'))
            candidates = [shutil.which('claude'), user_home/'.local/bin/claude', Path('/usr/local/bin/claude')]
            versions = user_home/'.local/share/claude/versions'
            if versions.is_dir():
                candidates.extend(path for path in versions.glob('[0-9]*') if path.is_file())
            for candidate in candidates:
                if not candidate:
                    continue
                path = Path(candidate).resolve()
                if not path.is_absolute() or str(path).startswith('/mnt/') or path.suffix.lower() in ('.exe', '.cmd', '.bat'):
                    continue
                try:
                    with path.open('rb') as stream:
                        if stream.read(4) != b'\x7fELF':
                            continue
                    info = path.stat()
                    native_claude.add((info.st_dev, info.st_ino))
                    native_names.update((path.name, Path(candidate).name))
                    native_paths.update((str(path), str(Path(candidate))))
                except FileNotFoundError:
                    continue
        processes = list(Path('/proc').iterdir())
        for process in processes:
            if not process.name.isdigit() or int(process.name) == os.getpid():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                args = (process/'cmdline').read_bytes().split(b'\0')
                names = [Path(os.fsdecode(arg)).name for arg in args[:3] if arg]
                relevant = ('codex' in names if provider == 'codex' else
                            any(n in ('claude', 'claude.js') for n in names) or
                            any(b'/@anthropic-ai/claude-code/' in a for a in args[:3]))
                if provider == 'claude' and not relevant and native_claude:
                    if not any(args):
                        fields = (process/'stat').read_text().rsplit(')', 1)[-1].split()
                        if fields and fields[0] == 'Z':
                            continue
                    elif (not any(name in native_names for name in names) and
                          not any(os.fsdecode(arg) in native_paths for arg in args[:3])):
                        if (process/'comm').read_text().strip() not in native_names:
                            continue
                    info = (process/'exe').stat()
                    relevant = (info.st_dev, info.st_ino) in native_claude
                if relevant:
                    executable = os.readlink(process/'exe')
                    if not executable.startswith('/') or executable.startswith('/mnt/') or executable.endswith('.exe'):
                        return ['客户端可执行文件属于外部或 Windows 接入，请使用原工具管理']
                    env = dict(item.split(b'=', 1) for item in (process/'environ').read_bytes().split(b'\0') if b'=' in item)
                    variable = b'CODEX_HOME' if provider == 'codex' else b'CLAUDE_CONFIG_DIR'
                    default = Path(os.fsdecode(env.get(b'HOME', os.fsencode(Path(os.path.expanduser('~')))))) / ('.codex' if provider == 'codex' else '.claude')
                    selected = Path(os.fsdecode(env[variable])) if variable in env else default
                    if not selected.is_absolute():
                        return ['WSL 客户端目录无法核验，暂不能切换账号']
                    if selected.resolve() != Path(directory).resolve():
                        continue
                    if provider == 'codex':
                        from bridge.platforms.codex_process_identity import codex_updater
                        expected = next((row for row in verified_updaters if row.get('pid') == int(process.name)), None)
                        if codex_updater(process, executable, args, env, directory, expected):
                            continue
                    return ['此 WSL 配置仍有客户端或共享服务运行，无法保证重新载入认证；请先在原终端退出后切换']
            except FileNotFoundError:
                if not process.exists():
                    continue
                return ['WSL 进程身份读取期间变化，暂不能切换账号']
            except OSError:
                return ['无法核验 WSL 进程状态，暂不能切换账号']
    except OSError:
        return ['无法核验 WSL 进程状态，暂不能切换账号']
    return []


class AccountStore:
    def __init__(self, provider, directory, *, guard=runtime_blockers):
        if provider not in ('codex', 'claude'):
            raise AccountError('WSL 客户端无效')
        self.provider = provider
        self.directory = Path(directory)
        if not self.directory.is_absolute() or '..' in self.directory.parts or self.directory.is_symlink():
            raise AccountError('WSL 配置目录无效')
        resolved = self.directory.resolve(strict=True)
        if resolved != self.directory or str(resolved).startswith('/mnt/'):
            raise AccountError('共享或重定向配置目录由原工具管理')
        if not resolved.is_dir() or resolved.stat().st_uid != os.getuid():
            raise AccountError('WSL 配置目录归属无法核验')
        self.root = resolved/'.codex-mobile-bridge-accounts'
        self.path = self.root/'store.json'
        self.guard = guard
        if self.root.is_symlink():
            raise AccountError('账号存储目录无效')
        if self.root.exists() and (self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077):
            raise AccountError('账号存储目录权限无效')
        raw = _bytes(self.path)
        self.store = json.loads(raw) if raw else {'version': 1, 'accounts': [], 'activeId': None}
        if self.store.get('version') != 1:
            raise AccountError('账号存储版本无法识别')

    def files(self):
        names = ('auth.json', 'config.toml') if self.provider == 'codex' else ('settings.json', '.credentials.json')
        return {name: _bytes(self.directory/name) for name in names}

    @staticmethod
    def static_auth(config):
        definition = (config.get('model_providers') or {}).get(config.get('model_provider', 'openai')) or {}
        return 'experimental_bearer_token' in definition or bool(definition.get('http_headers'))

    def capture_blockers(self, files):
        protected = self.protected(files)
        if protected:
            return protected
        if self.provider == 'codex' and self.static_auth(_toml_loads((files['config.toml'] or b'').decode())):
            return [ERROR_MESSAGES['static_capture']]
        return []

    def protected(self, files):
        if self.provider == 'codex':
            try:
                config = _toml_loads((files['config.toml'] or b'').decode())
            except (ImportError, ValueError, UnicodeError):
                return ['需要支持 TOML 核验的 WSL Python，或配置无法解析']
            definitions = config.get('model_providers') or {}
            selected = config.get('model_provider', 'openai')
            definition = definitions.get(selected) or {}
            if (not isinstance(selected, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', selected)
                    or (selected != 'openai' and selected not in definitions)
                    or not isinstance(definition, dict)
                    or definition.get('wire_api', 'responses') != 'responses'
                    or any(key != selected and isinstance(value, dict)
                           and (value.get('requires_openai_auth') or (key == 'openai' and value.get('base_url')))
                           for key, value in definitions.items())):
                return [ERROR_MESSAGES['custom_routing']]
            if ('magpie' in (files['config.toml'] or b'').decode().lower() or
                    re.search(r'(?i)https?://(?:localhost|127\.0\.0\.1|\[::1\]):3425\b', (files['config.toml'] or b'').decode()) or
                    config.get('model_catalog_json') or
                    config.get('cli_auth_credentials_store', 'file') != 'file' or
                    config.get('profile') or config.get('profiles') or config.get('credential_store') or config.get('api_key_helper') or
                    any(isinstance(v, dict) and (
                        any(v.get(key) for key in ('env_key', 'env_http_headers', 'auth', 'aws', 'query_params', 'gateway_oauth'))
                        or (key != selected and ('experimental_bearer_token' in v or v.get('http_headers')))
                        or ('experimental_bearer_token' in v and not isinstance(v['experimental_bearer_token'], str))
                        or (v.get('http_headers') and (not isinstance(v['http_headers'], dict)
                            or any(not isinstance(header, str) or header.lower() not in
                                   ('authorization', 'x-openai-actor-authorization') or not isinstance(value, str)
                                   for header, value in v['http_headers'].items()))))
                        for key, v in definitions.items())):
                return ['此接入包含外部管理、配置 profile 或系统凭据，请使用 WSL 原工具管理']
        else:
            if any(path.exists() for path in (self.directory/'managed-settings.json', Path('/etc/claude-code/managed-settings.json'))):
                return ['此 Claude Code 接入包含组织管理策略，请使用原工具管理']
            value = json.loads(files['settings.json'] or b'{}')
            if any(value.get(key) for key in ('apiKeyHelper', 'forceLoginMethod', 'forceLoginOrgUUID')) or any(
                    key in value.get('env', {}) for key in ('CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK',
                    'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')):
                return ['此 Claude Code 接入由外部认证或组织策略管理']
        return []

    def public(self):
        files = self.files()
        identity = _identity(files)
        active = self.store.get('activeId') if self.store.get('appliedIdentity') == identity else None
        if active and active != 'native' and self.store.get('appliedAccountIdentity') != _account_identity(self.row(active)):
            active = None
        rows = [{k: row[k] for k in ('id', 'name', 'kind', 'baseUrl', 'model', 'authScheme') if k in row}
                | {'active': row['id'] == active, 'keySaved': bool(row.get('key')),
                   'modelCatalogIdentity': _account_identity(row)}
                for row in self.store['accounts']]
        if self.store.get('native'):
            rows.insert(0, {'id': 'native', 'name': '原始 WSL 登录', 'kind': 'native',
                            'active': active == 'native', 'keySaved': False,
                            'identity': _identity({k: base64.b64decode(v) if v is not None else None for k, v in self.store['native'].items()})})
        protected = self.protected(files)
        configuration = list(protected)
        if self.store.get('native') and self.store.get('appliedIdentity') != identity:
            configuration.append(ERROR_MESSAGES['external_change'])
        runtime = self.guard(self.provider, self.directory)
        blockers = configuration + runtime
        restore = bool(self.store.get('native') and self.store.get('appliedIdentity') == identity and not runtime)
        return {'identity': identity, 'accounts': rows, 'activeId': active, 'available': True,
                'activeVerified': False, 'blockers': blockers,
                'configurationBlockers': configuration, 'runtimeBlockers': runtime,
                'runtimeReleaseCandidate': runtime == [ERROR_MESSAGES['runtime_running']],
                'capabilities': {'save': True, 'import': True, 'capture': not self.capture_blockers(files), 'switch': not blockers, 'restore': restore, 'models': True},
                'captureNotice': (ERROR_MESSAGES['static_capture_notice']
                                  if not self.store.get('native') and not protected and self.capture_blockers(files) else None),
                'loginGuidance': '官方登录请在此 WSL 环境的原 CLI 完成；文件切换后请重新打开 CLI，登录有效性尚未验证。'}

    def route_summary(self):
        """No credentials, process inspection, model requests, or disk writes."""
        identifier = self.store.get('activeId')
        if identifier in (None, 'native'):
            return {'managed': False}
        files = self.files()
        identity = _identity(files)
        row = self.row(identifier)
        if (not row or self.store.get('appliedIdentity') != identity
                or self.store.get('appliedAccountIdentity') != _account_identity(row)):
            return {'managed': True, 'valid': False}
        config = _toml_loads((files['config.toml'] or b'').decode())
        return {'managed': True, 'valid': not self.protected(files) and not self.static_auth(config),
                'appliedIdentity': identity, 'accountIdentity': _account_identity(row),
                'provider': config.get('model_provider', 'openai'), 'baseUrl': row['baseUrl']}

    def _save(self):
        self.root.mkdir(mode=0o700, exist_ok=True)
        _write(self.path, _json(self.store))

    def row(self, identifier):
        return next((r for r in self.store['accounts'] if r['id'] == identifier), None)

    def change(self, value):
        action = value['action']
        if action == 'save-api':
            name, model, url = value.get('name', ''), value.get('model', ''), value.get('baseUrl', '')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not isinstance(model, str) or not 1 <= len(model) <= 200 or any(c in model for c in '\r\n\0'):
                raise AccountError('接入名称或模型无效')
            if not isinstance(url, str) or len(url) > 2048 or any(c in url for c in '\r\n\0'):
                raise AccountError('API 地址无效')
            try:
                parsed = urlsplit(url)
                parsed.port
            except ValueError:
                raise AccountError('API 地址无效') from None
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or len(url) > 2000 or any(c.isspace() for c in url)
                    or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
                raise AccountError('API 地址须为 HTTPS，只有 WSL 本机服务可使用 HTTP')
            identifier = value.get('id') or uuid.uuid4().hex
            row = self.row(identifier)
            if not re.fullmatch('[0-9a-f]{32}', identifier) or (value.get('id') and row is None):
                raise AccountError('接入标识无效')
            if row and self.store.get('activeId') == identifier:
                raise AccountError(ERROR_MESSAGES['active_edit'])
            scheme = value.get('authScheme', 'x-api-key')
            if scheme not in ('x-api-key', 'bearer'):
                raise AccountError('认证方式无效')
            key = value.get('apiKey')
            if not key and row:
                if url.rstrip('/') != row.get('baseUrl') or scheme != row.get('authScheme','x-api-key'):
                    raise AccountError(ERROR_MESSAGES['key_origin'])
                key = row.get('key')
            if not isinstance(key, str) or not 1 <= len(key) <= 8192 or any(c in key for c in '\0\r\n'):
                raise AccountError('API Key 无效')
            saved = {'id': identifier, 'kind': 'api', 'name': name.strip(), 'baseUrl': url.rstrip('/'),
                     'model': model, 'key': key, 'authScheme': scheme}
            if row:
                row.update(saved)
            else:
                self.store['accounts'].append(saved)
            self._save()
        elif action == 'import-current':
            files = self.files()
            if self.store.get('activeId') not in (None, 'native') or self.store.get('native'):
                raise AccountError('已保存原始登录；请保留现有快照')
            capture = self.capture_blockers(files)
            if capture:
                raise AccountError(capture[0])
            self.store['native'] = {k: base64.b64encode(v).decode() if v is not None else None for k, v in files.items()}
            self.store.update(activeId='native', appliedIdentity=_identity(files))
            self._save()
        elif action in ('rename', 'delete'):
            row = self.row(value.get('id'))
            if not row:
                raise AccountError('接入不存在')
            if action == 'delete':
                if self.public()['activeId'] == row['id']:
                    raise AccountError('请先切换其他接入再删除')
                self.store['accounts'].remove(row)
            else:
                name = value.get('name')
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                    raise AccountError('接入名称无效')
                row['name'] = name.strip()
            self._save()
        elif action == 'switch':
            self.switch(value)
        else:
            raise AccountError('WSL 账号操作不支持')
        return self.public()

    def prepared(self, row, before):
        if row['id'] == 'native':
            native = self.store.get('native')
            if not native:
                raise AccountError('尚未保存原始登录')
            return {k: base64.b64decode(v) if v is not None else None for k, v in native.items()}
        if self.provider == 'claude':
            settings = json.loads(before['settings.json'] or b'{}')
            env = settings.setdefault('env', {})
            for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL'):
                env.pop(key, None)
            env.update({'ANTHROPIC_API_KEY' if row['authScheme'] == 'x-api-key' else 'ANTHROPIC_AUTH_TOKEN': row['key'],
                        'ANTHROPIC_BASE_URL': row['baseUrl'], 'ANTHROPIC_MODEL': row['model']})
            return {**before, 'settings.json': _json(settings)}
        text = (before['config.toml'] or b'').decode()
        config = _toml_loads(text)
        # Limit edits to conventional root assignments and the selected provider
        # table; reject dotted/multiline constructs instead of rewriting TOML.
        first = re.search(r'^\s*\[', text, re.MULTILINE)
        root, section_text = (text[:first.start()], text[first.start():]) if first else (text, '')
        keys = ('model_provider', 'model', 'cli_auth_credentials_store', 'openai_base_url', 'forced_login_method', 'service_tier')
        lines = root.splitlines(keepends=True)
        clean = []
        for line in lines:
            if any(re.match(r'^\s*'+key+r'\s*=', line) for key in keys):
                if line.count('"""') or line.count("'''"):
                    raise AccountError('复杂 TOML 配置请使用原 CLI 管理')
                continue
            clean.append(line)
        # Keep the selected provider ID so saved threads resume through the
        # same route. Only that provider's endpoint/auth fields are retargeted.
        selected = config.get('model_provider', 'openai')
        definitions = config.get('model_providers', {})
        definition = dict(definitions.get(selected) or {})
        removed = ('experimental_bearer_token', 'http_headers') if self.static_auth(config) else ()
        for key in removed:
            definition.pop(key, None)
        fields = {'base_url': row['baseUrl'], 'wire_api': 'responses',
                  'requires_openai_auth': True, 'supports_websockets': False}
        if not definition.get('name'):
            fields['name'] = 'WSL API'
        table = re.compile(r'^\s*\[model_providers\.(?:' + re.escape(selected)
                           + r'|"' + re.escape(selected) + r'"|\'' + re.escape(selected)
                           + r'\')\]\s*(?:#.*)?$', re.MULTILINE)
        if 'http_headers' in removed:
            # An explicit header subtable must be removed as a complete table,
            # rather than leaving old credentials attached to the stable ID.
            headers = re.compile(r'^\s*\[model_providers\.(?:' + re.escape(selected)
                                 + r'|"' + re.escape(selected) + r'"|\'' + re.escape(selected)
                                 + r'\')\.(?:http_headers|"http_headers"|\'http_headers\')\]\s*(?:#.*)?$', re.MULTILINE)
            header_match = headers.search(section_text)
            if header_match:
                following = re.search(r'^\s*\[', section_text[header_match.end():], re.MULTILINE)
                end = header_match.end() + following.start() if following else len(section_text)
                section_text = section_text[:header_match.start()] + section_text[end:]
        match = table.search(section_text)
        body = ''
        if match:
            following = re.search(r'^\s*\[', section_text[match.end():], re.MULTILINE)
            end = match.end() + following.start() if following else len(section_text)
            body = section_text[match.end():end]
            retained = []
            for line in body.splitlines(keepends=True):
                if any(re.match(r'^\s*(?:' + key + r'|"' + key + r'"|\'' + key + r'\')\s*=', line)
                       for key in (*fields, *removed)):
                    if line.count('"""') or line.count("'''"):
                        raise AccountError('复杂 TOML 配置请使用原 CLI 管理')
                    continue
                retained.append(line)
            body = ''.join(retained)
            section_text = section_text[:match.start()] + section_text[end:]
        result = ''.join(clean).rstrip()+'\n'
        result += 'model_provider = '+json.dumps(selected)+'\ncli_auth_credentials_store = "file"\n'
        result += 'model = '+json.dumps(row['model'])+'\nopenai_base_url = '+json.dumps(row['baseUrl'])+'\n'
        if config.get('forced_login_method'):
            result += 'forced_login_method = "api"\n'
        result += section_text.rstrip()+'\n[model_providers.'+selected+']\n'+body.strip()+'\n'
        for key, value in fields.items():
            result += key+' = '+json.dumps(value)+'\n'
        parsed = _toml_loads(result)
        expected = {key: value for key, value in config.items() if key not in keys and key != 'model_providers'}
        actual = {key: value for key, value in parsed.items() if key not in keys and key != 'model_providers'}
        expected_definitions = dict(definitions)
        expected_definitions[selected] = {**definition, **fields}
        if actual != expected or parsed.get('model_providers') != expected_definitions:
            raise AccountError('复杂 TOML 配置请使用原 CLI 管理')
        return {'config.toml': result.encode(), 'auth.json': _json({'auth_mode': 'apikey', 'OPENAI_API_KEY': row['key']})}

    def preview_switch(self, value):
        """Read-only preimage/intent proof for an explicit gateway cold handoff."""
        before = self.files()
        if value.get('expectedIdentity') != _identity(before):
            raise AccountError('账号身份已变化，请刷新后重新选择')
        restoring = value.get('id') == 'native' and self.store.get('native') and self.store.get('appliedIdentity') == _identity(before)
        if not restoring and self.protected(before):
            raise AccountError(self.protected(before)[0])
        if self.store.get('native') and self.store.get('appliedIdentity') != _identity(before):
            raise AccountError('原生配置已被外部修改，请使用原工具处理')
        row = {'id': 'native'} if value.get('id') == 'native' else self.row(value.get('id'))
        if not row:
            raise AccountError('接入不存在')
        basis = before
        if self.store.get('activeId') and self.store.get('activeId') != 'native':
            basis = {key: base64.b64decode(content) if content is not None else None
                     for key, content in self.store['native'].items()}
        return {'plannedIdentity': _identity(self.prepared(row, basis)), 'selectedAccount': row['id'],
                'provider': _toml_loads((before['config.toml'] or b'').decode()).get('model_provider', 'openai')}

    def switch(self, value):
        before = self.files()
        if value.get('expectedIdentity') != _identity(before):
            raise AccountError('账号身份已变化，请刷新后重新选择')
        restoring = (value.get('id') == 'native' and self.store.get('native')
                     and self.store.get('appliedIdentity') == _identity(before))
        blockers = ([] if restoring else self.protected(before)) + self.guard(self.provider, self.directory)
        if blockers:
            raise AccountError(blockers[0])
        if self.store.get('native') and self.store.get('appliedIdentity') != _identity(before):
            raise AccountError('原生配置已被外部修改，请使用原工具处理')
        row = {'id': 'native'} if value.get('id') == 'native' else self.row(value.get('id'))
        if not row:
            raise AccountError('接入不存在')
        # Rebase our own provider on the exact native preimage, preserving all
        # original unrelated configuration. External edits invalidate this path.
        basis = before
        if self.store.get('activeId') and self.store.get('activeId') != 'native':
            if self.store.get('appliedIdentity') != _identity(before):
                raise AccountError('原生配置已被外部修改，请使用原工具处理')
            basis = {k: base64.b64decode(v) if v is not None else None for k, v in self.store['native'].items()}
        after = self.prepared(row, basis)
        if 'native' not in self.store:
            self.store['native'] = {k: base64.b64encode(v).decode() if v is not None else None for k, v in before.items()}
            self.store.update(activeId='native', appliedIdentity=_identity(before))
            self._save()  # Recovery snapshot and its exact baseline are durable before native writes.
        if self.files() != before or self.guard(self.provider, self.directory):
            raise AccountError('WSL 状态已变化，请重新检查')
        written = []
        try:
            for name, content in after.items():
                if _bytes(self.directory/name) != before[name]:
                    raise AccountError('配置发生外部修改')
                _write(self.directory/name, content)
                written.append(name)
            if self.files() != after:
                raise AccountError('配置发生外部修改')
            self.store.update(activeId=row['id'], appliedIdentity=_identity(after),
                              appliedAccountIdentity=_account_identity(row))
            self._save()
        except Exception:
            for name in reversed(written):
                if _bytes(self.directory/name) == after[name]:
                    _write(self.directory/name, before[name])
            raise AccountError('切换未完成；已保留原始快照，请刷新核验配置') from None


def execute(request):
    guard = lambda provider, directory: runtime_blockers(provider, directory, request.get('_verifiedUpdaters') or ())
    store = AccountStore(request['provider'], request['directory'], guard=guard)
    if request.get('action') == 'read':
        return store.public()
    if request.get('action') == 'models':
        return fetch_models(store, request)
    if request.get('action') == 'preview-switch':
        return store.preview_switch(request)
    store.root.mkdir(mode=0o700, exist_ok=True)
    import fcntl
    lock = store.root/'lock'
    if lock.is_symlink():
        raise AccountError('账号锁无效')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Reload after acquiring cross-process serialization.
        store = AccountStore(request['provider'], request['directory'], guard=guard)
        return store.change(request)
    finally:
        os.close(fd)


def fetch_models(store, request):
    row = store.row(request.get('id'))
    if not row:
        return {'models': [], 'modelError': '原生登录模型请使用原 CLI 查看；此处支持已保存 API 的模型目录'}
    identity = _account_identity(row)
    if request.get('modelIdentity') != identity:
        raise AccountError('模型接入已变化，请刷新')
    url = row['baseUrl'].rstrip('/')
    headers = None
    if store.provider == 'claude':
        if not urlsplit(url).path.rstrip('/').endswith('/v1'):
            url += '/v1'
        auth = {'x-api-key': row['key']} if row['authScheme'] == 'x-api-key' else {'Authorization': 'Bearer '+row['key']}
        headers = {**auth, 'anthropic-version': '2023-06-01'}
    try:
        # Injected shared parser retains display names and reasoning metadata.
        models = model_catalog(url, row['key'], headers=headers)
        fresh = AccountStore(store.provider, str(store.directory))
        if _account_identity(fresh.row(row['id'])) != identity:
            return {'models': [], 'modelError': '模型接入已变化，请刷新', 'identityChanged': True}
        return {'models': models}
    except Exception:
        return {'models': [], 'modelError': '模型目录读取失败，请检查 API 权限、网络或手动填写模型 ID'}
