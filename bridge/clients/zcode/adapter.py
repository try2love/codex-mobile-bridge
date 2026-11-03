"""Bounded, opt-in native-session prototype. No App launch, patch or replacement CLI."""
import json
import base64
import mimetypes
import hashlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

from bridge.clients.errors import BridgeUnavailable, ResumePreflightBlocked
from bridge.features.sessions.identity import same_cwd
from bridge.features.workspace.workspace import MAX_TRANSFER, Workspace
from .attachments import attachments, read_image
from .installation import installation, listener_identity


ACCESS = {'build': '变更前确认', 'guarded': '自主模式', 'yolo': '完全访问'}
LOOPBACK_PLATFORMS = ('darwin', 'win32')


def project_files(root, paths):
    if not isinstance(paths, list) or len(paths) > 10:
        raise ValueError('每条消息最多 10 个项目文件')
    workspace, result, total = Workspace(root), [], 0
    if str(workspace.root) != str(root):
        raise ValueError('项目目录已变化，请重新接入')
    if len(set(paths)) != len(paths):
        raise ValueError('不能重复引用同一文件')
    for path in paths:
        # Reuse handle-based no-symlink reads and the existing 20 MiB file boundary.
        item = workspace.read(path, download=True)
        total += item['size']
        if total > MAX_TRANSFER:
            raise ValueError('原型单次附件总量不能超过 20 MB')
        result.append({'name': item['name'], 'data': item['data'],
                       'mime': mimetypes.guess_type(item['name'])[0] or 'application/octet-stream'})
    return result


