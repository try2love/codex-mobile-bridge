"""Claude Code/Cowork adapter over the Desktop renderer's signed file bridge."""
import asyncio
import json
import re
import secrets
import sys
import threading
import time
from pathlib import Path

import bridge.clients.claude.model as model
from bridge.clients.claude.setup import inspect_installation, console_source, developer_mode_enabled, native_action, running_app, claude_data_home, background_running_app
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.claude.mailbox import FileDesktop, MAX_REQUEST, CONNECTOR_REVISION
from bridge.app.lifecycle import private_json
from bridge.platforms.windows import session as windows_session
from bridge.platforms.windows.session import DesktopUnavailable

DESKTOP_WAIT_STATES = {'needs-screen-saver', 'needs-unlock', 'needs-desktop'}


class Claude:
    def __init__(self, directory, *, auto_connect=True):
        self.directory = Path(directory)
        self.desktop = None
        self.index = {}
        self.loop = None
        self.thread = None
        self.discovery = {}
        self.setup_lock = threading.RLock()
        self.setup_thread = None
        self.setup_mode = None
        self.setup_cancel = threading.Event()
        self.pending_calls = set()
        self.reconnecting = False
        discovery_path = self.directory/'discovery.json'
        if discovery_path.exists():
            try:
                value = json.loads(discovery_path.read_text())
                if isinstance(value, dict):
                    self.discovery = value
            except (OSError, ValueError):
                pass
        if auto_connect and (self.directory/'connection.json').exists() and self.discovery.get('autoConnect') is not False:
            self.prepare()
        if auto_connect and self.discovery.get('autoConnect') is True:
            self.reconnect()

    def configure(self, executable, data_home=None, *, explicit_home=False):
        """Remember auto-discovery even with the public gateway stopped.

        Do not create a file-bridge session or modify developer settings. A
        detected installation is distinct from a verified live connection.
        """
        previous = self.discovery
        self.discovery = inspect_installation(executable)
        if previous.get('autoConnect') is True:
            self.discovery['autoConnect'] = True
        if self.desktop or self.setup_thread:
            self.discovery.update({key: previous[key] for key in ('setupState', 'reason', 'consoleCleanupPending') if key in previous})
        if data_home:
            self.discovery['dataHome'] = str(Path(data_home).expanduser().resolve())
            self.discovery['dataHomeExplicit'] = explicit_home
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_json(self.directory/'discovery.json', self.discovery)
        return self.status()

    def prepare(self, reset=False):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory/'connection.json'
        if not path.exists():
            private_json(path, {'token': secrets.token_urlsafe(32)})
        token = json.loads(path.read_text())['token']
        if self.loop is None:
            self.loop = asyncio.new_event_loop()
            self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
            self.thread.start()
        if self.desktop is None or reset:
            if self.desktop:
                self.desktop.close()
            async def initialize():
                self.desktop = FileDesktop(self.directory, token)
            asyncio.run_coroutine_threadsafe(initialize(), self.loop).result(timeout=5)
        config = {'transport': 'file', 'cwd': str(self.directory), 'token': token,
                  'generation': self.desktop.generation, 'reconnect': True,
                  'connectorRevision': CONNECTOR_REVISION,
                  'request': 'request.json', 'response': 'response.json'}
        source = Path(__file__).with_name('connector.js').read_text(encoding='utf-8')
        script = source.replace('__BRIDGE_CONFIG__', json.dumps(config))
        target = self.directory/'connect-desktop.js'
        target.write_text(script, encoding='utf-8'); target.chmod(0o600)
        console = self.directory/'console-connect.js'
        console.write_text(console_source(source, config), encoding='utf-8')
        console.chmod(0o600)
        return {'workspace': str(self.directory), 'scriptPath': str(target), 'script': script,
                'consolePath': str(console)}

    def _setup_state(self, state, reason, **values):
        with self.setup_lock:
            self.discovery.update(setupState=state, reason=reason, **values)

    def reconnect(self, *, launch=False):
        """Recover a signed handoff, optionally starting Windows Claude without UI."""
        if sys.platform != 'win32':
            return self.connect(existing_only=True)
        old_monitor = None
        with self.setup_lock:
            if self.setup_thread and self.setup_thread.is_alive():
                if not self.setup_cancel.is_set():
                    return self.status()  # Preserve an explicit initialization in progress.
                old_monitor = self.setup_thread
        if old_monitor:
            old_monitor.join(timeout=5)
        with self.setup_lock:
            if self.setup_thread and self.setup_thread.is_alive():
                raise ValueError('请先取消正在进行的 Claude 连接')
            if not self.discovery.get('installed') or self.discovery.get('automaticConnection') != 'native-console':
                return self.status()
            reset = self.setup_cancel.is_set()
            self.setup_cancel = threading.Event()
            self.discovery['autoConnect'] = True
            if self.desktop is None or reset:
                self.prepare(reset=reset and self.desktop is not None)
            if self.status()['connected']:
                return self.status()
            if launch and sys.platform == 'win32':
                self._setup_state('starting', '正在后台启动 Claude')
                self.setup_mode = 'background'
                self.setup_thread = threading.Thread(target=self._connect_background,
                    args=(self.setup_cancel, self.discovery['executable'], self.discovery.get('dataHome')), daemon=True)
                self.setup_thread.start()
            else:
                self._setup_state('needs-initialization',
                    'Claude 完全退出或重新加载后需要初始化连接；可从手机发起，过程会使用电脑前台。')
            private_json(self.directory/'discovery.json', self.discovery)
            return self.status()

    def _background_state(self, cancel, state, reason):
        return self._attempt_state(cancel, state, reason)

    def _attempt_state(self, cancel, state, reason, *, allow_cancelled=False, **values):
        with self.setup_lock:
            if cancel is not self.setup_cancel or cancel.is_set() and not allow_cancelled:
                return False
            self._setup_state(state, reason, **values)
            return True

    def _connect_background(self, cancel, executable, data_home, *, foreground=False, cancel_path=None):
        def current():
            return cancel is self.setup_cancel and not cancel.is_set()
        def unavailable(state, reason):
            if not current():
                return
            if state != 'cancelled' and foreground and windows_session.status().get('interactive') is True:
                # Only an explicit initialization may use foreground setup, and
                # only when the background operation definitely sent no input.
                self._connect_native(False, cancel_path, cancel)
            else:
                self._background_state(cancel, state, reason)
        def finish_connection(submission, pid):
            if not current():
                return
            if submission not in ('submitted', 'uncertain'):
                self._background_state(cancel, 'connected', '桌面连接可用')
                return
            try:
                cleanup = native_action('close-background-devtools', pid=pid, executable=executable,
                                        cancel_path=cancel_path, cancelled=cancel)
                pending = cleanup.get('setupState') not in ('connected', 'submitted')
            except Exception:
                pending = True
            self._attempt_state(cancel, 'connected',
                'Claude 已连接；开发者工具未自动关闭，请手动关闭' if pending else '桌面连接可用',
                consoleCleanupPending=pending)
        native_started = False
        try:
            if not current():
                return
            if self.desktop and self.desktop.connected:
                self._background_state(cancel, 'connected', '桌面连接可用')
                return
            if not data_home:
                raise ValueError('未找到 Claude 数据目录，请重新扫描')
            app = background_running_app(executable, data_home, cancelled=cancel,
                                         launch_gate=self.setup_lock, initialize_console=True,
                                         explicit_home=bool(self.discovery.get('dataHomeExplicit')))
            if not self._background_state(cancel, 'connecting', 'Claude 已启动，正在等待已有桌面连接恢复'):
                return
            for _ in range(5):
                if not current():
                    return
                if self.desktop and self.desktop.connected:
                    self._background_state(cancel, 'connected', '桌面连接可用')
                    return
                if cancel.wait(.2):
                    return
            if not current():
                return
            if not self.discovery.get('dataHomeExplicit'):
                active_home = str(claude_data_home(executable, data_home))
                if active_home != data_home:
                    with self.setup_lock:
                        if not current():
                            return
                        self.discovery['dataHome'] = active_home
                        private_json(self.directory/'discovery.json', self.discovery)
                    data_home = active_home
            if not developer_mode_enabled(data_home):
                unavailable('needs-developer-mode', '请在 Claude 中确认开发者模式后重试初始化连接')
                return
            with self.setup_lock:
                if not current():
                    return
                prepared = self.prepare()
                cancel_path = cancel_path or self.directory/'cancel-native-connection'
                cancel_path.unlink(missing_ok=True)
            if not self._background_state(cancel, 'connecting', '正在后台初始化 Claude 连接'):
                return
            native_started = True
            result = native_action('connect-background', pid=app['pid'], executable=executable,
                                   script=prepared['consolePath'], cancel_path=cancel_path, cancelled=cancel)
            if not current():
                return
            if self.desktop and self.desktop.connected:
                finish_connection(result.get('submission'), app['pid'])
                return
            if result.get('submission') == 'none':
                unavailable(result.get('setupState', 'needs-initialization'),
                            result.get('reason', '当前 Claude 未提供可用的后台连接窗口，请显式初始化连接'))
                return
            # Submission (including an interrupted receipt) is not readiness.
            # Never invoke the foreground injector after input may have arrived.
            for _ in range(32):
                if not current():
                    return
                if self.desktop and self.desktop.connected:
                    finish_connection(result.get('submission'), app['pid'])
                    return
                if cancel.wait(.25):
                    return
            self._background_state(cancel, 'needs-retry',
                '后台初始化已提交，但尚未收到 Claude 连接回执，请检查目录信任或刷新状态后重试')
        except Exception as exc:
            reason = str(exc) if isinstance(exc, (ValueError, OSError)) else 'Claude 后台启动或初始化未完成，请重试'
            if native_started:
                self._background_state(cancel, 'needs-retry', reason)
            else:
                unavailable('failed', reason)

    def connect(self, restart=False, *, existing_only=False):
        """Initialize through verified background UI, with explicit foreground fallback."""
        old_monitor = None
        background_monitor = False
        with self.setup_lock:
            if existing_only:
                self._assert_reconnect_idle()
            if self.setup_cancel.is_set() and self.setup_thread:
                old_monitor = self.setup_thread
            if self.status()['connected']:
                if not restart:
                    return self.status()
                if any(row.get('status') in ('active', 'running', 'waiting') or row.get('requests')
                       for row in self.call('list').get('sessions', [])):
                    raise ValueError('Claude 有任务运行或等待确认，请先结束任务再重启')
                if self.setup_thread and self.setup_thread.is_alive():
                    self.setup_cancel.set()
                    old_monitor = self.setup_thread
            if self.setup_mode == 'background' and self.setup_thread:
                self.setup_cancel.set()
                old_monitor = self.setup_thread
                background_monitor = True
        # The monitor's final state update takes setup_lock; never join it while
        # holding that lock. This only stops our watcher, not the desktop app.
        if old_monitor:
            old_monitor.join(timeout=35 if background_monitor else 5)
        with self.setup_lock:
            if self.setup_thread and self.setup_thread.is_alive():
                if not restart and not self.setup_cancel.is_set():
                    return self.status()
                raise ValueError('请先取消正在进行的 Claude 连接')
            if not self.discovery.get('installed'):
                return self.status()
            if self.discovery.get('automaticConnection') != 'native-console':
                return self.status()
            self.setup_cancel = threading.Event()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.discovery['autoConnect'] = True
            private_json(self.directory/'discovery.json', self.discovery)
            cancel_path = self.directory/'cancel-native-connection'
            cancel_path.unlink(missing_ok=True)
            self._setup_state('connecting', '正在连接 Claude', consoleCleanupPending=False)
            if self.desktop is None and (self.directory/'connection.json').exists():
                self.prepare()
            if sys.platform == 'win32' and not restart and not existing_only:
                self.setup_mode = 'background'
                self.setup_thread = threading.Thread(target=self._connect_background,
                    args=(self.setup_cancel, self.discovery['executable'], self.discovery.get('dataHome')),
                    kwargs={'foreground': True, 'cancel_path': cancel_path}, daemon=True)
            else:
                self.setup_mode = 'native'
                self.setup_thread = threading.Thread(target=self._connect_native, args=(restart, cancel_path, self.setup_cancel),
                                                     kwargs={'existing_only': existing_only}, daemon=True)
            self.setup_thread.start()
            return self.status()

    def _await_desktop(self, state, cancel):
        self._setup_state(state['setupState'], state.get('reason', 'Claude 连接未就绪'),
                          **{k: state[k] for k in ('helperPath',) if k in state})
        result = native_action('wait-desktop', cancelled=cancel,
                               recovered=lambda: bool(self.desktop and self.desktop.connected))
        if result.get('setupState') in ('ready', 'connected'):
            return True
        self._setup_state(result.get('setupState', 'failed'), result.get('reason', 'Claude 连接未就绪'))
        return False

    def _assert_reconnect_idle(self):
        self.pending_calls = {call for call in self.pending_calls if not call.done()}
        if self.desktop:
            self.desktop.read()  # A late signed success may settle an earlier write.
        if (self.pending_calls or self.desktop and
                (self.desktop.lock.locked() or self.desktop.unconfirmed_mutations)):
            raise ValueError('Claude 仍有未确认的请求，请先在电脑端检查结果；如需重新建立连接，请在电脑端操作')

    def _connect_native(self, restart, cancel_path, cancel=None, *, existing_only=False):
        cancel = cancel if cancel is not None else self.setup_cancel
        asked_permission = False
        developer_prompt_pid = None
        try:
            # A newly created owner publishes a new signed generation. Give the
            # existing renderer time to follow it before requesting any UI work.
            if self.desktop and not restart:
                for _ in range(5):
                    if self.desktop.connected or cancel.wait(.2): break
            while not cancel.is_set():
                # Only the current renderer revision can recover without UI.
                # Older injectors may follow the generation, but connected
                # stays false until native setup loads this runtime's source.
                if self.desktop and self.desktop.connected and not restart:
                    self._attempt_state(cancel, 'connected', '桌面连接可用')
                    return
                if existing_only:
                    with self.setup_lock:
                        self._assert_reconnect_idle()
                permission = native_action('check', cancelled=cancel)
                if cancel.is_set() or cancel is not self.setup_cancel:
                    return
                if permission.get('setupState') != 'ready':
                    if permission.get('setupState') in DESKTOP_WAIT_STATES:
                        if sys.platform == 'win32':
                            self._attempt_state(cancel, permission['setupState'], permission.get('reason', 'Claude 连接未就绪'))
                            return
                        if self._await_desktop(permission, cancel): continue
                        return
                    self._setup_state(permission.get('setupState', 'failed'), permission.get('reason', 'Claude 连接未就绪'),
                                      **{k: permission[k] for k in ('helperPath',) if k in permission})
                    if permission.get('setupState') != 'needs-permission':
                        return
                    if not asked_permission:
                        native_action('request-permission', cancelled=cancel)
                        asked_permission = True
                    cancel.wait(3)
                    continue
                executable = self.discovery['executable']
                data_home = self.discovery.get('dataHome')
                if not data_home:
                    raise ValueError('未找到 Claude 数据目录，请重新扫描')
                pid = running_app(executable, data_home, restart=restart, cancelled=cancel, allow_launch=not existing_only)
                restart = False
                if cancel.is_set() or cancel is not self.setup_cancel: return
                if not self.discovery.get('dataHomeExplicit'):
                    # On first launch Claude chooses its own profile. Resolve it
                    # again before checking developer mode in that profile.
                    active_home = str(claude_data_home(executable, data_home))
                    if active_home != data_home:
                        with self.setup_lock:
                            if cancel.is_set() or cancel is not self.setup_cancel: return
                            self.discovery['dataHome'] = active_home
                            private_json(self.directory/'discovery.json', self.discovery)
                        data_home = active_home
                if not developer_mode_enabled(data_home):
                    if developer_prompt_pid != pid:
                        result = native_action('enable-devtools', pid=pid, executable=executable, cancelled=cancel)
                        if result.get('setupState') in DESKTOP_WAIT_STATES:
                            if sys.platform == 'win32':
                                self._attempt_state(cancel, result['setupState'], result.get('reason', 'Claude 连接未就绪'))
                                return
                            if self._await_desktop(result, cancel): continue
                            return
                        developer_prompt_pid = pid
                        if result.get('setupState') not in ('needs-developer-mode', 'submitted'):
                            self._attempt_state(cancel, result.get('setupState', 'failed'), result.get('reason', '请在 Claude 中确认开发者模式'))
                            return
                    self._attempt_state(cancel, 'needs-developer-mode',
                        '请确认 Claude 的开发者模式提示，完成后重新点击初始化连接' if sys.platform == 'win32'
                        else '请确认 Claude 的开发者模式提示，完成后会继续连接')
                    if sys.platform == 'win32':
                        return
                    cancel.wait(3)
                    continue
                if not self._attempt_state(cancel, 'connecting', '正在连接 Claude，请暂时保持 Console 焦点，按 Esc 可取消'):
                    return
                with self.setup_lock:
                    if cancel.is_set(): return
                    if existing_only:
                        self._assert_reconnect_idle()
                        self.reconnecting = True
                        if self.desktop and self.desktop.stopped:
                            # Old cached heartbeats cannot prove a stopped
                            # connector resumed. Require a newly generated reply.
                            self.desktop.resume_after = time.time()
                            self.desktop.write({'generation': self.desktop.generation, 'seq': self.desktop.seq, 'type': 'idle'})
                            self.desktop.stopped = False
                    prepared = self.prepare(reset=self.desktop is not None and not existing_only)
                # Signed reconnect may finish while the app was being located.
                for _ in range(5):
                    if self.desktop.connected or cancel.wait(.2): break
                if cancel.is_set(): return
                if self.desktop.connected: continue
                result = native_action('connect', pid=pid, executable=executable, script=prepared['consolePath'],
                                       cancel_path=cancel_path, cancelled=cancel)
                if result.get('setupState') != 'submitted':
                    if result.get('setupState') in DESKTOP_WAIT_STATES:
                        if sys.platform == 'win32':
                            self._attempt_state(cancel, result['setupState'], result.get('reason', 'Claude 连接未就绪'))
                            return
                        if self._await_desktop(result, cancel): continue
                        return
                    self._setup_state('needs-retry', result.get('reason', 'Claude 自动连接未完成，请点击连接重试'))
                    return
                deadline = time.monotonic() + (8 if sys.platform == 'win32' else 18)
                while not cancel.is_set() and time.monotonic() < deadline:
                    if self.desktop.connected:
                        try:
                            cleanup = native_action('close-devtools', pid=pid, executable=executable, cancelled=cancel)
                            pending = cleanup.get('setupState') not in ('connected', 'submitted')
                        except Exception:
                            pending = True
                        self._attempt_state(cancel, 'connected', 'Claude 已连接；开发者工具未自动关闭，请手动关闭' if pending else '桌面连接可用',
                                          consoleCleanupPending=pending)
                        return
                    cancel.wait(.25)
                else:
                    if not cancel.is_set():
                        result = native_action('inspect-error', pid=pid, executable=executable, cancelled=cancel)
                        if result.get('setupState') in DESKTOP_WAIT_STATES:
                            if sys.platform == 'win32':
                                self._attempt_state(cancel, result['setupState'], result.get('reason', 'Claude 连接未就绪'))
                                return
                            if self._await_desktop(result, cancel): continue
                            return
                        self._setup_state('needs-trust' if result.get('setupState') == 'needs-trust' else 'failed',
                                          result.get('reason') if result.get('setupState') == 'needs-trust'
                                          else '未收到 Claude 连接确认，请检查登录和目录信任后重试')
                    return
        except Exception as exc:
            if not cancel.is_set():
                self._attempt_state(cancel, exc.setup_state if isinstance(exc, DesktopUnavailable) else 'failed',
                                    str(exc) if isinstance(exc, ValueError) else 'Claude 自动连接未完成，请重试')
        finally:
            with self.setup_lock:
                self.reconnecting = False
            if cancel.is_set():
                self._attempt_state(cancel, 'cancelled', '已取消 Claude 连接', allow_cancelled=True)

    def cancel(self, persist=True):
        with self.setup_lock:
            self.setup_cancel.set()
            if self.directory.exists():
                (self.directory/'cancel-native-connection').touch(mode=0o600)
            if self.desktop:
                self.desktop.close()
            self._setup_state('cancelled', '已取消 Claude 连接')
            if persist and self.discovery:
                self.discovery['autoConnect'] = False
                private_json(self.directory/'discovery.json', self.discovery)
            return self.status()

    def status(self):
        connected = bool(self.desktop and self.desktop.connected and not self.setup_cancel.is_set())
        result = {**self.discovery, 'connected': connected, 'configured': connected,
                  'capabilities': self.desktop.capabilities if self.desktop else {}}
        if connected:
            result.update(setupState='connected', reason=self.discovery.get('reason') if result.get('consoleCleanupPending') else '桌面连接可用')
        elif self.discovery.get('setupState') == 'connected':
            result.update(setupState='needs-retry', reason='Claude 连接已中断，请点击连接重试')
        elif not self.discovery:
            result.update(setupState='not-scanned', reason='请扫描本机客户端')
        return result

    async def listing(self):
        rows = []; coverage = {'code': False, 'cowork': False}
        batch = (await self.desktop.call('code', 'mobileList')
                 if 'mobileList' in self.desktop.capabilities.get('code', []) else None)
        for kind in ('code', 'cowork'):
            if 'getAll' in self.desktop.capabilities.get(kind, []):
                raw = batch[kind] if batch is not None else await self.desktop.call(kind, 'getAll')
                rows.extend(model.session(kind, row) for row in model.rows(raw))
                coverage[kind] = True
        self.index = {row['id']: row for row in rows}
        return {'connected': True, 'sessions': rows, 'coverage': coverage, 'complete': all(coverage.values())}

    def session_capabilities(self, kind):
        methods = self.desktop.capabilities.get(kind, [])
        return {'models': 'setModel' in methods, 'effort': 'setEffort' in methods,
                'permissions': 'setPermissionMode' in methods, 'attachments': 'sendMessage' in methods,
                'skills': 'getSupportedCommands' in methods, 'queue': kind == 'code', 'steer': kind == 'code'}

    async def dispatch(self, action, sid, body):
        if not self.status()['connected']:
            raise BridgeUnavailable(self.status()['reason'])
        if action == 'list':
            return await self.listing()
        if action == 'account':
            return await self.desktop.call('code', 'mobileAccount')
        if action in ('projects', 'create'):
            rows = (await self.listing())['sessions']
            projects = {}
            for row in rows:
                kind = row['backend']
                roots = row['folders'] if kind == 'cowork' else [row['cwd']]
                if not roots or not roots[0] or 'start' not in self.desktop.capabilities.get(kind, []):
                    continue
                root = roots[0]; key = kind+':'+root
                projects[key] = {'key': key, 'cwd': root, 'folders': roots, 'surface': kind,
                                 'name': Path(root).name+' · '+kind, 'createRequiresMessage': True}
            if action == 'projects':
                return {'projects': list(projects.values()), 'createRequiresMessage': True}
            project = projects.get(body.get('projectKey'))
            if not project:
                raise ValueError('请选择桌面已有项目')
            message = body.get('firstMessage', body.get('text', ''))
            if not isinstance(message, str) or not message.strip() or len(message) > 100000:
                raise ValueError('请输入首条消息，Claude 将创建会话并发送此消息')
            options = {'title': body.get('title') or '新会话', 'message': message}
            if project['surface'] == 'code':
                options.update(cwd=project['cwd'], useWorktree=False)
            else:
                options['userSelectedFolders'] = project['folders']
            value = await self.desktop.call(project['surface'], 'start', options)
            return {'status': 'ready', 'result': {'id': model.gateway_id(project['surface'], value['sessionId'])}}
        if sid not in self.index:
            await self.listing()
        saved = self.index.get(sid)
        if not saved:
            raise ValueError('找不到 Claude 会话')
        kind, native = saved['backend'], saved['nativeId']
        detail = (await self.desktop.call(kind, 'mobileDetail', native)
                  if action == 'detail' and 'mobileDetail' in self.desktop.capabilities.get(kind, []) else None)
        row = model.session(kind, detail['session'] if detail is not None else await self.desktop.call(kind, 'getSession', native))
        if row['id'] != sid:
            raise BridgeUnavailable('桌面返回的会话标识不一致')
        if action == 'detail':
            row['contextUsage'] = detail.get('contextUsage') if detail is not None else None
            raw = detail['transcript'] if detail is not None else await self.desktop.call(kind, 'getTranscript', native)
            return {'connected': True, 'session': row, 'messages': model.transcript(raw), 'turns': model.turns(raw), 'notice': model.transcript_notice(raw),
                    'capabilities': self.session_capabilities(kind), 'maxRequestBytes': MAX_REQUEST}
        if action == 'catalog':
            value = await self.desktop.call(kind, 'mobileCatalog', native)
            value.update(currentModel=row.get('model'), currentEffort=row.get('effort'))
            return value
        if action == 'send':
            text = body.get('text', '')
            if body.get('plugins'):
                catalog = await self.desktop.call(kind, 'mobileCatalog', native)
                offered = {skill['id'] for skill in catalog.get('skills', []) if skill.get('selectable') is not False}
                if not isinstance(body['plugins'], list) or any(skill not in offered for skill in body['plugins']):
                    raise ValueError('技能已不可用，请刷新后选择')
                text = '请使用以下已安装技能：'+', '.join(body['plugins'])+'\n\n'+text
            images = []
            for image in body.get('resolvedImages', []):
                match = re.fullmatch(r'data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)', image.get('url', ''))
                if not match:
                    raise ValueError('图片附件格式无效')
                images.append({'mimeType': match[1], 'base64': match[2], **({'filename': image['name']} if isinstance(image.get('name'), str) else {})})
            args = [native, text, images or None]
            if kind == 'code':
                args += [None, None, ('now' if body.get('mode') == 'steer' else 'next') if row['status'] == 'active' else None,
                         None, body.get('id')]
            else:
                if body.get('mode') == 'steer':
                    raise ValueError('Cowork 未提供补充当前任务接口，请使用普通发送')
                args += [None, body.get('id')]
            result = await self.desktop.call(kind, 'sendMessage', *args, timeout=90)
        elif action == 'stop':
            result = await self.desktop.call(kind, 'interrupt' if kind == 'code' else 'stop', native)
        elif action == 'settings':
            catalog = await self.desktop.call(kind, 'mobileCatalog', native)
            selected = next((m for m in catalog['models'] if m['id'] == body.get('model', row.get('model'))), None)
            if not selected or catalog.get('capabilities', {}).get('models') is not True:
                raise ValueError('请选择桌面提供的模型')
            if body.get('effort') and body['effort'] not in selected.get('efforts', []):
                raise ValueError('该模型不支持此思考强度')
            effort = body.get('effort') or (selected.get('defaultEffort') if kind == 'cowork' else None)
            if 'effort' in body and (not self.session_capabilities(kind)['effort'] or kind == 'cowork' and not effort):
                raise ValueError('当前客户端未提供此思考强度设置')
            if selected['id'] != row.get('model'):
                await self.desktop.call(kind, 'setModel', native, selected['id'])
            if 'effort' in body:
                await self.desktop.call(kind, 'setEffort', native, effort)
            observed = model.session(kind, await self.desktop.call(kind, 'getSession', native))
            if observed.get('model') != selected['id'] or ('effort' in body and observed.get('effort') != effort):
                raise BridgeUnavailable('桌面未确认模型设置，请刷新后检查')
            result = True
        elif action == 'access':
            options = [{'value': mode, 'label': label, 'description': description} for mode, label, description in (
                ('default', '默认权限', '由 Claude 在需要时请求确认'),
                ('acceptEdits', '允许文件编辑', '允许编辑文件，其他操作沿用 Claude 的确认规则'),
                ('plan', '计划模式', '先制定计划，操作权限由 Claude 决定'))] if self.session_capabilities(kind)['permissions'] else []
            if 'mode' in body:
                if body['mode'] not in {option['value'] for option in options}:
                    raise ValueError('请选择当前客户端提供的权限选项')
                accepted = await self.desktop.call(kind, 'setPermissionMode', native, body['mode'])
                observed = model.session(kind, await self.desktop.call(kind, 'getSession', native))
                if accepted is False or observed.get('permissionMode') != body['mode']:
                    raise BridgeUnavailable('Claude 未确认权限设置，请在桌面检查组织策略')
                row = observed
            return {'status': 'accepted', 'mode': row['permissionMode'], 'modes': [option['value'] for option in options], 'options': options}
        elif action == 'respond':
            pending = next((p for p in row['requests'] if p['id'] == body.get('requestId')), None)
            if not pending:
                raise ValueError('请求已处理或过期')
            decision = {'accept': 'once', 'decline': 'deny'}.get(body.get('decision'))
            if decision is None:
                raise ValueError('请选择本次允许或拒绝')
            args = [pending['id'], decision]
            if pending['needsInput'] and decision == 'once':
                answers = body.get('answers', {})
                questions = pending['input'].get('questions', [])
                mapped = {}
                for i, question in enumerate(questions):
                    key = str(question.get('id') or question.get('question') or i)
                    value = answers.get(key, {}).get('answers')
                    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
                        raise ValueError('请回答每一项问题')
                    mapped[question.get('question') or key] = ', '.join(value)
                args.append({**pending['input'], 'answers': mapped})
            result = await self.desktop.call(kind, 'respondToToolPermission', *args)
        else:
            raise ValueError('不支持的 Claude 操作')
        if result is False or isinstance(result, dict) and any(result.get(k) is False for k in ('ok', 'delivered', 'dispatched', 'applied')):
            raise BridgeUnavailable('桌面未接受操作，请检查原会话')
        return {'status': 'accepted'}

    def call(self, action, sid=None, body=None, *, timeout=100):
        if self.loop is None:
            raise BridgeUnavailable(self.status()['reason'])
        future = self._submit_call(lambda: self.dispatch(action, sid, body or {}))
        try:
            return future.result(timeout=timeout)
        finally:
            if future.done():
                with self.setup_lock:
                    self.pending_calls.discard(future)

    def _submit_call(self, operation):
        with self.setup_lock:
            if self.reconnecting:
                raise BridgeUnavailable('Claude 正在重新连接，请稍后再试')
            # Prune on caller threads. A loop-thread done callback taking this
            # lock could deadlock prepare(), which waits for loop.initialize.
            self.pending_calls = {call for call in self.pending_calls if not call.done()}
            future = asyncio.run_coroutine_threadsafe(operation(), self.loop)
            self.pending_calls.add(future)
            return future

    def check_connection(self):
        """Probe the current connector without resetting it or replaying work."""
        if self.loop is None or self.desktop is None:
            raise BridgeUnavailable(self.status()['reason'])
        desktop = self.desktop
        async def probe():
            capabilities = desktop.capabilities
            for surface in ('code', 'cowork'):
                for method in ('mobileList', 'getAll'):
                    if method in capabilities.get(surface, []):
                        await desktop.call(surface, method, timeout=3)
                        if self.desktop is not desktop or self.setup_cancel.is_set():
                            raise BridgeUnavailable('Claude Desktop 文件桥接已断开；操作结果可能不确定')
                        return
            raise BridgeUnavailable('Claude Desktop 文件桥接未连接或不支持此操作')
        future = self._submit_call(probe)
        try:
            future.result(timeout=5)
        except Exception as exc:
            # A timed-out probe may still be writing via to_thread. Keep its
            # future and mailbox lock until that writer and its bounded RPC
            # finish, so a late read cannot overwrite the next request.
            raise BridgeUnavailable('Claude 桌面连接暂未响应，请检查电脑端状态后重试；未重启客户端') from exc
        finally:
            if future.done():
                with self.setup_lock:
                    self.pending_calls.discard(future)

    def close(self):
        # Ownership handoff must preserve explicit connection intent. A user's
        # Cancel action persists autoConnect=False instead.
        self.cancel(persist=False)
        if self.setup_thread:
            self.setup_thread.join(timeout=5)
        if self.desktop:
            self.desktop.close()
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)
            if not self.thread.is_alive():
                self.loop.close()
