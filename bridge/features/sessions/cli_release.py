"""Shared, explicit CLI handoff. Never discover or start an execution owner."""
import json
import threading
import uuid
from pathlib import Path



class ReleasedSessions:
    def __init__(self, directory, save):
        self.save = save
        self.path = Path(directory) / 'released-cli-sessions.json'
        self.lock = threading.RLock()
        self.ids = set(json.loads(self.path.read_text()) if self.path.exists() else [])

    def contains(self, identifier):
        with self.lock:
            return identifier in self.ids

    def set(self, identifier, released):
        with self.lock:
            if released:
                self.ids.add(identifier)
            else:
                self.ids.discard(identifier)
            self.save(self.path, sorted(self.ids))


class CliConnections:
    def __init__(self, directory, sources, save):
        self.save = save
        self.sources = sources
        self.path = Path(directory) / 'cli-release-operations.json'
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.jobs = json.loads(self.path.read_text()) if self.path.exists() else {}
        for job in self.jobs.values():
            for row in job['results']:
                if row['status'] in ('waiting', 'releasing'):
                    row.update(status='unknown', error='网关已重启，请核对原会话；不会自动重放释放操作')

    def _connections(self):
        rows = []
        for source in self.sources():
            rows.extend(source.cli_connections())
        return rows

    def view(self):
        rows = self._connections()
        with self.lock:
            return {'connections': [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows],
                    'operations': json.loads(json.dumps(list(self.jobs.values())))}

    def submit(self, body):
        if set(body) != {'id', 'connections', 'mode'} or body['mode'] not in ('wait', 'stop'):
            raise ValueError('CLI 释放请求无效')
        uuid.UUID(body['id'])
        selected = body['connections']
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 256
                or any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected)):
            raise ValueError('请选择需要释放的 CLI 连接')
        with self.lock:
            previous = self.jobs.get(body['id'])
            if previous:
                if previous['connections'] != selected or previous['mode'] != body['mode']:
                    raise ValueError('释放请求 ID 已用于其他操作')
                return json.loads(json.dumps(previous))
            busy = {row['key'] for job in self.jobs.values() for row in job['results']
                    if row['status'] in ('waiting', 'releasing', 'unknown')}
            if busy.intersection(selected):
                raise ValueError('所选连接已有释放请求，请查看该请求的结果')
            available = {row['key']: row for row in self._connections()}
            if any(key not in available for key in selected):
                raise ValueError('连接已变化，请刷新后重新选择')
            job = {**body, 'results': [{k: v for k, v in available[key].items() if not k.startswith('_')}
                                     | {'status': 'waiting'} for key in selected]}
            self.jobs[body['id']] = job
            # Bound completed receipts; pending/unknown operations remain attributable.
            if len(self.jobs) > 128:
                for key, old in list(self.jobs.items()):
                    if len(self.jobs) <= 128:
                        break
                    if key != body['id'] and all(r['status'] not in ('waiting', 'releasing', 'unknown') for r in old['results']):
                        del self.jobs[key]
            self.save(self.path, self.jobs)
            threading.Thread(target=self._run, args=(job, available), daemon=True,
                             name='cli-release').start()
            return json.loads(json.dumps(job))

    def _run(self, job, available):
        pending = list(job['results'])
        while pending and not self.closed.is_set():
            for row in list(pending):
                try:
                    # Adapter rechecks the SAME ownership generation before any mutation.
                    result = available[row['key']]['_release'](job['mode'] == 'stop')
                except Exception as error:
                    result = {'status': 'unknown', 'error': str(error)}
                with self.lock:
                    row.update(result)
                    self.save(self.path, self.jobs)
                if row['status'] != 'waiting':
                    pending.remove(row)
            # Only user-requested completion handoffs poll; ordinary listings never query native runtimes.
            if pending:
                self.closed.wait(5)

    def close(self):
        self.closed.set()
