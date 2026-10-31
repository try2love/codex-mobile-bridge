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

from bridge.clients.errors import BridgeUnavailable
from bridge.features.workspace.workspace import MAX_TRANSFER, Workspace
from bridge.platforms.macos.discovery import bundle
from bridge.platforms.macos.loopback import listener_identity


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


def project_snapshot(snapshot, workspace):
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
        if row['kind'] == 'toolCall':
            message.update(title=row.get('toolName', '执行过程'), text='\n'.join(
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                for value in (row.get('inputText') or row.get('input'), row.get('output'), row.get('error')) if value))
        if row.get('attachments'):
            message['attachments'] = [{'name': a['fileName'], 'mime': a['mime'], 'size': a['bytes'],
                                       'nativeRef': a['ref']} for a in row['attachments']]
        messages.append(message)
    control = snapshot['control']
    active = bool(control.get('activeWorks')) or bool(snapshot['pendingInteractions'])
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
    return {'session': {'id': snapshot['sessionId'], 'provider': 'zcode', 'host': 'local',
                        'cwd': workspace, 'title': snapshot.get('meta', {}).get('title', ''),
                        'status': 'active' if active else 'idle', 'backend': 'desktop', 'requests': requests,
                        'turnStatus': 'failed' if error else 'inProgress' if active else 'completed',
                        'model': '/'.join(filter(None, (snapshot.get('config', {}).get('provider'), snapshot.get('config', {}).get('model')))),
                        'modelDisplayName': snapshot.get('config', {}).get('model'), 'effort': snapshot.get('config', {}).get('thought'),
                        'runtimeKnown': True,
                        'stopTarget': {'executionId': foreground[0]} if len(foreground) == 1 else {},
                        'updatedAt': max((row.get('createdAt', 0) for row in snapshot['rows']['window']), default=0)}, 'messages': messages,
            'requests': snapshot['pendingInteractions'], 'control': control, 'turns': turns,
            'native': {'revision': snapshot['revision'], 'seq': snapshot['seq'], 'logEpoch': snapshot['logEpoch']},
            'history': {'hasMore': snapshot['rows']['totalCount'] > len(snapshot['rows']['window'])},
            'capabilities': {'send': not active and snapshot.get('inputRouting', {}).get('mode') == 'startNow',
                             'stop': bool(control.get('canStop')) and len(foreground) == 1,
                             'attachments': True, 'uploadFiles': True, 'resume': True, 'models': True, 'effort': True,
                             'queue': False, 'steer': False, 'skills': True, 'permissions': False, 'create': False}}


