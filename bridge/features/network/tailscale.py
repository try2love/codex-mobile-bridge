"""Own one foreground Serve/Funnel session; never reset the user's tailnet."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from bridge.features.notifications.channels import write_json


def executable():
    if sys.platform == 'darwin':
        from bridge.platforms.macos import tailscale as native
    elif sys.platform == 'win32':
        from bridge.platforms.windows import tailscale as native
    else:
        from bridge.platforms.linux import tailscale as native
    candidates = native.candidates()
    found = shutil.which('tailscale')
    if found:
        candidates.append(Path(found))
    return next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), '')


def command_json(binary, arguments):
    try:
        result = subprocess.run([binary, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
                                encoding='utf-8', errors='replace', timeout=8,
                                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('无法连接 Tailscale，请打开客户端并检查系统网络权限。') from exc
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        # CLI output can contain account details and login URLs; do not relay it.
        raise ValueError('无法读取 Tailscale 状态，请更新官方客户端并完成登录。') from exc
    if result.returncode and not value.get('BackendState'):
        raise ValueError('无法读取 Tailscale 状态，请更新官方客户端并完成登录。')
    return value


def authorization_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        return ''
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == 'https' and parsed.hostname == 'login.tailscale.com'
                and parsed.port in (None, 443) and not parsed.username and not parsed.password):
            return value
    except ValueError:
        pass
    return ''


def device_url(status, port):
    host = status.get('Self', {}).get('DNSName', '').rstrip('.').lower()
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net', host):
        raise ValueError('Tailscale 尚未提供固定域名，请启用 MagicDNS 并完成登录。')
    return 'https://' + host + (':' + str(port) if port != 443 else '')


def port_in_use(config, port):
    """Foreground sessions and background rules belong to their original owners."""
    if not isinstance(config, dict):
        return False
    if str(port) in config.get('TCP', {}):
        return True
    if any(str(host).rsplit(':', 1)[-1] == str(port) for host in config.get('Web', {})):
        return True
    return any(port_in_use(item, port) for item in config.get('Foreground', {}).values())


def _layers(config):
    if not isinstance(config, dict):
        return
    yield config
    foreground = config.get('Foreground')
    if isinstance(foreground, dict):
        for value in foreground.values():
            yield from _layers(value)


def _web_entries(config):
    for layer in _layers(config):
        web = layer.get('Web')
        if isinstance(web, dict):
            yield from web.items()


def _web_key(value):
    if not isinstance(value, str):
        return None
    host, separator, port = value.rpartition(':')
    if not separator or not host or not port.isdigit():
        return None
    return host.rstrip('.').lower(), int(port)


def desired_route(entry, backend_port):
    public = urlsplit(entry.get('publicUrl', ''))
    host = (public.hostname or '').rstrip('.').lower()
    port = entry.get('tailscalePort')
    if not host or type(port) is not int:
        raise ValueError('Tailscale 固定地址无效，请重新检测并保存连接配置。')
    return ((host, port), f'http://127.0.0.1:{backend_port}',
            entry.get('tailscaleMode') == 'funnel')


def route_state(config, entry, backend_port, target_validator=None):
    """Return none, same, or other without taking over an unrelated rule."""
    expected_key, expected_target, funnel = desired_route(entry, backend_port)
    matches = [(key, value) for key, value in _web_entries(config)
               if _web_key(key) == expected_key]
    if matches:
        if len(matches) != 1:
            return 'other'
        value = matches[0][1]
        handlers = value.get('Handlers') if isinstance(value, dict) else None
        root = handlers.get('/') if isinstance(handlers, dict) else None
        target = root.get('Proxy') if isinstance(root, dict) else None
        if set(handlers or {}) != {'/'} or not isinstance(target, str):
            return 'other'
        normalized = target.rstrip('/')
        if normalized != expected_target and not (target_validator and target_validator(normalized)):
            return 'other'
        allowed = any(layer.get('AllowFunnel', {}).get(f'{expected_key[0]}:{expected_key[1]}') is True
                      for layer in _layers(config)
                      if isinstance(layer.get('AllowFunnel'), dict))
        if allowed != funnel:
            return 'other'
        return 'same'
    return 'other' if port_in_use(config, expected_key[1]) else 'none'


def inspect(entry):
    binary = executable()
    if not binary:
        return {'state': 'missing', 'available': False,
                'message': '未找到 Tailscale，请先安装官方客户端并完成登录。'}
    status = command_json(binary, ['status', '--json'])
    version = str(status.get('Version', ''))
    match = re.match(r'(\d+)\.(\d+)', version)
    if not match or tuple(map(int, match.groups())) < (1, 52):
        raise ValueError('请更新 Tailscale 至 1.52 或更高版本。')
    if status.get('BackendState') != 'Running':
        return {'state': 'login', 'available': True, 'version': version,
                'authUrl': authorization_url(status.get('AuthURL', '')),
                'message': '请在 Tailscale 客户端完成登录并开启连接，然后重新检测。'}
    node = status.get('Self') or {}
    if node.get('KeyExpired'):
        raise ValueError('Tailscale 设备授权已过期，请重新登录。')
    node_id = node.get('ID', '')
    if not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', node_id):
        raise ValueError('无法读取 Tailscale 设备身份，请更新官方客户端并完成登录。')
    return {'state': 'ready', 'available': True, 'version': version,
            'url': device_url(status, entry.get('tailscalePort', 443)), 'nodeId': node_id,
            'message': '已读取 Tailscale 固定地址。保存配置后启动网关，再检测固定入口。'}


def require_identity(entry):
    state = inspect(entry)
    if state['state'] != 'ready':
        raise ValueError(state['message'])
    if state['url'] != entry.get('publicUrl') or state['nodeId'] != entry.get('tailscaleNodeId'):
        raise ValueError('Tailscale 账号或设备地址已变化，请重新检测并保存连接配置。')
    return state


class TailscaleTunnel:
    ROUTE_CHECK_INTERVAL = 10.0
    ROUTE_FAILURE_LIMIT = 3

    def __init__(self, entry, port, data_dir, target_validator=None):
        self.entry, self.port, self.data_dir = entry, port, Path(data_dir)
        self.target_validator = target_validator
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.process = self.thread = self.reader = None
        self.last_status = None

    def status(self, state, message, auth_url=''):
        record = {'pid': os.getpid(), 'state': state, 'message': message,
                  'url': self.entry['publicUrl'], 'authUrl': authorization_url(auth_url)}
        if record != self.last_status:
            write_json(self.data_dir/('ssh-status-' + self.entry['id'] + '.json'), record)
            self.last_status = record

    def start(self):
        self.thread = threading.Thread(target=self._run, name='tailscale-entry', daemon=True)
        self.thread.start()

    def network_changed(self):
        self.wake.set()
        return True

    def _route_state(self, binary):
        require_identity(self.entry)
        config = command_json(binary, ['serve', 'status', '--json'])
        return route_state(config, self.entry, self.port, self.target_validator)

    def _wait_for_route_check(self):
        self.wake.wait(self.ROUTE_CHECK_INTERVAL)
        self.wake.clear()

    def _monitor_adopted(self, binary):
        failures = 0
        while not self.stopped.is_set():
            self._wait_for_route_check()
            if self.stopped.is_set():
                return 'stopped'
            try:
                state = self._route_state(binary)
            except ValueError:
                failures += 1
                self.status('retrying', '暂时无法读取 Tailscale 状态，正在重新检查…')
                if failures >= self.ROUTE_FAILURE_LIMIT:
                    return 'retry'
                continue
            failures = 0
            if state == 'same':
                self.status('connected', 'Tailscale 固定入口已接管并保持连接。')
            elif state == 'none':
                self.status('retrying', 'Tailscale 固定入口已丢失，正在重新建立…')
                return 'retry'
            else:
                self.status('blocked', '此 Tailscale 端口已被其他服务使用，请选择其他端口；不会覆盖已有配置。')
                return 'blocked'
        return 'stopped'

    def _monitor_process(self, process, binary):
        failures = 0
        while not self.stopped.is_set() and process.poll() is None:
            if not self.ready.wait(1):
                continue
            self._wait_for_route_check()
            if self.stopped.is_set() or process.poll() is not None:
                break
            try:
                state = self._route_state(binary)
            except ValueError:
                failures += 1
                self.status('retrying', 'Tailscale 入口暂时不可用，正在重新检查…')
                if failures >= self.ROUTE_FAILURE_LIMIT:
                    return 'retry'
                continue
            failures = 0
            if state == 'same':
                self.status('connected', 'Tailscale 固定入口已启动并保持连接。')
            elif state == 'none':
                self.status('retrying', 'Tailscale 固定入口已丢失，正在重新建立…')
                return 'retry'
            else:
                self.status('blocked', '此 Tailscale 端口已被其他服务使用，请选择其他端口；不会覆盖已有配置。')
                return 'blocked'
        return 'retry'

    def _read(self, process):
        try:
            for line in process.stdout:
                if self.stopped.is_set():
                    break
                urls = re.findall(r'https://[^\s<>"\x1b]+', line)
                auth_url = next((authorization_url(url) for url in urls if authorization_url(url)), '')
                if auth_url:
                    self.status('authorization', '请完成 Tailscale HTTPS 或 Funnel 授权，完成后会继续连接。', auth_url)
                elif 'Available on the internet' in line or 'Available within your tailnet' in line:
                    self.ready.set()
                    self.status('connected', 'Tailscale 入口已启动，请检测固定入口并用手机验证。')
        finally:
            self.ready.clear()

    def _run(self):
        delay = 3
        while not self.stopped.is_set():
            self.status('connecting', '正在建立 Tailscale 固定入口…')
            process = None
            reader = None
            result = 'retry'
            self.ready.clear()
            try:
                require_identity(self.entry)
                binary = executable()
                config = command_json(binary, ['serve', 'status', '--json'])
                state = route_state(config, self.entry, self.port, self.target_validator)
                if state == 'other':
                    self.status('blocked', '此 Tailscale 端口已被其他服务使用，请选择其他端口；不会覆盖已有配置。')
                    return
                if state == 'same':
                    delay = 3
                    self.status('connected', 'Tailscale 固定入口已接管并保持连接。')
                    result = self._monitor_adopted(binary)
                else:
                    # Foreground sessions are removed by tailscaled when this CLI
                    # disconnects. Never use --bg, reset, down, or logout here.
                    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
                    with self.lock:
                        if self.stopped.is_set():
                            return
                        process = self.process = subprocess.Popen(
                            [binary, self.entry['tailscaleMode'], '--https=' + str(self.entry['tailscalePort']),
                             'http://127.0.0.1:' + str(self.port)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding='utf-8', errors='replace', **flags)
                    reader = self.reader = threading.Thread(
                        target=self._read, args=(process,), name='tailscale-entry-output', daemon=True)
                    reader.start()
                    result = self._monitor_process(process, binary)
            except (OSError, ValueError) as exc:
                if not self.stopped.is_set():
                    self.status('retrying', str(exc) if isinstance(exc, ValueError) else 'Tailscale 入口已断开，正在重试。')
            finally:
                if process:
                    self._terminate(process)
                    if reader:
                        reader.join(timeout=3)
                    if process.stdout:
                        process.stdout.close()
                with self.lock:
                    self.process = None
                    self.reader = None
                self.ready.clear()
            if result == 'blocked':
                return
            if self.stopped.is_set():
                break
            if self.last_status and self.last_status['state'] in ('connected', 'connecting', 'authorization'):
                self.status('retrying', 'Tailscale 入口未保持连接，请检查客户端、网络及 Funnel 授权。')
            if self.stopped.wait(delay):
                break
            delay = min(60, delay * 2)

    @staticmethod
    def _terminate(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def close(self):
        self.stopped.set()
        self.wake.set()
        with self.lock:
            if self.process:
                self._terminate(self.process)
        if self.thread:
            self.thread.join(timeout=20)
        if self.reader and self.reader is not threading.current_thread():
            self.reader.join(timeout=3)
        self.status('stopped', 'Tailscale 固定入口已停止')
