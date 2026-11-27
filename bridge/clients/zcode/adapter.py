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
from .transport import operation_timeout
from .presentation import COMPOSER_CAPABILITIES, composer_presentation


ACCESS = {'build': '变更前确认', 'guarded': '自主模式', 'yolo': '完全访问'}
SAVED_SYNC_CURSOR = 'zcode-saved-sync.'
LOOPBACK_PLATFORMS = ('darwin', 'win32')


def project_files(root, paths):
    if not isinstance(paths, list) or len(paths) > 10:
        raise ValueError('每条消息最多 10 个项目文件')
    if not paths:
        return []
    try:
        workspace = Workspace(root)
    except (OSError, ValueError) as error:
        raise ResumePreflightBlocked('ZCode 原项目目录不可用，请重新选择有效项目') from error
    result, total = [], 0
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


TURN_STATES = {'running': 'inProgress', 'completedSuccess': 'completed',
               'completedInterrupted': 'interrupted', 'failed': 'failed'}


def _dict(value):
    # ZCode 快照与原生回执中的嵌套对象可能显式返回 None，统一转为字典防止 AttributeError。
    return value if isinstance(value, dict) else {}


def _project_available(workspace):
    try:
        return bool(workspace) and Path(workspace).is_absolute() and Path(workspace).is_dir()
    except (OSError, TypeError, ValueError):
        return False


def saved_prefix(home, sid, workspace, native_rows):
    """Find the exact predecessor boundary when both native sources agree."""
    from .history import saved_detail
    oldest = next((row for row in native_rows if row.get('visibility') == 'visible'
                   and row.get('kind') in ('userInput', 'assistantText')), None)
    identifier = oldest.get('entityId') if oldest else None
    if not isinstance(identifier, str) or not identifier:
        return None
    try:
        saved = saved_detail(home, sid, {'historyBefore': identifier, 'historyLimit': 1, 'historySync': True})
    except (OSError, ValueError, BridgeUnavailable):
        return None
    if not same_cwd(workspace, saved['session']['cwd'], sys.platform == 'win32') or not saved['messages']:
        return None
    return identifier


