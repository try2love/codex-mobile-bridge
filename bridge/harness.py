"""Optional, desktop-owned Harness runtime. Launch credentials never leave here."""
import collections
import http.client
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, quote

from .notifications import read_json, write_json

PREFIX = '/harness/'


def configuration(data_dir):
    saved = read_json(Path(data_dir) / 'harness.json', {})
    return {key: saved.get(key, default) for key, default in {
        'executable': '', 'workspace': '', 'home': str(Path(data_dir) / 'harness-home')}.items()}


def detect():
    candidates = [shutil.which('dsh'), str(Path.home() / '.local/bin/dsh'),
                  '/opt/homebrew/bin/dsh', '/usr/local/bin/dsh']
    for path in candidates:
        if not path or not Path(path).is_file():
            continue
        if Path(path).suffix.lower() in ('.cmd', '.bat'):
            entry = Path(path).parent / 'node_modules/@deepseek-ai/dsh/lib/bin.js'
            if entry.is_file():
                return str(entry)
        else:
            return str(Path(path).resolve())
    return ''


def validate(value):
    result = {}
    for key in ('executable', 'workspace', 'home'):
        raw = value.get(key)
        if not isinstance(raw, str) or not raw.strip() or any(ord(c) < 32 for c in raw):
            raise ValueError('请填写 Harness 程序、工作目录和数据目录')
        result[key] = str(Path(raw).expanduser().resolve())
    if not Path(result['executable']).is_file() or not Path(result['workspace']).is_dir():
        raise ValueError('Harness 程序或工作目录不存在')
    if Path(result['home']).exists() and not Path(result['home']).is_dir():
        raise ValueError('Harness 数据目录必须是文件夹')
    command(result['executable'])
    return result


def command(executable):
    suffix = Path(executable).suffix.lower()
    if suffix in ('.js', '.mjs', '.cjs'):
        node = shutil.which('node')
        if not node:
            node = next((p for p in ('/opt/homebrew/bin/node', '/usr/local/bin/node') if Path(p).is_file()), None)
        if not node:
            raise ValueError('未找到 Node.js，请使用 Harness 独立可执行程序')
        return [node, executable]
    if suffix in ('.cmd', '.bat'):
        raise ValueError('请选择 Harness 可执行程序或 npm 包中的 JavaScript 入口，不支持批处理启动器')
    if os.name != 'nt' and not os.access(executable, os.X_OK):
        raise ValueError('Harness 程序没有执行权限')
    return [executable]


def save(data_dir, value):
    result = validate(value)
    write_json(Path(data_dir) / 'harness.json', result)
    return result


