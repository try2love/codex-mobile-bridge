"""Experimental attachment to Desktop-owned, already-open native side chats.

No app-server, thread/resume, persisted transcript, or UI automation is used.
Desktop logs supply candidate IDs only; a live owner snapshot is authoritative.
"""
import copy
import hashlib
import re
import sys
import threading
import time
import uuid
from pathlib import Path

from .ipc import DesktopIPC, IPCError
from .model import apply_patches, normalize_state


def native_candidates():
    if sys.platform != 'darwin':
        return []
    folder = Path.home() / 'Library/Logs/com.openai.codex'
    # Only the current day's newest log segments, and only routed injection IDs.
    folder = folder / time.strftime('%Y/%m/%d')
    pattern = re.compile(r'conversationId=([0-9a-f-]{36})\b.*\bmethod=thread/inject_items\b')
    found = []
    try:
        files = sorted(folder.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for path in files:
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 2 * 1024 * 1024))
                for line in reversed(stream.read().decode('utf-8', errors='replace').splitlines()):
                    match = pattern.search(line)
                    if match and match[1] not in found:
                        found.append(match[1])
                        if len(found) == 6:
                            return found
    except OSError:
        pass
    return found


class NativeSideChat:
    def __init__(self, endpoint, parent, child, host, ipc_factory=DesktopIPC):
        uuid.UUID(parent)
        uuid.UUID(child)
        if child == parent:
            raise ValueError('请选择当前会话下的原生侧边聊天')
        self.parent, self.id, self.host = parent, child, host
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.action_lock = threading.Lock()
        self.state = None
        self.owner = None
        self.revision = None
        self.snapshots = 0
        self.closed = False
        self.error = None
        self.submissions = {}
        self.checked = 0
        self.touched = time.monotonic()
        self.ipc = ipc_factory(endpoint, self._event, self._disconnected)

    def _valid(self, state):
        return (state.get('id') == self.id and state.get('ephemeral') is True
                and state.get('sideConversation') is True and state.get('forkedFromId') == self.parent)

    def _event(self, message):
        params = message.get('params', {})
        if (message.get('method') != 'thread-stream-state-changed'
                or params.get('conversationId') != self.id or params.get('hostId') != self.host):
            return
        with self.condition:
            if self.closed:
                return
            change = params.get('change', {})
            try:
                if message.get('version') != 11:
                    raise ValueError()
                if change.get('type') == 'snapshot':
                    state = change.get('conversationState', {})
                    if not self._valid(state) or not message.get('sourceClientId'):
                        raise ValueError()
                    if self.owner and self.owner != message['sourceClientId']:
                        raise ValueError()
                    self.owner = message['sourceClientId']
                    self.state = copy.deepcopy(state)
                    self.snapshots += 1
                elif change.get('type') == 'patches':
                    if message.get('sourceClientId') != self.owner:
                        return
                    if self.state is None or change.get('baseRevision') != self.revision:
                        raise ValueError()
                    state = apply_patches(copy.deepcopy(self.state), change['patches'])
                    if not self._valid(state):
                        raise ValueError()
                    self.state = state
                else:
                    return
                self.revision = change['revision']
                self.error = None
            except (KeyError, ValueError, TypeError, IndexError):
                self.error = '无法确认原生侧边聊天状态，请重新连接'
                self.revision = None
            self.condition.notify_all()

    def _disconnected(self):
        with self.condition:
            if not self.closed:
                self.error = '电脑连接已断开，请重新连接侧边聊天'
                self.state = None
                self.revision = None
            self.condition.notify_all()

    def refresh(self):
        with self.action_lock:
            self._refresh()
        return self.view()

    def _refresh(self):
        if self.closed:
            raise ValueError('侧边聊天连接已关闭')
        self.ipc.connect()
        with self.condition:
            before = self.snapshots
        self.ipc.follow(self.id, self.owner, host=self.host)
        with self.condition:
            arrived = self.condition.wait_for(lambda: self.snapshots > before or self.closed, timeout=2)
            if not arrived or self.closed or self.error:
                self.error = '侧边聊天已关闭、失效或暂时无法连接，请在电脑端检查'
                self.state = None
                self.revision = None
                raise IPCError(self.error)
            self.checked = self.touched = time.monotonic()

    def view(self):
        with self.lock:
            self.touched = time.monotonic()
            result = normalize_state(self.state or {'id': self.id}, not self.closed and not self.error)
            return {**result, 'error': self.error, 'parentId': self.parent,
                    'submissions': [{'id': k, 'status': v['status']} for k, v in self.submissions.items()]}

    def send(self, text, submission):
        uuid.UUID(str(submission))
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError('请输入 1–20000 字的消息')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        with self.action_lock:
            prior = self.submissions.get(submission)
            if prior:
                if prior['digest'] != digest:
                    raise ValueError('同一消息标识不能用于不同内容')
                return {'id': submission, 'status': prior['status']}
            self._refresh()  # Never send on stale state or activate an expired child.
            with self.lock:
                if self.state.get('threadRuntimeStatus', {}).get('type') == 'active':
                    raise ValueError('侧边聊天正在回复，请稍后再发送')
                if self.state.get('requests'):
                    raise ValueError('请先在电脑端处理侧边聊天中的确认请求')
                self.submissions[submission] = {'digest': digest, 'status': 'unknown'}
            request = {'threadId': self.id, 'clientUserMessageId': submission,
                       'input': [{'type': 'text', 'text': text, 'text_elements': []}]}
            try:
                self.ipc.request('thread-follower-start-turn', {'conversationId': self.id,
                    'turnStart': {'request': request, 'context': {'inheritThreadSettings': True,
                        'attachments': [], 'commentAttachments': []}}},
                    target=self.owner, host=self.host, timeout=90)
            except IPCError:
                # A missing reply is not permission to replay the model request.
                return {'id': submission, 'status': 'unknown'}
            with self.lock:
                self.submissions[submission]['status'] = 'accepted'
            return {'id': submission, 'status': 'accepted'}

    def close(self):
        # Detach only. Destroying a Desktop tab is not exposed by follower IPC.
        with self.action_lock:
            with self.condition:
                self.closed = True
                self.state = None
                self.submissions.clear()
                self.condition.notify_all()
            try:
                if self.ipc.client_id:
                    self.ipc.follow(self.id, self.owner, False, host=self.host)
            except IPCError:
                pass
            self.ipc.close()