def project_snapshot(snapshot, workspace, reply_models=None):
    messages = []
    turns = [{'turnId': row['turnId'], 'sequence': row['rowId'],
              'status': {'running': 'inProgress', 'completedSuccess': 'completed',
                         'completedInterrupted': 'interrupted', 'failed': 'failed'}[row['state']]}
             for row in snapshot['rows']['window'] if row['kind'] == 'turnHeader']
    for row in snapshot['rows']['window']:
        if row.get('visibility') != 'visible' or row['kind'] not in ('userInput', 'assistantText', 'reasoning', 'toolCall'):
            continue
        message = {'id': 'zcode:%s:%s' % (snapshot['sessionId'], row['rowId']),
                   'role': {'userInput': 'user', 'assistantText': 'assistant', 'reasoning': 'reasoning',
                            'toolCall': 'activity'}[row['kind']], 'text': row.get('text', '')}
        if row['kind'] == 'assistantText':
            if isinstance(row.get('model'), str) and row['model']:
                message['model'] = row['model']
            if reply_models:
                message.update(reply_models.get(row.get('entityId'), {}))
        if row['kind'] == 'toolCall':
            message.update(title=row.get('toolName', '执行过程'), text='\n'.join(
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                for value in (row.get('inputText') or row.get('input'), row.get('output'), row.get('error')) if value))
        if row.get('attachments'):
            message['attachments'] = attachments(snapshot['sessionId'], row['attachments'], row.get('sourceCommandId'))
        if row.get('entityId'):
            target = json.dumps({'rowId': row['rowId'], 'entityId': row['entityId'], 'logEpoch': snapshot['logEpoch']}, separators=(',', ':'))
            if row['kind'] == 'assistantText' and row.get('actions', {}).get('canFork'):
                message['forkMessageId'] = target
            if row['kind'] == 'userInput' and row.get('actions', {}).get('canEdit'):
                message['editMessageId'] = target
        messages.append(message)
    control = snapshot['control']
    active = bool(control.get('activeWorks')) or bool(snapshot['pendingInteractions'])
    routing = snapshot.get('inputRouting', {}).get('mode')
    send_modes = {'send': not active and routing == 'startNow' and not snapshot.get('queue', {}).get('items'),
                  'queue': routing in ('startNow', 'enqueue', 'guide', 'choice') and not snapshot['pendingInteractions'],
                  'steer': bool(control.get('activeWorks')) and routing in ('enqueue', 'guide') and not snapshot['pendingInteractions']}
    error = control.get('lastError') if control.get('phase') == 'error' else None
    if error:
        text = error.get('message') or 'ZCode 原生任务执行失败'
        if error.get('code') == '3009' and error.get('attribution', {}).get('reason') == 'rate_limited':
            text = 'ZCode 模型服务繁忙（3009）：已达到并发限制。请稍后手动发送，或在 ZCode 中切换模型。'
        messages.append({'id': 'zcode:%s:error:%s' % (snapshot['sessionId'], error.get('at', 'current')),
                         'role': 'error', 'text': text})
    requests = []
    for interaction in snapshot['pendingInteractions']:
        payload = interaction['payload']
        item = {'id': interaction['interactionId'], 'tool': payload.get('toolName', '等待确认'),
                'input': payload.get('detail') or payload.get('input') or {}}
        if interaction['kind'] == 'userInput':
            item.update(needsInput=True, input=payload.get('input') or payload.get('request') or payload)
        elif interaction['kind'] == 'permission':
            # Show the native offered choices; never invent an allow vocabulary.
            item['decisions'] = [{'id': option['optionId'], 'label': option['label']}
                                 for option in payload.get('options', []) if option['kind'] in ('allowOnce', 'deny')]
        requests.append(item)
    foreground = [work['foregroundExecutionId'] for work in control.get('activeWorks', []) if work.get('foregroundExecutionId')]
    native_goal = snapshot.get('goal')
    goal = ({'goalId': native_goal['targetId'], 'objective': native_goal['objective'],
             'status': {'verified': 'complete', 'verifying': 'active', 'notSatisfied': 'active',
                        'failed': 'blocked'}.get(native_goal['status'], native_goal['status'])}
            if native_goal else None)
    native_usage = snapshot.get('usage', {}).get('contextWindow')
    usage = ({'usedTokens': native_usage['usedTokens'], 'contextWindow': native_usage['maxTokens'],
              'measurement': 'native', 'model': snapshot.get('config', {}).get('model'),
              **{key: native_usage[key] for key in ('autoCompactThresholdTokens', 'cache') if key in native_usage}}
             if native_usage else None)
    return {'session': {'id': snapshot['sessionId'], 'provider': 'zcode', 'host': 'local',
                        'cwd': workspace, 'title': snapshot.get('meta', {}).get('title', ''),
                        'status': 'active' if active else 'idle', 'backend': 'desktop', 'requests': requests,
                        'turnStatus': 'failed' if error else 'inProgress' if active else 'completed',
                        'model': '/'.join(filter(None, (snapshot.get('config', {}).get('provider'), snapshot.get('config', {}).get('model')))),
                        'modelDisplayName': snapshot.get('config', {}).get('model'), 'effort': snapshot.get('config', {}).get('thought'),
                        'runtimeKnown': True,
                        'permissionMode': snapshot.get('config', {}).get('mode'),
                        'permissionLabel': ACCESS.get(snapshot.get('config', {}).get('mode')),
                        'collaborationMode': 'plan' if snapshot.get('config', {}).get('planEnabled') or snapshot.get('config', {}).get('mode') == 'plan' else 'default',
                        'goal': goal, 'contextUsage': usage,
                        'contextUsageStatus': 'available' if usage else 'unavailable',
                        'stopTarget': {'executionId': foreground[0]} if len(foreground) == 1 else {},
                        'updatedAt': max((row.get('createdAt', 0) for row in snapshot['rows']['window']), default=0)}, 'messages': messages,
            'requests': snapshot['pendingInteractions'], 'control': control, 'turns': turns,
            'native': {'revision': snapshot['revision'], 'seq': snapshot['seq'], 'logEpoch': snapshot['logEpoch']},
            'history': {'hasMore': snapshot['rows']['totalCount'] > len(snapshot['rows']['window'])},
            'capabilities': {'send': any(send_modes.values()), 'sendModes': send_modes,
                             'stop': bool(control.get('canStop')) and len(foreground) == 1,
                             'attachments': True, 'uploadFiles': True, 'resume': True, 'models': True, 'effort': True,
                             'queue': send_modes['queue'], 'steer': send_modes['steer'], 'skills': True, 'permissions': snapshot.get('config', {}).get('mode') in ACCESS,
                             'tasks': True, 'rename': True, 'fork': True, 'editMessage': True, 'compact': not active and not snapshot.get('queue', {}).get('items') and
                             snapshot.get('availability', {}).get('compact', {}).get('allowed') is True,
                             'plan': True, 'goal': True, 'goalEdit': False, 'create': True}}


def detached_session(row, workspace):
    return {**row, 'provider': 'zcode', 'host': 'local', 'cwd': workspace, 'backend': 'desktop',
            'status': 'unknown', 'runtimeKnown': False, 'requests': [], 'connectionState': 'detached',
            'capabilities': {'send': False, 'resume': True, 'plan': False, 'goal': False}}