class Harness:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.process = None
        self.port = None
        self.cookie = ''
        self.cookie_until = 0
        self.launch_token = ''
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.logs = collections.deque(maxlen=120)
        self.phase = 'stopped'

    def status(self, private=False):
        with self.lock:
            if self.process and self.process.poll() is not None and self.phase != 'stopped':
                self.phase = 'failed'
                self.cookie = ''
            result = {'state': self.phase, 'running': self.phase == 'running', 'url': PREFIX}
            if private:
                result.update(config=configuration(self.data_dir), logs=list(self.logs))
            return result

    def control(self, value):
        action = value.get('action', 'status')
        if action == 'status':
            return self.status(True)
        if action == 'detect':
            return {**self.status(True), 'detected': detect()}
        if action not in ('save', 'start', 'stop'):
            raise ValueError('不支持的 Harness 操作')
        if not self.operation.acquire(blocking=False):
            raise ValueError('Harness 正在启动或停止，请稍候')
        try:
            if action == 'save':
                if self.process and self.process.poll() is None:
                    raise ValueError('请先停止 Harness 再修改配置')
                save(self.data_dir, value.get('config', {}))
            elif action == 'start':
                self.start()
            else:
                self.stop()
            return self.status(True)
        finally:
            self.operation.release()

    def _read_output(self, process):
        # Do not retain arbitrary agent/model logs: they may contain credentials
        # or file contents. Only bounded, supervisor-generated lifecycle logs.
        try:
            for raw in iter(lambda: process.stdout.readline(16384), b''):
                line = raw.decode('utf-8', 'replace')
                if 'dsh web:' not in line:
                    continue
                match = re.search(r'https?://[^\s\x1b]+', line)
                if not match:
                    continue
                url = urlsplit(match[0])
                tokens = parse_qs(url.query).get('token', [])
                if (url.netloc == f'127.0.0.1:{self.port}' and url.path in ('/', PREFIX)
                        and len(tokens) == 1 and re.fullmatch(r'[\w-]{20,256}', tokens[0])):
                    with self.lock:
                        if self.process is process:
                            self.launch_token = tokens[0]
                            self.ready.set()
        finally:
            process.stdout.close()
            if self.process is process:
                self.ready.set()

    def _authenticate(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request('GET', '/?token=' + quote(self.launch_token), headers={'Host': f'127.0.0.1:{self.port}'})
            response = connection.getresponse()
            cookies = SimpleCookie()
            for key, value in response.getheaders():
                if key.lower() == 'set-cookie':
                    cookies.load(value)
            values = [m for key, m in cookies.items() if key.startswith('dsh-auth-')]
            if response.status != 303 or len(values) != 1:
                raise ValueError('Harness 登录握手失败，请检查版本兼容性')
            self.cookie = values[0].OutputString(attrs=[])
            self.cookie_until = time.monotonic() + min(int(values[0]['max-age'] or 3600), 86400) - 60
        finally:
            connection.close()

    def endpoint(self):
        with self.lock:
            if not self.status()['running'] or not self.cookie:
                raise ValueError('请在电脑 App 中启动 DeepSeek Harness')
            if time.monotonic() >= self.cookie_until:
                self._authenticate()
            return self.port, self.cookie, self.process

    def start(self):
        if self.closed.is_set():
            raise ValueError('网关正在停止')
        if self.process and self.process.poll() is None:
            return
        config = validate(configuration(self.data_dir))
        Path(config['home']).mkdir(parents=True, exist_ok=True, mode=0o700)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        args = command(config['executable']) + ['--profile', 'web', '--no-open', '--port', str(port)]
        environment = {**os.environ, 'DSH_HOME': config['home'], 'NO_COLOR': '1'}
        options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        with self.lock:
            self.phase, self.port, self.cookie, self.launch_token = 'starting', port, '', ''
            self.ready.clear()
            self.logs.append('Harness starting')
        try:
            self.process = subprocess.Popen(args, cwd=config['workspace'], env=environment, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
            threading.Thread(target=self._read_output, args=(self.process,), daemon=True).start()
            deadline = time.monotonic() + 60
            while not self.ready.wait(.2):
                if self.closed.is_set() or time.monotonic() > deadline or self.process.poll() is not None:
                    raise ValueError('Harness 未能就绪，请检查程序版本和配置')
            if self.closed.is_set() or not self.launch_token:
                raise ValueError('Harness 启动失败，请检查程序版本和配置')
            with self.lock:
                self._authenticate()
                self.phase = 'running'
                self.logs.append('Harness ready')
        except Exception:
            self.stop()
            self.phase = 'failed'
            self.logs.append('Harness startup failed')
            raise ValueError('Harness 启动失败，请检查程序版本、Node.js 和数据目录') from None

    def stop(self):
        with self.lock:
            process = self.process
            self.phase, self.cookie, self.launch_token = 'stopped', '', ''
        if process:
            if os.name == 'nt':
                if process.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            elif process.poll() is None:
                # Dedicated process group; never signal an exited or saved PID.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise ValueError('Harness 未能停止，请检查运行进程') from None
            self.process = None
            self.logs.append('Harness stopped')

    def close(self):
        self.closed.set()
        with self.operation:
            self.stop()
