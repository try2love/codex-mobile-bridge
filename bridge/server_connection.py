"""SSH setup and supervised loopback forwarding. Host keys are explicitly pinned."""
import base64
import hashlib
import os
import select
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .notifications import read_json, write_json


def library():
    try:
        import paramiko
        return paramiko
    except ImportError as exc:
        raise ValueError('缺少服务器连接组件，请使用完整桌面 App 或安装 requirements-desktop.txt。') from exc


def endpoint(entry):
    result = {'host': entry.get('sshHost', ''), 'port': entry.get('sshPort', 22),
              'user': entry.get('sshUser', ''), 'key': entry.get('sshKeyPath', '')}
    if entry.get('sshAuth', 'config') == 'config':
        target = entry['sshTarget']
        user, _, alias = target.rpartition('@')
        config = Path.home()/'.ssh/config'
        options = library().SSHConfig.from_path(str(config)).lookup(alias) if config.exists() else {}
        if options.get('proxycommand') or options.get('proxyjump'):
            raise ValueError('此 SSH 别名使用跳板连接，请导出部署包，或改为可直连的服务器地址。')
        result = {'host': options.get('hostname', alias), 'port': int(options.get('port', 22)),
                  'user': user or options.get('user', ''), 'key': next(iter(options.get('identityfile', [])), '')}
        if not result['user']:
            import getpass
            result['user'] = getpass.getuser()
    return result


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def identity(entry):
    ep = endpoint(entry)
    return f'{ep["host"]}:{ep["port"]}'


def host_probe(entry, data_dir):
    ep = endpoint(entry)
    with socket.create_connection((ep['host'], ep['port']), timeout=10) as sock:
        transport = library().Transport(sock)
        try:
            transport.start_client(timeout=10)
            key = transport.get_remote_server_key()
            value = {'host': ep['host'], 'port': ep['port'], 'type': key.get_name(),
                     'key': key.get_base64(), 'fingerprint': fingerprint(key)}
        finally:
            transport.close()
    known = read_json(Path(data_dir)/'server-host-keys.json', {}).get(identity(entry))
    value['trusted'] = bool(known and known['key'] == value['key'] and known['type'] == value['type'])
    value['changed'] = bool(known and not value['trusted'])
    return value


def trust(entry, data_dir, expected):
    current = host_probe(entry, data_dir)
    if current['fingerprint'] != expected:
        raise ValueError('服务器主机指纹已变化，请重新检查；尚未保存信任。')
    known = read_json(Path(data_dir)/'server-host-keys.json', {})
    known[identity(entry)] = {key: current[key] for key in ('type', 'key', 'fingerprint')}
    write_json(Path(data_dir)/'server-host-keys.json', known)
    return {'message': '主机指纹已保存，可以检查 SSH 连接。'}


def credentials(entry, data_dir, temporary=None):
    if temporary is not None:
        return temporary
    if entry.get('sshAuth', 'config') in ('password', 'key') or entry['accessMode'] == 'cloudflare':
        from .connection_secrets import read
        return read(data_dir, entry['id'])
    return {}


def managed(entry, data_dir):
    if entry.get('sshAuth', 'config') != 'config':
        return True
    known = read_json(Path(data_dir)/'server-host-keys.json', {})
    if not known:
        return False
    try:
        return identity(entry) in known
    except ValueError:
        return False


def connect(entry, data_dir, secrets=None):
    api = library()
    ep = endpoint(entry)
    known = read_json(Path(data_dir)/'server-host-keys.json', {}).get(identity(entry))
    if not known:
        raise ValueError('请先检查并确认服务器主机指纹。')
    client = api.SSHClient()
    host = ep['host'] if ep['port'] == 22 else f'[{ep["host"]}]:{ep["port"]}'
    key = api.PKey.from_type_string(known['type'], base64.b64decode(known['key']))
    client.get_host_keys().add(host, known['type'], key)
    secret = credentials(entry, data_dir, secrets)
    mode = entry.get('sshAuth', 'config')
    if mode == 'password' and not secret.get('password'):
        raise ValueError('请填写 SSH 密码并保存；仅本次使用的密码需在重新打开 App 后再次输入。')
    try:
        client.connect(ep['host'], port=ep['port'], username=ep['user'],
                       password=secret.get('password') if mode == 'password' else None,
                       key_filename=str(Path(ep['key']).expanduser()) if ep['key'] else None,
                       passphrase=secret.get('passphrase'), allow_agent=mode in ('agent', 'config'),
                       look_for_keys=mode == 'config', timeout=10, banner_timeout=10, auth_timeout=15)
        client.get_transport().set_keepalive(15)
        return client
    except api.BadHostKeyException as exc:
        client.close()
        raise ValueError('服务器主机指纹与已保存记录不符，已拒绝连接。') from exc
    except api.AuthenticationException as exc:
        client.close()
        raise ValueError('SSH 认证失败，请检查用户名、密码或私钥。') from exc
    except Exception as exc:
        client.close()
        raise ValueError('SSH 连接失败，请检查服务器地址、端口、密钥和网络。') from exc


def inspect(entry, data_dir, secrets=None):
    """Authenticate only: never open a remote shell or change server settings."""
    client = connect(entry, data_dir, secrets)
    try:
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(
                urlsplit(entry['publicUrl']).hostname, 443, type=socket.SOCK_STREAM)})
        except OSError:
            addresses = []
        return {'message': 'SSH 登录通过；尚未验证转发权限和 HTTPS 入口。请完成手动配置，再启动连接并检测固定入口。',
                'dnsAddresses': addresses, 'dnsReady': bool(addresses),
                'upstream': 'http://127.0.0.1:'+str(entry['sshRemotePort'])}
    finally:
        client.close()


