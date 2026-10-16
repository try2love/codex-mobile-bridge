"""An opt-in Cloudflare Quick Tunnel whose lifetime follows this gateway."""
import os
import re
import subprocess
import threading
from pathlib import Path


class QuickTunnel:
    URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com\b')

    def __init__(self, executable, port, data_dir, on_origin):
        self.executable = Path(executable)
        self.port = port
        self.data_dir = Path(data_dir)
        self.on_origin = on_origin
        self.process = None
        self.url = None
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.failure = None
        self.reader = None
        self.closed = threading.Event()

    def start(self, cancel=None, on_progress=None):
        if cancel is not None and cancel.is_set():
            raise RuntimeError('外网连接已取消')
        if not self.executable.is_file():
            raise RuntimeError("缺少 cloudflared，请查看 README 的外网访问配置")
        config = self.data_dir / 'cloudflared.yml'
        config.write_text('{}\n', encoding='utf-8')
        config.chmod(0o600)
        args = [str(self.executable), 'tunnel', '--config', str(config), '--no-autoupdate',
                '--url', 'http://127.0.0.1:' + str(self.port), '--protocol', 'http2',
                '--metrics', '127.0.0.1:0', '--grace-period', '2s']
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', start_new_session=True,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        for elapsed in range(60):
            if cancel is not None and cancel.is_set():
                self.close()
                raise RuntimeError('外网连接已取消')
            if on_progress and elapsed % 5 == 0:
                phase = '正在建立 Cloudflare 外网隧道' if self.url else '正在向 Cloudflare 申请临时网址'
                on_progress(f'{phase}（已等待 {elapsed} / 60 秒）')
            if self.ready.wait(1):
                return self.url
            if self.finished.is_set():
                break
        self.close()
        reason = self.failure or ('临时网址已生成，但隧道未连通；请检查网络是否允许访问 Cloudflare 的 TCP 7844 端口'
                                  if self.url else '未能申请临时网址；请检查 DNS 和 api.trycloudflare.com 的 HTTPS 访问')
        raise RuntimeError(reason + '。日志：' + str(self.data_dir / 'tunnel.log'))

    def _read(self):
        try:
            with (self.data_dir / 'tunnel.log').open('a', encoding='utf-8') as log:
                for line in self.process.stdout:
                    log.write(line)
                    log.flush()
                    lower = line.lower()
                    if not self.closed.is_set() and (' err ' in lower or 'error=' in lower or 'status=fail' in lower):
                        if any(word in lower for word in ('no such host', 'lookup ', 'dns resolution')):
                            self.failure = 'Cloudflare 域名解析失败，请检查 DNS 或切换网络'
                        elif 'x509' in lower or 'certificate' in lower:
                            self.failure = 'Cloudflare 安全连接校验失败，请检查系统时间、证书或代理设置'
                        elif '7844' in lower or 'dial edge' in lower:
                            self.failure = '无法连接 Cloudflare 隧道端口 TCP 7844，请检查防火墙或切换网络'
                        elif '429' in lower or 'too many requests' in lower:
                            self.failure = 'Cloudflare 临时网址申请频率受限，请稍后重试'
                        elif 'failed to request quick tunnel' in lower or 'api.trycloudflare.com' in lower:
                            self.failure = '无法向 Cloudflare 申请临时网址，请检查 HTTPS 网络或稍后重试'
                    match = self.URL.search(line)
                    if match and not self.url:
                        self.url = match[0]
                        self.on_origin(self.url)
                    if 'Registered tunnel connection' in line and self.url and not self.closed.is_set():
                        (self.data_dir / '外网地址.txt').write_text(self.url + '\n\n账号和密码与局域网网关相同。重启隧道后地址会变化。\n', encoding='utf-8')
                        self.ready.set()
        except (OSError, RuntimeError) as exc:
            self.failure = '临时连接日志或状态写入失败：' + str(exc)
        finally:
            self.finished.set()

    def close(self):
        self.closed.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.reader and self.reader is not threading.current_thread():
            self.reader.join(timeout=2)
        path = self.data_dir / '外网地址.txt'
        if path.exists() and self.url and path.read_text(encoding='utf-8').startswith(self.url):
            path.unlink()
