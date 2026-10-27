"""Cooperative local shutdown; never signal a PID that may have been reused."""
import json
import os
import secrets
import threading
import time
from pathlib import Path


def read_record(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class GatewayControl:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'gateway-control.json'
        self.request_path = Path(data_dir) / 'gateway.stop'
        self.record = {'pid': os.getpid(), 'token': secrets.token_hex(32)}
        launch_id = os.environ.get('CODEX_MOBILE_GATEWAY_LAUNCH_ID')
        if launch_id:
            self.record['launchId'] = launch_id
        self.closed = threading.Event()
        self.worker = None

    def start(self, shutdown):
        self.request_path.unlink(missing_ok=True)
        self.path.write_text(json.dumps(self.record), encoding='utf-8')
        self.path.chmod(0o600)
        def watch():
            while not self.closed.wait(.2):
                if read_record(self.request_path) == self.record:
                    shutdown()
                    return
        self.worker = threading.Thread(target=watch, daemon=True)
        self.worker.start()

    def close(self):
        self.closed.set()
        if self.worker:
            self.worker.join(timeout=2)
        for path in (self.path, self.request_path):
            if read_record(path) == self.record:
                path.unlink(missing_ok=True)


def request_stop(data_dir, timeout=15):
    path = Path(data_dir) / 'gateway-control.json'
    record = read_record(path)
    if not isinstance(record, dict) or not isinstance(record.get('pid'), int) or not record.get('token'):
        raise RuntimeError('No gateway control record; use Ctrl+C in its terminal if it is running.')
    request = path.with_name('gateway.stop')
    request.write_text(json.dumps(record), encoding='utf-8')
    request.chmod(0o600)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if read_record(path) != record:
                return
            time.sleep(.1)
        raise RuntimeError('Gateway did not acknowledge shutdown; the record may be stale. No process was killed.')
    finally:
        if read_record(request) == record:
            request.unlink(missing_ok=True)
