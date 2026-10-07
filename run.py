#!/usr/bin/env python3
"""Run the desktop bridge with Python 3.9+; no package installation required."""
import argparse
import getpass
import ipaddress
import json
import logging
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from bridge.auth import password_record
from bridge.httpd import GatewayServer
from bridge.lifecycle import GatewayControl
from bridge.service import Bridge
from bridge.tunnel import QuickTunnel
from bridge.ssh_tunnel import SSHTunnel
from bridge.address_notifications import AddressNotifications, ready_url, gateway_ready, entry_urls
from bridge.notifications import Notifications
from bridge import network

ROOT = Path(__file__).resolve().parent


def addresses():
    result = {"127.0.0.1", "localhost"}
    if sys.platform == 'linux':
        executable = shutil.which('ip')
        if executable:
            try:
                output = subprocess.run([executable, '-j', '-4', 'address', 'show', 'up'],
                                        capture_output=True, text=True, check=True, timeout=5)
                interfaces = json.loads(output.stdout)
                if not isinstance(interfaces, list):
                    raise ValueError('Invalid interface list')
                for interface in interfaces:
                    for address in interface.get('addr_info', []):
                        if address.get('family') == 'inet':
                            ip = ipaddress.IPv4Address(address['local'])
                            if not ip.is_unspecified and not ip.is_multicast:
                                result.add(str(ip))
                return sorted(result)
            except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
                pass
    if os.name == "posix" and Path("/sbin/ifconfig").exists():
        # Enumerate local interfaces without waiting for hostname DNS.
        output = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True, check=False).stdout
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) > 1 and parts[0] == "inet":
                result.add(parts[1])
    else:
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                result.add(item[4][0])
        except OSError:
            pass
    return sorted(result)


def save_config(path, config):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(path)


