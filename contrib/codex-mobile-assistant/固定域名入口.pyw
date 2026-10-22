"""Windows mobile bridge with temporary and self-hosted access modes."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import runpy
import select
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from PIL import Image, ImageDraw
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import webbrowser

FROZEN = getattr(sys, 'frozen', False)
BASE = Path(sys.executable if FROZEN else __file__).resolve().parent
ASSETS = Path(getattr(sys, '_MEIPASS', BASE))
DATA = BASE / 'app' / '.local'
PREFS = DATA / 'fixed-domain.json'
KNOWN_HOSTS = DATA / 'fixed-domain-known-hosts'
ORIGIN = ''
SERVER = ''
REMOTE_PORT = 18787
LOCAL_PORT = 8787
sys.path.insert(0, str(ASSETS / 'app'))
sys.path.insert(0, str(ASSETS / 'vendor'))
from bridge.lifecycle import request_stop
from bridge.tunnel import QuickTunnel
from qrcodegen import QrCode
from desktop_support import VERSION, TrayIcon, startup_command, restore_existing, check_update, https_url

APP_KEY = hashlib.sha256(str(BASE).lower().encode()).hexdigest()[:20]
PREFS_LOCK = threading.RLock()


def gateway_worker():
    """Use the same EXE as the backend, with persistent data outside its bundle."""
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / 'gateway.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        sys.stdin = open(os.devnull, encoding='utf-8')
        try:
            origin = normalize_origin(sys.argv[sys.argv.index('--gateway-origin') + 1])
            sys.argv = [str(ASSETS / 'app' / 'run.py'), '--config', str(DATA / 'config.json'),
                        '--origin', origin]
            runpy.run_path(sys.argv[0], run_name='__main__')
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
    return 0


def normalize_origin(value):
    value = value.strip()
    parsed = urlsplit(value if '://' in value else 'https://' + value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('请输入 HTTPS 域名，不包含其他路径，例如 https://codex.example.com')
    host = parsed.hostname.encode('idna').decode('ascii').lower()
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('域名端口必须为 1–65535')
    return 'https://' + host + (':' + str(port) if port else '')


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect_password(raw, decrypt=False):
    """DPAPI binds remembered credentials to the current Windows user."""
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def load_preferences():
    try:
        value = json.loads(PREFS.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            return {}
        # Legacy profiles retain only their explicitly configured endpoint.
        if 'mode' not in value:
            value.update(mode='fixed', origin=value.get('origin', ''), server=value.get('server', ''), sshPort=value.get('sshPort', 22),
                         autoConnect=bool(value.get('passwordCipher')))
        cipher = value.get('passwordCipher')
        if cipher:
            try:
                value['password'] = protect_password(base64.b64decode(cipher), decrypt=True).decode('utf-8')
            except (OSError, ValueError, TypeError):
                value['password'] = ''
        return value
    except (OSError, ValueError, TypeError):
        return {}


def save_preferences(settings, password='', clear_password=False):
    with PREFS_LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        try:
            previous = json.loads(PREFS.read_text(encoding='utf-8'))
            value = previous if isinstance(previous, dict) else {}
        except (OSError, ValueError):
            value = {}
        if 'mode' not in value and value.get('passwordCipher'):
            value.setdefault('server', SERVER)
            value.setdefault('sshPort', 22)
        if any(value.get(key, settings.get(key)) != settings.get(key) for key in ('server', 'sshPort', 'username')):
            value.pop('passwordCipher', None)
        value.update(settings)
        value.pop('password', None)
        if clear_password or (settings['mode'] == 'fixed' and not settings.get('remember')):
            value.pop('passwordCipher', None)
        elif settings['mode'] == 'fixed' and password and settings.get('remember'):
            value['passwordCipher'] = base64.b64encode(protect_password(password.encode('utf-8'))).decode('ascii')
        temporary = PREFS.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(PREFS)


def gateway_auth(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url + '/api/auth', timeout=6) as response:
        if response.geturl() != url + '/api/auth':
            raise ValueError('网关请求被重定向，请检查反向代理')
        value = json.loads(response.read(16384))
    if not isinstance(value, dict) or not isinstance(value.get('authenticated'), bool):
        raise ValueError('网站未返回网关状态')
    if value.get('passwordless') is not False:
        raise ValueError('网关没有启用账号密码')
    return value


def quick_gateway_auth(url, cancel, timeout=8):
    # urllib's socket timeout does not bound a stalled DNS resolver.
    result = queue.Queue(maxsize=1)
    def probe():
        try:
            result.put((gateway_auth(url), None))
        except Exception as exc:
            result.put((None, exc))
    threading.Thread(target=probe, daemon=True).start()
    deadline = time.monotonic() + timeout
    while not cancel.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('临时网址访问超时，请检查 DNS、网络或代理设置')
        try:
            value, error = result.get(timeout=min(.2, remaining))
        except queue.Empty:
            continue
        if error:
            raise error
        return value
    raise RuntimeError('临时连接已取消')


class HostRejected(Exception):
    pass


class Connection:
    def __init__(self, events):
        self.events = events
        self.cancel = threading.Event()
        self.thread = None
        self.gateway = None
        self.client = None
        self.tunnel = None
        self.settings = {}
        self.slots = threading.BoundedSemaphore(64)

    def emit(self, kind, value=None):
        self.events.put((kind, value))

    def log(self, message):
        try:
            with (DATA / 'fixed-domain.log').open('a', encoding='utf-8') as stream:
                stream.write(time.strftime('%Y-%m-%d %H:%M:%S ') + str(message) + '\n')
        except OSError:
            pass

    def start(self, settings, password):
        self.cancel.clear()
        self.settings = dict(settings)
        self.thread = threading.Thread(target=self.run, args=(self.settings, password), daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel.set()
        if self.client:
            self.client.close()

    def confirm_host(self, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode('ascii').rstrip('=')
        request = {'hostname': hostname, 'type': key.get_name(), 'fingerprint': 'SHA256:' + fingerprint,
                   'event': threading.Event(), 'accepted': False}
        self.emit('host', request)
        while not request['event'].wait(.2):
            if self.cancel.is_set():
                raise HostRejected('连接已取消')
        if not request['accepted']:
            raise HostRejected('尚未信任服务器，请核对主机指纹后重新连接')

    def prepare_gateway(self):
        config_path = DATA / 'config.json'
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding='utf-8'))
            if config.get('auth', {}).get('mode') != 'password':
                raise RuntimeError('现有网关未启用密码，请先在原网关启用账号密码')
        try:
            occupied = socket.create_connection(('127.0.0.1', LOCAL_PORT), timeout=.5)
        except OSError:
            occupied = None
        if occupied:
            occupied.close()
            if not (DATA / 'gateway-control.json').is_file():
                raise RuntimeError('8787 端口被其他程序占用，请关闭该程序后重试')
            self.emit('status', '正在停止原网关，切换访问模式…')
            request_stop(DATA)

    def ensure_gateway(self, origin):
        if self.cancel.is_set():
            return
        command = ([sys.executable] if FROZEN else [str(Path(sys.executable).with_name('python.exe')), '-B', str(Path(__file__).resolve())])
        worker_env = os.environ.copy()
        launch_id = os.urandom(16).hex()
        worker_env['CODEX_MOBILE_GATEWAY_LAUNCH_ID'] = launch_id
        if FROZEN:
            # The worker needs its own bundle lifetime if the desktop process exits.
            worker_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        self.gateway = subprocess.Popen(command + ['--gateway-worker', '--gateway-origin', origin], cwd=str(BASE),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, env=worker_env)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not self.cancel.is_set():
            if self.gateway.poll() is not None:
                raise RuntimeError('网关启动失败，请点击“查看日志”查看 gateway.log')
            try:
                record = json.loads((DATA / 'gateway-control.json').read_text(encoding='utf-8'))
                # A onefile bootloader has a different PID from its Python worker.
                if record.get('launchId') == launch_id:
                    gateway_auth('http://127.0.0.1:' + str(LOCAL_PORT))
                    return
            except (OSError, ValueError):
                pass
            self.cancel.wait(.3)
        if not self.cancel.is_set():
            raise RuntimeError('网关启动超时，请查看 gateway.log')

    def connect_ssh(self, username, password):
        import paramiko
        owner = self

        class TrustHost(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                owner.confirm_host(hostname, key)
                client.get_host_keys().add(hostname, key.get_name(), key)
                client.save_host_keys(str(KNOWN_HOSTS))

        client = paramiko.SSHClient()
        self.client = client
        if KNOWN_HOSTS.is_file():
            client.load_host_keys(str(KNOWN_HOSTS))
        client.set_missing_host_key_policy(TrustHost())
        server, port = self.settings['server'], self.settings['sshPort']
        wire = socket.create_connection((server, port), timeout=10)
        try:
            wire.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
                wire.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30000, 10000))
            client.connect(server, port=port, username=username, password=password, sock=wire,
                           timeout=10, auth_timeout=15, banner_timeout=15,
                           look_for_keys=False, allow_agent=False)
        except BaseException:
            wire.close()
            raise
        transport = client.get_transport()
        transport.set_keepalive(15)
        try:
            transport.request_port_forward('127.0.0.1', REMOTE_PORT)
        except paramiko.SSHException as exc:
            raise RuntimeError('服务器拒绝转发：18787 端口可能被占用，或 SSH 未允许远程端口转发') from exc
        return transport

    def forward(self, channel):
        local = None
        try:
            local = socket.create_connection(('127.0.0.1', LOCAL_PORT), timeout=10)
            local.settimeout(30)
            channel.settimeout(30)
            while not self.cancel.is_set():
                readable, _, _ = select.select([local, channel], [], [], 1)
                if local in readable:
                    content = local.recv(65536)
                    if not content:
                        break
                    channel.sendall(content)
                if channel in readable:
                    content = channel.recv(65536)
                    if not content:
                        break
                    local.sendall(content)
        except (OSError, EOFError):
            pass
        finally:
            channel.close()
            if local:
                local.close()
            self.slots.release()

    def check_site(self, transport):
        try:
            gateway_auth(self.settings['origin'])
            message = ('ready', '已连接，可以用手机扫码登录')
        except (OSError, ValueError) as exc:
            self.log('域名检查：' + str(exc))
            message = ('waiting', 'SSH 已连接；网站尚未就绪，请点击“宝塔首次设置”')
        if not self.cancel.is_set() and self.client and self.client.get_transport() is transport and transport.is_active():
            self.emit(*message)

    def stop_gateway(self):
        if self.gateway and self.gateway.poll() is None:
            try:
                request_stop(DATA)
            except (OSError, RuntimeError):
                if self.gateway.poll() is None:
                    self.gateway.terminate()
            try:
                self.gateway.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.gateway.kill()
                self.gateway.wait()
        self.gateway = None

    def run_quick(self, settings):
        executable = ASSETS / 'bin' / 'cloudflared.exe'
        if not FROZEN:
            executable = BASE / 'vendor' / 'bin' / 'cloudflared.exe'
        if not executable.is_file():
            raise RuntimeError('临时连接组件缺失，请使用完整的双模式 EXE')
        failures = 0
        while not self.cancel.is_set():
            try:
                self.emit('address', '')
                self.emit('status', '正在生成临时访问链接，无需域名或服务器…')
                self.tunnel = QuickTunnel(executable, LOCAL_PORT, DATA, lambda origin: None)
                origin = self.tunnel.start(cancel=self.cancel, on_progress=lambda text: self.emit('status', text))
                if self.cancel.is_set():
                    return
                self.emit('status', '外网隧道已建立，正在启动本机网关…')
                self.ensure_gateway(origin)
                if self.cancel.is_set():
                    return
                save_preferences(settings)
                self.emit('address', origin)
                self.emit('status', '本机网关已启动，正在检查临时网址…')
                next_check = 0
                unready_since = time.monotonic()
                while not self.cancel.is_set() and not self.tunnel.finished.is_set():
                    if self.gateway.poll() is not None:
                        raise RuntimeError('电脑网关已退出，请查看 gateway.log 后重新连接')
                    if time.monotonic() >= next_check:
                        try:
                            quick_gateway_auth(origin, self.cancel)
                            failures = 0
                            unready_since = None
                            if not self.cancel.is_set():
                                self.emit('ready', '临时入口已就绪，可以用手机扫码登录')
                        except (OSError, ValueError) as exc:
                            self.log('临时入口检查：' + str(exc))
                            if unready_since is None:
                                unready_since = time.monotonic()
                            if time.monotonic() - unready_since >= 90:
                                raise RuntimeError('隧道和本机网关已启动，但临时网址持续 90 秒无法访问：' + str(exc)) from exc
                            if not self.cancel.is_set():
                                self.emit('waiting', '临时网址尚不可用（最多等待 90 秒）：' + str(exc))
                        next_check = time.monotonic() + 15
                    self.cancel.wait(1)
                if not self.cancel.is_set():
                    raise RuntimeError('临时隧道已断开')
            except (OSError, RuntimeError) as exc:
                if not self.cancel.is_set():
                    self.log(str(exc))
                    self.emit('address', '')
                    failures += 1
                    if failures >= 3:
                        raise RuntimeError('临时连接连续 3 次失败，已停止自动重试。\n' + str(exc)) from exc
                    self.emit('status', f'临时连接第 {failures} / 3 次失败：{exc}。5 秒后重试，可点击停止')
            finally:
                self.stop_gateway()
                if self.tunnel:
                    self.tunnel.close()
                    self.tunnel = None
            self.cancel.wait(5)

    def run(self, settings, password):
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            self.prepare_gateway()
            if self.cancel.is_set():
                return
            if settings['mode'] == 'quick':
                self.run_quick(settings)
                return
            import paramiko
            self.emit('status', '正在启动电脑网关…')
            self.ensure_gateway(settings['origin'])
            self.emit('address', settings['origin'])
            while not self.cancel.is_set():
                try:
                    self.emit('status', '正在连接服务器…')
                    transport = self.connect_ssh(settings['username'], password)
                    save_preferences(settings, password)
                    self.emit('status', '服务器已连接，正在检查固定网址…')
                    self.log('SSH 已连接，远程监听 127.0.0.1:' + str(REMOTE_PORT))
                    next_check = 0
                    checker = None
                    while not self.cancel.is_set() and transport.is_active():
                        if self.gateway.poll() is not None:
                            raise RuntimeError('电脑网关已退出，请查看 gateway.log 后重新连接')
                        if time.monotonic() >= next_check and (checker is None or not checker.is_alive()):
                            checker = threading.Thread(target=self.check_site, args=(transport,), daemon=True)
                            checker.start()
                            next_check = time.monotonic() + 30
                        channel = transport.accept(timeout=1)
                        if channel:
                            if self.slots.acquire(blocking=False):
                                threading.Thread(target=self.forward, args=(channel,), daemon=True).start()
                            else:
                                channel.close()
                    if not self.cancel.is_set():
                        raise OSError('服务器连接已断开')
                except paramiko.AuthenticationException as exc:
                    raise RuntimeError('服务器登录失败，请检查 SSH 用户名和密码') from exc
                except paramiko.BadHostKeyException as exc:
                    raise RuntimeError('服务器主机密钥已变化，已停止连接；请核对服务器身份和日志') from exc
                except (OSError, EOFError, paramiko.SSHException) as exc:
                    if not self.cancel.is_set():
                        self.log('连接中断：' + str(exc))
                        self.emit('status', '连接中断，5 秒后自动重连；可点击停止')
                finally:
                    if self.client:
                        self.client.close()
                        self.client = None
                self.cancel.wait(5)
        except Exception as exc:
            if not self.cancel.is_set():
                self.log(str(exc))
                self.emit('error', str(exc))
        finally:
            self.cancel.set()
            if self.client:
                self.client.close()
            if self.tunnel:
                self.tunnel.close()
                self.tunnel = None
            self.stop_gateway()
            self.emit('done')


class Panel:
    def __init__(self, window):
        self.window = window
        self.events = queue.Queue()
        self.connection = Connection(self.events)
        self.busy = False
        self.closing = False
        self.last_error = False
        prefs = load_preferences()
        self.close_to_tray = tk.BooleanVar(value=bool(prefs.get('closeToTray', False)))
        self.update_url = tk.StringVar(value=prefs.get('updateUrl', ''))
        try:
            autorun = bool(startup_command(APP_KEY))
            if autorun and FROZEN:
                startup_command(APP_KEY, sys.executable, True)
        except OSError:
            autorun = False
        self.autorun = tk.BooleanVar(value=autorun)
        self.update_busy = False
        self.pending_minimize = '--minimized' in sys.argv
        self.tray = TrayIcon(APP_KEY, ASSETS / 'assets' / 'codex-mobile.ico', lambda kind, value=None: self.events.put((kind, value)))
        self.mode = tk.StringVar(value='fixed' if prefs.get('mode') == 'fixed' else 'quick')
        self.fixed_origin = tk.StringVar(value=prefs.get('origin', ''))
        self.server = tk.StringVar(value=prefs.get('server', ''))
        self.ssh_port = tk.StringVar(value=str(prefs.get('sshPort', 22)))
        self.quick_auto = tk.BooleanVar(value=prefs.get('autoConnect', True))
        self.url = ''
        self.address = tk.StringVar(value='连接后生成访问网址')
        window.title('Codex 手机助手')
        window.geometry('940x660')
        window.minsize(880, 660)
        window.configure(fg_color='#ffffff')
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.grid_columnconfigure(1, weight=1)
        window.grid_rowconfigure(0, weight=1)
        self.font = 'Microsoft YaHei UI'
        self.logo = ctk.CTkImage(Image.open(ASSETS / 'assets' / 'codex-mobile.png'), size=(38, 38))
        self.placeholder_logo = ctk.CTkImage(Image.open(ASSETS / 'assets' / 'codex-mobile.png'), size=(56, 56))

        sidebar = ctk.CTkFrame(window, width=202, corner_radius=0, fg_color='#f7f7f8')
        sidebar.grid(row=0, column=0, sticky='nsew')
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(8, weight=1)
        brand = ctk.CTkFrame(sidebar, fg_color='transparent')
        brand.grid(row=0, column=0, padx=19, pady=(26, 32), sticky='ew')
        ctk.CTkLabel(brand, text='', image=self.logo, width=38).pack(side='left', padx=(0, 10))
        ctk.CTkLabel(brand, text='Codex\n手机助手', justify='left', anchor='w', font=(self.font, 16, 'bold'), text_color='#202020').pack(side='left')
        self.mode_buttons = {
            'quick': self.nav_button(sidebar, '临时访问 · 免服务器', lambda: self.select_mode('quick'), 1),
            'fixed': self.nav_button(sidebar, '固定域名 · 自有服务器', lambda: self.select_mode('fixed'), 2),
        }
        self.nav_button(sidebar, '收起到托盘', self.hide_to_tray, 3)
        self.nav_button(sidebar, '手机登录密码', self.show_password, 4)
        self.setup_button = self.nav_button(sidebar, '宝塔首次设置', self.setup, 5)
        self.nav_button(sidebar, '查看日志', self.logs, 6)
        self.nav_button(sidebar, '应用设置 / 检查更新', self.app_settings, 7)
        self.sidebar_status = ctk.CTkLabel(sidebar, text='●  尚未连接', anchor='w', font=(self.font, 12), text_color='#777777')
        self.sidebar_status.grid(row=9, column=0, sticky='ew', padx=24, pady=(12, 5))
        ctk.CTkLabel(sidebar, text='双模式版  ·  ' + VERSION, anchor='w', font=(self.font, 11), text_color='#9a9a9a').grid(row=10, column=0, sticky='ew', padx=24, pady=(0, 24))

        main = ctk.CTkFrame(window, fg_color='transparent', corner_radius=0)
        main.grid(row=0, column=1, sticky='nsew', padx=34, pady=(34, 26))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(5, weight=1)
        ctk.CTkLabel(main, text='把 Codex 带到手机上', anchor='w', font=(self.font, 27, 'bold'), text_color='#171717').grid(row=0, column=0, sticky='ew')
        self.description = tk.StringVar(value='选择一种访问方式，随时继续电脑上的对话。')
        ctk.CTkLabel(main, textvariable=self.description, anchor='w', font=(self.font, 13), text_color='#777777').grid(row=1, column=0, sticky='ew', pady=(3, 14))
        self.status = tk.StringVar(value='选择一种模式，然后点击连接')
        status_box = ctk.CTkFrame(main, fg_color='#f7f7f8', corner_radius=12)
        status_box.grid(row=2, column=0, sticky='ew', pady=(0, 18))
        ctk.CTkLabel(status_box, textvariable=self.status, font=(self.font, 12), text_color='#606060', anchor='w', justify='left', wraplength=570).pack(fill='x', padx=15, pady=8)

        content = ctk.CTkFrame(main, fg_color='transparent')
        content.grid(row=3, column=0, sticky='new')
        content.grid_columnconfigure(1, weight=1)
        qr_card = ctk.CTkFrame(content, fg_color='#f7f7f8', corner_radius=18, width=220)
        qr_card.grid(row=0, column=0, sticky='n', padx=(0, 24))
        ctk.CTkLabel(qr_card, text='手机扫码连接', font=(self.font, 13, 'bold'), text_color='#353535').pack(pady=(18, 3))
        self.qr_label = ctk.CTkLabel(qr_card, text='', image=self.placeholder_logo, width=200, height=190, fg_color='transparent')
        self.qr_label.pack(padx=10)
        self.qr_caption = ctk.CTkLabel(qr_card, text='连接成功后显示二维码', font=(self.font, 11), text_color='#8a8a8a')
        self.qr_caption.pack(pady=(0, 12))
        ctk.CTkLabel(qr_card, textvariable=self.address, wraplength=190, font=(self.font, 11), text_color='#565656').pack()
        self.copy_button = ctk.CTkButton(qr_card, text='复制网址', command=lambda: self.copy(self.url), state='disabled', width=100, height=30, fg_color='transparent', hover_color='#e8e8e8', text_color='#222222', font=(self.font, 12))
        self.copy_button.pack(pady=(5, 14))

        right = ctk.CTkFrame(content, fg_color='transparent')
        right.grid(row=0, column=1, sticky='new')
        right.grid_columnconfigure(0, weight=1)
        login = self.fixed_panel = ctk.CTkFrame(right, fg_color='transparent')
        login.grid(row=0, column=0, sticky='new')
        login.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(login, text='连接服务器', anchor='w', font=(self.font, 16, 'bold'), text_color='#242424').grid(row=0, column=0, sticky='ew')
        self.settings_button = ctk.CTkButton(login, text='设置', command=self.configure_server, width=52, height=28, corner_radius=8, fg_color='#f3f3f3', hover_color='#e5e5e5', text_color='#4d4d4d', font=(self.font, 11))
        self.settings_button.place(relx=1, y=0, anchor='ne')
        self.server_label = ctk.CTkLabel(login, text='', anchor='w', font=(self.font, 11), text_color='#909090')
        self.server_label.grid(row=1, column=0, sticky='ew', pady=(0, 16))
        ctk.CTkLabel(login, text='用户名', anchor='w', font=(self.font, 12), text_color='#4d4d4d').grid(row=2, column=0, sticky='ew', pady=(0, 5))
        self.username = tk.StringVar(value=prefs.get('username', 'root'))
        self.user_entry = ctk.CTkEntry(login, textvariable=self.username, height=42, corner_radius=11, fg_color='#f9f9f9', border_color='#e3e3e3', border_width=1, text_color='#252525', font=(self.font, 13))
        self.user_entry.grid(row=3, column=0, sticky='ew', pady=(0, 14))
        ctk.CTkLabel(login, text='服务器密码', anchor='w', font=(self.font, 12), text_color='#4d4d4d').grid(row=4, column=0, sticky='ew', pady=(0, 5))
        self.password = tk.StringVar(value=prefs.get('password', ''))
        self.password_entry = ctk.CTkEntry(login, textvariable=self.password, show='●', height=42, corner_radius=11, fg_color='#f9f9f9', border_color='#e3e3e3', border_width=1, text_color='#252525', font=(self.font, 13))
        self.password_entry.grid(row=5, column=0, sticky='ew')
        ctk.CTkLabel(login, text='填写 SSH 密码，与手机登录密码不同。', anchor='w', font=(self.font, 10), text_color='#999999').grid(row=6, column=0, sticky='ew', pady=(4, 13))
        self.remember = tk.BooleanVar(value=prefs.get('remember', True))
        self.remember_button = ctk.CTkCheckBox(login, text='记住服务器密码，下次自动连接', variable=self.remember, checkbox_width=18, checkbox_height=18, corner_radius=5, border_width=1, fg_color='#242424', hover_color='#424242', border_color='#a0a0a0', text_color='#666666', font=(self.font, 11))
        self.remember_button.grid(row=7, column=0, sticky='w')
        self.quick_panel = ctk.CTkFrame(right, fg_color='transparent')
        self.quick_panel.grid(row=0, column=0, sticky='new')
        ctk.CTkLabel(self.quick_panel, text='无需域名，也无需服务器', anchor='w', font=(self.font, 16, 'bold'), text_color='#242424').pack(fill='x', pady=(0, 16))
        for title, detail in (
            ('01  一键生成链接', '通过 Cloudflare 生成临时 HTTPS 网址。'),
            ('02  手机扫码登录', '使用独立的手机登录密码访问你的聊天。'),
            ('03  电脑保持在线', '入口重建后地址会变化，需要重新扫码。'),
        ):
            ctk.CTkLabel(self.quick_panel, text=title, anchor='w', font=(self.font, 13, 'bold'), text_color='#444444').pack(fill='x')
            ctk.CTkLabel(self.quick_panel, text=detail, anchor='w', font=(self.font, 11), text_color='#888888', wraplength=365, justify='left').pack(fill='x', pady=(0, 12))
        self.quick_auto_button = ctk.CTkCheckBox(self.quick_panel, text='下次打开自动连接', variable=self.quick_auto, checkbox_width=18, checkbox_height=18, corner_radius=5, border_width=1, fg_color='#242424', hover_color='#424242', border_color='#a0a0a0', text_color='#666666', font=(self.font, 11))
        self.quick_auto_button.pack(anchor='w', pady=(5, 0))
        actions = ctk.CTkFrame(right, fg_color='transparent')
        actions.grid(row=1, column=0, sticky='ew', pady=(19, 0))
        actions.grid_columnconfigure(0, weight=1)
        self.connect_button = ctk.CTkButton(actions, text='连接手机助手  →', command=self.start, height=42, corner_radius=12, fg_color='#171717', hover_color='#333333', text_color='#ffffff', text_color_disabled='#a0a0a0', font=(self.font, 13, 'bold'))
        self.connect_button.grid(row=0, column=0, sticky='ew')
        self.stop_button = ctk.CTkButton(actions, text='停止', command=self.stop, state='disabled', width=64, height=42, corner_radius=12, fg_color='#f2f2f2', hover_color='#e5e5e5', text_color='#444444', font=(self.font, 12))
        self.stop_button.grid(row=0, column=1, padx=(8, 0))

        links = ctk.CTkFrame(main, fg_color='transparent')
        links.grid(row=4, column=0, sticky='ew', pady=(15, 0))
        self.open_button = ctk.CTkButton(links, text='打开手机网址 ↗', command=lambda: webbrowser.open(self.url) if self.url else None, state='disabled', width=130, height=32, fg_color='transparent', hover_color='#f5f5f5', text_color='#464646', font=(self.font, 12))
        self.open_button.pack(side='left')
        self.forget_button = ctk.CTkButton(links, text='清除记住的密码', command=self.forget, width=120, height=32, fg_color='transparent', hover_color='#f5f5f5', text_color='#8b8b8b', font=(self.font, 11))
        self.forget_button.pack(side='right')
        ctk.CTkLabel(main, text='请保持电脑和 Codex 在线。可收起到托盘；关闭行为可在应用设置中调整。', anchor='w', font=(self.font, 10), text_color='#9b9b9b').grid(row=6, column=0, sticky='ew', pady=(10, 0))
        self.select_mode(self.mode.get())
        self.tray.start()
        window.after(200, self.drain)
        if prefs.get('autoConnect') and (self.mode.get() == 'quick' or prefs.get('password')):
            window.after(350, self.start)

    def select_mode(self, mode):
        if self.busy:
            return
        self.mode.set(mode)
        for key, button in self.mode_buttons.items():
            button.configure(fg_color='#eaeaec' if key == mode else 'transparent', text_color='#272727' if key == mode else '#656565')
        if mode == 'quick':
            self.fixed_panel.grid_remove()
            self.quick_panel.grid()
            self.setup_button.grid_remove()
            self.forget_button.pack_forget()
            self.description.set('没有域名或服务器，也能把 Codex 带到手机上。')
            self.status.set('临时访问：点击生成链接即可，无需填写 SSH 密码')
            self.connect_button.configure(text='生成访问链接  →')
        else:
            self.quick_panel.grid_remove()
            self.fixed_panel.grid()
            self.setup_button.grid()
            self.forget_button.pack(side='right')
            self.description.set('使用自己的域名和服务器，保留长期访问地址。')
            self.status.set('固定域名：先完成服务器设置，再输入 SSH 密码连接')
            self.connect_button.configure(text='连接手机助手  →')
        self.server_label.configure(text=(self.server.get() + '  ·  SSH ' + self.ssh_port.get()) if self.server.get() else '点击“设置”填写自己的域名和服务器')
        self.set_address('')
        self.qr_placeholder('连接成功后显示二维码')
        self.sidebar_status.configure(text='●  尚未连接', text_color='#777777')

    def set_address(self, url):
        self.url = url
        self.address.set(urlsplit(url).netloc if url else ('连接后生成临时网址' if self.mode.get() == 'quick' else (self.fixed_origin.get() or '尚未设置固定域名')))
        for button in (self.copy_button, self.open_button):
            button.configure(state='normal' if url else 'disabled')

    def settings(self, validate=True):
        mode = self.mode.get()
        origin = self.fixed_origin.get().strip()
        server = self.server.get().strip()
        try:
            port = int(self.ssh_port.get())
        except ValueError:
            port = 0
        if mode == 'fixed' and validate:
            origin = normalize_origin(origin)
            if not server or any(char.isspace() for char in server) or any(char in server for char in '/\\@'):
                raise ValueError('请在“设置”中填写 SSH 服务器 IP 或主机名')
            if not 1 <= port <= 65535:
                raise ValueError('SSH 端口必须为 1–65535')
        return {'mode': mode, 'origin': origin, 'server': server, 'sshPort': port or 22,
                'username': self.username.get().strip(), 'remember': self.remember.get(),
                'autoConnect': self.remember.get() if mode == 'fixed' else self.quick_auto.get()}

    def app_settings(self):
        dialog = self.dialog('应用设置', 560, 410)
        dialog.grab_set()
        close_tray = tk.BooleanVar(value=self.close_to_tray.get())
        autorun = tk.BooleanVar(value=self.autorun.get())
        update_url = tk.StringVar(value=self.update_url.get())
        ctk.CTkLabel(dialog, text='使用偏好', font=(self.font, 21, 'bold')).pack(anchor='w', padx=28, pady=(24, 18))
        ctk.CTkCheckBox(dialog, text='关闭窗口时收起到托盘，保持连接', variable=close_tray).pack(anchor='w', padx=28, pady=8)
        ctk.CTkCheckBox(dialog, text='登录 Windows 时启动并收起到托盘', variable=autorun).pack(anchor='w', padx=28, pady=8)
        ctk.CTkLabel(dialog, text='更新信息地址（HTTPS JSON，可留空）', anchor='w', font=(self.font, 12)).pack(fill='x', padx=28, pady=(20, 4))
        ctk.CTkEntry(dialog, textvariable=update_url, height=38, placeholder_text='https://example.com/latest.json').pack(fill='x', padx=28)
        ctk.CTkLabel(dialog, text='当前版本 ' + VERSION + ' · 检查更新不会自动下载安装', anchor='w', font=(self.font, 11), text_color='#888888').pack(fill='x', padx=28, pady=8)
        buttons = ctk.CTkFrame(dialog, fg_color='transparent')
        buttons.pack(fill='x', padx=28, pady=18)
        def apply():
            try:
                address = update_url.get().strip()
                if address:
                    https_url(address)
                if autorun.get() and not FROZEN:
                    raise ValueError('开机启动请在打包后的 EXE 中设置')
                startup_command(APP_KEY, sys.executable, autorun.get())
                self.autorun.set(autorun.get())
                self.close_to_tray.set(close_tray.get())
                self.update_url.set(address)
                save_preferences({**self.settings(validate=False), 'closeToTray': self.close_to_tray.get(), 'updateUrl': address})
            except (OSError, ValueError) as exc:
                messagebox.showerror('设置未保存', str(exc), parent=dialog)
                return
            dialog.destroy()
        ctk.CTkButton(buttons, text='保存设置', command=apply, fg_color='#222222', hover_color='#444444').pack(side='right')
        ctk.CTkButton(buttons, text='检查更新', command=lambda: self.check_for_updates(update_url.get()), fg_color='#f0f0f0', hover_color='#e3e3e3', text_color='#222222').pack(side='left')

    def check_for_updates(self, url):
        if self.update_busy:
            return
        self.update_busy = True
        self.status.set('正在检查软件更新…')
        def check():
            try:
                result = check_update(url)
            except Exception as exc:
                result = {'status': 'error', 'message': '无法检查更新，请确认地址、网络和更新信息格式。\n' + str(exc)}
            self.events.put(('update-result', result))
        threading.Thread(target=check, daemon=True).start()

    def configure_server(self):
        if self.busy:
            return
        dialog = self.dialog('固定域名设置', 500, 410)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text='使用你自己的服务器', font=(self.font, 20, 'bold'), text_color='#222222').pack(pady=(20, 12))
        fields = []
        for label, value, placeholder in (
            ('HTTPS 域名', self.fixed_origin.get(), 'https://codex.example.com'),
            ('SSH 服务器', self.server.get(), '服务器 IP 或主机名'),
            ('SSH 端口', self.ssh_port.get(), '22'),
        ):
            ctk.CTkLabel(dialog, text=label, anchor='w', font=(self.font, 12), text_color='#555555').pack(fill='x', padx=28)
            entry = ctk.CTkEntry(dialog, height=38, corner_radius=10, fg_color='#f8f8f8', border_color='#e3e3e3', border_width=1, text_color='#222222', placeholder_text=placeholder)
            entry.insert(0, value)
            entry.pack(fill='x', padx=28, pady=(0, 10))
            fields.append(entry)

        def apply():
            if self.busy:
                return
            try:
                origin = normalize_origin(fields[0].get())
                server, port = fields[1].get().strip(), int(fields[2].get())
                if not server or any(c.isspace() for c in server) or any(c in server for c in '/\\@') or not 1 <= port <= 65535:
                    raise ValueError('请填写有效的 SSH 主机名和 1–65535 端口')
            except ValueError as exc:
                messagebox.showerror('设置未完成', str(exc), parent=dialog)
                return
            if server != self.server.get() or str(port) != self.ssh_port.get():
                self.password.set('')
            self.fixed_origin.set(origin)
            self.server.set(server)
            self.ssh_port.set(str(port))
            self.select_mode('fixed')
            dialog.destroy()

        ctk.CTkButton(dialog, text='应用设置', command=apply, height=38, corner_radius=10, fg_color='#171717', hover_color='#333333').pack(pady=(8, 0))

    def nav_button(self, parent, text, command, row, selected=False):
        button = ctk.CTkButton(parent, text=text, command=command, anchor='w', height=42, corner_radius=10, fg_color='#eaeaec' if selected else 'transparent', hover_color='#eaeaec', text_color='#272727' if selected else '#656565', font=(self.font, 13, 'bold' if selected else 'normal'))
        button.grid(row=row, column=0, sticky='ew', padx=13, pady=3)
        return button

    def qr_placeholder(self, text):
        self.qr_label.configure(image=self.placeholder_logo)
        self.qr_caption.configure(text=text)

    def dialog(self, title, width, height):
        dialog = ctk.CTkToplevel(self.window)
        dialog.title(title)
        dialog.geometry(f'{width}x{height}')
        dialog.configure(fg_color='#ffffff')
        dialog.transient(self.window)
        dialog.after(250, lambda: dialog.iconbitmap(str(ASSETS / 'assets' / 'codex-mobile.ico')) if dialog.winfo_exists() else None)
        return dialog

    def start(self):
        if self.busy:
            return
        try:
            settings = self.settings()
        except ValueError as exc:
            messagebox.showinfo('请先配置服务器', str(exc), parent=self.window)
            self.configure_server()
            return
        password = self.password.get() if settings['mode'] == 'fixed' else ''
        if settings['mode'] == 'fixed' and (not settings['username'] or not password):
            messagebox.showinfo('首次连接', '请填写服务器 SSH 用户名和密码。\n这里不是手机网页的登录密码。', parent=self.window)
            return
        self.busy = True
        self.last_error = False
        self.set_address('')
        for widget in (self.connect_button, self.user_entry, self.password_entry, self.remember_button, self.forget_button, self.settings_button, self.quick_auto_button, *self.mode_buttons.values()):
            widget.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.connection.start(settings, password)

    def stop(self):
        if self.busy:
            self.status.set('正在停止网关和服务器连接…')
            self.stop_button.configure(state='disabled')
            self.connection.stop()

    def restore(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def hide_to_tray(self):
        if self.tray.ready.is_set():
            self.window.withdraw()
        else:
            messagebox.showinfo('托盘暂不可用', '托盘未就绪，窗口将保持可见。', parent=self.window)

    def close(self):
        if self.close_to_tray.get() and self.tray.ready.is_set() and not self.closing:
            self.hide_to_tray()
            return
        self.exit()

    def destroy(self):
        self.tray.stop()
        self.window.destroy()

    def exit(self):
        if not self.busy:
            self.destroy()
            return
        self.closing = True
        self.stop()

    def drain(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'tray-show':
                self.restore()
            elif kind == 'tray-connect':
                self.restore()
                self.start()
            elif kind == 'tray-stop':
                self.stop()
            elif kind == 'tray-exit':
                self.exit()
                if not self.busy:
                    return
            elif kind == 'tray-ready':
                if self.pending_minimize:
                    self.pending_minimize = False
                    self.hide_to_tray()
            elif kind == 'tray-error':
                self.restore()
                self.status.set('托盘不可用，窗口保持显示；连接功能不受影响。')
            elif kind == 'update-result':
                self.update_busy = False
                self.status.set(value['message'])
                self.restore()
                if value.get('url'):
                    detail = value['message'] + '\n\n' + value.get('notes', '') + '\n\n打开下载页面？'
                    if messagebox.askyesno('软件更新', detail, parent=self.window):
                        webbrowser.open(value['url'])
                else:
                    messagebox.showinfo('软件更新', value['message'], parent=self.window)
            elif kind == 'address'  and not self.closing and not self.connection.cancel.is_set():
                self.set_address(value)
            elif kind == 'host':
                value['accepted'] = not self.closing and not self.connection.cancel.is_set() and messagebox.askyesno(
                    '首次连接：确认服务器身份', '服务器：' + value['hostname'] + '\n主机密钥：' + value['type'] + '\n\n' + value['fingerprint'] +
                    '\n\n请与服务器的 SSH 主机指纹核对。确认可信后，是否保存并继续？', parent=self.window)
                value['event'].set()
            elif kind in ('status', 'waiting', 'ready') and not self.closing and not self.connection.cancel.is_set():
                self.status.set(value)
                if kind == 'ready':
                    self.draw_qr()
                    self.sidebar_status.configure(text='●  已连接', text_color='#238765')
                else:
                    self.qr_placeholder('正在连接…' if kind == 'status' else ('等待网络恢复' if self.mode.get() == 'quick' else '请完成宝塔站点设置'))
                    self.sidebar_status.configure(text='●  连接中' if kind == 'status' else '●  等待入口就绪', text_color='#777777')
            elif kind == 'error' and not self.closing:
                self.last_error = True
                self.status.set(value)
                self.qr_placeholder('连接未完成')
                self.sidebar_status.configure(text='●  连接未完成', text_color='#b44e4e')
                messagebox.showerror('连接提示', value, parent=self.window)
            elif kind == 'done':
                self.busy = False
                if self.closing:
                    self.destroy()
                    return
                for widget in (self.connect_button, self.user_entry, self.password_entry, self.remember_button, self.forget_button, self.settings_button, self.quick_auto_button, *self.mode_buttons.values()):
                    widget.configure(state='normal')
                self.stop_button.configure(state='disabled')
                self.set_address('')
                self.qr_placeholder('尚未连接')
                self.sidebar_status.configure(text='●  尚未连接', text_color='#777777')
                if not self.last_error:
                    self.status.set('连接已停止，点击连接即可重新使用')
        self.window.after(200, self.drain)

    def draw_qr(self):
        if not self.url:
            return
        qr = QrCode.encode_text(self.url, QrCode.Ecc.MEDIUM)
        size = qr.get_size()
        scale = 10
        offset = 4 * scale
        image = Image.new('RGB', ((size + 8) * scale, (size + 8) * scale), 'white')
        draw = ImageDraw.Draw(image)
        for y in range(size):
            for x in range(size):
                if qr.get_module(x, y):
                    draw.rectangle((offset+x*scale, offset+y*scale, offset+(x+1)*scale-1, offset+(y+1)*scale-1), fill='black')
        self.qr_image = ctk.CTkImage(image, size=(190, 190))
        self.qr_label.configure(image=self.qr_image)
        self.qr_caption.configure(text='用手机相机扫码，继续对话')

    def copy(self, text):
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.window.update_idletasks()

    def show_password(self):
        try:
            text = (DATA / '首次登录.txt').read_text(encoding='utf-8')
        except OSError:
            messagebox.showinfo('手机登录密码', '首次连接后自动生成手机登录密码。\n若你已修改过网关密码，请使用自己设置的密码。', parent=self.window)
            return
        dialog = self.dialog('手机网页登录信息', 480, 220)
        account = [line for line in text.splitlines() if line.startswith(('账号：', '密码：'))]
        ctk.CTkLabel(dialog, text='手机网页登录', font=(self.font, 20, 'bold'), text_color='#222222').pack(pady=(23, 12))
        ctk.CTkLabel(dialog, text='\n'.join(account), font=(self.font, 13), text_color='#555555').pack(padx=18, pady=8)
        secret = next((line.split('：', 1)[1] for line in account if line.startswith('密码：')), '')
        ctk.CTkButton(dialog, text='复制手机登录密码', command=lambda: self.copy(secret), fg_color='#171717', hover_color='#333333', corner_radius=10, height=36).pack(pady=14)

    def forget(self):
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            settings = self.settings(validate=False)
            settings['remember'] = False
            if settings['mode'] == 'fixed':
                settings['autoConnect'] = False
            save_preferences(settings, clear_password=True)
            self.password.set('')
            self.remember.set(False)
            self.status.set('已清除本机记住的 SSH 密码')
        except OSError as exc:
            messagebox.showerror('无法保存', str(exc), parent=self.window)

    def logs(self):
        DATA.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DATA))

    def setup(self):
        dialog = self.dialog('宝塔首次设置（只需一次）', 600, 500)
        domain = urlsplit(self.fixed_origin.get()).netloc or '你配置的子域名'
        text = ('1. 宝塔 → 网站 → 添加站点\n   域名：' + domain + '，选择纯静态。\n\n'
                '2. 新站点 → SSL\n   申请并部署 Let’s Encrypt 证书，启用强制 HTTPS 和续期。\n\n'
                '3. 新站点 → 反向代理 → 添加\n   目标 URL：http://127.0.0.1:18787\n   发送域名：' + domain + '；代理目录：/；关闭缓存。\n\n'
                '4. 返回本窗口连接服务器，等待显示二维码。\n   连接前网站显示 502，通常是因为电脑隧道尚未启动。\n\n'
                '单独新增子域名站点，保留原网站。\n'
                '上传限制建议 16m，代理超时 300s。详细配置见随附说明。')
        ctk.CTkLabel(dialog, text='只需设置一次', font=(self.font, 22, 'bold'), text_color='#222222').pack(pady=(22, 8))
        ctk.CTkLabel(dialog, text=text, justify='left', font=(self.font, 12), text_color='#606060', wraplength=550).pack(padx=24, pady=14)
        ctk.CTkButton(dialog, text='复制反向代理目标', command=lambda: self.copy('http://127.0.0.1:18787'), fg_color='#171717', hover_color='#333333', corner_radius=10, height=36).pack(pady=4)


def main():
    if '--gateway-worker' in sys.argv:
        return gateway_worker()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = 'Local\\CodexMobilePanel-' + APP_KEY
    mutex = kernel.CreateMutexW(None, False, name)
    exists = ctypes.get_last_error() == 183
    shell = ctypes.WinDLL('shell32')
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    shell.SetCurrentProcessExplicitAppUserModelID('CodexMobile.DualMode')
    ctk.set_appearance_mode('light')
    ctk.set_default_color_theme('blue')
    window = ctk.CTk()
    window.iconbitmap(default=str(ASSETS / 'assets' / 'codex-mobile.ico'))
    if exists and restore_existing(APP_KEY):
        window.destroy()
        kernel.CloseHandle(mutex)
        return 0
    if not mutex or exists:
        window.withdraw()
        messagebox.showinfo('Codex 手机助手', '入口窗口已经打开，请从任务栏切换到它；打开新版前请关闭旧入口。' if exists else '无法建立启动锁，请重新打开。')
        window.destroy()
    else:
        try:
            Panel(window)
            window.mainloop()
        finally:
            kernel.CloseHandle(mutex)
        return 0
    if mutex:
        kernel.CloseHandle(mutex)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
