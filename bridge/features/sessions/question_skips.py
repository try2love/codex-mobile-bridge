"""Gateway-owned async-question dismissals, separate from native answers/approvals."""
import hashlib
import json
import threading
from pathlib import Path


class QuestionSkips:
    def __init__(self, directory, scope, save):
        digest = hashlib.sha256(scope.encode('utf-8')).hexdigest()
        self.path = Path(directory) / ('question-skips-' + digest + '.json')
        self.save = save
        self.lock = threading.RLock()
        self.records = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    @staticmethod
    def _key(thread_id, request_id):
        return json.dumps([thread_id, request_id], separators=(',', ':'))

    def contains(self, thread_id, request_id, question_id):
        with self.lock:
            return question_id in self.records.get(self._key(thread_id, request_id), [])

    def skip(self, thread_id, request_id, question_id):
        with self.lock:
            key = self._key(thread_id, request_id)
            previous = self.records.get(key, [])
            if question_id in previous:
                return
            records = {**self.records, key: [*previous, question_id]}
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.save(self.path, records)
            self.records = records

    def project(self, thread_id, requests):
        with self.lock:
            result = []
            for request in requests:
                if request.get('method') != 'bridge/requestUserInputAsync':
                    result.append(request)
                    continue
                skipped = self.records.get(self._key(thread_id, request['id']), [])
                questions = [q for q in request['params']['questions'] if q['id'] not in skipped]
                if questions:
                    result.append({**request, 'params': {**request['params'], 'questions': questions}})
            return result
