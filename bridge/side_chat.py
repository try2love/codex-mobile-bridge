"""Bridge-owned ephemeral forks. Parent threads are never resumed or executed.

One runtime owns each temporary child; closing it destroys the in-memory fork.
Views are shared by authenticated gateway clients, never persisted in Bridge.
"""
import copy
import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from .model import normalize_state, normalize_request


BOUNDARY = """You are in a temporary side conversation, separate from the main thread.
Inherited history is reference context only. Do not continue any task, goal, plan,
request, approval, or tool call from before this boundary. Wait for a new user
message in this side conversation. Do not interact with inherited subagents.
Answer questions and perform lightweight exploration without disrupting the main
thread. Do not modify files, Git state, settings, or permissions unless the user
explicitly requests that mutation in this side conversation. Shared files are
live, not a snapshot. Keep explicitly requested changes local to the request.
"""


class SideChatError(ValueError):
    pass


class SideRuntime:
    def __init__(self, executable, home, cwd, event):
        self.event = event
        self.lock = threading.RLock()
        self.pending = {}
        self.counter = 0
        self.closed = False
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        self.process = subprocess.Popen([str(executable), 'app-server', '--listen', 'stdio://'],
            cwd=cwd, env={**os.environ, 'CODEX_HOME': str(home)}, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', **options)
        self.reader = threading.Thread(target=self._read, daemon=True)

    def start(self):
        self.reader.start()
        try:
            self.request('initialize', {'clientInfo': {'name': 'codex_mobile_side_chat', 'version': '0.1'},
                                       'capabilities': {'experimentalApi': True}})
            self.write({'method': 'initialized'})
        except Exception:
            self.close()
            raise

    def write(self, value):
        with self.lock:
            if self.closed:
                raise SideChatError('侧边聊天已失效，请关闭后新建')
            try:
                self.process.stdin.write(json.dumps(value) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise SideChatError('侧边聊天运行时已断开') from exc

    def request(self, method, params, timeout=30):
        with self.lock:
            self.counter += 1
            identifier = self.counter
            result = queue.Queue()
            self.pending[identifier] = result
        try:
            self.write({'id': identifier, 'method': method, 'params': params})
            try:
                value = result.get(timeout=timeout)
            except queue.Empty as exc:
                raise SideChatError('侧边聊天操作结果未确认，请刷新状态，不要重复发送') from exc
            if value is None:
                raise SideChatError('侧边聊天运行时已断开')
            if 'error' in value:
                error = SideChatError('Codex 未完成侧边聊天操作，请检查模型接入和运行时版本')
                error.rpc_error = value['error']
                raise error
            return value.get('result') or {}
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(value, dict):
                    continue
                if 'method' in value:
                    self.event(value)
                else:
                    with self.lock:
                        result = self.pending.get(value.get('id'))
                    if result is not None:
                        result.put(value)
        finally:
            with self.lock:
                for result in self.pending.values():
                    result.put(None)
            self.event({'method': 'bridge/disconnected'})

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=2)
        self.process.stdout.close()


def check_runtime(executable, directory):
    """Reject older runtimes before fork: ignored fields could persist or run goals."""
    with tempfile.TemporaryDirectory(prefix='side-schema-', dir=directory) as folder:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run([str(executable), 'app-server', 'generate-json-schema', '--experimental', '--out', folder],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, **options)
        paths = list(Path(folder).rglob('ThreadForkParams.json'))
        properties = json.loads(paths[0].read_text()).get('properties', {}) if paths else {}
        if result.returncode or not {'ephemeral', 'excludeTurns'} <= properties.keys():
            raise SideChatError('此 Codex 运行时不支持临时侧边聊天，请更新 Codex')


class SideChat:
    def __init__(self, executable, home, parent, cwd, settings, runtime_factory=SideRuntime):
        self.parent, self.id = parent, None
        self.lock = threading.RLock()
        self.actions = threading.Lock()
        self.closed = False
        self.ready = False
        self.error = None
        self.submissions = {}
        self.state = {'cwd': cwd, 'latestModel': settings['model'], 'modelProvider': settings['modelProvider'],
                      'turns': [], 'requests': [], 'threadRuntimeStatus': {'type': 'idle'}}
        self.runtime = runtime_factory(executable, home, cwd, self._event)
        try:
            self.runtime.start()
            result = self.runtime.request('thread/fork', {**settings, 'threadId': parent, 'cwd': cwd,
                'ephemeral': True, 'excludeTurns': True})
            child = result['thread']
            self.id = str(uuid.UUID(child['id']))
            if self.id == parent or child.get('ephemeral') is not True:
                raise SideChatError('无法确认临时分支，已取消创建')
            if result.get('modelProvider') != settings['modelProvider'] or result.get('model') != settings['model']:
                raise SideChatError('侧边聊天的模型接入与主聊天不一致，已取消创建')
            if str(result.get('cwd', child.get('cwd'))) != cwd:
                raise SideChatError('侧边聊天工作目录不一致，已取消创建')
            self.state['id'] = self.id
            # Ephemeral threads do not support goals; deferGoalContinuation and
            # goal/clear are rejected by Codex. No parent goal is resumed here.
            self.runtime.request('thread/inject_items', {'threadId': self.id, 'items': [
                {'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': BOUNDARY}]}]})
            self.ready = True
        except Exception:
            self.close()
            raise

    def _turn(self, identifier):
        turn = next((t for t in self.state['turns'] if t['turnId'] == identifier), None)
        if turn is None:
            turn = {'turnId': identifier, 'status': 'inProgress', 'items': []}
            self.state['turns'].append(turn)
        return turn

    def _event(self, message):
        method, params = message.get('method'), message.get('params') or {}
        with self.lock:
            if self.closed:
                return
            if method == 'bridge/disconnected':
                self.error = '侧边聊天已失效，请关闭后新建'
                self.state['requests'] = []
                return
            if 'id' in message:
                # No automatic approvals, credential refresh, or unknown tool dispatch.
                if params.get('threadId') != self.id or not self.ready:
                    self.runtime.write({'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported side-chat request'}})
                    return
                self.state['requests'].append(copy.deepcopy(message))
                return
            if not self.ready or params.get('threadId') != self.id:
                return
            if method == 'thread/closed':
                self.error = '侧边聊天已关闭或失效'
                self.state['requests'] = []
            elif method in ('turn/started', 'turn/completed'):
                source = params['turn']
                turn = self._turn(source['id'])
                for key in ('status', 'error'):
                    if key in source:
                        turn[key] = source[key]
                if source.get('items'):
                    turn['items'] = copy.deepcopy(source['items'])
                self.state['threadRuntimeStatus']['type'] = 'active' if method == 'turn/started' else 'idle'
                if method == 'turn/completed':
                    self.state['requests'] = [r for r in self.state['requests'] if r['params'].get('turnId') != source['id']]
            elif method in ('item/started', 'item/completed'):
                turn = self._turn(params['turnId'])
                item = copy.deepcopy(params['item'])
                index = next((i for i, x in enumerate(turn['items']) if x['id'] == item['id']), None)
                if index is None:
                    turn['items'].append(item)
                else:
                    turn['items'][index] = item
            elif method in ('item/agentMessage/delta', 'item/commandExecution/outputDelta'):
                turn = self._turn(params['turnId'])
                item = next((i for i in turn['items'] if i['id'] == params['itemId']), None)
                if item is not None:
                    key = 'text' if method == 'item/agentMessage/delta' else 'aggregatedOutput'
                    item[key] = item.get(key, '') + params.get('delta', '')
            elif method == 'serverRequest/resolved':
                self.state['requests'] = [r for r in self.state['requests'] if r['id'] != params.get('requestId')]
            elif method == 'error' and not params.get('willRetry'):
                self.error = '侧边聊天执行失败，请检查模型接入后重试'
                self.state['threadRuntimeStatus']['type'] = 'idle'

    def view(self):
        with self.lock:
            result = normalize_state(self.state, self.ready and not self.closed and not self.error)
            # Never direct an unsupported sidecar request to an unrelated desktop tab.
            for request in result['requests']:
                if not request['supported'] or request['method'] in ('item/plan/requestImplementation', 'bridge/requestUserInputAsync'):
                    request['supported'] = False
                    request['params'] = {'message': '此请求暂不支持在侧边聊天处理，请停止本次回复。'}
            return {**result, 'parentId': self.parent, 'error': self.error,
                    'submissions': [{'id': k, 'status': v['status']} for k, v in self.submissions.items()]}

    def send(self, text, submission):
        uuid.UUID(str(submission))
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError('请输入 1–20000 字的消息')
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.actions:
            with self.lock:
                prior = self.submissions.get(submission)
                if prior:
                    if prior['digest'] != digest:
                        raise ValueError('同一消息标识不能用于不同内容')
                    return {'id': submission, 'status': prior['status']}
                if not self.view()['connected']:
                    raise SideChatError('侧边聊天已失效，请关闭后新建')
                if self.state['threadRuntimeStatus']['type'] == 'active' or self.state['requests']:
                    raise ValueError('请先等待回复完成或处理确认请求')
                self.submissions[submission] = {'digest': digest, 'status': 'unknown'}
                self.state['threadRuntimeStatus']['type'] = 'active'
            try:
                self.runtime.request('turn/start', {'threadId': self.id,
                    'clientUserMessageId': submission,
                    'input': [{'type': 'text', 'text': text, 'text_elements': []}]})
            except SideChatError:
                return {'id': submission, 'status': 'unknown'}
            with self.lock:
                self.submissions[submission]['status'] = 'accepted'
            return {'id': submission, 'status': 'accepted'}

    def stop(self):
        with self.actions:
            with self.lock:
                turn = next((t for t in reversed(self.state['turns']) if t.get('status') == 'inProgress'), None)
            if turn:
                self.runtime.request('turn/interrupt', {'threadId': self.id, 'turnId': turn['turnId']})
            return self.view()

    def respond(self, identifier, response):
        if not isinstance(response, dict):
            raise ValueError('确认回复格式不正确')
        with self.actions:
            with self.lock:
                request = next((r for r in self.state['requests'] if str(r['id']) == str(identifier)), None)
                if not request:
                    raise ValueError('此请求已处理或已失效')
                method, params = request['method'], request['params']
                if not normalize_request(request)['supported']:
                    raise ValueError('此请求暂不支持，请停止本次回复')
                if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
                    decision = response.get('decision')
                    if set(response) != {'decision'} or decision not in ('accept', 'decline', 'cancel') or (params.get('availableDecisions') and decision not in params['availableDecisions']):
                        raise ValueError('请选择本次允许、拒绝或取消')
                    result = {'decision': decision}
                elif method == 'item/permissions/requestApproval':
                    if set(response) != {'decision'} or response['decision'] not in ('accept', 'decline'):
                        raise ValueError('请选择本次允许或拒绝')
                    result = {'permissions': params.get('permissions', {}) if response['decision'] == 'accept' else {}, 'scope': 'turn'}
                elif method in ('item/tool/requestUserInput', 'tool/requestUserInput'):
                    answers = response.get('answers')
                    keys = {q['id'] for q in params.get('questions', [])}
                    if set(response) != {'answers'} or not isinstance(answers, dict) or not keys or set(answers) != keys or any(not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], str) or not v[0].strip() or len(v[0]) > 20000 for v in answers.values()):
                        raise ValueError('请完整回答问题')
                    result = {'answers': {k: {'answers': v} for k, v in answers.items()}}
                elif method == 'mcpServer/elicitation/request':
                    from .service import validate_form
                    action = response.get('action')
                    if set(response) - {'action', 'content'} or action not in ('accept', 'decline', 'cancel'):
                        raise ValueError('确认操作无效')
                    if action == 'accept':
                        validate_form(response.get('content'), params.get('requestedSchema', {}))
                    result = {'action': action, 'content': response.get('content') if action == 'accept' else None}
                else:
                    raise ValueError('此请求暂不支持，请停止本次回复')
                self.runtime.write({'id': request['id'], 'result': result})
                self.state['requests'].remove(request)
            return self.view()

    def close(self):
        with self.actions:
            with self.lock:
                self.closed = True
                self.ready = False
            self.runtime.close()
            with self.lock:
                self.state['turns'] = []
                self.state['requests'] = []
                self.submissions.clear()