def project_snapshot(snapshot, workspace, reply_models=None):
    project_available = _project_available(workspace)
    messages = []
    # 未知的 turnHeader.state 不能直接下标索引（KeyError 会被 HTTP 层误判为 404），
    # 也不应伪造 completed/failed，以免误触发完成或异常通知。
    turns = [{'turnId': row['turnId'], 'sequence': row['rowId'],
              'status': TURN_STATES.get(row.get('state'), row.get('state') or 'unknown')}
             for row in snapshot['rows']['window'] if row['kind'] == 'turnHeader']
    headers = {row.get('productTurnId') or row['turnId']: row
               for row in snapshot['rows']['window'] if row['kind'] == 'turnHeader'}
    for row in snapshot['rows']['window']:
        if row.get('visibility') != 'visible' or row['kind'] not in ('userInput', 'assistantText', 'reasoning', 'toolCall'):
            continue
        message = {'id': 'zcode:%s:%s' % (snapshot['sessionId'], row['rowId']),
                   'role': {'userInput': 'user', 'assistantText': 'assistant', 'reasoning': 'reasoning',
                            'toolCall': 'activity'}[row['kind']], 'text': row.get('text', '')}
        turn_id = row.get('productTurnId') or row.get('turnId')
        if isinstance(turn_id, str) and turn_id:
            message['turnId'] = turn_id
            header = headers.get(turn_id)
            if header:
                message['turnStatus'] = TURN_STATES.get(header.get('state'), 'unknown')
                for source, target in (('startedAt', 'turnStartedAt'), ('endedAt', 'turnCompletedAt')):
                    if header.get(source) is not None:
                        message[target] = header[source]
        if row['kind'] in ('userInput', 'assistantText') and row.get('createdAt') is not None:
            message['timestamp'] = row['createdAt']
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
            actions = _dict(row.get('actions'))
            if row['kind'] == 'assistantText' and actions.get('canFork'):
                message['forkMessageId'] = target
            if row['kind'] == 'userInput' and actions.get('canEdit'):
                message['editMessageId'] = target
        messages.append(message)
    control = _dict(snapshot.get('control'))
    config = _dict(snapshot.get('config'))
    queue_items = _dict(snapshot.get('queue')).get('items')
    works = control.get('activeWorks')
    latest_header = next((row for row in reversed(snapshot['rows']['window']) if row['kind'] == 'turnHeader'), None)
    latest_turn = turns[-1]['status'] if turns else None
    runtime_known = (isinstance(snapshot.get('control'), dict) and isinstance(works, list) and
                     all(isinstance(work, dict) for work in works) and isinstance(queue_items, list) and
                     (latest_header is None or latest_header.get('state') in TURN_STATES))
    active = bool(works) or bool(snapshot['pendingInteractions']) or bool(queue_items) or latest_turn == 'inProgress'
    routing = _dict(snapshot.get('inputRouting')).get('mode')
    send_modes = {'send': runtime_known and not active and routing == 'startNow',
                  'queue': runtime_known and routing in ('startNow', 'enqueue', 'guide', 'choice') and not snapshot['pendingInteractions'],
                  'steer': runtime_known and bool(works) and routing in ('enqueue', 'guide') and not snapshot['pendingInteractions']}
    error = control.get('lastError') if control.get('phase') == 'error' else None
    if isinstance(error, dict):
        text = error.get('message') or 'ZCode 原生任务执行失败'
        if error.get('code') == '3009' and _dict(error.get('attribution')).get('reason') == 'rate_limited':
            text = 'ZCode 模型服务繁忙（3009）：已达到并发限制。请稍后手动发送，或在 ZCode 中切换模型。'
        messages.append({'id': 'zcode:%s:error:%s' % (snapshot['sessionId'], error.get('at', 'current')),
                         'role': 'error', 'text': text})
    requests = []
    for interaction in snapshot['pendingInteractions']:
        payload = _dict(interaction.get('payload'))
        item = {'id': interaction['interactionId'], 'tool': payload.get('toolName', '等待确认'),
                'input': payload.get('detail') or payload.get('input') or {}}
        if interaction['kind'] == 'userInput':
            item.update(needsInput=True, input=payload.get('input') or payload.get('request') or payload)
        elif interaction['kind'] == 'permission':
            # Show the native offered choices; never invent an allow vocabulary.
            item['decisions'] = [{'id': option['optionId'], 'label': option['label']}
                                 for option in (payload.get('options') or []) if option['kind'] in ('allowOnce', 'deny')]
        requests.append(item)
    foreground = [work['foregroundExecutionId'] for work in (works if isinstance(works, list) else [])
                  if isinstance(work, dict) and work.get('foregroundExecutionId')]
    native_goal = snapshot.get('goal')
    goal = ({'goalId': native_goal['targetId'], 'objective': native_goal['objective'],
             'status': {'verified': 'complete', 'verifying': 'active', 'notSatisfied': 'active',
                        'failed': 'blocked'}.get(native_goal['status'], native_goal['status'])}
            if isinstance(native_goal, dict) else None)
    native_usage = _dict(_dict(snapshot.get('usage')).get('contextWindow'))
    usage = ({'usedTokens': native_usage['usedTokens'], 'contextWindow': native_usage['maxTokens'],
              'measurement': 'native', 'model': config.get('model'),
              **{key: native_usage[key] for key in ('autoCompactThresholdTokens', 'cache') if key in native_usage}}
             if native_usage else None)
    value = {'session': {'id': snapshot['sessionId'], 'provider': 'zcode', 'host': 'local',
                        'cwd': workspace, 'title': _dict(snapshot.get('meta')).get('title', ''),
                        'status': 'active' if runtime_known and active else 'idle' if runtime_known else 'unknown', 'backend': 'desktop', 'requests': requests,
                        'turnStatus': 'failed' if error else 'unknown' if not runtime_known else 'inProgress' if active else 'completed',
                        'model': '/'.join(filter(None, (config.get('provider'), config.get('model')))),
                        'modelDisplayName': config.get('model'), 'effort': config.get('thought'),
                        'composerCapabilities': dict(COMPOSER_CAPABILITIES),
                        'runtimeKnown': runtime_known,
                        'permissionMode': config.get('mode'),
                        'permissionLabel': ACCESS.get(config.get('mode')),
                        'collaborationMode': 'plan' if config.get('planEnabled') or config.get('mode') == 'plan' else 'default',
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
                             'projectReferences': project_available,
                             'queue': send_modes['queue'], 'steer': send_modes['steer'], 'skills': True, 'permissions': config.get('mode') in ACCESS,
                             'tasks': True, 'rename': True, 'fork': True, 'editMessage': True, 'compact': runtime_known and not active and not queue_items and
                             _dict(_dict(snapshot.get('availability')).get('compact')).get('allowed') is True,
                             'plan': True, 'goal': True, 'goalEdit': False, 'create': True}}
    if not project_available:
        value['notice'] = '原项目目录已不可用；文字会话仍可继续，项目文件、终端和 Git 暂不可用。'
    return value


