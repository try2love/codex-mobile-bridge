"""Allowlisted desktop integrations with durable at-most-once mutation receipts."""
import hashlib
import json
import sqlite3
import threading
import time
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from contextlib import contextmanager, nullcontext
from concurrent.futures import Future

from .claude import Claude
from .deepseek import DeepSeek, BRIDGE_REVISION, UPDATE_REASON
from .errors import BridgeUnavailable
from ..lifecycle import private_json
from .. import windows_session

READS = {'list', 'detail', 'catalog', 'projects', 'account', 'access'}
WRITES = {'send', 'stop', 'settings', 'respond', 'create', 'access'}


class TaskStateUnavailable(ValueError):
    """No task evidence; distinct from known busy tasks or ambiguous processes."""


def _copy_read(value):
    # Native responses are JSON; only containers need copying, not large strings.
    if isinstance(value, dict):
        return {key: _copy_read(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_read(item) for item in value]
    return value


class DesktopSessions:
    def __init__(self, directory, bridge=None, *, gateway_running=False):
        self.bridge = bridge
        self.gateway_running = gateway_running
        self.closed = threading.Event()
        self.startup_thread = None
        self.client_lock = threading.RLock()
        self.client_cache = None
        self.workspace_roots = {}
        self.account_stores = {}
        self.account_operations = set()
        self.deepseek_recovery = None
        self.directory = Path(directory)/'desktop-sessions'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.directory/'settings.json'
        self.config = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
        self.adapters = {'deepseek': DeepSeek(self.directory/'deepseek', self.config.get('deepseekHome')),
                         'claude': Claude(self.directory/'claude', auto_connect=False)}
        self.deepseek_restore_error = None
        if self.config.get('deepseekAdapterDirectory'):
            try:
                self.adapters['deepseek'].use_existing(self.config['deepseekAdapterDirectory'])
            except (OSError, ValueError, TypeError):
                self.deepseek_restore_error = '已有 Harness 接入无法验证，请重新扫描'
                self.config.setdefault('setup', {})['deepseek'] = {
                    'setupStatus': 'failed', 'reason': '已有 Harness 接入无法验证，请重新扫描'}
        self.locks = {name: threading.RLock() for name in ('codex', *self.adapters)}
        self.read_lock = threading.Lock()
        self.reads = {}
        self.read_epochs = dict.fromkeys(self.locks, 0)
        self.read_changes = dict.fromkeys(self.locks, 0)
        self.binding_epochs = dict.fromkeys(self.locks, 0)
        self.binding_changes = dict.fromkeys(self.locks, 0)
        self.db_lock = threading.Lock()
        self.db = sqlite3.connect(self.directory/'requests.sqlite', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, result TEXT)')
        self.db.commit()
        (self.directory/'requests.sqlite').chmod(0o600)
        self.receipt_dbs = {}
        self._codex_guard = lambda: self.require_enabled('codex')
        if self.bridge is not None:
            self.bridge.accounts.require_client_enabled = self._codex_guard

    def _receipts(self, adapter):
        # A recovered connector shares its original mutation receipts with the
        # still-running gateway. Never replay an uncertain request after recovery.
        path = getattr(adapter, 'receipts_path', None)
        if not isinstance(path, (str, Path)):
            return self.db
        path = Path(path).resolve()
        if path == self.directory/'requests.sqlite':
            return self.db
        if path not in self.receipt_dbs:
            if not path.is_file():
                raise BridgeUnavailable('此前接入的操作记录不可用，请重新扫描')
            self.receipt_dbs[path] = sqlite3.connect(path, check_same_thread=False)
        return self.receipt_dbs[path]

    @contextmanager
    def _changing(self, provider, rebind=True):
        # Keep progress reads available during writes, but never join a read from
        # an earlier account/connector generation. Safety checks bypass _read.
        with self.locks[provider]:
            with self.read_lock:
                self.read_epochs[provider] += 1
                self.read_changes[provider] += 1
                if rebind:
                    self.binding_epochs[provider] += 1
                    self.binding_changes[provider] += 1
            try:
                yield
            finally:
                with self.read_lock:
                    self.read_epochs[provider] += 1
                    self.read_changes[provider] -= 1
                    if rebind:
                        self.binding_epochs[provider] += 1
                        self.binding_changes[provider] -= 1

    def _read(self, provider, action, sid=None, body=None):
        body = body or {}
        with self.read_lock:
            if self.closed.is_set():
                raise BridgeUnavailable('客户端连接已关闭')
            adapter = self.adapters[provider]
            epoch = self.read_epochs[provider]
            binding = self.binding_epochs[provider]
            key = (provider, id(adapter), epoch, action, sid, json.dumps(body, sort_keys=True))
            future = self.reads.get(key) if not self.read_changes[provider] else None
            owner = future is None
            if owner:
                future = Future()
                if not self.read_changes[provider]:
                    self.reads[key] = future
        if owner:
            try:
                future.set_result(adapter.call(action, sid, body))
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                with self.read_lock:
                    if self.reads.get(key) is future:
                        del self.reads[key]
        # Copy before the last generation check so a slow copy cannot publish
        # a response from an account that was switched in the meantime.
        result = _copy_read(future.result())
        with self.read_lock:
            if self.closed.is_set() or self.binding_epochs[provider] != binding or self.adapters[provider] is not adapter:
                raise BridgeUnavailable('客户端状态已变化，请重试')
        # Consumers may annotate rows. Sharing a result object crosses requests.
        return result

    def call(self, provider, action, sid=None, body=None):
        if provider not in self.adapters or action not in READS | WRITES:
            raise ValueError('不支持的桌面操作')
        if provider == 'deepseek' and self.deepseek_restore_error:
            raise BridgeUnavailable(self.deepseek_restore_error)
        if action not in ('list', 'projects', 'create', 'account') and (not isinstance(sid, str) or not sid or len(sid) > 512):
            raise ValueError('会话标识无效')
        body = body or {}
        adapter = self.adapters[provider]
        if action in READS and not (action == 'access' and 'mode' in body):
            return self._read(provider, action, sid, body)
        rid = str(uuid.UUID(body.get('id', '')))
        if action == 'send' and (not isinstance(body.get('text'), str) or
                                 not body['text'].strip() and not body.get('resolvedImages') or len(body['text']) > 100000):
            raise ValueError('请输入 1–100000 字的消息')
        if action == 'create' and (not isinstance(body.get('title', ''), str) or len(body.get('title', '')) > 120):
            raise ValueError('聊天名称不能超过 120 字')
        fingerprint = hashlib.sha256(json.dumps([provider, action, sid, body], sort_keys=True).encode()).hexdigest()
        # Normal chat writes keep progress reads available; account/lifecycle
        # operations additionally invalidate the connector binding.
        with self._changing(provider, rebind=False):
            if self.config.get('enabled', {}).get(provider) is False:
                raise ValueError('此应用已关闭，请在应用管理中开启')
            with self.db_lock:
                db = None
                try:
                    db = self._receipts(adapter)
                    result = {'status': 'unknown', 'id': rid, 'message': '结果待核对，请检查桌面原会话；相同请求不会重复执行'}
                    inserted = db.execute('INSERT OR IGNORE INTO requests VALUES (?,?,?)', (rid, fingerprint, json.dumps(result))).rowcount
                    db.commit()
                    if not inserted:
                        old = db.execute('SELECT fingerprint,result FROM requests WHERE id=?', (rid,)).fetchone()
                        if old[0] != fingerprint:
                            raise ValueError('相同请求 ID 不能用于不同操作')
                        return json.loads(old[1])
                except sqlite3.OperationalError as exc:
                    if db is not None:
                        db.rollback()
                    if any(word in str(exc).lower() for word in ('locked', 'busy')):
                        raise BridgeUnavailable('接入的操作记录正被另一网关占用，请停止旧网关后重试') from None
                    raise
            try:
                result = adapter.call(action, sid, body)
            except Exception as exc:
                # The desktop may already have accepted a command before a timeout.
                # Do not turn an uncertain outcome into a safe-to-retry failure.
                result['message'] = str(exc) if isinstance(exc, (ValueError, BridgeUnavailable)) else result['message']
            with self.db_lock:
                db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(result), rid))
                db.commit()
            return result

    def notification_rows(self):
        """Read live desktop state without connecting, activating or creating chats."""
        if not self.gateway_running or self.closed.is_set():
            return []
        rows = []
        for provider, adapter in self.adapters.items():
            if not self.enabled(provider):
                continue
            try:
                status = adapter.call('status') if provider == 'deepseek' else adapter.status()
                if status.get('connected') is not True:
                    continue
                listing = self._read(provider, 'list')
                if listing.get('connected') is False:
                    continue
                for row in listing.get('sessions', []):
                    if isinstance(row, dict) and isinstance(row.get('id'), str) and row['id']:
                        rows.append({**row, 'host': 'desktop:' + provider, 'provider': provider})
            except (OSError, ValueError, BridgeUnavailable):
                continue
        return rows

    def status(self):
        result = {}
        for provider, adapter in self.adapters.items():
            try:
                if provider == 'deepseek' and self.deepseek_restore_error:
                    raise BridgeUnavailable(self.deepseek_restore_error)
                value = adapter.call('status') if provider == 'deepseek' else adapter.status()
            except (ValueError, OSError):
                value = {'connected': False}
            result[provider] = value
        return result

    def enabled(self, provider):
        return self.config.get('enabled', {}).get(provider, provider == 'codex')

    def require_enabled(self, provider):
        if provider == 'deepseek' and self.deepseek_restore_error:
            raise BridgeUnavailable(self.deepseek_restore_error)
        if not self.enabled(provider):
            raise ValueError('此应用已关闭，请在应用管理中开启')

    @staticmethod
    def _deepseek_endpoint(adapter):
        # An unchanged live endpoint may still be running the previous connector
        # source. Keep its restart action until a different host generation runs.
        try:
            value = json.loads((adapter.directory/'endpoint.json').read_text())
            return {'port': value['port'], 'pid': value['pid']}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _deepseek_restart_pending(setup, endpoint, connected, revision=None):
        if setup.get('setupStatus') != 'restart-required':
            return False
        if setup.get('requiredBridgeRevision'):
            return not connected or revision != setup['requiredBridgeRevision']
        if connected and revision == BRIDGE_REVISION:
            return False
        if not connected or endpoint is None:
            return True
        previous = setup.get('restartEndpoint')
        if previous is not None:
            return previous == endpoint
        # A previously live connector with no readable identity cannot prove
        # it reloaded. Only an explicit restart can clear that uncertain case.
        return setup.get('restartConnected', True)

    def scan(self, setup=True):
        # Discovery only: neither a GUI refresh nor a manual rescan launches apps.
        # Keep the old setup argument for callers from older desktop packages.
        from ..desktop import Desktop
        from .discovery import discover_clients
        with self.client_lock:
            preferences = {**Desktop(self.directory.parent).preferences(),
                           **{k: v for k, v in self.config.items() if k.endswith('Home')}}
            if self.bridge:
                preferences['desktopExecutable'] = self.bridge.accounts.index.get('desktopExecutable')
            discovered = discover_clients(preferences)
            self.config['discovered'] = discovered
            outcomes = self.config.setdefault('setup', {})
            for provider in ('claude', 'deepseek'):
                row = discovered[provider]
                if not row['installed']:
                    outcomes[provider] = {'setupStatus': 'not-installed', 'reason': '尚未安装此客户端'}
                    continue
                with self._changing(provider):
                    try:
                        if provider == 'claude':
                            options = {'explicit_home': True} if row.get('dataDirectoryExplicit') else {}
                            status = self.adapters[provider].configure(row['executable'], row['dataDirectory'], **options)
                            outcomes[provider] = {'setupStatus': status.get('setupState', 'discovered'),
                                                  'reason': status.get('reason', '')}
                        else:
                            adapter = self.adapters[provider]
                            if str(adapter.home) != row['dataDirectory']:
                                adapter = DeepSeek(self.directory/'deepseek', row['dataDirectory'])
                                self.adapters[provider] = adapter
                            self.config['deepseekHome'] = str(adapter.home)
                            installed = adapter.discover_existing()
                            self.deepseek_restore_error = None
                            if installed.get('reused'):
                                self.config['deepseekAdapterDirectory'] = str(adapter.directory)
                            if installed.get('updateRequired'):
                                from .client_launch import inspect_client
                                native = inspect_client(row)
                                if native['running'] and not native.get('mainPids'):
                                    outcomes[provider] = {'setupStatus': 'failed',
                                        'reason': '检测到残留 Harness 后台实例且无法核对任务状态，请在电脑端退出 Harness 后重试'}
                                else:
                                    outcomes[provider] = {'setupStatus': 'restart-required', 'reason': UPDATE_REASON,
                                                          'requiredBridgeRevision': BRIDGE_REVISION}
                            elif not installed.get('installed'):
                                outcomes[provider] = {'setupStatus': 'discovered',
                                                      'reason': '已发现客户端，启用后随网关启动并接入'}
                    except (ValueError, OSError, subprocess.SubprocessError) as exc:
                        outcomes[provider] = {'setupStatus': 'failed', 'reason': str(exc)}
                        if provider == 'deepseek':
                            self.deepseek_restore_error = str(exc)
            private_json(self.config_path, self.config)
            self.client_cache = None
            return self.clients(refresh=True)

    def start_enabled(self):
        """Start selected clients only after the gateway service has started."""
        if not self.gateway_running or self.closed.is_set():
            return
        self.scan()
        for provider in ('codex', 'claude', 'deepseek'):
            with self.client_lock:
                if self.closed.is_set():
                    return
                if not self.enabled(provider):
                    continue
                try:
                    self.toggle_client({'provider': provider, 'enabled': True})
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    setup = self.config.setdefault('setup', {})
                    previous = setup.get(provider, {}) if provider == 'deepseek' else {}
                    setup[provider] = {**previous, 'setupStatus': (
                        'restart-required' if previous.get('requiredBridgeRevision') else 'failed'), 'reason': str(exc)}
                    private_json(self.config_path, self.config)
                    self.client_cache = None

    def start(self):
        if self.startup_thread is None and not self.closed.is_set():
            self.startup_thread = threading.Thread(target=self.start_enabled, daemon=True)
            self.startup_thread.start()

    def _descriptor(self, provider):
        row = dict(self.config.get('discovered', {}).get(provider, {}))
        if provider == 'codex' and self.bridge:
            accounts = self.bridge.for_host('local').accounts
            executable = self._codex_executable(accounts)
            row.update(id='codex', executable=executable, dataDirectory=str(accounts.home),
                       installed=bool(executable and Path(executable).is_file()))
        return row

    def _codex_executable(self, accounts):
        from ..desktop_app import DesktopApp
        # Windows may cache the CLI outside its Store-installed desktop bundle.
        return (accounts.index.get('desktopExecutable') or DesktopApp.discover(accounts.runtime)
                or self.config.get('discovered', {}).get('codex', {}).get('executable', ''))

    def _verified(self, provider):
        row = self.config.get('discovered', {}).get(provider, {})
        return {key: row.get(key) for key in ('executable', 'dataDirectory')}

    def _assert_idle(self, provider, state, *, native_confirmation=False):
        unknown = '无法确认客户端所有任务均已结束；可选择“仅停用手机接入”保留电脑 App，或在电脑端退出后重试'
        if provider == 'deepseek' and len(state.get('runtimePids', [])) > 1:
            raise ValueError('检测到多个 Harness 后台实例，无法确认全部任务状态；请在电脑上结束多余实例后重试')
        if state.get('unknown') or len(state.get('mainPids', [])) > 1:
            raise ValueError(unknown)
        if provider == 'codex':
            self.bridge.accounts.idle()
            self.bridge.assert_desktop_idle()
            return
        adapter = self.adapters[provider]
        try:
            status = adapter.call('status') if provider == 'deepseek' else adapter.status()
        except (ValueError, OSError, BridgeUnavailable):
            raise TaskStateUnavailable(unknown) from None
        if not status.get('connected'):
            raise TaskStateUnavailable('客户端尚未连接，无法确认任务状态；可选择“仅停用手机接入”，或在电脑端退出 App 后重试')
        if provider == 'deepseek':
            hosts = state.get('runtimePids', [])
            endpoint = self._deepseek_endpoint(adapter)
            if len(hosts) != 1 or not endpoint or endpoint.get('pid') != hosts[0]:
                raise ValueError(unknown)
            if status.get('bridgeRevision') != BRIDGE_REVISION:
                raise ValueError('当前 Harness 接入版本无法核对任务状态，请在电脑端退出 Harness 后再连接')
        try:
            listing = (adapter.call('list', timeout=8) if native_confirmation else
                       adapter.call('lifecycle' if provider == 'deepseek' else 'list'))
        except (ValueError, OSError, BridgeUnavailable):
            raise TaskStateUnavailable(unknown) from None
        sessions = listing.get('sessions')
        if not isinstance(sessions, list):
            raise TaskStateUnavailable(unknown)
        # An incomplete list may still prove a task busy; check that evidence first.
        if any(isinstance(row, dict) and (row.get('requests') or
               row.get('status') in ('active', 'running', 'waiting', 'busy')) for row in sessions):
            raise ValueError('有任务运行或等待确认，请先结束任务再关闭或重启客户端')
        if (listing.get('complete') is not True or
                provider == 'deepseek' and listing.get('bridgeRevision') != BRIDGE_REVISION or
                any(not isinstance(row, dict) or row.get('status') not in ('idle', 'stopped', 'completed') or
                    row.get('runtimeKnown') is not True for row in sessions)):
            raise TaskStateUnavailable(unknown)

    def _stop_client(self, provider, descriptor, state, *, native_confirmation=False):
        from .client_launch import stop_client, stop_deepseek
        native_confirmation = native_confirmation and provider == 'claude' and sys.platform == 'win32'
        if not state['running']:
            if provider == 'claude':
                self.adapters[provider].cancel()
            return
        try:
            self._assert_idle(provider, state, native_confirmation=native_confirmation)
        except TaskStateUnavailable:
            # Only explicit user-requested quit can defer unavailable task evidence
            # to Claude's own normal exit confirmation. Account changes cannot.
            if not (native_confirmation and provider == 'claude' and sys.platform == 'win32'):
                raise
        if provider == 'claude':
            # Cancel the monitor before native quit, so it cannot relaunch Claude.
            self.adapters[provider].cancel(persist=False)
        try:
            if provider == 'deepseek' and sys.platform == 'win32':
                stop_deepseek(descriptor, self.adapters[provider], state=state)
            else:
                stop_client(descriptor, state=state)
        except Exception:
            if provider == 'claude':
                try:
                    self.adapters[provider].reconnect()
                except Exception:
                    pass  # A recovery failure must not hide the native quit error.
            raise
        if provider == 'claude':
            self.adapters[provider].cancel()

    def _deepseek_connection(self, phase, message, *, clear=(), **values):
        setup = self.config.setdefault('setup', {}).setdefault('deepseek', {})
        previous = dict(setup)
        for key in clear:
            setup.pop(key, None)
        setup.update(connectionState=phase, connectionReason=message, **values)
        if phase not in ('starting', 'connecting'):
            for key in ('connectionStartedAt', 'connectionDeadline'):
                setup.pop(key, None)
        if previous != setup:
            private_json(self.config_path, self.config)
            self.client_cache = None

    def _begin_deepseek_connection(self):
        now = time.time()
        self._deepseek_connection('starting', '正在启动 Harness 桌面应用',
                                  connectionStartedAt=now, connectionDeadline=now + (120 if sys.platform == 'win32' else 60))

    def _deepseek_launched(self, result, *, first_launch=False):
        if not result.get('running') and result.get('launched') is not True:
            raise ValueError('客户端尚未启动，请在电脑端检查后重试')
        setup = self.config['setup']['deepseek']
        if first_launch and result.get('running'):
            reason = 'Harness 已打开，请完成首次设置后点击接入'
            self._deepseek_connection('idle', reason, setupStatus='needs-first-launch', reason=reason)
            return
        phase = 'connecting' if result.get('running') else 'starting'
        reason = 'Harness 已启动，正在等待桌面连接' if result.get('running') else '正在启动 Harness 桌面应用'
        values = {'pendingFirstLaunch': first_launch}
        if not setup.get('requiredBridgeRevision'):
            values.update(setupStatus=phase, reason=reason)
        self._deepseek_connection(phase, reason, **values)

    def _start_deepseek(self, restart=False):
        try:
            return self._prepare_deepseek(restart)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            setup = self.config.setdefault('setup', {}).get('deepseek', {})
            values = {} if setup.get('requiredBridgeRevision') else {'setupStatus': 'failed', 'reason': str(exc)}
            self._deepseek_connection('error', str(exc), **values)
            raise

    def _prepare_deepseek(self, restart=False):
        from .client_launch import inspect_client, launch_deepseek
        if self.deepseek_restore_error:
            raise BridgeUnavailable(self.deepseek_restore_error)
        row = self._descriptor('deepseek')
        adapter = self.adapters['deepseek']
        setup = self.config.setdefault('setup', {})
        previous = setup.get('deepseek', {})
        state = inspect_client(row)
        if state['running'] and not state.get('mainPids'):
            raise ValueError('检测到残留 Harness 后台实例且无法核对任务状态，请在电脑端退出 Harness 后重试')
        if not (adapter.home/'profiles/desktop/cordis.patch.yml').is_file():
            self._begin_deepseek_connection()
            result = launch_deepseek(row)
            self._deepseek_launched(result, first_launch=True)
            return
        installed = adapter.ensure_installed()
        if installed.get('reused'):
            self.config['deepseekAdapterDirectory'] = str(adapter.directory)
        try:
            status = adapter.call('status')
        except BridgeUnavailable:
            status = {}
        needs_update = bool(installed.get('updateRequired') or previous.get('requiredBridgeRevision') and
                            status.get('bridgeRevision') != BRIDGE_REVISION or status.get('updateRequired'))
        needs_reload = needs_update or previous.get('setupStatus') == 'restart-required' and not status.get('connected')
        if state['running'] and (restart or needs_reload):
            self._stop_client('deepseek', row, state)
        if needs_update or getattr(adapter, 'reused', False) and (restart or not state['running']):
            adapter.update_existing()
            setup['deepseek'] = {'setupStatus': 'restart-required', 'reason': UPDATE_REASON,
                                 'requiredBridgeRevision': BRIDGE_REVISION}
            private_json(self.config_path, self.config)
        self._begin_deepseek_connection()
        result = launch_deepseek(row)
        self._deepseek_launched(result)

    def connect_deepseek(self, restart=False):
        if not self.gateway_running:
            return self.toggle_client({'provider': 'deepseek', 'enabled': True})
        with self.client_lock, self._changing('deepseek'):
            self._start_deepseek(restart)
            return self.clients(refresh=True)

    def recover_deepseek(self, value):
        """Private desktop control only; web app controls never dispatch here."""
        from .deepseek_recovery import DeepSeekRecovery
        with self.client_lock, self._changing('deepseek'):
            if not self.config.get('discovered', {}).get('deepseek'):
                self.scan()
            descriptor, adapter = self._descriptor('deepseek'), self.adapters['deepseek']
            if self.deepseek_recovery is None:
                self.deepseek_recovery = DeepSeekRecovery()
            if value.get('action') == 'deepseek-recovery-preview':
                return self.deepseek_recovery.preview(descriptor, adapter, value.get('restart', True))
            restart = self.deepseek_recovery.confirm(descriptor, adapter, value)
            # Native exit has completed; source upgrades cannot invalidate live tasks.
            if restart:
                installed = adapter.ensure_installed()
                if installed.get('updateRequired'):
                    adapter.update_existing()
                self.deepseek_restore_error = None
                self.config.setdefault('setup', {})['deepseek'] = {'setupStatus': 'connecting', 'reason': '正在等待 Harness 连接'}
                self._start_deepseek()
            else:
                self.config.setdefault('setup', {})['deepseek'] = {'setupStatus': 'ready', 'reason': 'Harness 已完整退出'}
            # Preserve the user's startup choice if quitting or restarting failed.
            self.config.setdefault('enabled', {})['deepseek'] = restart
            private_json(self.config_path, self.config)
            self.client_cache = None
            return self.clients(refresh=True)

    def connect_claude(self, restart=False):
        if not self.gateway_running:
            return self.toggle_client({'provider': 'claude', 'enabled': True})
        from .client_launch import inspect_client, launch_client
        with self.client_lock, self._changing('claude'):
            if not restart and self.adapters['claude'].status().get('connected') is True:
                return self.clients(refresh=True)
            if sys.platform == 'win32':
                windows_session.require_interactive()
            descriptor = self._descriptor('claude')
            if restart:
                self._stop_client('claude', descriptor, inspect_client(descriptor))
            result = launch_client(descriptor)
            if not result.get('running'):
                raise ValueError('客户端尚未启动，请在电脑端检查后重试')
            self.adapters['claude'].connect()
            return self.clients(refresh=True)

    def clients(self, refresh=False):
        with self.client_lock:
            pending = self.client_cache and any(row.get('connectionState') in ('starting', 'connecting')
                                               for row in self.client_cache[1]['clients'])
            if not refresh and self.client_cache and time.monotonic() - self.client_cache[0] < (1 if pending else 15):
                result = self.client_cache[1]
                return {**result, 'windowsSession': windows_session.status()} if sys.platform == 'win32' else result
            rows = []
            discovered = self.config.get('discovered', {})
            ready = False
            reason = '请在电脑端安装 Codex 并配置账号或 API'
            if self.bridge is not None:
                try:
                    accounts = self.bridge.for_host('local').accounts
                    current = accounts.info.current()
                    installed = self._codex_executable(accounts)
                    ready = bool(installed and Path(installed).is_file() and current.get('status') == 'ready' and current.get('kind') in ('chatgpt', 'api'))
                    if ready and current.get('kind') == 'api':
                        ready = bool((accounts.info.identity or {}).get('key') or accounts.active_matches())
                    reason = '接入配置已就绪' if ready else ('正在检查账号配置' if current.get('status') == 'checking' else reason)
                except (AttributeError, ValueError, OSError):
                    pass
            rows.append({'id': 'codex', 'name': 'Codex', 'installed': discovered.get('codex', {}).get('installed', ready), 'configured': ready, 'connected': False,
                         'setupStatus': 'ready' if ready else 'unconfigured',
                         'enabled': self.config.get('enabled', {}).get('codex', ready), 'reason': reason})
            for provider, name in [('claude', 'Claude'), ('deepseek', 'DSH')]:
                connected = ready = False
                invalid = False
                status = {}
                setup = self.config.get('setup', {}).get(provider, {})
                reason = setup.get('reason') or '请在电脑端连接客户端并配置账号或 API'
                setup_status = setup.get('setupStatus', 'not-scanned')
                try:
                    adapter = self.adapters[provider]
                    if provider == 'deepseek' and self.deepseek_restore_error:
                        raise BridgeUnavailable(self.deepseek_restore_error)
                    status = adapter.call('status') if provider == 'deepseek' else adapter.status()
                    if provider == 'claude':
                        setup_status = status.get('setupState', setup_status)
                        reason = status.get('reason') or reason
                    connected = status.get('connected') is True if provider == 'deepseek' else bool(status.get('connected'))
                    if connected:
                        if provider == 'deepseek':
                            ready = status.get('configured') is True and not status.get('updateRequired')
                            invalid = status.get('configured') is False
                            reason = (('已恢复此前的 Harness 接入' if setup.get('setupStatus') == 'recovered' else '接入配置已就绪') if ready else '请在桌面配置账号或 API')
                        else:
                            sessions = self._read(provider, 'list').get('sessions', [])
                            if sessions:
                                catalog = self._read(provider, 'catalog', sessions[0]['id'], {'section': 'models'})
                                ready = bool(catalog.get('models'))
                                invalid = catalog.get('models') == []
                            reason = (status.get('reason') if status.get('consoleCleanupPending') else '桌面连接与模型配置可用') if ready else '请在桌面登录并配置模型、创建一个项目会话'
                except (ValueError, OSError, BridgeUnavailable):
                    reason = setup.get('reason') or '客户端连接不可用，请在电脑端检查'
                pending_restart = provider == 'deepseek' and self._deepseek_restart_pending(
                    setup, self._deepseek_endpoint(adapter), connected, status.get('bridgeRevision'))
                if provider == 'deepseek' and (status.get('updateRequired') or setup.get('requiredBridgeRevision') and pending_restart):
                    ready = False
                    setup_status = 'restart-required'
                    reason = UPDATE_REASON
                elif pending_restart:
                    reason = setup.get('reason') or '请结束当前任务后重启 Harness 完成接入'
                elif provider == 'deepseek' and connected:
                    # Transport readiness and account setup are separate. A live
                    # current connector also clears stale restart/failure labels.
                    setup_status = 'recovered' if ready and setup_status == 'recovered' else 'ready' if ready else 'unconfigured'
                verified = self.config.setdefault('verified', {})
                binding = self._verified(provider)
                if invalid and provider in verified:
                    del verified[provider]
                    private_json(self.config_path, self.config)
                if ready and binding.get('executable'):
                    if verified.get(provider) != binding:
                        verified[provider] = binding
                        private_json(self.config_path, self.config)
                remembered = bool(binding.get('executable') and verified.get(provider) == binding and
                                  discovered.get(provider, {}).get('installed'))
                configured = ready or remembered
                if provider == 'deepseek' and (pending_restart or status.get('updateRequired')):
                    configured = False
                if not connected and remembered and setup_status not in (
                        'failed', 'needs-retry', 'needs-unlock', 'needs-desktop', 'needs-developer-mode', 'needs-trust',
                        'needs-permission', 'restart-required', 'needs-initialization', 'needs-first-launch', 'starting', 'connecting'):
                    reason = '客户端已配置，可开启桌面应用'
                if provider == 'deepseek' and setup.get('setupStatus') == 'failed' and not connected:
                    setup_status, reason = 'failed', setup.get('reason', reason)
                rows.append({'id': provider, 'name': name, 'installed': discovered.get(provider, {}).get('installed', connected),
                             'setupStatus': ('recovered' if provider == 'deepseek' and setup_status == 'recovered' else 'ready') if ready and not pending_restart else setup_status, 'configured': configured, 'connected': connected,
                             'enabled': self.enabled(provider), 'reason': reason})
            from .client_launch import inspect_clients
            descriptors = []
            for row in rows:
                if row['installed']:
                    try:
                        descriptors.append(self._descriptor(row['id']))
                    except (AttributeError, ValueError, OSError):
                        pass
            try:
                native_states = inspect_clients(descriptors) if descriptors else {}
            except (OSError, subprocess.SubprocessError):
                native_states = {}
            for row in rows:
                row.update(running=False, mainRunning=False, backgroundRunning=False, backgroundCount=0)
                if row['installed']:
                    try:
                        native = native_states.get(row['id'])
                        if native is None:
                            raise ValueError('客户端进程状态不可用')
                        row.update(running=native['running'], mainRunning=bool(native.get('mainPids')),
                                   backgroundRunning=bool(native.get('runtimePids')), backgroundCount=len(native.get('runtimePids', [])))
                        if row['id'] == 'codex':
                            row['connected'] = row['configured'] and native['running']
                        if row['id'] == 'deepseek' and (row['backgroundCount'] > 1 or row['backgroundRunning'] and not row['mainRunning']):
                            row.update(setupStatus='recovery-required', reason='Harness 存在残留或多个后台实例，请在桌面使用完整退出并重新接入')
                    except (ValueError, OSError, subprocess.SubprocessError):
                        pass
                if row['id'] == 'deepseek':
                    self._deepseek_progress(row, native_states.get('deepseek'))
                elif row['id'] == 'claude':
                    self._claude_progress(row, native_states.get('claude'))
                # Selection is a startup preference; readiness still requires live evidence.
                row['selectable'] = bool(row['configured'] or row['installed'] and row['setupStatus'] != 'unsupported')
                if not self.gateway_running and row['enabled'] and row['setupStatus'] != 'recovery-required':
                    row['reason'] = ('已选择，启动网关后在后台启动并连接' if row['id'] == 'claude'
                                     else '已选择，启动网关后自动接入')
            result = {'clients': rows, 'computer': socket.gethostname(), 'gatewayRunning': self.gateway_running}
            if sys.platform == 'win32':
                result['windowsSession'] = windows_session.status()
            self.client_cache = (time.monotonic(), result)
            return result

    def _claude_progress(self, row, native):
        row.update(connectionState='connected' if row['connected'] else 'idle', retryable=False)
        if row['connected'] or not self.gateway_running or not row['enabled']:
            return
        phase = row['setupStatus']
        if phase in ('starting', 'connecting', 'needs-initialization'):
            row['connectionState'] = phase
            if phase != 'starting' and native is not None and not native['running']:
                row.update(connectionState='error', reason='Claude 未运行，请重试后台启动', retryable=True)
        elif phase in ('failed', 'needs-retry'):
            row.update(connectionState='error', retryable=True)
        elif phase in ('needs-developer-mode', 'needs-trust', 'needs-permission', 'needs-unlock', 'needs-desktop'):
            row['connectionState'] = 'needs-initialization'

    def _deepseek_progress(self, row, native):
        """Describe observed startup without relaunching or touching desktop tasks."""
        setup = self.config.get('setup', {}).get('deepseek', {})
        row.update(connectionState='connected' if row['connected'] else 'idle', retryable=False)
        if row['setupStatus'] == 'recovery-required':
            row['connectionState'] = 'error'
            return
        if row['connected']:
            if row['setupStatus'] != 'restart-required':
                self._deepseek_connection('connected', row['reason'], setupStatus=row['setupStatus'], reason=row['reason'],
                    clear=('requiredBridgeRevision', 'restartEndpoint', 'restartConnected', 'pendingFirstLaunch'))
            else:
                self._deepseek_connection('connected', row['reason'])
            return
        if not self.gateway_running or not row['enabled']:
            return
        phase = setup.get('connectionState') or setup.get('setupStatus')
        reason = setup.get('connectionReason') or row['reason']
        if phase in ('starting', 'connecting'):
            deadline = setup.get('connectionDeadline')
            if not isinstance(deadline, (int, float)):
                deadline = time.time() + (120 if sys.platform == 'win32' else 60)
                self._deepseek_connection(phase, reason, connectionDeadline=deadline)
            if time.time() >= deadline:
                phase, reason = 'timeout', 'Harness 连接超时，请检查桌面应用后重试接入'
            elif native is not None and not native['running'] and phase == 'connecting':
                phase, reason = 'error', 'Harness 已退出，连接未完成，请重试启动'
            elif native is not None and native['running']:
                if setup.get('pendingFirstLaunch'):
                    reason = 'Harness 已打开，请完成首次设置后点击接入'
                    self._deepseek_connection('idle', reason, setupStatus='needs-first-launch', reason=reason)
                    row.update(setupStatus='needs-first-launch', reason=reason, retryable=True)
                    return
                phase, reason = 'connecting', 'Harness 已启动，正在等待桌面连接'
            values = {} if setup.get('requiredBridgeRevision') else {
                'setupStatus': 'failed' if phase == 'error' else phase, 'reason': reason}
            self._deepseek_connection(phase, reason, **values)
            row['setupStatus'] = setup.get('setupStatus', row['setupStatus'])
        elif phase == 'connected':
            phase = 'error'
            reason = ('Harness 连接已中断，请重试接入' if native is None or native['running']
                      else 'Harness 未运行，请重试启动')
        elif row['setupStatus'] == 'failed':
            phase = 'error'
        if phase in ('starting', 'connecting', 'error', 'timeout'):
            row.update(connectionState=phase, reason=reason, retryable=phase in ('error', 'timeout'))
        elif row['setupStatus'] in ('needs-first-launch', 'restart-required'):
            row['retryable'] = True

    def toggle_client(self, value):
        provider, enabled = value.get('provider'), value.get('enabled')
        if provider not in ('codex', 'claude', 'deepseek') or type(enabled) is not bool:
            raise ValueError('应用开关无效')
        if 'quitDesktop' in value and (type(value['quitDesktop']) is not bool or enabled):
            raise ValueError('应用开关无效')
        if 'initializeDesktop' in value and (type(value['initializeDesktop']) is not bool
                or provider != 'claude' or enabled is not True):
            raise ValueError('应用初始化请求无效')
        # Older clients use disabling as native quit. New clients choose explicitly.
        quit_desktop = value.get('quitDesktop', True)
        from .client_launch import inspect_client, launch_client
        accounts = self.bridge.accounts if provider == 'codex' and self.bridge else None
        with self.client_lock, self._changing(provider), accounts.gate if accounts else nullcontext(), accounts.lock if accounts else nullcontext():
            if not enabled and not quit_desktop:
                # Revoke gateway access without inspecting or interrupting tasks.
                # Keep the same write gate so a queued send sees the new setting.
                if provider == 'claude':
                    self.adapters[provider].cancel()
                self.config.setdefault('enabled', {})[provider] = False
                private_json(self.config_path, self.config)
                self.client_cache = None
                return self.clients()
            if accounts:
                accounts.assert_editable()
            if enabled:
                row = next(row for row in self.clients(refresh=True)['clients'] if row['id'] == provider)
                if not row['selectable']:
                    raise ValueError(row['reason'])
            if not self.gateway_running:
                self.config.setdefault('enabled', {})[provider] = enabled
                private_json(self.config_path, self.config)
                self.client_cache = None
                return self.clients()
            descriptor = self._descriptor(provider)
            if enabled:
                if provider == 'deepseek':
                    self._start_deepseek()
                elif provider == 'claude':
                    if value.get('initializeDesktop') is True:
                        self.adapters[provider].connect()
                    else:
                        self.adapters[provider].reconnect(launch=True)
                else:
                    result = launch_client(descriptor)
                    if not result.get('running'):
                        raise ValueError('客户端尚未启动，请在电脑端检查后重试')
            else:
                self._stop_client(provider, descriptor, inspect_client(descriptor),
                                  native_confirmation=value.get('quitDesktop') is True)
            # Persist only after the native lifecycle operation succeeds.
            self.config.setdefault('enabled', {})[provider] = enabled
            private_json(self.config_path, self.config)
            if not enabled and hasattr(self, 'terminals'):
                identifiers = {str(uuid.uuid5(uuid.NAMESPACE_URL, p + ':' + sid)) for p, sid in self.workspace_roots if p == provider}
                with self.terminals.lock:
                    for collection in (self.terminals.sessions, self.terminals.jobs):
                        for (_, thread, _), process in collection.items():
                            if thread in identifiers:
                                process.stop()
            self.client_cache = None
            return self.clients()

    def _account_store(self, provider):
        if provider not in self.adapters:
            raise ValueError('不支持的桌面客户端')
        if provider not in self.config.get('discovered', {}):
            self.scan()
        descriptor = self._descriptor(provider)
        home = descriptor.get('dataDirectory')
        if not home or not descriptor.get('installed'):
            raise ValueError('尚未安装此客户端')
        key = (provider, home)
        if provider == 'claude':
            key += (descriptor.get('executable'), descriptor.get('packageFamilyName'),
                    bool(descriptor.get('dataDirectoryExplicit')))
        if key not in self.account_stores:
            if provider == 'claude':
                from .claude_accounts import ClaudeAccounts
                store = ClaudeAccounts(self.directory/'claude-accounts', home,
                    executable=descriptor.get('executable'), package_family=descriptor.get('packageFamilyName'),
                    explicit_home=bool(descriptor.get('dataDirectoryExplicit')))
            else:
                from .deepseek_accounts import DeepSeekAccounts
                store = DeepSeekAccounts(self.directory/'deepseek-accounts', home)
            self.account_stores[key] = store
        return self.account_stores[key]

    def _accounts_public(self, provider, value):
        return {**value, 'provider': provider, 'gatewayRunning': self.gateway_running,
                'canSwitch': provider not in self.account_operations}

    def _launch_account_client(self, provider, descriptor):
        from .client_launch import launch_client
        if provider == 'deepseek':
            self._start_deepseek()
        else:
            result = launch_client(descriptor)
            if not result.get('running'):
                raise ValueError('客户端尚未启动，请在电脑端检查后重试')
            if self.gateway_running and self.enabled(provider):
                self.adapters[provider].reconnect()

    def client_accounts(self, provider, value):
        operation = value.get('operation', 'list')
        store = self._account_store(provider)
        if operation == 'list':
            return self._accounts_public(provider, store.public())
        if operation == 'details':
            return self._accounts_public(provider, store.details(value.get('id'), refresh=value.get('refresh') is True))
        if operation not in ('import-current', 'switch'):
            raise ValueError('不支持的账号操作')
        from .client_launch import inspect_client
        with self.client_lock, self._changing(provider):
            # Harness persists one atomic credential file; saving it does not
            # require the multi-file profile snapshot used by Claude.
            if provider == 'deepseek' and operation == 'import-current':
                result = store.import_current(name=value.get('name', ''), kind=value.get('kind', ''))
                return self._accounts_public(provider, result)
            descriptor = self._descriptor(provider)
            state = inspect_client(descriptor)
            restart = state['running'] or self.gateway_running and self.enabled(provider)
            if state['running'] and not self.gateway_running:
                raise ValueError('请先退出客户端，再保存或切换账号；也可启动网关后操作')
            # Stop only after all tasks and requests are confirmed idle.
            self._stop_client(provider, descriptor, state)
            self.account_operations.add(provider)
            rollback = None
            try:
                if operation == 'import-current':
                    options = {'kind': value.get('kind', '')} if provider == 'deepseek' else {}
                    result = store.import_current(name=value.get('name', ''), **options)
                else:
                    rollback = store.restore(value.get('id'))
                    result = store.public()
                    active = result.get('activeIds', [result.get('activeId')])
                    if value.get('id') not in active:
                        raise ValueError('账号配置尚未生效，请重试')
                if restart:
                    self._launch_account_client(provider, descriptor)
                    if operation == 'switch':
                        result = store.public()
                        active = result.get('activeIds', [result.get('activeId')])
                        if value.get('id') not in active:
                            raise ValueError('账号配置尚未生效，请重试')
            except Exception as error:
                try:
                    if rollback is not None:
                        current = inspect_client(descriptor)
                        if current['running']:
                            self._stop_client(provider, descriptor, current)
                        store.rollback(rollback)
                    if state['running']:
                        self._launch_account_client(provider, descriptor)
                except (ValueError, OSError, subprocess.SubprocessError):
                    raise ValueError('账号操作未完成，请在电脑端检查；恢复备份已保留') from error
                raise
            else:
                if rollback is not None and hasattr(store, 'commit'):
                    try:
                        store.commit(rollback)
                    except OSError:
                        pass  # Keep the private rollback snapshot if cleanup fails.
            finally:
                self.account_operations.discard(provider)
                with self.read_lock:
                    self.workspace_roots = {key: row for key, row in self.workspace_roots.items() if key[0] != provider}
                self.client_cache = None
            return self._accounts_public(provider, result)

    def workspace_bridge(self, provider, sid):
        # Obtain the root from the desktop, never from a browser-supplied path.
        self.require_enabled(provider)
        if not isinstance(sid, str) or not sid or len(sid) > 512:
            raise ValueError('会话标识无效')
        with self.read_lock:
            epoch = self.binding_epochs[provider]
            if self.binding_changes[provider]:
                raise BridgeUnavailable('客户端状态已变化，请重试')
            cached = self.workspace_roots.get((provider, sid))
            cached = cached if cached and cached[2] == epoch and time.monotonic() - cached[0] < 10 else None
        if cached:
            row = cached[1]
        else:
            rows = self.call(provider, 'list').get('sessions', [])
            row = next((row for row in rows if row.get('id') == sid), None)
            if row is None:
                raise ValueError('桌面会话不存在')
            with self.read_lock:
                if self.binding_epochs[provider] != epoch or self.binding_changes[provider]:
                    raise BridgeUnavailable('客户端状态已变化，请重试')
                self.workspace_roots[(provider, sid)] = (time.monotonic(), row, epoch)
        from ..workspace import Workspace
        root = row.get('cwd')
        Workspace(root)  # Reject unavailable and non-local workspaces (e.g. Cowork VM).
        return DesktopWorkspace(self, provider, sid, root)

    def control(self, value):
        action = value.get('action', 'status')
        if action in ('deepseek-recovery-preview', 'deepseek-recovery-confirm'):
            return self.recover_deepseek(value)
        if action == 'accounts':
            return self.client_accounts(value.get('provider'), value)
        if action == 'account':
            provider = value.get('provider')
            if provider not in self.adapters:
                raise ValueError('不支持的桌面客户端')
            return self._read(provider, 'account')
        if action == 'scan':
            return self.scan(setup=value.get('setup', True) is True)
        if action in ('connect-deepseek', 'restart-deepseek'):
            return self.connect_deepseek(restart=action == 'restart-deepseek')
        if action in ('connect-claude', 'restart-claude', 'cancel-claude'):
            adapter = self.adapters['claude']
            if action == 'cancel-claude':
                with self._changing('claude'):
                    adapter.cancel()
            else:
                return self.connect_claude(restart=action == 'restart-claude')
            return self.clients(refresh=True)
        if action == 'clients':
            return self.clients(refresh=value.get('refresh') is True)
        if action == 'toggle-client':
            return self.toggle_client(value)
        if action == 'status':
            return {'backends': self.status(), 'deepseekHome': str(self.adapters['deepseek'].home),
                    'claudeWorkspace': str(self.adapters['claude'].directory)}
        provider = 'claude' if action == 'prepare-claude' else 'deepseek'
        with self._changing(provider):
            if action == 'prepare-claude':
                return self.adapters['claude'].prepare()
            if action == 'install-deepseek':
                home = value.get('home')
                if not isinstance(home, str) or not Path(home).expanduser().is_dir():
                    raise ValueError('请选择 Harness 数据目录')
                adapter = DeepSeek(self.directory/'deepseek', home)
                result = adapter.install()
                self.config['deepseekHome'] = str(adapter.home)
                private_json(self.config_path, self.config)
                self.adapters['deepseek'] = adapter
                return result
            if action == 'remove-deepseek':
                return self.adapters['deepseek'].uninstall()
        raise ValueError('不支持的接入操作')

    def close(self):
        self.closed.set()
        if self.startup_thread:
            self.startup_thread.join(timeout=5)
        if self.bridge is not None and getattr(self.bridge.accounts, 'require_client_enabled', None) is self._codex_guard:
            del self.bridge.accounts.require_client_enabled
        if hasattr(self, 'terminals'):
            self.terminals.close()
        for lock in self.locks.values():
            lock.acquire()
        try:
            self.adapters['claude'].close()
            with self.db_lock:
                for db in self.receipt_dbs.values():
                    db.close()
                self.db.close()
        finally:
            for lock in self.locks.values():
                lock.release()


class DesktopWorkspace:
    """Use the existing file and terminal services with a desktop-verified root."""
    host = 'local'

    def __init__(self, manager, provider, sid, root):
        from ..terminal import TerminalManager
        self.manager, self.provider = manager, provider
        self.root = root
        self.identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, provider + ':' + sid))
        with manager.client_lock:
            if not hasattr(manager, 'terminals'):
                manager.terminals = TerminalManager()
        self.terminals = manager.terminals
        self.store = self

    def get(self, identifier):
        if identifier != self.identifier:
            raise ValueError('会话标识不一致')
        return {'cwd': self.root}

    def workspace(self, sid, action, params):
        from ..workspace import operate
        # HTTP may resolve this object before waiting for an upload body. Check
        # again under the same gate used by disabling access before any write.
        with self.manager.locks[self.provider]:
            self.manager.require_enabled(self.provider)
            return operate(self.root, action, params)

    def terminal(self, sid, owner, action, params):
        from ..service import Bridge
        with self.manager.locks[self.provider]:
            self.manager.require_enabled(self.provider)
            return Bridge.terminal(self, self.identifier, owner, action, params)
