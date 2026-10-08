"""Local desktop controller. No management API is exposed to the network."""
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from .auth import Auth, password_record, session_hours
from .lifecycle import read_record, request_stop, request_pairing
from .notifications import Notifications, read_json, write_json, settings, save_settings, publish, publish_bark, publish_pushplus
from .store import SessionStore, StoreUnavailable
from .remote import AppHosts
from . import access, network
from .validation import FieldError, at_field


class Desktop:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.data_dir/'config.json'
        self.local_bridge = None
        self.local_sessions = None
        self.starting_until = 0

    def close_local(self, check_busy=False):
        if self.local_bridge:
            accounts = self.local_bridge.accounts
            if check_busy:
                accounts.assert_editable()
                if accounts.thread and accounts.thread.is_alive():
                    raise ValueError('请等待账号操作完成后再启动网关')
            # Finish in-flight account reads before transferring ownership.
            with self.local_bridge.account.lock:
                if self.local_sessions:
                    self.local_sessions.close()
                self.local_bridge.close()
            self.local_bridge = self.local_sessions = None

    def local_services(self):
        if time.monotonic() < self.starting_until:
            raise ValueError('网关正在启动，请稍后重试')
        if self.local_bridge is None:
            from .service import Bridge
            from .integrations.manager import DesktopSessions
            from .integrations.discovery import discover_clients
            p = self.preferences()
            discovered = discover_clients(p)
            runtime = p.get('codexBin') or discovered['codex'].get('runtime')
            self.local_bridge = Bridge(Path(p['codexHome']).expanduser(), self.data_dir,
                                       ipc_path=p.get('ipcPath') or None, codex_bin=runtime or None)
            self.local_sessions = DesktopSessions(self.data_dir, self.local_bridge)
        return self.local_bridge, self.local_sessions

    def local_control(self, action, capability, value):
        record = read_record(self.data_dir/'gateway-control.json')
        if record:
            if not self.status()['running']:
                raise ValueError('网关状态暂不可用，请稍后刷新')
            if not record.get(capability):
                raise ValueError('请重新启动网关以启用客户端管理')
            self.close_local(check_busy=True)
            self.starting_until = 0
            return request_pairing(self.data_dir, {'action': action, 'value': value}, timeout=100)
        bridge, sessions = self.local_services()
        if action == 'desktop-sessions':
            return sessions.control(value)
        return bridge.accounts.control(value) if action == 'accounts' else bridge.accounts.account(value)

    def config(self):
        if not self.config_path.exists():
            password = secrets.token_urlsafe(18)
            write_json(self.config_path, {'auth': {'mode': 'password', 'username': 'admin', **password_record(password)}, 'origins': []})
            credentials = self.data_dir/'首次登录.txt'
            credentials.write_text('Codex App 手机网关\n账号：admin\n密码：'+password+'\n', encoding='utf-8')
            credentials.chmod(0o600)
        return read_json(self.config_path, {})

    def shared_relay(self, value):
        from .shared_relay import Controller
        return Controller(self.data_dir).control(value)

    def preferences(self):
        name = 'cloudflared.exe' if os.name == 'nt' else 'cloudflared'
        executable = self.data_dir/'bin'/name
        bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]/'dist'))/'cloudflared'/name
        detected = str(bundled) if bundled.is_file() else str(executable) if executable.is_file() else shutil.which(name) or ''
        if not detected and sys.platform == 'darwin':
            detected = next((value for value in ('/opt/homebrew/bin/cloudflared', '/usr/local/bin/cloudflared') if Path(value).is_file()), '')
        defaults = {'autoStart': False, 'port': 8787, 'lan': True, 'lanAddresses': None, 'localAccess': True, 'tunnel': (self.data_dir/'外网地址.txt').exists(),
                    'cloudflared': detected,
                    'codexHome': os.environ.get('CODEX_HOME', str(Path.home()/'.codex')),
                    'ipcPath': '', 'codexBin': ''}
        saved = read_json(self.data_dir/'desktop.json', {})
        if not saved.get('cloudflared') or not Path(saved['cloudflared']).is_file():
            saved['cloudflared'] = defaults['cloudflared']
        rows = access.connections({**defaults, **saved})
        return {**defaults, **{k: v for k, v in saved.items() if k not in access.DEFAULTS},
                'connections': rows, 'tunnel': any(c['enabled'] and c['accessMode'] == 'quick' for c in rows)}

    def status(self):
        preferences = self.preferences()
        connected, supports, instance = False, False, None
        try:
            connection = http.client.HTTPConnection('127.0.0.1', preferences['port'], timeout=1)
            try:
                connection.request('GET', '/api/health')
                response = connection.getresponse()
                payload = json.loads(response.read(65536))
                if response.status != 200 or payload.get('service') != 'codex-mobile-bridge':
                    connection.request('GET', '/api/auth')
                    response = connection.getresponse()
                    payload = json.loads(response.read(65536))
                connected = response.status == 200 and (payload.get('service') == 'codex-mobile-bridge' or ('authenticated' in payload and 'passwordless' in payload))
                supports = bool(payload.get('notifications'))
                instance = payload.get('instanceId')
            finally:
                connection.close()
        except (OSError, ValueError, http.client.HTTPException):
            pass
        record = read_record(self.data_dir/'gateway-control.json')
        managed = connected and bool(record) and (not record.get('instanceId') or record['instanceId'] == instance)
        return {'running': managed, 'portOccupied': connected and not managed, 'supportsNotifications': supports,
                'pid': record.get('pid') if managed else None, 'instanceId': instance if managed else None}

    def snapshot(self):
        config = self.config()
        preferences = self.preferences()
        runtime = self.status()
        from run import addresses
        selected = preferences.get('lanAddresses')
        hosts = (addresses() if selected is None else ['127.0.0.1'] + selected) if preferences['lan'] else ['127.0.0.1']
        if not preferences['localAccess']:
            hosts = [host for host in hosts if host not in ('127.0.0.1', 'localhost')]
        urls = [f'http://{host}:{preferences["port"]}/' for host in hosts if host != 'localhost']
        quick = read_json(self.data_dir/'cloudflare-status.json', {})
        if not runtime['running'] or quick.get('pid') != runtime.get('pid'):
            quick = {}
        quick_url = ''
        public = self.data_dir/'外网地址.txt'
        # Never offer a Quick Tunnel URL unless cloudflared says it is still ready.
        # A lost tunnel keeps its old text for a moment but its DNS record is gone.
        if runtime['running'] and preferences['tunnel'] and public.exists() and quick.get('state') == 'ready':
            value = public.read_text(encoding='utf-8').splitlines()[0]
            if value.startswith('https://'):
                quick_url = value
                urls.insert(0, value)
        urls = [url+'/' for url in access.public_urls(preferences)] + urls
        external = {}
        for entry in preferences['connections']:
            status = read_json(self.data_dir/('ssh-status-'+entry['id']+'.json'), {})
            if runtime['running'] and status.get('pid') == runtime.get('pid'):
                external[entry['id']] = status
        executable = preferences['cloudflared']
        cloudflared = {'path': executable, 'available': bool(executable and Path(executable).is_file() and os.access(executable, os.X_OK))}
        from .address_notifications import entry_urls
        notification_urls = entry_urls(preferences, hosts, quick_url, external) if runtime['running'] else []
        notifications = settings(self.data_dir)
        return {'preferences': preferences, 'auth': {'username': config['auth'].get('username', 'admin'), 'mode': config['auth']['mode'], 'sessionHours': config['auth'].get('sessionHours', 12)},
                'origins': config.get('origins', []), 'notifications': {**notifications, 'token': '', 'hasToken': bool(notifications['token']), 'barkKey': '', 'hasBarkKey': bool(notifications['barkKey']), 'pushplusToken': '', 'hasPushplusToken': bool(notifications['pushplusToken'])},
                'watches': self.notification_watches({'action': 'list'})['watches'],
                'notificationUrls': notification_urls,
                'securityNotificationStatus': read_json(self.data_dir/'security-notifications.json', {}),
                'addressNotificationStatus': read_json(self.data_dir/'address-notifications.json', {}),
                'notificationStatus': read_json(self.data_dir/'notification-status.json', {}),
                'dataDir': str(self.data_dir), 'credentialsAvailable': (self.data_dir/'首次登录.txt').exists(),
                'runtime': runtime, 'urls': urls, 'externalStatus': external, 'cloudflared': cloudflared, 'quickTunnel': quick}

    def save(self, value):
        old_preferences = self.preferences()
        preferences = {**old_preferences, **{k: v for k, v in value['preferences'].items() if k in old_preferences}}
        preferences['connections'] = access.validate_connections(preferences)
        preferences['tunnel'] = any(c['enabled'] and c['accessMode'] == 'quick' for c in preferences['connections'])
        port = preferences['port']
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise FieldError('端口必须为 1–65535', 'port')
        preferences['lanAddresses'] = at_field('lan-scope', network.selected_addresses, preferences['lanAddresses'])
        for key in ('lan', 'tunnel', 'autoStart', 'localAccess'):
            if not isinstance(preferences[key], bool):
                raise ValueError('网络开关格式不正确')
        for key in ('codexHome', 'codexBin', 'ipcPath', 'cloudflared'):
            if not isinstance(preferences[key], str) or '\x00' in preferences[key] or len(preferences[key]) > 4096:
                raise FieldError('路径格式不正确', {'codexHome': 'codex-home', 'codexBin': 'codex-bin', 'ipcPath': 'ipc-path', 'cloudflared': 'cloudflared'}[key])
        if not Path(preferences['codexHome']).expanduser().is_dir():
            raise FieldError('Codex 数据目录不存在', 'codex-home')
        if self.status()['running'] and any(preferences[k] != old_preferences[k] for k in preferences if k != 'autoStart'):
            raise ValueError('请先停止网关再更改网络或运行路径，以免正在使用的地址失效')
        config = self.config()
        auth = value['auth']
        if auth.get('mode') not in ('password', 'none') or not isinstance(auth.get('username'), str) or not auth['username'].strip() or len(auth['username']) > 200:
            raise FieldError('请填写有效的登录账号和登录方式', 'username')
        password = auth.get('password', '')
        hours = at_field('session-hours', session_hours, auth.get('sessionHours', config['auth'].get('sessionHours', 12)))
        if not isinstance(password, str) or (password and not 12 <= len(password) <= 1000):
            raise FieldError('新密码至少需要 12 个字符', 'password')
        origins = value.get('origins', [])
        if not isinstance(origins, list):
            raise FieldError('额外 HTTPS 源格式不正确', 'origins')
        origins = list(dict.fromkeys(at_field('origins', access.origin, origin) for origin in origins))
        old_fixed = access.public_urls(old_preferences)
        origins = [origin for origin in origins if origin not in old_fixed]
        for fixed in access.public_urls(preferences):
            if fixed not in origins:
                origins.append(fixed)
        if self.status()['running'] and origins != config.get('origins', []):
            raise ValueError('请先停止网关再更改 HTTPS 地址，保存后重新启动')
        if any(preferences[k] != old_preferences[k] for k in ('codexHome', 'codexBin', 'ipcPath')):
            self.close_local(check_busy=True)
        # Validate all values before writing any setting.
        notification_value = value.get('notifications', {})
        # save_settings performs the remaining validation; settings files have separate owners.
        save_settings(self.data_dir, notification_value)
        config['auth'].update(mode=auth['mode'], username=auth['username'].strip(), sessionHours=hours)
        if password:
            config['auth'].update(password_record(password))
        config['origins'] = origins
        config['publicUrl'] = access.public_url(preferences)
        config['connections'] = preferences['connections']
        config['lanAddresses'] = preferences['lanAddresses']
        config['localAccess'] = preferences['localAccess']
        write_json(self.config_path, config)
        write_json(self.data_dir/'desktop.json', preferences)
        if password:
            (self.data_dir/'首次登录.txt').unlink(missing_ok=True)
        return self.snapshot()

    def argv(self):
        p = self.preferences()
        args = ['--config', str(self.config_path), '--port', str(p['port']), '--codex-home', str(Path(p['codexHome']).expanduser())]
        for flag, key in [('--lan', 'lan'), ('--tunnel', 'tunnel')]:
            if p[key]:
                args.append(flag)
        for flag, key in [('--cloudflared', 'cloudflared'), ('--ipc-path', 'ipcPath'), ('--codex-bin', 'codexBin')]:
            if p[key]:
                args.extend([flag, p[key]])
        return args

    def start(self, value=None):
        temporary = (value or {}).get('connectionSecrets', {})
        self.config()
        state = self.status()
        if state['running']:
            return {'started': False, 'message': '已连接正在运行的网关'}
        self.close_local(check_busy=True)
        preferences = self.preferences()
        if not Path(preferences['codexHome']).expanduser().is_dir():
            raise ValueError('请先选择存在的 Codex 数据目录')
        if any(c['enabled'] and c['accessMode'] in ('quick', 'cloudflare') for c in preferences['connections']):
            executable = preferences['cloudflared']
            if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
                raise ValueError('未找到可运行的 cloudflared，请在“运行配置”中一键安装并保存，或关闭临时 Cloudflare 连接。')
        access.validate_connections(preferences)
        from .server_connection import managed
        if any(c['enabled'] and c['accessMode'] == 'server' and not managed(c, self.data_dir) for c in preferences['connections']) and not shutil.which('ssh'):
            raise ValueError('未找到 OpenSSH 客户端；请先安装或启用系统 SSH 客户端')
        from .server_connection import credentials
        for entry in preferences['connections']:
            if not entry['enabled']:
                continue
            if entry['accessMode'] == 'cloudflare' or (entry['accessMode'] == 'server' and entry.get('sshAuth', 'config') != 'config'):
                secret = credentials(entry, self.data_dir, temporary.get(entry['id']))
                if entry['accessMode'] == 'cloudflare' and not secret.get('tunnelToken'):
                    raise FieldError('请先填写并保存 Cloudflare Tunnel Token。', 'tunnelToken', entry['id'])
                if entry['accessMode'] == 'server' and entry.get('sshAuth') == 'password' and not secret.get('password'):
                    raise FieldError('请先填写并保存 SSH 密码。', 'password', entry['id'])
                temporary[entry['id']] = secret
        for address in network.bindings(preferences):
            probe = socket.socket()
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((address, preferences['port']))
            except OSError as exc:
                raise ValueError('所选地址不可用或端口已被占用，请检查网卡与端口；不会自动开放其他地址') from exc
            finally:
                probe.close()
        command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve().parents[1]/'desktop.py')]
        command += ['serve', '--data-dir', str(self.data_dir), '--connection-secrets-stdin']
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        # Frozen children run as independent applications, not multiprocessing workers.
        environment = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
        with (self.data_dir/'gateway.log').open('ab') as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, env=environment, **kwargs)
            process.stdin.write(json.dumps(temporary).encode('utf-8'))
            process.stdin.close()
        self.starting_until = time.monotonic() + 30
        return {'started': True, 'pid': process.pid, 'message': '正在启动网关'}

    def stop(self):
        record = read_record(self.data_dir/'gateway-control.json') or {}
        if record.get('accountsManagement'):
            state = request_pairing(self.data_dir, {'action': 'accounts', 'value': {'action': 'list'}}, timeout=5)
            if state.get('switch', {}).get('phase') in ('preparing', 'stopping', 'applying', 'starting', 'verifying', 'restoring'):
                raise ValueError('请等待账号切换完成后再停止网关')
        request_stop(self.data_dir)
        self.starting_until = 0
        return {'message': '网关已停止'}

    def accounts(self, value):
        return self.local_control('accounts', 'accountsManagement', value)

    def desktop_sessions(self, value):
        return self.local_control('desktop-sessions', 'desktopSessionsManagement', value)

    def harness(self, value):
        from .harness import configuration, detect, save
        record = read_record(self.data_dir/'gateway-control.json') or {}
        if self.status()['running']:
            if not record.get('harnessManagement'):
                raise ValueError('请重新启动网关以启用 Harness')
            return request_pairing(self.data_dir, {'action': 'harness', 'value': value}, timeout=80)
        if record:
            raise ValueError('网关状态暂不可用，请稍后刷新')
        action = value.get('action', 'status')
        if action == 'save':
            save(self.data_dir, value.get('config', {}))
        elif action not in ('status', 'detect'):
            raise ValueError('请先启动网关')
        result = {'state': 'stopped', 'running': False, 'config': configuration(self.data_dir), 'logs': [], 'url': '/harness/'}
        if action == 'detect':
            result['detected'] = detect()
        return result

    def account(self, value):
        return self.local_control('account', 'accountManagement', value)

    def devices(self, value):
        if self.status()['running']:
            record = read_record(self.data_dir/'gateway-control.json') or {}
            if not record.get('deviceManagement'):
                raise ValueError('请重新启动网关以启用设备管理')
            return request_pairing(self.data_dir, {'action': 'devices', 'value': value})
        if read_record(self.data_dir/'gateway-control.json'):
            raise ValueError('网关状态暂不可用，请稍后刷新设备列表')
        return Auth(self.config()['auth'], self.data_dir).manage(value)

    def notification_watches(self, value):
        if value.get('action') != 'list':
            record = read_record(self.data_dir/'gateway-control.json')
            if self.status()['running']:
                if not record or not record.get('notificationManagement'):
                    raise ValueError('请重新启动网关以管理会话通知')
                request_pairing(self.data_dir, {'action': 'notification-watches', 'value': value}, timeout=35)
            elif record:
                raise ValueError('网关状态暂不可用，请稍后刷新')
            else:
                Notifications(None, self.data_dir).control(value)
        rows = read_json(self.data_dir/'notification-watches.json', [])
        home = Path(self.preferences()['codexHome']).expanduser()
        store, hosts = SessionStore(home), AppHosts(home).hosts()
        for row in rows:
            row['hostLabel'] = '此电脑' if row['host'] == 'local' else hosts.get(row['host'], {}).get('displayName') or hosts.get(row['host'], {}).get('alias') or row['host']
            if row['host'] == 'local':
                try:
                    meta = store.get(row['id'])
                    row.update(title=meta.get('name') or meta.get('title') or '未命名聊天', cwd=meta.get('cwd', ''))
                except (KeyError, StoreUnavailable):
                    pass
            row['notifyOnCompletion'] = bool(row.get('notifyOnCompletion'))
        return {'watches': rows}

    def test_notification(self, value=None):
        if (value or {}).get('channel') == 'address':
            from .address_notifications import send_address
            from .notifications import channels
            config = settings(self.data_dir)
            snapshot = self.snapshot()
            urls = snapshot['notificationUrls']
            targets = channels(config)
            if not config['addressEnabled'] or not targets:
                raise ValueError('请先开启入口通知及至少一个通知通道，并保存配置。')
            if not urls:
                raise ValueError('请先启动网关，并启用局域网或等待外网入口就绪。')
            failed = []
            for channel in targets:
                try:
                    send_address(config, channel, urls, test=True)
                except Exception:
                    failed.append(channel)
            if failed:
                raise ValueError('部分通道发送失败：' + ', '.join(failed) + '；请在手机确认其他通道是否收到。')
            return {'message': '已向启用通道发送当前入口，请在手机确认是否收到。'}
        channel = (value or {}).get('channel', 'ntfy')
        if channel not in ('ntfy', 'bark', 'pushplus'):
            raise ValueError('未知通知通道')
        name = {'bark': 'Bark', 'ntfy': 'ntfy', 'pushplus': 'PushPlus'}[channel]
        sender = {'bark': publish_bark, 'ntfy': publish, 'pushplus': publish_pushplus}[channel]
        try:
            sender(settings(self.data_dir), 'Codex 手机通知测试', f'收到这条消息表示 {name} 通道已连通。')
        except Exception:
            raise ValueError(f'{name} 测试失败，请检查服务地址、认证和网络') from None
        return {'message': f'{name} 已接受测试通知，请在手机确认是否收到'}

    def deployment(self, value):
        entry = access.select_connection(self.preferences(), value)
        files = access.deployment(entry)
        return {'files': files, 'accessMode': entry['accessMode']}

    def export_deployment(self, value):
        files = access.deployment(access.select_connection(self.preferences(), value))
        path = self.data_dir/'固定入口部署.zip'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        path.chmod(0o600)
        return {'path': str(path)}

    def read_credentials(self, value):
        # Explicit private IPC only. Never include plaintext in polled snapshots.
        if value.get('id'):
            from .connection_secrets import read
            if not any(c['id'] == value['id'] for c in self.preferences()['connections']):
                raise ValueError('请先保存连接配置。')
            return read(self.data_dir, value['id'])
        notification = settings(self.data_dir)
        result = {key: notification[key] for key in ('token', 'barkKey', 'pushplusToken')}
        initial = self.data_dir/'首次登录.txt'
        result['password'] = ''
        if initial.exists():
            result['password'] = next((line[3:] for line in initial.read_text(encoding='utf-8').splitlines() if line.startswith('密码：')), '')
        return result

    def connection_credentials(self, value):
        entry = next((c for c in self.preferences()['connections'] if c['id'] == value.get('id')), None)
        if not entry:
            raise ValueError('请先保存连接配置。')
        from .connection_secrets import save
        save(self.data_dir, entry['id'], value.get('secrets', {}))
        return {'message': '连接凭据已保存到系统凭据存储。'}

    def server_setup(self, value):
        entry = access.select_connection(self.preferences(), value, allow_disabled=True)
        if entry['accessMode'] != 'server':
            raise ValueError('请选择自有服务器连接。')
        from . import server_connection
        action = value.get('action')
        if action == 'diagnostics':
            return {'prompt': server_connection.diagnostic_prompt(entry, value.get('language'))}
        if action == 'host':
            return server_connection.host_probe(entry, self.data_dir)
        if action == 'trust':
            return server_connection.trust(entry, self.data_dir, value.get('fingerprint'))
        if action == 'inspect':
            return server_connection.inspect(entry, self.data_dir, value.get('secrets'))
        raise ValueError('App 仅支持 SSH 连接检查；服务器安装与权限配置请手动完成。')

    def check_entry(self, value):
        if not self.status()['running']:
            raise ValueError('请先启动网关，再检测固定入口')
        return access.check_entry(access.select_connection(self.preferences(), value))

    def pairing(self, value):
        if value.get('action') == 'create':
            from .pairing import phone_origin
            snapshot = self.snapshot()
            url = value.get('url')
            if not snapshot['runtime']['running'] or url not in snapshot['urls']:
                raise ValueError('请先启动网关，再生成二维码')
            origin = phone_origin(url)
            # Probe the actual entry, without credentials or redirects, before granting access.
            remote = access.read_auth(origin)
            if remote.get('instanceId') != snapshot['runtime']['instanceId']:
                raise ValueError('此地址未指向当前网关，请检查连接配置')
        return request_pairing(self.data_dir, value)

    def logs(self):
        # Reverse complete timestamped records, keeping traceback lines readable.
        import re
        paths = [('Gateway', self.data_dir/'gateway.log'), ('Cloudflare', self.data_dir/'tunnel.log')]
        for entry in self.preferences()['connections']:
            if entry['accessMode'] == 'server':
                paths.append((entry['name'] or entry['id'], self.data_dir/('ssh-tunnel-'+entry['id']+'.log')))
        sections = []
        for label, path in paths:
            if not path.exists():
                continue
            with path.open('rb') as handle:
                offset = max(0, path.stat().st_size - 18000)
                handle.seek(offset)
                if offset:
                    handle.readline()
                text = handle.read().decode('utf-8', errors='replace')
            records = []
            for line in text.splitlines():
                continuation = line.startswith((' ', '\t', 'Traceback', 'During handling', 'The above')) or re.match(r'^[\w.]*(?:Error|Exception|Interrupt|Exit|Warning):', line)
                if not records or (line and not continuation):
                    records.append([line])
                else:
                    records[-1].append(line)
            sections.append({'name': label, 'text': '\n'.join('\n'.join(record) for record in reversed(records))})
        notifications = settings(self.data_dir)
        for secret in (notifications.get('token'), notifications.get('barkKey'), notifications.get('pushplusToken')):
            if secret:
                for section in sections:
                    section['text'] = section['text'].replace(secret, '[REDACTED]')
        return {'sections': sections, 'text': '\n\n'.join('--- '+s['name']+' ---\n'+s['text'] for s in sections)}