def detached_session(row, workspace):
    row = dict(row)
    native_rename = row.pop('nativeRename', False) is True
    return {**row, 'provider': 'zcode', 'host': 'local', 'cwd': workspace, 'backend': 'desktop',
            **composer_presentation(row.get('model'), row.get('effort')),
            'status': 'unknown', 'runtimeKnown': False, 'requests': [], 'connectionState': 'detached',
            'capabilities': {'send': False, 'resume': True, 'plan': False, 'goal': False, 'rename': native_rename}}


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

    def record_path(self, sid):
        from bridge.clients.zcode.history import saved_target
        row = saved_target(self.home, sid)
        return str(self.home/'tasks-index.sqlite' if row.get('indexOnly') else self.home.parent/'cli/db/db.sqlite')

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
        if not self.sid and not self.pipe and transport is None and config.get('debugFallbackPort') != 17742:
            raise ValueError('请先启用 ZCode 本机兼容连接')
        self.process = config.get('process')
        self.application_owner = None
        self.application_create = False
        self.application_start = None
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

    def release_window(self):
        super().release_window()
        self.application_start = None

    def _confirm_application(self, owner, result):
        if not isinstance(result, dict) or result.get('ready') is not True or self._live_process() != owner:
            raise BridgeUnavailable('ZCode 原 App 未确认连接。')
        self._adopt_process(owner)
        self.application_create = result.get('canCreate') is True

    def start_private_application(self, owner):
        # Only the verified gateway-owned original may enter this state. Inventory
        # observations never launch an App, acquire a chat, or send a command.
        self.application_start = {'owner': owner, 'id': str(uuid.uuid4()), 'stage': -1, 'until': time.monotonic() + 180,
                                  'next': 0, 'delay': 2}
        state = self.observe_application_start()
        if state and state['connectionState'] == 'error':
            raise BridgeUnavailable(state['reason'])

    def observe_application_start(self):
        attempt = self.application_start
        if not attempt:
            return None
        def failed(phase, reason):
            if self.application_start is not attempt:
                return None
            self._adopt_process(None)
            self.application_start = {'failure': {'connectionState': phase, 'reason': reason}}
            return self.application_start['failure']
        if 'failure' in attempt:
            return attempt['failure']
        now = time.monotonic()
        if now >= attempt['until']:
            return failed('timeout', 'ZCode 的会话服务长时间未就绪，请重试连接。')
        waiting = {'connectionState': 'connecting', 'reason': 'ZCode 已启动，正在连接原生会话服务。'}
        if now < attempt['next']:
            return waiting
        owner = attempt['owner']
        try:
            if self._live_process() != owner:
                raise BridgeUnavailable('ZCode 原进程已变化，请重新连接。')
            try:
                result = self.transport({'action': 'application', 'expectedProcess': owner,
                                         'body': {'startupId': attempt['id']},
                                         'expiresAt': int((time.time() + min(12, max(3, attempt['delay']))) * 1000)})
            except (TimeoutError, subprocess.TimeoutExpired):
                result = {'ready': False, 'stage': 'waiting'}
            if self.application_start is not attempt:
                return None
            if result.get('ready') is True:
                self._confirm_application(owner, result)
                self.application_start = None
                return None
            stages = ('waiting', 'window', 'services', 'handshake', 'projects')
            if result.get('ready') is not False or result.get('stage') not in stages:
                raise BridgeUnavailable('ZCode 原 App 未确认会话服务。')
            stage = stages.index(result['stage'])
        except (OSError, EOFError, ValueError, AttributeError, subprocess.SubprocessError) as error:
            return failed('error', (str(error).splitlines() or ['ZCode 原生连接失败，请重试。'])[0])
        self._adopt_process(None)
        now = time.monotonic()
        if stage > attempt['stage']:
            attempt.update(stage=stage, until=now + 180, delay=2)
        attempt.update(next=now + attempt['delay'], delay=min(20, attempt['delay'] * 2))
        return waiting

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
                    self._confirm_application(owner, result)
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
                   'expiresAt': min(int((time.time() + operation_timeout(request['action'])) * 1000),
                                    request.get('expiresAt', float('inf')))}
        request.pop('expectedProcess', None)
        from bridge.clients.zcode.transport import request_native
        try:
            return request_native(request)
        except (OSError, TimeoutError, ValueError, RuntimeError) as error:
            raise BridgeUnavailable(str(error)[:1500]) from None


    def call(self, action, sid=None, body=None):
        if action == 'image':
            return super().call(action, sid, body)
        before = (body or {}).get('historyBefore') or ''
        if action == 'detail' and (body or {}).get('historySync') and before.startswith(SAVED_SYNC_CURSOR):
            from .history import saved_detail
            value = saved_detail(self.home, sid, {**body, 'historyBefore': before.removeprefix(SAVED_SYNC_CURSOR)})
            history = value['history']
            history['complete'] = False  # The native window and saved prefix can have different process evidence.
            if history.get('before'):
                history['before'] = SAVED_SYNC_CURSOR + history['before']
            return value
        if not self.pipe and action in ('list', 'detail', 'history', 'resume', 'send') and self._window_app():
            if action == 'resume':
                ready = self._try_window_resume(sid, body)
                if ready is not None:
                    return ready
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

    def _try_window_resume(self, sid, body):
        try:
            result = super().call('resume', sid, body)
        except ResumePreflightBlocked:
            # Only an explicit operation rejected before dispatch may use the compatible transport.
            return None
        self.debug_sessions.discard(sid)
        return result

    def _resume_debug(self, sid, body):
        if not isinstance(sid, str) or not sid.startswith('sess_') or set(body or {}) != {'id'}:
            raise ResumePreflightBlocked('ZCode 会话接入参数无效')
        result, _ = self._resume_transaction(sid, body['id'], progressive=False)
        return result

    def resume_step(self, sid, request_id, continuation=None):
        if not isinstance(sid, str) or not sid.startswith('sess_') or not isinstance(request_id, str):
            raise ResumePreflightBlocked('ZCode 会话接入参数无效')
        try:
            uuid.UUID(request_id)
        except ValueError as error:
            raise ResumePreflightBlocked('ZCode 会话接入参数无效') from error
        if continuation is None and not self.pipe and self._window_app():
            ready = self._try_window_resume(sid, {'id': request_id})
            if ready is not None:
                return ready, None
        return self._resume_transaction(sid, request_id, progressive=True, continuation=continuation)

    def _resume_transaction(self, sid, request_id, *, progressive, continuation=None):
        try:
            process = self._live_process()
            target = {'sessionId': sid, 'expectedProcess': process}
            if progressive:
                # Each visit observes a native subscription; its lifetime is independent of this RPC.
                target['expiresAt'] = int((time.time() + 8) * 1000)
            if continuation is None:
                expected = self.workspaces.get(sid)
                if self.config.get('debugFallbackPort') or (self.home/'tasks-index.sqlite').is_file():
                    from bridge.clients.zcode.history import resume_workspace
                    expected = resume_workspace(self.home, sid)
                workspace = self.transport({**target, 'action': 'target'})['workspace']
                if not isinstance(workspace, str) or not Path(workspace).is_absolute() or (
                        expected is not None and not same_cwd(expected, workspace, sys.platform == 'win32')):
                    raise ValueError('原 App 会话或项目已变化，请刷新列表')
            else:
                adapter, original, session_id, operation, workspace = continuation
                if adapter is not self or (original, session_id, operation) != (process, sid, request_id):
                    raise ValueError('原 App 会话或接续已变化，请重新连接')
            args = {**target, 'action': 'attach', 'workspace': workspace, 'binding': None}
            if progressive:
                args['body'] = {'resumeId': request_id, 'observe': continuation is not None}
            result = self.transport(args)
            if progressive and result.get('status') == 'pending':
                progress = result.get('progress')
                if type(progress) is not int or progress < 0 or result.get('stage') != 'restoring':
                    raise ValueError('ZCode 接续状态无效')
                return ({'id': request_id, 'status': 'pending', 'stage': 'restoring', 'progress': progress},
                        (self, process, sid, request_id, workspace))
            binding = result['binding']
            if not binding.get('clientId') or not binding.get('connectionId') or not result.get('subscriptionId') or (
                    not binding.get('runtime', {}).get('identity') or binding['runtime'].get('workspaceKey') != workspace):
                raise ValueError('ZCode 未确认原会话连接')
            if progressive and self._live_process() != process:
                raise ValueError('原 App 进程已变化，请重新接续')
        except (OSError, EOFError, ValueError, AttributeError, KeyError, TypeError) as error:
            raise ResumePreflightBlocked(str(error)) from error
        self._adopt_process(process)
        self.debug_sessions.add(sid)
        self.workspace, self.sid = workspace, sid
        self.workspaces[sid], self.bindings[workspace] = workspace, binding
        return {'status': 'ready', 'id': request_id}, None

    def _call_live(self, action, sid=None, body=None):
        if action == 'resume':
            return self._resume_debug(sid, body)
        if action == 'rename':
            body = dict(body or {})
            title = body.get('title')
            if (not isinstance(sid, str) or not sid.startswith('sess_') or
                    not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or
                    any(ord(char) < 32 or ord(char) == 127 for char in title) or set(body)-{'title', 'id'}):
                raise ResumePreflightBlocked('原生改名请求无效')
            body.setdefault('id', str(uuid.uuid4()))
            uuid.UUID(body['id'])
            body['title'] = title.strip()
            try:
                owner = self._live_process()
                if self.application_owner != owner:
                    raise ValueError('ZCode 原 App 连接已变化，请先重新连接')
                target = self.transport({'action': 'target', 'sessionId': sid, 'expectedProcess': owner})
                workspace = target['workspace']
                if not isinstance(workspace, str) or not Path(workspace).is_absolute():
                    raise ValueError('原会话工作目录无法确认')
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ResumePreflightBlocked(str(error)) from error
            result = self.transport({'action': 'rename', 'sessionId': sid, 'workspace': workspace,
                                     'expectedProcess': owner, 'body': body})
            if result.get('notSent') is True:
                return result
            if result.get('status') != 'confirmed' or result.get('result') != {'id': sid, 'title': body['title']}:
                raise BridgeUnavailable('改名结果待核对，请检查原客户端；不会重复提交')
            return {**result, 'id': body['id']}
        if action in ('account', 'quota') and self.application_owner is not None:
            return self.transport({'action': action, 'expectedProcess': self.application_owner,
                                   **({'body': body or {}} if action == 'quota' else {})})
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
            rows = []
            for item in result['sessions']:
                row = (project_snapshot(item['snapshot'], item['cwd'])['session']
                       if isinstance(item.get('snapshot'), dict) else detached_session(item, item['cwd']))
                if 'title' in item:
                    row['title'] = item['title']
                row['capabilities'] = {**row.get('capabilities', {}), 'rename': item.get('nativeRename') is True}
                rows.append(row)
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
                created_sid = result['result']['id']
                self.workspaces[created_sid] = workspace
                if binding:
                    self.bindings[workspace] = binding
                    self.debug_sessions.add(created_sid)
                    if not self.sid:
                        self.sid, self.workspace = created_sid, workspace
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
            project_paths = body.pop('projectFiles', [])
            references = body.pop('resolvedReferences', [])
            paths = [str(Path(item['path']).relative_to(workspace)) for item in references]
            body['files'] = project_files(workspace, project_paths + paths)
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
            decision = body.get('decision')
            if not isinstance(decision, str) or not decision:
                # 在发出原生 IPC 前明确拦截缺失或非法的决定，避免在 requests.sqlite 中残留 unknown。
                raise ResumePreflightBlocked('请选择本次允许或拒绝')
            raw_answers = body.get('answers')
            if decision == 'accept':
                if raw_answers is None:
                    raw_answers = {}
                if not isinstance(raw_answers, dict) or any(
                        not isinstance(value, dict) or not isinstance(value.get('answers'), list)
                        or any(not isinstance(item, str) for item in value['answers'])
                        for value in raw_answers.values()):
                    raise ResumePreflightBlocked('提问回复格式无效')
                answers = {key: ', '.join(value['answers']) for key, value in raw_answers.items()}
                body['answer'] = {'action': 'accept', 'content': {'answers': answers}}
            else:
                body['answer'] = {'optionId': decision}
            body['interactionId'] = body.pop('requestId')
            body.pop('decision', None)
            body.pop('answers', None)
        if action == 'receipt':
            request['commandId'] = str(uuid.UUID(body['commandId']))
        if action == 'settings' and (set(body) - {'id', 'model', 'effort', 'modelCatalogIdentity', 'manualModel', 'manualEffort'} or
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
                    if _dict(info.get('semantics')).get('uiVisibility') != 'visible':
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
                        'history': {'hasMore': False, 'complete': False},
                        'notice': '当前显示 ZCode 历史；点击继续会话连接原生运行状态。'}
            from .reply_metadata import native_reply_models
            snapshot = result['snapshot']
            models = native_reply_models(self.home, sid, workspace, (row.get('entityId') for row in
                snapshot['rows']['window'] if row['kind'] == 'assistantText' and row.get('visibility') == 'visible'))
            value = project_snapshot(snapshot, workspace, models)
            value['capabilities'].update(result.get('capabilities', {}))
            if not _project_available(workspace):
                value['capabilities']['projectReferences'] = False
            if not value['session']['runtimeKnown']:
                # Capability advertisements cannot turn an unknown snapshot into idle evidence.
                value['capabilities'].update(send=False, queue=False, steer=False, compact=False,
                                             sendModes=dict.fromkeys(('send', 'queue', 'steer'), False))
            if sid in self.debug_sessions:
                # The client inventory may still describe the previous connection.
                value['session'].update(controlScope='session', connectionState='ready')
            value['draftAccountIdentity'] = _dict(result.get('account')).get('identity', '')
            if value['history']['hasMore']:
                value['notice'] = '当前显示 ZCode 已加载的历史；更早内容请在原 App 中加载。'
                value['history'].update(hasMore=False, complete=False)
                if body.get('historySync'):
                    boundary = saved_prefix(self.home, sid, workspace, snapshot['rows']['window'])
                    if boundary:
                        value['history'].update(hasMore=True, before=SAVED_SYNC_CURSOR + boundary)
                        value['notice'] = '更早内容和可用的执行过程来自 ZCode 已保存的记录。'
            return value
        if action in ('send', 'stop', 'respond', 'settings', 'access', 'work-mode', 'goal', 'compact', 'stop-task', 'fork', 'edit-message', 'rename'):
            status = result['status']
            # 原生回执的 result 字段可能显式为 null；若直接 .get('result', {}).get(...) 会抛出 AttributeError，
            # 导致已被原 App 接受的 send 被 requests.sqlite 误记为 status='unknown' 并阻塞后续归档。
            native = _dict(result.get('result'))
            if action == 'send' and status == 'accepted' and native.get('delivery') == 'queue':
                status = 'queued'
            if action in ('settings', 'access', 'work-mode') and status == 'noop' and result.get('reasonCode') == 'config.unchanged':
                status = 'accepted'
            extra = {}
            if action in ('fork', 'edit-message') and status == 'accepted':
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