def main(connections=None, connection_secrets=None):
    connection_secrets = connection_secrets or {}
    # Redirected Windows streams may use a codec that cannot encode Chinese.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description="Codex App 手机网关")
    parser.add_argument("--config", type=Path, default=ROOT / ".local/config.json")
    parser.add_argument("--lan", action="store_true", help="监听局域网；默认只监听本机")
    parser.add_argument("--tunnel", action="store_true", help="同时启动 Cloudflare 临时 HTTPS 外网入口")
    parser.add_argument("--ssh-target", help="自有服务器的已有 SSH Host 别名或 user@hostname")
    parser.add_argument("--ssh-remote-port", type=int, default=18787, help="SSH 服务器回环监听端口")
    tunnel_name = 'cloudflared.exe' if os.name == 'nt' else 'cloudflared'
    tunnel_bin = ROOT / '.local/bin' / tunnel_name
    parser.add_argument("--cloudflared", type=Path, default=tunnel_bin if tunnel_bin.is_file() else Path(shutil.which(tunnel_name) or str(tunnel_bin)), help="Cloudflare 客户端路径")
    parser.add_argument("--ipc-path", help="桌面 IPC 地址；Windows 为本机命名管道路径")
    parser.add_argument("--codex-bin", type=Path, help="桌面 App 的 Codex 可执行文件路径")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--origin", action="append", default=[], help="允许的 HTTPS 穿透源，例如 https://codex.example.com")
    parser.add_argument("--set-password", action="store_true", help="交互式设置登录密码，不启动服务")
    parser.add_argument("--no-auth", action="store_true", help="本次运行明确关闭账号密码验证")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须为 1–65535")
    if args.ssh_target:
        from bridge.access import validate
        validate({'sshTarget': args.ssh_target, 'sshRemotePort': args.ssh_remote_port})
    os.umask(0o077)
    args.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    first_login = args.config.parent / "首次登录.txt"
    if args.config.exists():
        config = json.loads(args.config.read_text(encoding='utf-8'))
    else:
        password = secrets.token_urlsafe(18)
        config = {"auth": {"mode": "password", "username": "admin", **password_record(password)}, "origins": []}
        save_config(args.config, config)
        first_login.write_text("Codex App 手机网关\n账号：admin\n密码：" + password + "\n\n仅用于此网关，与 Codex 模型登录无关。\n修改密码：python run.py --set-password\n", encoding='utf-8')
        first_login.chmod(0o600)
    if args.set_password:
        password = getpass.getpass("新密码（至少 12 位）：")
        if len(password) < 12 or password != getpass.getpass("再次输入："):
            parser.error("密码太短或两次输入不一致")
        config["auth"].update(mode="password", **password_record(password))
        save_config(args.config, config)
        first_login.unlink(missing_ok=True)
        print("密码已更新。请重新启动网关。")
        return
    if config["auth"].get("mode") not in ("password", "none"):
        parser.error("auth.mode 只能为 password 或 none")
    if args.no_auth:
        config["auth"]["mode"] = "none"
    from bridge.access import validate_connections, public_urls, public_url
    preferences = {'connections': config.get('connections', []) if connections is None else connections,
                   'lan': args.lan, 'port': args.port, 'lanAddresses': network.selected_addresses(config.get('lanAddresses'))}
    entries = validate_connections(preferences)
    args.tunnel = args.tunnel or any(c['enabled'] and c['accessMode'] == 'quick' for c in entries)
    if entries:
        config['publicUrl'] = public_url(preferences)
    origins = config.get("origins", []) + args.origin + public_urls(preferences)
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            parser.error("额外 origin 必须是完整 HTTPS 源且不能有路径")
    selected = preferences['lanAddresses']
    hosts = (addresses() if selected is None else ['127.0.0.1', 'localhost'] + selected) if args.lan else ['127.0.0.1', 'localhost']
    config["origins"] = sorted(set(origins + [f"http://{host}:{args.port}" for host in hosts]))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bridge = Bridge(args.codex_home, args.config.parent, ipc_path=args.ipc_path, codex_bin=args.codex_bin)
    servers = []
    try:
        for address in network.bindings(preferences):
            servers.append(GatewayServer((address, args.port), bridge, config, ROOT / 'web', args.config.parent,
                                         **({'shared': servers[0]} if servers else {})))
    except OSError:
        for listener in servers:
            listener.server_close()
        bridge.close()
        raise
    server = servers[0]
    listener_threads = []
    pid_file = args.config.parent / "gateway.pid"
    pid_file.write_text(str(os.getpid()), encoding='utf-8')
    control = GatewayControl(args.config.parent)
    tunnel = None
    tunnel_thread = None
    ssh_tunnels = []
    shared_relay = None
    def notification_urls():
        if not gateway_ready(args.port, server.instance_id):
            return []
        from bridge.notifications import read_json
        external = {}
        for entry in entries:
            status = read_json(args.config.parent/('ssh-status-'+entry['id']+'.json'), {})
            if status.get('pid') == os.getpid():
                external[entry['id']] = status
        return entry_urls(preferences, hosts, ready_url(tunnel, args.port, server.instance_id), external)
    address_notifications = AddressNotifications(args.config.parent, notification_urls, server.instance_id)
    notifications = Notifications(bridge, args.config.parent, lambda: server.origins, lambda: config.get('publicUrl', ''))
    def valid_push_session(key):
        with server.auth.lock:
            session = server.auth.sessions.get(key)
            return bool(session and server.auth.valid(session) and server.auth.permitted(session["ip"]))
    notifications.mobile_push.session_valid = valid_push_session
    for listener in servers:
        listener.notifications = notifications
    def stop_signal(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop_signal)
    print("Codex App 手机网关已启动", flush=True)
    for origin in config["origins"]:
        if not config.get("localAccess", True) and urlsplit(origin).hostname in ("127.0.0.1", "localhost"):
            continue
        print("  " + origin, flush=True)
    print("登录方式：" + ("免密（已显式启用）" if config["auth"]["mode"] == "none" else "账号密码"), flush=True)
    if first_login.exists():
        print("首次登录凭据：" + str(first_login), flush=True)
    try:
        forwards = [c for c in entries if c['enabled'] and c['accessMode'] == 'server']
        if args.ssh_target:
            forwards.append({'sshTarget': args.ssh_target, 'sshRemotePort': args.ssh_remote_port, 'id': 'cli'})
        for entry in forwards:
            from bridge.server_connection import managed
            if not managed(entry, args.config.parent):
                ssh_tunnel = SSHTunnel(entry['sshTarget'], entry['sshRemotePort'], args.port, args.config.parent, entry['id'])
            else:
                from bridge.server_connection import ManagedForward
                ssh_tunnel = ManagedForward(entry, args.port, args.config.parent, connection_secrets.get(entry['id']))
            ssh_tunnels.append(ssh_tunnel)
            ssh_tunnel.start()
        for entry in entries:
            if entry['enabled'] and entry['accessMode'] == 'cloudflare':
                from bridge.named_tunnel import NamedTunnel
                from bridge.server_connection import credentials
                secret = credentials(entry, args.config.parent, connection_secrets.get(entry['id']))
                named = NamedTunnel(args.cloudflared, entry, args.config.parent, secret.get('tunnelToken', ''))
                ssh_tunnels.append(named)
                named.start()
        if args.tunnel:
            print("正在建立临时 HTTPS 外网连接…", flush=True)
            quick_origin = None
            def allow_origin(origin):
                nonlocal quick_origin
                if quick_origin and quick_origin != origin:
                    # A new Quick Tunnel has a new random hostname.  Remove the
                    # obsolete origin instead of leaving it trusted forever.
                    server.origins.discard(quick_origin)
                    old_host = urlsplit(quick_origin).netloc
                    server.hosts.discard(old_host)
                    server.secure_hosts.discard(old_host)
                quick_origin = origin
                server.origins.add(origin)
                host = urlsplit(origin).netloc
                server.hosts.add(host)
                server.secure_hosts.add(host)
            tunnel = QuickTunnel(args.cloudflared, args.port, args.config.parent, allow_origin)
            def connect_tunnel():
                try:
                    url = tunnel.start()
                    print("外网地址：" + url, flush=True)
                except (RuntimeError, OSError) as exc:
                    tunnel.status('failed', '临时 HTTPS 连接失败，请检查网络并查看 Cloudflare 日志；局域网仍可使用。')
                    print(str(exc), flush=True)
            tunnel_thread = threading.Thread(target=connect_tunnel, daemon=True)
            tunnel_thread.start()
        from bridge.shared_relay import start as start_shared_relay
        shared_relay = start_shared_relay(args.config.parent, server)
        notifications.start()
        address_notifications.start()
        control.start(server.shutdown, server.pairing.control, server.instance_id, server.auth, bridge.account.control, server.notifications.control, bridge.accounts.control)
        for listener in servers[1:]:
            thread = threading.Thread(target=listener.serve_forever, kwargs={'poll_interval': 0.5}, daemon=True)
            thread.start()
            listener_threads.append((listener, thread))
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for listener, thread in listener_threads:
            listener.shutdown()
            thread.join()
        if shared_relay:
            shared_relay.close()
        address_notifications.close()
        notifications.close()
        if tunnel:
            tunnel.close()
            if tunnel_thread:
                tunnel_thread.join(timeout=5)
        for ssh_tunnel in ssh_tunnels:
            ssh_tunnel.close()
        bridge.close()
        for listener in servers:
            listener.server_close()
        if pid_file.exists() and pid_file.read_text(encoding='utf-8').strip() == str(os.getpid()):
            pid_file.unlink()
        control.close()


if __name__ == "__main__":
    main()