class ManagedForward:
    def __init__(self, entry, local_port, data_dir, secrets=None):
        self.entry, self.local_port, self.data_dir, self.secrets = entry, local_port, Path(data_dir), secrets
        self.stopped = threading.Event()
        self.client = None
        self.thread = None
        self.channels = set()
        self.lock = threading.Lock()

    def status(self, state, message):
        write_json(self.data_dir/('ssh-status-'+self.entry['id']+'.json'),
                   {'pid': os.getpid(), 'state': state, 'message': message})

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _relay(self, channel):
        sock = None
        try:
            sock = socket.create_connection(('127.0.0.1', self.local_port), timeout=10)
            sock.settimeout(30)
            channel.settimeout(30)
            while not self.stopped.is_set():
                ready, _, _ = select.select([sock, channel], [], [], 1)
                if sock in ready:
                    data = sock.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in ready:
                    data = channel.recv(65536)
                    if not data:
                        break
                    sock.sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            if sock:
                sock.close()
            channel.close()
            with self.lock:
                self.channels.discard(channel)

    def _run(self):
        delay = 2
        while not self.stopped.is_set():
            try:
                self.status('connecting', '正在连接自有服务器…')
                self.client = connect(self.entry, self.data_dir, self.secrets)
                if self.stopped.is_set():
                    break
                transport = self.client.get_transport()
                transport.request_port_forward('127.0.0.1', self.entry['sshRemotePort'])
                self.status('connected', 'SSH 转发已建立；请检测固定 HTTPS 入口')
                delay = 2
                while not self.stopped.is_set() and transport.is_active():
                    channel = transport.accept(1)
                    if channel:
                        with self.lock:
                            if len(self.channels) >= 128:
                                channel.close()
                                continue
                            self.channels.add(channel)
                        threading.Thread(target=self._relay, args=(channel,), daemon=True).start()
            except Exception:
                self.status('retrying', 'SSH 连接失败或已中断，正在重试；请检查认证、主机指纹及回环端口。')
            finally:
                if self.client:
                    self.client.close()
            if self.stopped.wait(delay):
                break
            delay = min(30, delay*2)
        self.status('stopped', 'SSH 转发已停止')

    def close(self):
        self.stopped.set()
        if self.client:
            self.client.close()
        with self.lock:
            for channel in list(self.channels):
                channel.close()
        if self.thread:
            self.thread.join(timeout=5)


def diagnostic_prompt(entry, language=None):
    """Prepare text for the user to paste into an Agent on the server; never run it."""
    import json
    reference = json.dumps({'httpsUrl': entry['publicUrl'], 'sshUser': entry.get('sshUser') or '(from SSH config)',
                            'sshPort': entry['sshPort'] if entry.get('sshAuth') != 'config' else '(from SSH config)',
                            'loopbackUpstream': 'http://127.0.0.1:'+str(entry['sshRemotePort'])},
                           ensure_ascii=False, indent=2)
    if language == 'en':
        return '''Please inspect this server for a Codex Mobile Bridge SSH reverse-tunnel entry using read-only checks.
Reference configuration (not shell commands):
''' + reference + '''
Check the current user and OS; existing Caddy/Nginx/other HTTPS proxies and their service status; listeners on ports 80, 443 and the configured loopback port; domain resolution; and the configured user's remote forwarding permissions and loopback binding rules, where readable. Distinguish observed facts, missing services, conflicts and checks you could not perform. A local check does not prove public reachability or phone access.
Do not run sudo, request administrator credentials, install software, edit files, change permissions, firewall or DNS rules, reload services, start containers, or create an SSH tunnel. Do not read or output passwords, tokens, private keys or unrelated configuration. If access is insufficient, report that check as unverified without escalating privileges.
Return: a findings table; whether an existing HTTPS proxy can be reused; missing prerequisites or conflicts; and a minimal numbered list of actions for the user to perform manually. Explain administrative actions, but do not execute them. Preserve all existing sites and services. The computer will run the gateway and initiate SSH; this server only hosts the HTTPS proxy and loopback listener.
'''
    return '''请在这台服务器上，为 Codex Mobile Bridge 的 SSH 反向隧道入口做只读现状检查。
参考配置（不是待执行的 Shell 命令）：
''' + reference + '''
请检查：当前用户和系统；是否已有 Caddy、Nginx 或其他 HTTPS 反向代理及其运行状态；80、443 和上述回环端口的占用；域名解析；在可读范围内核对目标普通用户的远程端口转发权限及回环绑定规则。区分已观察到的事实、缺少的服务、配置冲突和无法检查的项目。本机检查不能证明公网或手机访问正常。
只读检查，不运行 sudo，不索取管理员凭据，不安装软件、修改文件/权限/防火墙/DNS，不重载服务、不启动容器或隧道。不读取或输出密码、Token、SSH 私钥及无关配置。权限不足时标注“未验证”，不要提权。
请输出：现状表格；能否复用已有 HTTPS 服务；缺少的条件或冲突；用户需要手动完成的最小步骤清单。涉及管理员权限的步骤只解释，不执行。保留已有网站与服务。电脑负责运行网关并主动建立 SSH，此服务器仅提供 HTTPS 代理和回环入口。
'''
