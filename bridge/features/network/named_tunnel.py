"""Remotely managed Cloudflare connector, with token kept out of argv and logs."""
import os
import subprocess
import threading
from pathlib import Path

from bridge.features.notifications.channels import write_json


class NamedTunnel:
    def __init__(self, executable, entry, data_dir, token):
        self.executable, self.entry, self.data_dir, self.token = str(executable), entry, Path(data_dir), token
        self.stopped = threading.Event()
        self.process = None
        self.thread = None

    def status(self, state, message):
        write_json(self.data_dir/('ssh-status-'+self.entry['id']+'.json'),
                   {'pid': os.getpid(), 'state': state, 'message': message})

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        delay = 2
        while not self.stopped.is_set():
            self.status('connecting', '正在连接 Cloudflare 固定隧道…')
            try:
                # Ignore ambient cloudflared config and credentials. Token is not a command argument.
                config = self.data_dir/('cloudflare-'+self.entry['id']+'.yml')
                config.write_text('{}\n', encoding='utf-8')
                config.chmod(0o600)
                environment = {key: value for key, value in os.environ.items() if not key.startswith('TUNNEL_')}
                environment['TUNNEL_TOKEN'] = self.token
                flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
                self.process = subprocess.Popen([self.executable, 'tunnel', '--config', str(config), '--no-autoupdate',
                    '--protocol', 'http2', '--metrics', '127.0.0.1:0', 'run'], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', env=environment, **flags)
                if self.stopped.is_set():
                    self.process.terminate()
                for line in self.process.stdout:
                    # Do not persist raw provider output: it can contain credentials or remote config.
                    if 'Registered tunnel connection' in line:
                        delay = 2
                        self.status('connected', '固定隧道已连接；请检测域名是否指向当前网关。')
                    elif 'ERR' in line or 'Unregistered tunnel connection' in line:
                        self.status('retrying', '固定隧道连接异常，请检查 Token、公开主机名和网络。')
                self.process.wait()
            except (OSError, ValueError):
                pass
            finally:
                if self.process and self.process.stdout:
                    self.process.stdout.close()
            if self.stopped.is_set():
                break
            self.status('retrying', '固定隧道已断开，正在自动重连。')
            if self.stopped.wait(delay):
                break
            delay = min(30, delay*2)
        self.status('stopped', '固定隧道已停止')

    def close(self):
        self.stopped.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.thread:
            self.thread.join(timeout=5)