class ZCodePrototype:
    def __init__(self, directory, config, transport=None):
        self.directory = Path(directory)
        self.config = dict(config)
        self.transport = transport or self._native
        self.workspace = str(Path(config['workspace']).resolve(strict=True))
        self.home = Path(config.get('dataDirectory', Path.home()/'.zcode/v2'))
        self.sid = config['sessionId']
        if not self.sid.startswith('sess_'):
            raise ValueError('必须指定原 App 中的独立测试会话')

    def _native(self, request):
        if sys.platform != 'darwin':
            raise BridgeUnavailable('此原型仅验证 macOS ZCode 3.17.1，其他平台尚未开放')
        application = Path(self.config['application'])
        app = bundle(application, 'dev.zcode.app')
        if not app or app['version'] != '3.17.1':
            raise BridgeUnavailable('ZCode 安装身份或版本不匹配')
        process = listener_identity(self.config['port'], app['executable'])
        if process != self.config['process']:
            raise BridgeUnavailable('ZCode App 进程已变化，请重新核验并显式接入')
        request = {**request, 'application': str(application), 'port': self.config['port'],
                   'expiresAt': int(time.time() * 1000) + 12000}
        try:
            result = subprocess.run([self.config.get('node', 'node'), str(Path(__file__).with_name('transport.mjs'))],
                                    input=json.dumps(request), text=True, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise BridgeUnavailable('ZCode 回执超时，结果待核对；不会自动重放') from None
        if result.returncode:
            raise BridgeUnavailable(result.stderr.strip()[:1500] or 'ZCode 原生连接不可用')
        return json.loads(result.stdout)

    def call(self, action, sid=None, body=None):
        if action in ('account', 'lifecycle'):
            return self.transport({'action': action, 'workspace': self.workspace, 'sessionId': self.sid,
                                   'expectedRuntime': self.config.get('binding', {}).get('runtime')})
        if action == 'list':
            detail = self.call('detail', self.sid)
            return {'sessions': [detail['session']], 'connected': detail.get('connected', True)}
        if action == 'projects':
            return {'projects': [], 'capabilities': {'create': False}}
        if sid != self.sid:
            raise ValueError('此原型只能操作显式绑定的测试会话')
        if action not in ('attach', 'detach', 'resume', 'history', 'detail', 'receipt', 'send', 'stop', 'respond', 'catalog', 'settings'):
            raise ValueError('尚未验证的 ZCode 操作')
        body = dict(body or {})
        if action == 'resume':
            # Only an explicit phone action may adopt a new App process/owner.
            app = bundle(Path(self.config['application']), 'dev.zcode.app')
            if not app or app['version'] != '3.17.1':
                raise BridgeUnavailable('ZCode 安装身份或版本不匹配')
            process = listener_identity(self.config['port'], app['executable'])
            previous = dict(self.config)
            try:
                self.config['process'] = process
                result = self.call('attach', sid)
                self.config['binding'] = result['binding']
            except Exception:
                self.config = previous
                raise
            return {'status': 'ready', 'id': body['id']}
        request = {'action': action, 'workspace': self.workspace, 'sessionId': sid,
                   'binding': None if action == 'attach' else self.config.get('binding')}
        if action in ('send', 'stop', 'respond', 'settings'):
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
            if body.pop('mode', 'send') != 'send':
                raise ValueError('ZCode 原型暂只支持发送新消息')
            body['files'] = project_files(self.workspace, body.pop('projectFiles', []))
            references = body.pop('resolvedReferences', [])
            paths = [str(Path(item['path']).relative_to(self.workspace)) for item in references]
            body['files'].extend(project_files(self.workspace, paths))
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
        result = self.transport(request)
        if action == 'detail':
            value = project_snapshot(result['snapshot'], self.workspace)
            value['draftAccountIdentity'] = result.get('account', {}).get('identity', '')
            if value['history']['hasMore']:
                value['notice'] = '当前显示 ZCode 已加载的历史；更早内容请在原 App 中加载。'
                value['history']['hasMore'] = False
            return value
        if action in ('send', 'stop', 'respond', 'settings'):
            return {'id': body['id'], 'status': result['status'], 'nativeAck': result}
        return result

    def account_scope(self):
        """Invalidate shared catalogs on account/provider changes, not UI settings."""
        from bridge.clients.zcode.accounts import read, account_key, SETTINGS
        settings = read(self.home/'setting.json')
        value = [{k: v for k, v in read(self.home/'credentials.json').items() if account_key(k)},
                 read(self.home/'provider_config.json'), {k: settings[k] for k in SETTINGS if k in settings}]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def launch(self):
        """Explicit account transaction only; normal Launch Services, no force quit."""
        from bridge.platforms.macos.desktop import start
        app = bundle(Path(self.config['application']), 'dev.zcode.app')
        if not app or app['version'] != '3.17.1':
            raise BridgeUnavailable('ZCode 安装身份或版本已变化')
        start(Path(app['executable']), self.home, {}, arguments=(
            '--remote-debugging-address=127.0.0.1', '--remote-debugging-port='+str(self.config['port'])))
        deadline = time.monotonic() + 25
        while True:
            try:
                self.config['process'] = listener_identity(self.config['port'], app['executable'])
                account = self.call('account')['account']
                if not account.get('identity'):
                    raise BridgeUnavailable('ZCode 尚未恢复账号，请在原 App 检查登录')
                # The account action explicitly authorizes reconnecting the same native chat.
                try:
                    self.config['binding'] = self.call('attach', self.sid)['binding']
                except (OSError, ValueError):
                    pass  # A cold App stays disconnected until explicit resume; never send a turn.
                return
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise BridgeUnavailable('ZCode 启动或登录核验超时；恢复备份已保留') from None
                time.sleep(.5)

    def status(self):
        try:
            self.call('detail', self.sid)
            return {'connected': True}
        except (BridgeUnavailable, ValueError, OSError):
            return {'connected': False}