class SideChats:
    def __init__(self, executable, home, directory, host, factory=SideChat):
        self.executable, self.home, self.directory, self.host = executable, home, directory, host
        self.factory = factory
        self.lock = threading.RLock()
        self.chats = {}
        self.creations = {}
        self.checked = False

    def operate(self, parent, action, body, snapshot=None, settings=None):
        with self.lock:
            chat = self.chats.get(parent)
            if action == 'read':
                return chat.view() if chat else {'id': None, 'connected': False, 'turns': [], 'requests': []}
            if action == 'create':
                token = str(uuid.UUID(str(body.get('creationId', ''))))
                if token in self.creations:
                    original_parent, child = self.creations[token]
                    if original_parent != parent or not chat or chat.id != child:
                        raise ValueError('此次创建的侧边聊天已关闭，请重新打开标签后新建')
                    return chat.view()
                if chat:
                    return chat.view()
                if self.host != 'local':
                    raise ValueError('此预览暂支持网关电脑上的聊天，SSH 远程工作区尚未接入侧边聊天')
                if len(self.chats) >= 8 or len(self.creations) >= 1000:
                    raise ValueError('临时侧边聊天数量已达上限，请关闭不用的聊天；必要时重启网关')
                if not self.checked:
                    check_runtime(self.executable, self.directory)
                    self.checked = True
                chat = self.factory(self.executable, self.home, parent, snapshot['cwd'], settings)
                self.chats[parent] = chat
                self.creations[token] = (parent, chat.id)
                return chat.view()
            if not chat or chat.id != body.get('id'):
                raise ValueError('此侧边聊天已关闭或已失效，请刷新标签')
            if action == 'close':
                chat.close()
                self.chats.pop(parent)
                return {'closed': True}
        if action == 'send':
            return chat.send(body.get('text'), body.get('submissionId'))
        if action == 'stop':
            return chat.stop()
        if action == 'respond':
            return chat.respond(body.get('requestId'), body.get('response'))
        raise ValueError('无效侧边聊天操作')

    def close(self):
        with self.lock:
            for chat in self.chats.values():
                chat.close()
            self.chats.clear()
            self.creations.clear()