class NativeSideChats:
    def __init__(self, endpoint, host, candidates=native_candidates, ipc_factory=DesktopIPC):
        self.endpoint, self.host = endpoint, host
        self.candidates, self.ipc_factory = candidates, ipc_factory
        self.lock = threading.RLock()
        self.chats = {}

    def operate(self, parent, owner, action, body):
        connection = str(body.get('connectionId', ''))
        uuid.UUID(connection)
        key = (owner, parent, connection)
        self.reap()
        with self.lock:
            chat = self.chats.get(key)
        if action == 'connect':
            if chat:
                try:
                    return chat.refresh()
                except IPCError:
                    with self.lock:
                        if self.chats.get(key) is chat:
                            self.chats.pop(key)
                    chat.close()
            ids = [body['id']] if body.get('id') else self.candidates()
            for child in ids:
                if not isinstance(child, str):
                    raise ValueError('侧边聊天标识无效')
                candidate = NativeSideChat(self.endpoint, parent, child, self.host, self.ipc_factory)
                try:
                    view = candidate.refresh()
                except (IPCError, ValueError, OSError):
                    candidate.close()
                    continue
                with self.lock:
                    previous = self.chats.setdefault(key, candidate)
                if previous is not candidate:
                    candidate.close()
                    return previous.refresh()
                return view
            raise ValueError('未找到当前聊天下已打开的原生侧边聊天，请先在电脑端创建，再点击连接')
        if action == 'disconnect':
            with self.lock:
                chat = self.chats.pop(key, None)
            if chat:
                chat.close()
            return {'disconnected': True}
        if not chat:
            raise ValueError('请先连接电脑端已打开的侧边聊天')
        if action == 'read':
            if time.monotonic() - chat.checked >= 5:
                try:
                    return chat.refresh()
                except IPCError:
                    return chat.view()
            return chat.view()
        if action == 'send':
            return chat.send(body.get('text'), body.get('submissionId'))
        raise ValueError('无效侧边聊天操作')

    def reap(self):
        with self.lock:
            expired = [self.chats.pop(k) for k, chat in list(self.chats.items())
                       if time.monotonic() - chat.touched > 300]
        for chat in expired:
            chat.close()

    def close(self):
        with self.lock:
            chats, self.chats = list(self.chats.values()), {}
        for chat in chats:
            chat.close()
