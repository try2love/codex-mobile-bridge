"""Short-lived metadata RPCs. Model turns always remain with the desktop."""
import json
import os
import queue
import subprocess
import threading
import time


class RuntimeErrorDetail(RuntimeError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class MetadataRuntime:
    METHODS = {'initialize', 'thread/start', 'thread/name/set', 'thread/read',
               'thread/archive', 'thread/unarchive', 'thread/delete'}

    def __init__(self, executable, home, cwd):
        self.executable, self.home, self.cwd = executable, home, cwd
        self.process = None
        self.counter = 0

    def __enter__(self):
        if not self.executable:
            raise RuntimeErrorDetail('找不到 Codex 运行时，请先安装并打开 Codex。')
        env = dict(os.environ, CODEX_HOME=str(self.home), CODEX_INTERNAL_ORIGINATOR_OVERRIDE='Codex Desktop')
        try:
            self.process = subprocess.Popen([str(self.executable), 'app-server', '--listen', 'stdio://'],
                cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError as exc:
            raise RuntimeErrorDetail('无法启动 Codex 运行时，请检查安装。') from exc
        self.messages = queue.Queue()
        def read():
            try:
                for line in self.process.stdout:
                    try:
                        self.messages.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                self.messages.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.request('initialize', {'clientInfo': {'name': 'codex_mobile_bridge', 'version': '1.5.0'},
                                       'capabilities': {'experimentalApi': True}})
            self.process.stdin.write('{"method":"initialized"}\n')
            self.process.stdin.flush()
        except Exception:
            self.close()
            raise
        return self

    def request(self, method, params):
        if method not in self.METHODS:
            raise ValueError('不支持的操作')
        self.counter += 1
        uncertain = method not in {'initialize', 'thread/read'}
        try:
            self.process.stdin.write(json.dumps({'id': self.counter, 'method': method, 'params': params}) + '\n')
            self.process.stdin.flush()
        except OSError as exc:
            raise RuntimeErrorDetail('Codex 连接中断，请先刷新确认结果。', uncertain) from exc
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                value = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if value is None:
                raise RuntimeErrorDetail('Codex 连接中断，请先刷新确认结果。', uncertain)
            if isinstance(value, dict) and value.get('id') == self.counter:
                if 'error' in value:
                    raise RuntimeErrorDetail(value['error'].get('message', 'Codex 操作失败'))
                if 'result' not in value:
                    raise RuntimeErrorDetail('Codex 返回格式不兼容，请刷新确认结果。', uncertain)
                return value['result']
        raise RuntimeErrorDetail('Codex 响应超时，请先刷新确认结果，不会自动重试写入。', uncertain)

    def close(self):
        if self.process is None:
            return
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=1)
        self.process.stdout.close()

    def __exit__(self, *args):
        self.close()
