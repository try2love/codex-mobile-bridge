"""Durable receipts for explicitly requested native context maintenance."""
import json
import threading
import uuid
from pathlib import Path


class CompactCommands:
    def __init__(self, directory, save):
        self.path = Path(directory) / 'session-compactions.json'
        self.save = save
        self.lock = threading.RLock()

    def perform(self, session_id, request_id, prepare):
        uuid.UUID(session_id)
        uuid.UUID(request_id)
        with self.lock:
            entries = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
            prior = entries.get(request_id)
            if prior:
                if prior['sessionId'] != session_id:
                    raise ValueError('相同请求标识不能用于不同会话')
                return dict(prior)
            if any(row['sessionId'] == session_id and row['status'] == 'unknown' for row in entries.values()):
                raise ValueError('此前压缩结果尚未确认，请先在原客户端核对；不会重复提交')
            # Fresh native idle/identity checks precede the durable write intent.
            dispatch = prepare()
            receipt = {'id': request_id, 'sessionId': session_id, 'status': 'unknown',
                       'message': '压缩结果尚未确认，请检查原会话；不会自动重复提交'}
            entries[request_id] = receipt
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.save(self.path, entries)
            try:
                dispatch()
            except Exception:
                return dict(receipt)
            receipt.update(status='accepted', message='已提交上下文压缩')
            self.save(self.path, entries)
            return dict(receipt)
