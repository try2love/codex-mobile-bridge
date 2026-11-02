"""Guarded local CLI account selection; never touches SSH profiles."""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from bridge.clients.desktop_app import DesktopApp
from bridge.features.accounts.accounts import ManagedRPC, private_bytes, private_json, token_owner


CLAUDE_ENV_KEYS = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL')


class LocalCliSync:
    def __init__(self, bridge):
        self.bridge = bridge
        self._claude_backup = None
        self._claude_created_dir = None

    def _accounts(self):
        return self.bridge.accounts

    def _codex_runtime(self):
        accounts = self._accounts()
        if accounts.independent:
            return False, None
        executable = accounts.index.get('desktopExecutable') or DesktopApp.discover(accounts.runtime)
        app = DesktopApp(executable, accounts.home) if executable else None
        return bool(app and app.processes()), app

    def prepare_profile_switch(self):
        """An explicit account switch may close only this idle independent runtime."""
        accounts = self._accounts()
        if not accounts.independent:
            return
        accounts.idle()
        cli = self.bridge.cli_sessions
        status = cli.daemon_status()
        if status == 'stopped':
            return
        if status != 'running':
            raise ValueError('此配置的原生服务状态未知，未切换账号')
        from bridge.clients.codex.cli_session import _managed_cli
        from bridge.platforms.codex_daemon_env import read_daemon_environment, stop_daemon_gracefully
        executable = _managed_cli(accounts.home)
        if executable is None:
            raise ValueError('此配置的原生运行组件不可用，未切换账号')
        identity = read_daemon_environment(accounts.home, executable, ())[1]
        self.check_idle_daemon(cli.client, accounts.home)
        accounts.idle()
        stop_daemon_gracefully(accounts.home, executable, identity)
        cli.close()
        # A native watchdog/restart must not be mistaken for a stopped runtime.
        if cli.daemon_status() != 'stopped':
            raise ValueError('此配置的原生服务未确认退出，未切换账号')

    @staticmethod
    def check_idle_daemon(client, home):
        from bridge.features.sessions.create import check_archive_runtime
        from bridge.clients.codex.app_server import AppServerUnsupported
        info = client.connect()
        if Path(info['codexHome']).resolve() != home.resolve():
            raise ValueError('原生服务不属于此配置，未切换账号')
        def request(method, params):
            return client.request(method, params, replay_safe=True)
        identifiers = client.loaded_threads()
        def check(threads):
            blockers = []
            for identifier in threads:
                try:
                    check_archive_runtime(request, [identifier])
                    tasks = request('thread/backgroundTerminals/list', {'threadId': identifier, 'limit': 1})
                    if not isinstance(tasks.get('data'), list) or tasks['data'] or tasks.get('nextCursor'):
                        raise ValueError('有后台任务或状态未知')
                    try:
                        goal = request('thread/goal/get', {'threadId': identifier})['goal']
                    except AppServerUnsupported:
                        goal = None
                    if goal and goal.get('status') == 'active':
                        raise ValueError('有运行中的目标')
                except ValueError as error:
                    blockers.append(f"{identifier}：{error}")
            if blockers:
                raise ValueError('；'.join(blockers))
        check(identifiers)
        if client.loaded_threads() != identifiers:
            raise ValueError('此配置的会话已变化，请重新检查')
        check(identifiers)


    def _codex_idle(self):
        cli = self.bridge.cli_sessions
        with cli.lock:
            if cli.joined or cli.attaching or cli.requests:
                raise ValueError('请先结束或断开本机 Codex CLI 会话后再切换接入')
        if self._accounts().blockers():
            raise ValueError('有任务或待发送消息，请先处理后再切换接入')
        if cli.daemon_may_be_running():
            raise ValueError('本机 Codex CLI 后台服务仍在运行或状态未知，请先正常结束后再切换')

    def _claude_dir(self, row):
        candidate = Path(row['directory']).expanduser()
        path = candidate.resolve()
        if candidate.is_symlink():
            raise ValueError('Claude Code 配置目录不可用')
        if not path.is_dir():
            from bridge.clients.claude.cli_session import _profile
            native = _profile()
            default = (Path.home()/'.claude').resolve()
            if (row['kind'] != 'api' or path != default or not native
                    or not native['origin'].startswith('default:')
                    or Path(native['directory']).resolve() != path or path.exists()):
                raise ValueError('Claude Code 配置目录不可用')
        return path

    def _claude_key(self, row):
        path = Path(row['secretPath']).resolve(strict=True)
        base = (self.bridge.data_dir/'cli-access'/'claude').resolve()
        if path.parent.parent != base or path.parent.name != row['id'] or path.name != 'api.json':
            raise ValueError('Claude API 密钥不属于本机接入管理器')
        key = json.loads(path.read_text(encoding='utf-8'))['key']
        if not isinstance(key, str) or not key:
            raise ValueError('Claude Code API Key 不可用')
        return key

    def _claude_baseline(self, directory):
        digest = hashlib.sha256(str(directory).encode()).hexdigest()
        return self.bridge.data_dir/'cli-access'/'claude-baselines'/(digest+'.json')

    @staticmethod
    def _claude_settings(directory):
        path = directory/'settings.json'
        settings = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if not isinstance(settings, dict) or not isinstance(settings.get('env', {}), dict):
            raise ValueError('Claude Code 设置无法安全合并')
        return path, settings

    def _close_claude_idle(self):
        manager = getattr(self.bridge, 'desktop_sessions', None)
        cli = manager.adapters['claude'].cli if manager else None
        if not cli:
            return
        with cli.lock:
            for owned in cli.owned.values():
                if owned.process.poll() is None:
                    with owned.condition:
                        if owned.active or owned.pending:
                            raise ValueError('请先结束已继续的本机 Claude Code 会话再切换接入')
            for native, owned in list(cli.owned.items()):
                if owned.process.poll() is None:
                    owned.close()
                cli.owned.pop(native, None)
            cli.auth_cache = None

    @staticmethod
    def _claude_auth(directory, expected):
        from bridge.clients.claude.cli_session import _profile, managed_api_env
        profile = _profile()
        if not profile:
            raise ValueError('找不到 Claude Code CLI')
        env = managed_api_env() if expected else None
        if env is None:
            import os
            env = os.environ.copy()
        env['CLAUDE_CONFIG_DIR'] = str(directory)
        try:
            result = subprocess.run([profile['binary'], 'auth', 'status', '--json'],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=10)
            status = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise ValueError('Claude Code CLI 认证状态无法核验') from exc
        if not isinstance(status, dict):
            raise ValueError('Claude Code CLI 认证状态无法核验')
        if (result.returncode or status.get('loggedIn') is not True
                or expected and status.get('authMethod') != expected):
            raise ValueError('Claude Code CLI 未识别所选认证方式')
        if 'configDirectory' not in status:
            # Older Claude Code versions omit the directory in auth status.
            # Require a negative control to prove this invocation uses the
            # selected CLAUDE_CONFIG_DIR rather than another local profile.
            try:
                with tempfile.TemporaryDirectory(prefix='.cmb-claude-auth-', dir=directory.parent) as control_dir:
                    control_env = {**env, 'CLAUDE_CONFIG_DIR': control_dir}
                    control = subprocess.run([profile['binary'], 'auth', 'status', '--json'],
                                             env=control_env, capture_output=True, text=True, encoding='utf-8', timeout=10)
                    control_status = json.loads(control.stdout)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                raise ValueError('Claude Code CLI 认证状态无法核验') from exc
            if (control.returncode != 1 or not isinstance(control_status, dict)
                    or control_status.get('loggedIn') is not False
                    or control_status.get('authMethod') != 'none'):
                raise ValueError('Claude Code CLI 配置目录无法核验')
        else:
            config_dir = status['configDirectory']
            if not isinstance(config_dir, str) or Path(config_dir).resolve() != directory.resolve():
                raise ValueError('Claude Code CLI 配置目录无法核验')
        return status

    def preflight(self, provider):
        if provider == 'codex':
            running, _ = self._codex_runtime()
            self._codex_idle()
            return {'sharedWithDesktop': True, 'desktopRunning': running,
                    'needsDesktopSwitch': running}
        if provider == 'claude':
            adapter = self.bridge.desktop_sessions.adapters['claude'] if hasattr(self.bridge, 'desktop_sessions') else None
            if adapter:
                with adapter.cli.lock:
                    for owner in adapter.cli.owned.values():
                        if owner.process.poll() is None:
                            with owner.condition:
                                if owner.active or owner.pending:
                                    raise ValueError('请先结束已继续的本机 Claude Code 会话再切换接入')
            return {'sharedWithDesktop': False, 'desktopRunning': False}
        raise ValueError('不支持的本机 CLI 客户端')

    def snapshot(self, provider):
        if provider == 'codex':
            accounts = self._accounts()
            return {'files': accounts.snapshot_files(), 'activeId': accounts.index.get('activeId')}
        if provider == 'claude':
            return {}  # Profile selection is held by CliAccess; native profiles are read-only.
        raise ValueError('不支持的本机 CLI 客户端')

    def apply(self, provider, row):
        if provider == 'codex':
            accounts = self._accounts()
            current = accounts.info.current()
            if current.get('id') == row['id'] and accounts.active_matches():
                return {'status': 'applied', 'sharedWithDesktop': True, 'mutated': False}
            running, _ = self._codex_runtime()
            if running:
                raise ValueError('Codex Desktop 正在运行，请先通过桌面账号切换流程')
            self._codex_idle()
            with accounts.gate, accounts.lock:
                accounts.check_ready()
                accounts.idle()
                before = accounts.snapshot_files()
                old_id = accounts.index.get('activeId')
                folder = accounts.prepare(row, before)
                try:
                    private_bytes(accounts.home/'config.toml', (folder/'config.toml').read_bytes())
                    private_bytes(accounts.home/'auth.json', (folder/'auth.json').read_bytes())
                    self.verify(provider, row)
                    accounts.mark_active(row['id'])
                except Exception:
                    accounts.restore_files(before)
                    if old_id:
                        accounts.mark_active(old_id)
                    else:
                        accounts.index['activeId'] = None
                        accounts.index['activeStamp'] = None
                        accounts.save()
                    raise
                finally:
                    import shutil
                    shutil.rmtree(folder, ignore_errors=True)
            return {'status': 'applied', 'sharedWithDesktop': True, 'mutated': True}
        if provider == 'claude':
            directory = self._claude_dir(row)
            self._close_claude_idle()
            from bridge.clients.claude.session_registry import assert_profile_idle
            assert_profile_idle(directory)
            if not directory.exists():
                directory.mkdir(mode=0o700)
                self._claude_created_dir = directory
            settings_path, settings = self._claude_settings(directory)
            original = settings_path.read_bytes() if settings_path.is_file() else None
            env = dict(settings.get('env') or {})
            baseline = self._claude_baseline(directory)
            if row['kind'] == 'native':
                if baseline.is_file():
                    baseline_content = baseline.read_bytes()
                    saved = json.loads(baseline_content)
                    for key in CLAUDE_ENV_KEYS:
                        if key in saved:
                            env[key] = saved[key]
                        else:
                            env.pop(key, None)
                    settings['env'] = env
                    private_json(settings_path, settings)
                    self._claude_backup = (settings_path, original, baseline, None, baseline_content)
                return {'status': 'selected', 'directory': str(directory)}
            if row['kind'] != 'api':
                raise ValueError('不支持的 Claude Code 接入')
            key = self._claude_key(row)
            created = None
            if not baseline.is_file():
                private_json(baseline, {key: env[key] for key in CLAUDE_ENV_KEYS if key in env})
                created = baseline
            for name in CLAUDE_ENV_KEYS:
                env.pop(name, None)
            env['ANTHROPIC_BASE_URL'] = row['baseUrl']
            env['ANTHROPIC_AUTH_TOKEN' if row.get('authScheme') == 'bearer' else 'ANTHROPIC_API_KEY'] = key
            if row.get('model'):
                env['ANTHROPIC_MODEL'] = row['model']
            settings['env'] = env
            self._claude_backup = (settings_path, original, baseline, created, None)
            private_json(settings_path, settings)
            return {'status': 'applied', 'directory': str(directory)}
        raise ValueError('不支持的本机 CLI 客户端')

    def verify(self, provider, row):
        if provider == 'codex':
            accounts = self._accounts()
            with ManagedRPC(accounts.home, accounts.runtime) as rpc:
                config = rpc.request('config/read', {'includeLayers': False}).get('config', {})
                auth = rpc.request('account/read', {'refreshToken': False}).get('account') or {}
            expected = 'openai' if row['kind'] == 'chatgpt' else 'bridge_api'
            if (config.get('model_provider') or 'openai') != expected:
                raise ValueError('本机 Codex CLI 模型提供商未生效')
            if row['kind'] == 'chatgpt':
                tokens = json.loads((accounts.home/'auth.json').read_text(encoding='utf-8')).get('tokens') or {}
                if (auth.get('type') != 'chatgpt' or auth.get('email') != row.get('email')
                        or token_owner({'tokens': tokens}) != row.get('tokenOwner')):
                    raise ValueError('本机 Codex CLI 账号身份未生效')
            else:
                provider_config = (config.get('model_providers') or {}).get('bridge_api') or {}
                secret = json.loads((accounts.directory(row['id'])/'api.json').read_text(encoding='utf-8'))['key']
                actual = json.loads((accounts.home/'auth.json').read_text(encoding='utf-8')).get('OPENAI_API_KEY')
                if (auth.get('type') != 'apiKey' or provider_config.get('base_url') != row['baseUrl']
                        or config.get('openai_base_url') != row['baseUrl']
                        or config.get('model') != row['model'] or actual != secret):
                    raise ValueError('本机 Codex CLI API 接入未生效')
            return {'status': 'verified', 'sharedWithDesktop': True}
        if provider == 'claude':
            directory = self._claude_dir(row)
            if row['kind'] == 'native':
                self._claude_auth(directory, None)
                baseline = self._claude_baseline(directory)
                baseline.unlink(missing_ok=True)
                return {'status': 'configured', 'directory': str(directory),
                        'authentication': 'native-profile'}
            key = self._claude_key(row)
            _, settings = self._claude_settings(directory)
            env = settings.get('env') or {}
            credential = 'ANTHROPIC_AUTH_TOKEN' if row.get('authScheme') == 'bearer' else 'ANTHROPIC_API_KEY'
            if (env.get('ANTHROPIC_BASE_URL') != row['baseUrl'] or env.get(credential) != key
                    or env.get('ANTHROPIC_MODEL', '') != row.get('model', '')
                    or any(env.get(name) for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN') if name != credential)):
                raise ValueError('Claude Code CLI API 配置未生效')
            self._claude_auth(directory, 'oauth_token' if row.get('authScheme') == 'bearer' else 'api_key')
            return {'status': 'configured', 'directory': str(directory),
                    'authentication': 'configured'}
        raise ValueError('不支持的本机 CLI 客户端')

    def restore(self, provider, snapshot):
        if provider == 'codex':
            accounts = self._accounts()
            self._codex_idle()
            with accounts.gate, accounts.lock:
                accounts.restore_files(snapshot['files'])
                if snapshot.get('activeId'):
                    accounts.mark_active(snapshot['activeId'])
                else:
                    accounts.index['activeId'] = None
                    accounts.index['activeStamp'] = None
                    accounts.save()
            return {'status': 'restored'}
        if provider == 'claude':
            if self._claude_backup:
                path, content, baseline, created, baseline_content = self._claude_backup
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    private_bytes(path, content)
                if created:
                    created.unlink(missing_ok=True)
                elif baseline_content is not None:
                    private_bytes(baseline, baseline_content)
                self._claude_backup = None
            if self._claude_created_dir:
                try:
                    self._claude_created_dir.rmdir()
                except OSError:
                    pass  # Preserve files created by the native CLI or another writer.
                self._claude_created_dir = None
            return {'status': 'restored'}
        raise ValueError('不支持的本机 CLI 客户端')

    def import_desktop_claude(self, desktop_id, store=None):
        """Map a verified Desktop API profile; never transfer Desktop OAuth cookies."""
        if store is None:
            manager = getattr(self.bridge, 'desktop_sessions', None)
            if manager is None:
                raise ValueError('Claude Desktop 账号管理器不可用')
            store = manager._account_store('claude')
        current = store.public()
        if current.get('activeId') != desktop_id or current.get('current', {}).get('kind') != 'api':
            raise ValueError('仅可导入已生效的 Claude Desktop API 接入；官方登录不能复制到 CLI')
        saved, path = store._account(desktop_id)
        if saved['kind'] != 'api':
            raise ValueError('Claude Desktop 官方登录不能复制到 CLI')
        gateway = json.loads((path/'gateway.json').read_text(encoding='utf-8'))
        config = gateway['config']
        digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        manifest = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
        if digest != saved.get('fingerprint') or manifest.get('fingerprint') != digest:
            raise ValueError('Claude Desktop API 快照与当前接入不一致')
        auth = config.get('inferenceGatewayAuthScheme')
        key = config.get('inferenceGatewayApiKey')
        models = config.get('inferenceModels') or []
        model = models[0] if models else ''
        if isinstance(model, dict):
            model = model.get('name', '')
        if not (isinstance(key, str) and key and auth in ('bearer', 'x-api-key')
                and isinstance(model, str) and isinstance(config.get('inferenceGatewayBaseUrl'), str)):
            raise ValueError('Claude Desktop API 配置不能转换为本机 CLI 接入')
        return {'name': saved['name'], 'baseUrl': config['inferenceGatewayBaseUrl'],
                'model': model, 'apiKey': key, 'authScheme': auth}