class _NoLocalTransport(BridgeUnavailable):
    """A saved prototype has no verified endpoint to reconnect to."""


class ZCodeDesktop:
    """Ordinary installation, with OS management independent of chat transport."""
    unavailable = 'ZCode 已安装，当前没有可用的本机聊天连接。'

    def __init__(self, directory, config):
        self.directory = Path(directory)
        self.config = dict(config)
        self.home = Path(config.get('dataDirectory', Path.home()/'.zcode/v2'))
        self.window_binding = None

    def _window_app(self):
        path = self.config.get('application')
        if sys.platform == 'darwin' and path:
            app = installation(path)
            if app:
                from bridge.platforms.macos.zcode import supports_window_send
                if supports_window_send():
                    return app['executable']
        return None

    def release_window(self):
        self.window_binding = None

    def call(self, action, sid=None, body=None):
        from bridge.clients.zcode.history import saved_sessions, saved_detail
        if action == 'image':
            return read_image(self.home, sid, (body or {}).get('source', {}))
        executable = self._window_app()
        if action == 'list':
            view = saved_sessions(self.home)
            if executable:
                view['sessionAccess'] = True
            return view
        if action in ('detail', 'history'):
            view = saved_detail(self.home, sid, body)
            row = view['session']
            if executable and not row['archived'] and row['title']:
                bound = self.window_binding is not None and self.window_binding[0] == sid
                caps = {**row['capabilities'], 'send': bound, 'resume': not bound, 'attachments': False,
                        'projectReferences': False, 'notifications': False, 'models': False, 'skills': False,
                        'queue': False, 'steer': False}
                row.update(capabilities=caps, readOnly=not bound, connectionState='ready' if bound else 'detached',
                           controlScope='session')
                view.update(readOnly=not bound, capabilities=caps, notice=(
                    '已接入原窗口，可发送文字消息。' if bound else '可先查看历史；继续会话时会在电脑上打开此原生聊天。'))
            return view
        if action not in ('resume', 'send') or not executable:
            raise BridgeUnavailable(self.unavailable)
        if action == 'resume':
            return self._resume_window(executable, sid, body or {})
        return self._send_window(executable, sid, body or {})

    def _resume_window(self, executable, sid, body):
        from bridge.clients.zcode.history import send_boundary
        from bridge.platforms.macos.zcode import operation
        if set(body) != {'id'}:
            raise ResumePreflightBlocked('ZCode 会话接入参数无效')
        self.release_window()
        try:
            title, _ = send_boundary(self.home, sid)
            receipt = operation('resume', executable, sid, title)
            pid = receipt.get('pid')
            if receipt.get('status') != 'ready' or type(pid) is not int or pid <= 0:
                raise ValueError('ZCode 未确认原生聊天')
        except (ValueError, OSError) as exc:
            raise ResumePreflightBlocked(str(exc)) from exc
        self.window_binding = (sid, pid)
        return {'id': body['id'], 'status': 'ready'}

    def _send_window(self, executable, sid, body):
        from bridge.clients.zcode.history import send_boundary
        from bridge.platforms.macos.zcode import operation
        if (set(body) - {'id', 'text', 'mode'} or body.get('mode', 'send') != 'send'
                or not isinstance(body.get('text'), str) or not body['text'].strip()):
            raise ResumePreflightBlocked('此 ZCode 原窗口只支持发送纯文字')
        binding = self.window_binding
        if not binding or binding[0] != sid:
            raise ResumePreflightBlocked('请先继续此 ZCode 会话')
        title, after = send_boundary(self.home, sid)
        started = int(time.time() * 1000)
        try:
            receipt = operation('send', executable, sid, title, body['text'], expected_pid=binding[1])
        except (ValueError, OSError):
            self.release_window()
            raise
        if receipt.get('status') != 'submitted' or receipt.get('pid') != binding[1]:
            self.release_window()
            raise BridgeUnavailable('ZCode 发送结果待核对')
        pending = {'id': body['id'], 'status': 'unknown', 'windowReceiptAfter': after, 'windowReceiptSince': started,
                   'message': '尚未在原聊天记录中确认这条消息，请稍后核对。'}
        for delay in (0, .1, .25, .5, 1):
            if delay:
                time.sleep(delay)
            result = self.check_window_receipt(sid, body, pending)
            if result['status'] == 'accepted':
                return result
        return pending

    def check_window_receipt(self, sid, body, pending):
        from bridge.clients.zcode.history import new_user_message
        try:
            mid = new_user_message(self.home, sid, pending['windowReceiptAfter'], pending['windowReceiptSince'], body['text'])
        except (ValueError, OSError):
            return pending
        return {'status': 'accepted', 'id': body['id'], 'nativeMessageId': mid} if mid else pending

    def status(self):
        available = (self.home/'tasks-index.sqlite').is_file()
        session_access = bool(available and self._window_app())
        return {'connected': False, 'historyAvailable': available, 'sessionAccess': session_access,
                'reason': ('可在聊天中继续原生会话。' if session_access
                           else '本机历史（只读）' if available else self.unavailable)}

    def inventory_status(self):
        return self.status()

    def account_scope(self):
        """Invalidate shared catalogs on account/provider changes, not UI settings."""
        from bridge.clients.zcode.accounts import read, account_key, SETTINGS
        settings = read(self.home/'setting.json')
        value = [{k: v for k, v in read(self.home/'credentials.json').items() if account_key(k)},
                 read(self.home/'provider_config.json'), {k: settings[k] for k in SETTINGS if k in settings}]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ZCodePrototype(ZCodeDesktop):
    def __init__(self, directory, config, transport=None, *, pipe=None):
        super().__init__(directory, config)
        self.pipe = pipe
        self.transport = transport or self._native
        self.port = config.get('debugFallbackPort', config.get('port'))
        self.sid = config.get('sessionId')
        self.workspace = str(Path(config['workspace']).resolve()) if config.get('workspace') else None
        if bool(self.workspace) != bool(self.sid) or self.sid is not None and (
                not isinstance(self.sid, str) or not self.sid.startswith('sess_')):
            raise ValueError('ZCode 原会话或工作区无效')
        if not self.sid and not self.pipe and config.get('debugFallbackPort') != 17742:
            raise ValueError('请先启用 ZCode 本机兼容连接')
        self.process = config.get('process')
        self.application_owner = None
        self.application_create = False
        self.debug_sessions = set()
        self.workspaces = {self.sid: self.workspace} if self.sid else {}
        self.bindings = {self.workspace: config['binding']} if config.get('binding') and self.workspace else {}

    @property
    def binding(self):
        return self.bindings.get(self.workspace)

    def _live_process(self):
        if self.pipe is not None:
            owner = self.pipe.owner()
            if owner is None:
                raise _NoLocalTransport('请在应用管理中初始化 ZCode 连接。')
            return owner
        if sys.platform not in LOOPBACK_PLATFORMS:
            raise _NoLocalTransport('此平台的 ZCode 原生接入尚未验证')
        application = self.config.get('application')
        app = installation(application) if application else None
        if not app:
            raise _NoLocalTransport('当前安装的 ZCode 不支持此本机实时连接。')
        try:
            return listener_identity(self.port, app['executable'])
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            reason = ('未检测到兼容端口。请确认任务已结束，在应用管理中正常退出 ZCode，再由网关启动。'
                      if self.config.get('debugFallbackPort') else '未检测到可接入的 ZCode 本机实时连接。')
            raise _NoLocalTransport(reason) from error

    def connection_owner(self, executable):
        if self.pipe is not None:
            return self.pipe.owner()
        return self.compatibility_owner(executable)

    def compatibility_owner(self, executable):
        """A free port can be used for launch. Never displace an unknown listener."""
        try:
            return listener_identity(self.port, executable, required=False)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeUnavailable('无法核验本机兼容端口，请检查 17742 是否已被其他进程占用。') from exc

    def connect_application(self, pid, *, wait=False, deadline=None):
        # Cold startup needs time to mount its native services. Only probe the App,
        # never create/select a workspace or submit a command during this retry.
        bounded_probe = deadline is not None
        deadline = deadline if bounded_probe else time.monotonic() + (10 if wait else 0)
        delay = .25
        while True:
            try:
                owner = self._live_process()
                if owner.get('pid') == pid:
                    request = {'action': 'application', 'expectedProcess': owner}
                    if bounded_probe:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise TimeoutError('原 App 会话服务尚未就绪')
                        request['expiresAt'] = int((time.time() + min(12, left)) * 1000)
                    result = self.transport(request)
                    if result.get('ready') is not True or self._live_process() != owner:
                        raise BridgeUnavailable('ZCode 原 App 未确认连接。')
                    self._adopt_process(owner)
                    self.application_create = result.get('canCreate') is True
                    return
            except (ValueError, OSError, EOFError, subprocess.TimeoutExpired) as error:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise BridgeUnavailable('ZCode 连接未就绪，请重试。' + str(error)) from error
                time.sleep(min(left, delay))
                delay = min(1, delay * 2)
                continue
            raise BridgeUnavailable('ZCode 进程已变化，请重新检查。')

    def _adopt_process(self, owner):
        if self.process != owner:
            self.application_create = False
            self.debug_sessions.clear()
            self.bindings.clear()
            self.workspaces.clear()
            self.workspace = self.sid = None
        self.application_owner = self.process = owner

    def _native(self, request):
        process = self._live_process()
        if process != request.get('expectedProcess', self.process):
            raise BridgeUnavailable('ZCode App 进程已变化，请重新核验并显式接入')
        if self.pipe is not None:
            return self.pipe.call({**request, 'expectedProcess': process})
        request = {**request, 'application': str(self.config['application']), 'port': self.port,
                   'expiresAt': min(int(time.time() * 1000) + 12000, request.get('expiresAt', float('inf')))}
        request.pop('expectedProcess', None)
        from bridge.clients.zcode.transport import request_native
        try:
            return request_native(request)
        except (OSError, TimeoutError, ValueError, RuntimeError) as error:
            raise BridgeUnavailable(str(error)[:1500]) from None


    def call(self, action, sid=None, body=None):
        if action == 'image':
            return super().call(action, sid, body)
        if not self.pipe and action in ('list', 'detail', 'history', 'resume', 'send') and self._window_app():
            if action == 'resume':
                try:
                    result = super().call(action, sid, body)
                    self.debug_sessions.discard(sid)
                    return result
                except ResumePreflightBlocked:
                    # Resume sends no message. Only this explicit, pre-dispatch failure
                    # may try the configured compatible transport (e.g. a locked UI).
                    pass
            elif sid not in self.debug_sessions and (action != 'list' or not self.debug_sessions):
                return super().call(action, sid, body)
        try:
            return self._call_live(action, sid, body)
        except (OSError, ValueError) as error:
            if action not in ('list', 'detail', 'history') or not (self.home/'tasks-index.sqlite').is_file():
                raise
            from .history import _TranscriptUnavailable, resume_workspace
            try:
                view = super().call(action, sid, body)
            except _TranscriptUnavailable:
                if action != 'detail' or self.application_owner is None:
                    raise
                workspace = resume_workspace(self.home, sid)
                target = self.transport({'action': 'target', 'sessionId': sid, 'expectedProcess': self.application_owner})
                if not same_cwd(workspace, target.get('workspace'), sys.platform == 'win32'):
                    raise BridgeUnavailable('原 App 会话或项目已变化，请刷新列表')
                row = detached_session({'id': sid, 'title': target.get('title', '')}, workspace)
                view = {'session': row, 'messages': [], 'readOnly': True, 'capabilities': row['capabilities']}
            if not isinstance(error, _NoLocalTransport):
                if action == 'list':
                    view['sessionAccess'] = True
                elif not view['session'].get('archived'):
                    view['capabilities']['resume'] = True
                    view['notice'] = '当前显示 ZCode 历史；点击继续会话连接原生运行状态。'
            return view

    def _resume_debug(self, sid, body):
        if not isinstance(sid, str) or not sid.startswith('sess_') or set(body or {}) != {'id'}:
            raise ResumePreflightBlocked('ZCode 会话接入参数无效')
        # Look up the target on the newly verified listener, never via the retired
        # process. Only commit its identity and lease after the original App agrees.
        try:
            process = self._live_process()
            expected = self.workspaces.get(sid)
            if self.config.get('debugFallbackPort') or (self.home/'tasks-index.sqlite').is_file():
                from bridge.clients.zcode.history import resume_workspace
                expected = resume_workspace(self.home, sid)
            target = {'sessionId': sid, 'expectedProcess': process}
            workspace = self.transport({**target, 'action': 'target'})['workspace']
            if not isinstance(workspace, str) or not Path(workspace).is_absolute() or (
                    expected is not None and not same_cwd(expected, workspace, sys.platform == 'win32')):
                raise ValueError('原 App 会话或项目已变化，请刷新列表')
            result = self.transport({**target, 'action': 'attach', 'workspace': workspace, 'binding': None})
            binding = result['binding']
            if not binding.get('clientId') or not binding.get('connectionId') or not result.get('subscriptionId') or (
                    not binding.get('runtime', {}).get('identity') or binding['runtime'].get('workspaceKey') != workspace):
                raise ValueError('ZCode 未确认原会话连接')
        except (OSError, ValueError, AttributeError, KeyError, TypeError) as error:
            raise ResumePreflightBlocked(str(error)) from error
        self._adopt_process(process)
        self.debug_sessions.add(sid)
        self.workspace, self.sid = workspace, sid
        self.workspaces[sid], self.bindings[workspace] = workspace, binding
        return {'status': 'ready', 'id': body['id']}

    def _call_live(self, action, sid=None, body=None):
        if action == 'resume':
            return self._resume_debug(sid, body)
        if action == 'lifecycle' and not self.sid:
            # The original renderer can still hold active leases after a gateway restart.
            return self.transport({'action': action, 'expectedProcess': self._live_process()})
        if not self.sid:
            if action == 'list' and self.application_owner is not None:
                result = self.transport({'action': 'list', 'expectedProcess': self.application_owner})
                return {'sessions': [detached_session(row, row['cwd']) for row in result['sessions']],
                        'sessionAccess': True, 'connected': False, 'readOnly': True, 'capabilities': {'create': self.application_create}}
            if action not in ('projects', 'create') or self.application_owner is None:
                self._live_process()  # Reading saved history never selects a session.
                raise BridgeUnavailable('请在要继续的 ZCode 聊天中连接原会话。')
        if action in ('account', 'lifecycle'):
            return self.transport({'action': action, 'workspace': self.workspace, 'sessionId': self.sid,
                                   'expectedRuntime': (self.binding or {}).get('runtime')})
        if action == 'list':
            result = self.transport({'action': 'list', 'workspace': self.workspace, 'sessionId': self.sid,
                                     'binding': self.binding})
            rows = [project_snapshot(row['snapshot'], row['cwd'])['session'] if 'snapshot' in row
                    else detached_session(row, row['cwd']) for row in result['sessions']]
            self.workspaces.update({row['id']: row['cwd'] for row in rows})
            return {'sessions': rows, 'connected': True}
        if action == 'projects':
            result = self.transport({'action': action, 'workspace': self.workspace, 'sessionId': self.sid})
            if self.workspace and not any(row['cwd'] == self.workspace for row in result['projects']):
                result['projects'].append({'key': self.workspace, 'cwd': self.workspace,
                                           'name': Path(self.workspace).name, 'surface': 'desktop', 'host': 'local'})
            return {**result, 'browseDirectories': True}
        if action == 'create':
            if not isinstance(body.get('projectKey'), str) or not body['projectKey']:
                raise ValueError('请选择 ZCode 项目')
            uuid.UUID(body.get('id', ''))
            if not isinstance(body.get('firstMessage'), str) or not 1 <= len(body['firstMessage'].strip()) <= 100000:
                raise ValueError('请输入首条消息')
            if not self.binding and not self.application_create:
                raise BridgeUnavailable('必须先显式接入原生项目')
            # Managed application connections use the selected project, independent
            # of which conversation was last visited. Legacy leases keep their config.
            plain_workspace = self.binding and body['projectKey'] == self.workspace and not body.get('directoryProject')
            context = ({'expectedProcess': self.application_owner} if self.application_create and not plain_workspace else
                       {'workspace': self.workspace, 'sessionId': self.sid, 'binding': self.binding})
            result = self.transport({'action': action, **context, 'body': body})
            workspace, binding = result.pop('workspace', None), result.pop('binding', None)
            if result.get('status') == 'accepted' and workspace:
                self.workspaces[result['result']['id']] = workspace
                if binding:
                    self.bindings[workspace] = binding
                    if not self.sid:
                        self.sid, self.workspace = result['result']['id'], workspace
                        self.debug_sessions.add(self.sid)
            return result
        if not isinstance(sid, str) or not sid.startswith('sess_'):
            raise ValueError('ZCode 会话标识无效')
        if action not in ('attach', 'detach', 'resume', 'history', 'detail', 'receipt', 'send', 'stop', 'respond', 'catalog', 'settings', 'access', 'work-mode', 'goal', 'compact', 'tasks', 'stop-task', 'fork', 'edit-message', 'rename'):
            raise ValueError('尚未验证的 ZCode 操作')
        body = dict(body or {})
        if action == 'access':
            if 'mode' not in body:
                detail = self.call('detail', sid)
                if not detail['capabilities'].get('permissions'):
                    raise ValueError('当前会话未提供权限切换')
                return {'mode': detail['session']['permissionMode'],
                        'options': [{'value': mode, 'label': label, 'requiresConfirmation': mode == 'yolo'}
                                    for mode, label in ACCESS.items()]}
            if body.get('mode') not in ACCESS or set(body) - {'id', 'mode', 'confirmed'} or (
                    body['mode'] == 'yolo' and body.get('confirmed') is not True):
                raise ResumePreflightBlocked('请确认本次权限切换')
        if action == 'rename':
            body.setdefault('id', str(uuid.uuid4()))  # Outer RenameCommands owns durable deduplication.
        if sid not in self.workspaces:
            self.workspaces[sid] = self.transport({'action': 'target', 'workspace': self.workspace,
                                                  'sessionId': sid})['workspace']
        workspace = self.workspaces[sid]
        request = {'action': action, 'workspace': workspace, 'sessionId': sid,
                   'anchorSessionId': self.sid,
                   'binding': None if action == 'attach' else self.bindings.get(workspace)}
        if action in ('send', 'stop', 'respond', 'settings', 'access', 'work-mode', 'goal', 'compact', 'stop-task', 'fork', 'edit-message', 'rename'):
            uuid.UUID(body.get('id', ''))
            if not request['binding']:
                raise BridgeUnavailable('必须先显式接入原生测试会话')
        if action == 'send':
            if not isinstance(body.get('text'), str) or not 1 <= len(body['text'].strip()) <= 100000:
                raise ValueError('请输入 1–100000 字的消息')
            if set(body) - {'id', 'text', 'mode', 'projectFiles', 'resolvedReferences', 'resolvedImages', 'resolvedFiles', 'plugins'}:
                raise ValueError('原型只接受文字和当前项目文件')
            skills = body.get('plugins', [])
            if not isinstance(skills, list) or len(skills) > 8 or any(not isinstance(v, str) for v in skills) or len(set(skills)) != len(skills):
                raise ValueError('最多选择 8 个有效 Skill')
            if body.get('mode', 'send') not in ('send', 'queue', 'steer'):
                raise ValueError('发送方式无效')
            body['files'] = project_files(workspace, body.pop('projectFiles', []))
            references = body.pop('resolvedReferences', [])
            paths = [str(Path(item['path']).relative_to(workspace)) for item in references]
            body['files'].extend(project_files(workspace, paths))
            for item in body.pop('resolvedImages', []):
                header, data = item['url'].split(',', 1)
                body['files'].append({'name': item['name'], 'mime': header[5:].split(';')[0], 'data': data})
            for item in body.pop('resolvedFiles', []):
                path = Path(item['path'])
                file = Workspace(path.parent).read(path.name, download=True)
                body['files'].append({'name': item['name'], 'mime': mimetypes.guess_type(item['name'])[0] or
                                     'application/octet-stream', 'data': file['data']})
            if len(body['files']) > 10 or sum(len(base64.b64decode(f['data'], validate=True)) for f in body['files']) > MAX_TRANSFER:
                raise ValueError('最多 10 个附件，附件总量不能超过 20 MB')
        if action in ('fork', 'edit-message'):
            try:
                target = json.loads(body['messageId'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('消息标识无效') from None
            if not isinstance(target, dict) or set(target) != {'rowId', 'entityId', 'logEpoch'}:
                raise ValueError('消息标识无效')
            body['target'] = target
        if action == 'respond' and 'requestId' in body:
            body['interactionId'] = body.pop('requestId')
            decision = body.pop('decision')
            if decision == 'accept':
                answers = {key: ', '.join(value['answers']) for key, value in body.pop('answers', {}).items()}
                body['answer'] = {'action': 'accept', 'content': {'answers': answers}}
            else:
                body['answer'] = {'optionId': decision}
        if action == 'receipt':
            request['commandId'] = str(uuid.UUID(body['commandId']))
        if action == 'settings' and (set(body) - {'id', 'model', 'effort', 'modelCatalogIdentity'} or
                                    not isinstance(body.get('model'), str) or not isinstance(body.get('effort', ''), str)):
            raise ValueError('模型设置格式无效')
        request['body'] = body
        detached = action == 'detail' and not request['binding']
        if detached:
            request['action'] = 'history'
        result = self.transport(request)
        if action == 'detail':
            if detached:
                result = {'detached': True, 'history': result}
            if result.get('detached'):
                history = result['history']
                row = history['session']
                messages = []
                for item in history['messages']:
                    info = item['info']
                    if info.get('semantics', {}).get('uiVisibility') != 'visible':
                        continue
                    message = {'id': info['messageId'], 'role': info['role'],
                               'text': '\n'.join(part.get('text', '') for part in item.get('parts', []) if part.get('type') == 'text')}
                    if info['role'] == 'user':
                        metadata = info.get('metadata') or {}
                        intent = metadata.get('conversationInputIntent') or metadata.get('inputIntent') or {}
                        files = attachments(sid, intent.get('attachments') or intent.get('attachmentRefs'), intent.get('sourceCommandId'))
                        if files:
                            message['attachments'] = files
                    messages.append(message)
                from .reply_metadata import native_reply_models
                models = native_reply_models(self.home, sid, workspace, (m['id'] for m in messages if m['role'] == 'assistant'))
                for message in messages:
                    message.update(models.get(message['id'], {}))
                session = detached_session({'id': row['sessionId'], 'title': row.get('title', '')}, workspace)
                return {'session': session, 'messages': messages, 'capabilities': session['capabilities'],
                        'notice': '当前显示 ZCode 历史；点击继续会话连接原生运行状态。'}
            from .reply_metadata import native_reply_models
            snapshot = result['snapshot']
            models = native_reply_models(self.home, sid, workspace, (row.get('entityId') for row in
                snapshot['rows']['window'] if row['kind'] == 'assistantText' and row.get('visibility') == 'visible'))
            value = project_snapshot(snapshot, workspace, models)
            value['capabilities'].update(result.get('capabilities', {}))
            if sid in self.debug_sessions:
                # The client inventory may still describe the previous connection.
                value['session'].update(controlScope='session', connectionState='ready')
            value['draftAccountIdentity'] = result.get('account', {}).get('identity', '')
            if value['history']['hasMore']:
                value['notice'] = '当前显示 ZCode 已加载的历史；更早内容请在原 App 中加载。'
                value['history']['hasMore'] = False
            return value
        if action in ('send', 'stop', 'respond', 'settings', 'access', 'work-mode', 'goal', 'compact', 'stop-task', 'fork', 'edit-message', 'rename'):
            status = result['status']
            if action == 'send' and status == 'accepted' and result.get('result', {}).get('delivery') == 'queue':
                status = 'queued'
            if action in ('settings', 'access', 'work-mode') and status == 'noop' and result.get('reasonCode') == 'config.unchanged':
                status = 'accepted'
            extra = {}
            if action in ('fork', 'edit-message') and status == 'accepted':
                native = result.get('result', {})
                if not native.get('sessionId') or native.get('disposition') == 'blocked':
                    status = 'rejected'
                else:
                    self.workspaces[native['sessionId']] = workspace
                    extra['result'] = {'id': native['sessionId']}
            return {'id': body['id'], 'status': status, 'nativeAck': result, **extra}
        return result

    def reconnect_existing(self):
        try:
            self.call('resume', self.sid, {'id': str(uuid.uuid4())})
            return True
        except (OSError, ValueError):
            return False

    def inventory_status(self):
        # The app inventory does not need a full conversation. Model lookup and
        # operation-specific callers retain their live session status check.
        if self.application_create or not self.sid:
            return self._application_status()
        return self.status()

    def _application_status(self):
        base = super().status()
        if self.application_owner is not None:
            try:
                if self._live_process() == self.application_owner:
                    return {**base, 'sessionAccess': True, 'canCreate': self.application_create,
                            'reason': '可在聊天中继续原生会话。'}
            except _NoLocalTransport as error:
                return {**base, 'reason': str(error)}
        return base

    def status(self):
        if not self.sid:
            return self._application_status()
        if not self.pipe and self._window_app() and not self.debug_sessions:
            return super().status()
        try:
            detail = self._call_live('detail', self.sid)
            if detail.get('native'):
                return {'connected': True, 'canCreate': detail['capabilities'].get('create') is True}
        except _NoLocalTransport as error:
            return {**super().status(), 'reason': str(error)}
        except (ValueError, OSError):
            pass
        # A compatible live endpoint can renew a retired session binding.
        return {**super().status(), 'retryable': True}
