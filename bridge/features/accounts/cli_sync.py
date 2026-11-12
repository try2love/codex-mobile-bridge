"""Guarded local CLI account selection; never touches SSH profiles."""

import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from bridge.clients.desktop_app import DesktopApp
from bridge.features.accounts.accounts import ManagedRPC, private_bytes, private_json, token_owner
from bridge.features.sessions.cli_maintenance import CliTasksPending


CLAUDE_ENV_KEYS = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL')


def _matches_chatgpt_owner(status, owner):
    try:
        encoded = status['authToken'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        return bool(owner and len(owner) == 3 and owner[0] and owner[1] and status.get('authMethod') == 'chatgpt'
                    and (claims.get('https://api.openai.com/auth') or {}).get('chatgpt_account_id') == owner[0]
                    and claims.get('sub') == owner[1])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return False


class LocalCliSync:
    def __init__(self, bridge):
        self.bridge = bridge
        self._claude_backup = None
        self._claude_created_dir = None

    def _accounts(self):
        return self.bridge.accounts

    @staticmethod
    def _file_auth(config, provider):
        from bridge.clients.codex.daemon_network import file_auth_provider
        definition = (config.get('model_providers') or {}).get(provider) or {}
        return file_auth_provider(config, provider) and not any(
            definition.get(key) for key in ('http_headers', 'env_http_headers'))

    def recovery_target(self, config):
        """Resolve only an explicitly selected, unchanged managed file credential."""
        from bridge.clients.codex.daemon_network import AUTH_ENV_NAMES
        accounts = self._accounts()
        if accounts.management()['external'] or not accounts.index.get('activeId'):
            return None
        if not accounts.active_matches():
            return None
        row = accounts.row(accounts.index['activeId'])
        provider = 'bridge_api' if row['kind'] == 'api' else 'openai'
        if (config.get('profile') or (config.get('model_provider') or 'openai') != provider
                or config.get('cli_auth_credentials_store') != 'file'
                or not self._file_auth(config, provider)):
            raise ValueError('当前 CLI 的认证注入来源与已选择接入不同，请重新确认接入')
        aliases = [provider]
        if row['kind'] == 'api':
            definition = (config.get('model_providers') or {}).get(provider) or {}
            auth = json.loads((accounts.home/'auth.json').read_text(encoding='utf-8'))
            key = json.loads((accounts.directory(row['id'])/'api.json').read_text(encoding='utf-8'))['key']
            if (not key or auth.get('OPENAI_API_KEY') != key or definition.get('base_url') != row['baseUrl']
                    or config.get('openai_base_url') != row['baseUrl'] or config.get('model') != row['model']):
                raise ValueError('当前 CLI 的 API 接入与已选择账号不同')
            if not (config.get('model_providers') or {}).get('openai'):
                aliases.append('openai')
        else:
            auth = json.loads((accounts.home/'auth.json').read_text(encoding='utf-8'))
            if (not row.get('tokenOwner') or token_owner(auth) != row['tokenOwner']
                    or config.get('openai_base_url')
                    or ((config.get('model_providers') or {}).get('openai') or {}).get('base_url')):
                raise ValueError('当前 CLI 的登录账号与已选择账号不同')
            alias = (config.get('model_providers') or {}).get('bridge_api') or {}
            if self._file_auth(config, 'bridge_api') and not alias.get('base_url'):
                aliases.append('bridge_api')
        identity = hashlib.sha256(json.dumps([row['id'], accounts.index['activeStamp'], config],
                                            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return {'accountId': row['id'], 'provider': provider, 'aliases': aliases,
                'environment': dict.fromkeys(AUTH_ENV_NAMES),
                'identity': identity, 'kind': row['kind']}

    def verify_runtime_auth(self, probe, target):
        """Compare native cached auth in memory; never return or persist a token."""
        row = self._accounts().row(target['accountId'])
        status = probe.request('getAuthStatus', {'includeToken': True, 'refreshToken': False}, replay_safe=True)
        token = status.get('authToken')
        if row['kind'] == 'api':
            key = json.loads((self._accounts().directory(row['id'])/'api.json').read_text(encoding='utf-8'))['key']
            valid = status.get('authMethod') == 'apikey' and bool(key) and token == key
        else:
            valid = _matches_chatgpt_owner(status, row.get('tokenOwner'))
        if not valid:
            raise ValueError('原生服务尚未使用所选账号，请明确确认同步或维护后再继续')
        return True

    def file_auth_identity(self, kind):
        try:
            auth = json.loads((self._accounts().home/'auth.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(auth, dict):
            return None
        if kind == 'api':
            expected = auth.get('OPENAI_API_KEY')
            return expected if auth.get('auth_mode') in (None, 'apikey') and isinstance(expected, str) and expected else None
        if kind == 'chatgpt' and auth.get('auth_mode') in (None, 'chatgpt') and isinstance(auth.get('tokens'), dict):
            owner = token_owner(auth)
            return owner if owner and owner[0] and owner[1] else None

    def current_file_auth_matches(self, probe, target):
        """Compare the selected home file with native auth without a login write."""
        from bridge.clients.codex.app_server import AppServerError
        kind = target.get('kind')
        if kind not in ('api', 'chatgpt'):
            return None
        expected = self.file_auth_identity(kind)
        if expected is None:
            return False
        try:
            current = probe.request('getAuthStatus', {'includeToken': True, 'refreshToken': False}, replay_safe=True)
        except (AppServerError, OSError) as error:
            raise ValueError('无法核验此配置的原生认证缓存') from error
        if not isinstance(current, dict):
            return False
        if kind == 'api':
            return current.get('authMethod') == 'apikey' and current.get('authToken') == expected
        return _matches_chatgpt_owner(current, expected)

    def _api_runtime(self):
        from bridge.clients.codex.cli_session import _managed_cli
        from bridge.clients.codex.daemon_network import graceful_restart_supported
        from bridge.platforms.codex_daemon_env import read_daemon_environment
        accounts = self._accounts()
        client = self.bridge.cli_sessions.client
        info = client.connect()
        if (Path(info.get('codexHome', '')).resolve() != accounts.home.resolve()
                or not graceful_restart_supported(info)):
            raise ValueError('原生服务的配置目录或账号同步协议无法核验')
        executable = _managed_cli(accounts.home)
        if executable is None:
            raise ValueError('找不到此配置的受管原生服务')
        generation = read_daemon_environment(accounts.home, executable, ())[1]
        return client, json.loads(json.dumps(generation))

    def switch_live_api(self, row, request_id, *, reconcile=False):
        """One acknowledged login, or read-only reconciliation of its receipt."""
        accounts = self._accounts()
        cli = self.bridge.cli_sessions
        running, _ = self._codex_runtime()
        if running or row['kind'] != 'api':
            return False
        if not reconcile and cli.daemon_status() != 'running':
            return False
        if not reconcile:
            with cli.lock:
                if cli.attaching or any(cli.requests.values()):
                    raise ValueError('请先完成本机 CLI 连接或待处理请求后再同步账号')
            accounts.idle()
        client, generation = self._api_runtime()
        config = client.request('config/read', {'includeLayers': False}, replay_safe=True)['config']
        definition = (config.get('model_providers') or {}).get('bridge_api') or {}
        if (config.get('profile') or config.get('model_provider') != 'bridge_api'
                or config.get('cli_auth_credentials_store') != 'file'
                or not self._file_auth(config, 'bridge_api')
                or definition.get('base_url') != row['baseUrl']
                or config.get('openai_base_url') != row['baseUrl'] or config.get('model') != row['model']):
            if reconcile:
                raise ValueError('接入定义已变化，账号写入结果仍待核验，请勿重复切换')
            return False
        config_hash = hashlib.sha256((accounts.home/'config.toml').read_bytes()).hexdigest()
        key = json.loads((accounts.directory(row['id'])/'api.json').read_text(encoding='utf-8'))['key']
        saved = (accounts.directory(row['id'])/'api.json').stat()
        key_version = [saved.st_dev, saved.st_ino, saved.st_mtime_ns, saved.st_size]
        if reconcile:
            if (accounts.state.get('generation') != generation or accounts.state.get('configHash') != config_hash
                    or accounts.state.get('keyVersion') != key_version):
                raise ValueError('原生服务或配置已变化，账号写入结果仍待核验，请勿重复切换')
        else:
            previous = accounts.index.get('activeId')
            if (not previous or accounts.row(previous)['kind'] != 'api'
                    or (accounts.index.get('activeStamp') or {}).get('config') != config_hash):
                return False
            cached = client.request('getAuthStatus', {'includeToken': False, 'refreshToken': False}, replay_safe=True)
            if cached.get('authMethod') != 'apikey':
                return False
            self.check_idle_daemon(client, accounts.home)
            accounts.idle()
            if self._api_runtime()[1] != generation or hashlib.sha256((accounts.home/'config.toml').read_bytes()).hexdigest() != config_hash:
                raise ValueError('原生服务或配置已变化，请重新确认账号切换')
            # This receipt precedes the only write. Repeated requests only read.
            accounts.state = {'operation': 'cliAuth', 'requestId': request_id, 'targetId': row['id'],
                              'generation': generation, 'configHash': config_hash, 'keyVersion': key_version}
            accounts.phase('authUnknown', error='账号同步结果待核验，请勿重复切换')
            from bridge.features.notifications.channels import read_json
            ledger = read_json(accounts.root/'requests.json', {})
            ledger[request_id] = row['id']
            private_json(accounts.root/'requests.json', ledger)
            try:
                client.request('account/login/start', {'type': 'apiKey', 'apiKey': key})
            except Exception as error:
                # An explicit native rejection is known; transport failures are not.
                if getattr(error, 'rpc_error', None) is not None:
                    accounts.phase('failed', error='原生服务拒绝账号同步，未确认恢复')
                raise ValueError('账号同步尚未核验，请使用同一请求检查，勿重复提交') from None
        self.verify_runtime_auth(client, {'accountId': row['id']})
        if self._api_runtime()[1] != generation or hashlib.sha256((accounts.home/'config.toml').read_bytes()).hexdigest() != config_hash:
            raise ValueError('原生服务或配置已变化，账号同步尚未核验')
        verified_auth = (accounts.home/'auth.json').read_bytes()
        if json.loads(verified_auth).get('OPENAI_API_KEY') != json.loads((accounts.directory(row['id'])/'api.json').read_text(encoding='utf-8'))['key']:
            raise ValueError('账号文件已变化，账号同步尚未核验')
        accounts.mark_active(row['id'])
        # Do not absorb later, unverified file changes into this baseline.
        cli.configuration_stamp = {'config.toml': config_hash,
                                   'auth.json': hashlib.sha256(verified_auth).hexdigest()}
        cli.configuration_generation = hashlib.sha256(json.dumps(generation).encode()).hexdigest()
        accounts.phase('complete', error=None, cliSync={'status': 'verified'})
        return True

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
    def check_idle_daemon(client, home, *, target_platform=None):
        from bridge.features.sessions.create import check_archive_runtime
        from bridge.clients.codex.app_server import AppServerError, AppServerUnsupported
        from bridge.clients.codex.release import GOAL_STATUSES
        from bridge.clients.codex.daemon_network import graceful_restart_supported
        info = client.connect()
        if target_platform == 'linux':
            from pathlib import PurePosixPath
            same_home = PurePosixPath(info['codexHome']) == PurePosixPath(str(home))
        else:
            same_home = Path(info['codexHome']).resolve() == home.resolve()
        if not same_home:
            raise ValueError('原生服务不属于此配置，未切换账号')
        def request(method, params):
            return client.request(method, params, replay_safe=True)
        def loaded_threads():
            try:
                return client.loaded_threads()
            except (AppServerError, OSError) as error:
                raise CliTasksPending('无法核验此配置的原生任务') from error
        identifiers = loaded_threads()
        def check(threads):
            blockers = []
            for identifier in threads:
                try:
                    check_archive_runtime(request, [identifier], allow_terminal_error=graceful_restart_supported(info, platform=target_platform))
                    tasks = request('thread/backgroundTerminals/list', {'threadId': identifier, 'limit': 1})
                    if not isinstance(tasks.get('data'), list) or tasks['data'] or tasks.get('nextCursor'):
                        raise ValueError('有后台任务或状态未知')
                    try:
                        goal = request('thread/goal/get', {'threadId': identifier})['goal']
                    except AppServerUnsupported:
                        goal = None
                    if goal is not None and (not isinstance(goal, dict) or goal.get('status') not in GOAL_STATUSES):
                        raise ValueError('目标状态未知')
                    if goal and goal.get('status') == 'active':
                        raise ValueError('有运行中的目标')
                except (AppServerError, OSError, ValueError, KeyError, TypeError) as error:
                    blockers.append(f"{identifier}：{error}")
            if blockers:
                raise CliTasksPending('；'.join(blockers))
        check(identifiers)
        if loaded_threads() != identifiers:
            raise CliTasksPending('此配置的会话已变化，请重新检查')
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
