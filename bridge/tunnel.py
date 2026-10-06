"""An opt-in Cloudflare Quick Tunnel whose lifetime follows this gateway."""
import os
import re
import subprocess
import threading
from pathlib import Path

from .notifications import write_json


class QuickTunnel:
    URL = re.compile(r'https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com\b')
    LOST_TUNNEL = re.compile(r'Unauthorized: Tunnel not found')
    RESTART_DELAY = 2
    START_POLL = 0.5

    def __init__(self, executable, port, data_dir, on_origin):
        self.executable = Path(executable)
        self.port = port
        self.data_dir = Path(data_dir)
        self.on_origin = on_origin
        self.process = None
        self.url = None
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.broken = threading.Event()
        self.failure = None
        self.closed = threading.Event()
        self.lifecycle_lock = threading.Lock()
        self.supervisor = None
        self.reader = None

    def status(self, state, message):
        write_json(self.data_dir/'cloudflare-status.json', {'pid': os.getpid(), 'state': state, 'message': message})

    def start(self):
        if self.closed.is_set():
            raise RuntimeError("隧道已停止")
        if not self.executable.is_file():
            raise RuntimeError("缺少 cloudflared，请查看 README 的外网访问配置")

        # A Quick Tunnel URL is created by cloudflared.  If the tunnel is
        # tombstoned after sleep/network loss, retrying the same process cannot
        # recover the old DNS name; a new process creates the replacement URL.
        self.supervisor = threading.Thread(target=self._supervise, name='quick-tunnel', daemon=True)
        self.supervisor.start()
        # Cloudflare retries Quick Tunnel creation inside the same process.
        # Keep the gateway available while that handshake is unhealthy; the
        # supervisor only replaces the child when it exits or reports a loss.
        while not self.closed.wait(self.START_POLL):
            if self.ready.is_set():
                return self.url
        raise RuntimeError("隧道已停止")

    def _supervise(self):
        delay = 0
        while not self.closed.wait(delay):
            delay = self.RESTART_DELAY
            if self._spawn():
                while not (self.broken.wait(0.5) or self.finished.wait(0) or self.closed.is_set()):
                    pass
                if self.closed.is_set():
                    break
                self.status('reconnecting', '临时 HTTPS 已中断，正在自动重建连接；网址会变化。')
            elif self.closed.is_set():
                break
            elif not self.url:
                self.status('connecting', '正在建立临时 HTTPS 连接，局域网可独立使用。')
            self._retire()

    def _spawn(self):
        self.status('connecting', '正在建立临时 HTTPS 连接，局域网可独立使用。')
        config = self.data_dir / 'cloudflared.yml'
        config.write_text('{}\n', encoding='utf-8')
        config.chmod(0o600)
        args = [str(self.executable), 'tunnel', '--config', str(config), '--no-autoupdate',
                '--url', 'http://127.0.0.1:' + str(self.port), '--protocol', 'http2',
                '--metrics', '127.0.0.1:0', '--grace-period', '2s']
        with self.lifecycle_lock:
            if self.closed.is_set():
                return False
            self.url = None
            self.ready.clear()
            self.finished.clear()
            self.broken.clear()
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', start_new_session=True)
            self.process = process
            self.reader = threading.Thread(target=self._read, args=(process,), daemon=True)
            self.reader.start()
        while not self.closed.is_set():
            if self.ready.is_set():
                return True
            if self.broken.is_set() or self.finished.is_set():
                return False
            self.ready.wait(self.START_POLL)
        return False

    def _read(self, process):
        try:
            with (self.data_dir / 'tunnel.log').open('a', encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    match = self.URL.search(line)
                    with self.lifecycle_lock:
                        if self.closed.is_set():
                            break
                        if match and not self.url:
                            self.url = match[0]
                            self.on_origin(self.url)
                        if 'Registered tunnel connection' in line and self.url:
                            (self.data_dir / '外网地址.txt').write_text(self.url + '\n\n账号和密码与局域网网关相同。重启隧道后地址会变化。\n', encoding='utf-8')
                            self.status('ready', '临时 HTTPS 已连接。')
                            self.ready.set()
                        if self.LOST_TUNNEL.search(line) and not self.closed.is_set():
                            self.broken.set()
                            self.ready.clear()
                            self.status('reconnecting', '临时 HTTPS 已失效，正在自动重建连接；网址会变化。')
        finally:
            if not self.closed.is_set() and self.process is process and not self.broken.is_set():
                self.status('failed', '临时 HTTPS 连接已中断，请查看 Cloudflare 日志并重启网关重试；局域网仍可使用。')
            self.finished.set()

    def _retire(self):
        with self.lifecycle_lock:
            process = self.process
            reader = self.reader
            self.process = None
            self.reader = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2)

    def close(self):
        # Mark closed before waiting for a concurrent spawn to finish.
        self.closed.set()
        self._retire()
        with self.lifecycle_lock:
            path = self.data_dir / '外网地址.txt'
            if path.exists() and self.url and path.read_text(encoding='utf-8').startswith(self.url):
                path.unlink()
        if self.supervisor and self.supervisor is not threading.current_thread():
            self.supervisor.join(timeout=5)
