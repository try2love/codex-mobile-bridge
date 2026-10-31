"""Bounded, opt-in native-session prototype. No App launch, patch or replacement CLI."""
import json
import mimetypes
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
    for row in snapshot['rows']['window']:
        if row.get('visibility') != 'visible' or row['kind'] not in ('userInput', 'assistantText'):
            continue
        message = {'id': 'zcode:%s:%s' % (snapshot['sessionId'], row['rowId']),
                   'role': 'user' if row['kind'] == 'userInput' else 'assistant', 'text': row.get('text', '')}
        if row.get('attachments'):
            message['attachments'] = [{'name': a['fileName'], 'mime': a['mime'], 'size': a['bytes'],
                                       'nativeRef': a['ref']} for a in row['attachments']]
        messages.append(message)
    control = snapshot['control']
    active = bool(control.get('activeWorks')) or bool(snapshot['pendingInteractions'])
    return {'session': {'id': snapshot['sessionId'], 'provider': 'zcode', 'host': 'local',
                        'cwd': workspace, 'title': snapshot.get('meta', {}).get('title', ''),
                        'status': 'active' if active else 'idle'}, 'messages': messages,
            'requests': snapshot['pendingInteractions'], 'control': control,
            'native': {'revision': snapshot['revision'], 'seq': snapshot['seq'], 'logEpoch': snapshot['logEpoch']},
            'history': {'hasMore': snapshot['rows']['totalCount'] > len(snapshot['rows']['window'])},
            'capabilities': {'send': True, 'stop': bool(control.get('canStop')), 'attachments': True}}


class ZCodePrototype:
    def __init__(self, directory, config, transport=None):
        self.directory = Path(directory)
        self.config = dict(config)
        self.transport = transport or self._native
        self.workspace = str(Path(config['workspace']).resolve(strict=True))
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
            result = subprocess.run(['node', str(Path(__file__).with_name('transport.mjs'))],
                                    input=json.dumps(request), text=True, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise BridgeUnavailable('ZCode 回执超时，结果待核对；不会自动重放') from None
        if result.returncode:
            raise BridgeUnavailable(result.stderr.strip()[:1500] or 'ZCode 原生连接不可用')
        return json.loads(result.stdout)

    def call(self, action, sid=None, body=None):
        if sid != self.sid:
            raise ValueError('此原型只能操作显式绑定的测试会话')
        if action not in ('attach', 'detach', 'history', 'detail', 'receipt', 'send', 'stop', 'respond'):
            raise ValueError('尚未验证的 ZCode 操作')
        body = dict(body or {})
        request = {'action': action, 'workspace': self.workspace, 'sessionId': sid,
                   'binding': None if action == 'attach' else self.config.get('binding')}
        if action in ('send', 'stop', 'respond'):
            uuid.UUID(body.get('id', ''))
            if not request['binding']:
                raise BridgeUnavailable('必须先显式接入原生测试会话')
        if action == 'send':
            if not isinstance(body.get('text'), str) or not 1 <= len(body['text'].strip()) <= 100000:
                raise ValueError('请输入 1–100000 字的消息')
            if set(body) - {'id', 'text', 'projectFiles'}:
                raise ValueError('原型只接受文字和当前项目文件')
            body['files'] = project_files(self.workspace, body.pop('projectFiles', []))
        if action == 'receipt':
            request['commandId'] = str(uuid.UUID(body['commandId']))
        request['body'] = body
        result = self.transport(request)
        if action == 'detail':
            return project_snapshot(result['snapshot'], self.workspace)
        if action in ('send', 'stop', 'respond'):
            return {'id': body['id'], 'status': result['status'], 'nativeAck': result}
        return result
