"""Durable asynchronous creation receipts and exact recovery by request ID."""
import copy
import json
import logging
import subprocess
import threading
import time
import uuid

from .creator import CreationError, open_desktop_chat
from .ipc import IPCError


class CreationMixin:
    def recent_creations(self):
        with self.creation_lock:
            entries = sorted(self.creations.items(), key=lambda item: item[1].get('at', 0), reverse=True)[:15]
            return [{'requestId': key, 'projectKey': value['input'][0], 'title': value['input'][1],
                     'message': value.get('message', '可检查创建结果'), 'status': value['status']}
                    for key, value in entries]

    def _save_creations(self):
        target = self.creations_path.with_suffix('.tmp')
        target.write_text(json.dumps(self.creations, ensure_ascii=False), encoding='utf-8')
        target.chmod(0o600)
        target.replace(self.creations_path)

    def start_creation(self, project_key, title, request_id):
        if not isinstance(request_id, str):
            raise ValueError('新建请求标识无效')
        request_id = str(uuid.UUID(request_id))
        if not isinstance(project_key, str) or not isinstance(title, str) or len(title) > 120:
            raise ValueError('请选择项目，标题不能超过 120 字')
        title = title.strip() or '新对话'
        with self.creation_lock:
            prior = self.creations.get(request_id)
            if prior:
                if prior['input'] != [project_key, title]:
                    raise ValueError('同一新建请求不能更改项目或标题')
                if prior['status'] != 'failed':
                    return self.creation_status(request_id)
            project = next((p for p in self.projects() if p['key'] == project_key), None)
            if not project:
                raise ValueError('项目已不存在，请重新选择')
            if len(self.active_creations) >= 4:
                raise ValueError('已有对话正在创建，请稍后再试')
            self.creations[request_id] = {'input': [project_key, title], 'host': project['host'],
                                         'status': 'pending', 'message': '等待创建', 'at': time.time()}
            self._save_creations()
            done = threading.Event()
            self.active_creations[request_id] = done
            threading.Thread(target=self._create_worker, args=(request_id, project, title, done), daemon=True).start()
            return self.creation_status(request_id)

    def _creation_update(self, request_id, **values):
        with self.creation_lock:
            self.creations[request_id].update(values)
            self._save_creations()

    def _create_worker(self, request_id, project, title, done):
        submitted = False
        phases = {'starting': '正在连接 Codex', 'creating': '正在创建对话', 'naming': '正在保存标题'}
        try:
            self._creation_update(request_id, status='starting', message=phases['starting'])
            bridge = self.for_host(project['host'])
            self.ipc.connect()
            cwd, roots = project['cwd'], project['roots']
            if project['key'] == 'local|projectless':
                folder = self.codex_home / 'mobile-workspaces' / request_id
                folder.mkdir(parents=True, exist_ok=True)
                cwd, roots = str(folder.resolve()), [str(folder.resolve())]
            def created(value):
                self._creation_update(request_id, result={**value, 'host': project['host']})
            def progress(phase):
                self._creation_update(request_id, status=phase, message=phases[phase])
            submitted = True
            value = bridge.creator.create(cwd, title, roots, project['projectId'], on_created=created,
                                          request_id=request_id, on_progress=progress)
            created(value)
            self._creation_update(request_id, status='opening', message='正在打开桌面聊天')
            try:
                open_desktop_chat(value['id'], project['host'])
            except (OSError, subprocess.SubprocessError):
                value['warning'] = '已创建，请点击“打开桌面聊天”重新连接。'
                created(value)
            self._creation_update(request_id, status='ready', message='创建完成，正在连接聊天')
        except Exception as exc:
            with self.creation_lock:
                saved = self.creations[request_id].get('result')
            if saved:
                self._creation_update(request_id, status='ready', message='对话已创建，可进入聊天')
            else:
                uncertain = exc.uncertain if isinstance(exc, CreationError) else submitted
                message = str(exc) if isinstance(exc, (CreationError, IPCError, ValueError)) else '创建中断，可点击“找回对话”检查结果。'
                self._creation_update(request_id, status='unknown' if uncertain else 'failed', message=message)
                logging.getLogger(__name__).warning('Creation ended without a result: %s', type(exc).__name__)
        finally:
            with self.creation_lock:
                self.active_creations.pop(request_id, None)
            done.set()

    def creation_status(self, request_id, recover=False):
        request_id = str(uuid.UUID(request_id))
        with self.creation_lock:
            entry = copy.deepcopy(self.creations.get(request_id))
            active = request_id in self.active_creations
        if entry is None:
            return {'requestId': request_id, 'status': 'missing', 'message': '请求尚未提交，可继续创建'}
        if not active and entry['status'] not in ('ready', 'failed', 'deleted'):
            if entry.get('result'):
                entry.update(status='ready', message='已找回创建的对话')
            elif recover:
                host = entry.get('host', 'local')
                result = self.for_host(host).store.find_creation(request_id)
                if result:
                    entry.update(status='ready', message='已找回创建的对话', result={**result, 'host': host})
                else:
                    entry.update(status='unknown', message='暂未找到匹配记录，请稍后找回，或刷新聊天列表查看；不会重复创建。')
            else:
                entry.update(status='unknown', message='创建结果待确认，请点击“找回对话”。')
            self._creation_update(request_id, **entry)
        return {key: entry[key] for key in ('status', 'message', 'result') if key in entry} | {'requestId': request_id}

    def create_chat(self, project_key, title, request_id):
        # Compatibility for the 1.4 web page until it reloads the new assets.
        self.start_creation(project_key, title, request_id)
        with self.creation_lock:
            done = self.active_creations.get(str(uuid.UUID(request_id)))
        if done:
            done.wait(80)
        status = self.creation_status(request_id)
        if status['status'] == 'ready':
            return status['result']
        raise CreationError(status['message'], status['status'] != 'failed')
