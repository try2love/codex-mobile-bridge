#!/usr/bin/env python3
"""Run the desktop bridge with Python 3.9+; no package installation required."""
import argparse
import getpass
import json
import logging
import os
import secrets
import shutil
import signal
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from bridge.features.auth.auth import password_record
from bridge.api.httpd import GatewayServer
from bridge.app.lifecycle import GatewayControl
from bridge.app.service import Bridge
from bridge.features.network.tunnel import QuickTunnel
from bridge.features.network.ssh_tunnel import SSHTunnel
from bridge.features.notifications.addresses import AddressNotifications, ready_url, gateway_ready, entry_urls
from bridge.features.notifications.channels import Notifications
import bridge.features.network.addresses as network
import bridge.features.network.interfaces as interfaces
from bridge.features.network.runtime import DynamicListenerManager, RuntimeNetworkMonitor, notify_route_rechecks
from bridge.features.network.adaptation import NetworkAdaptation, settings as adaptation_settings, migrate_legacy_interfaces

ROOT = Path(__file__).resolve().parent


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
        from bridge.features.network.access import validate
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
    rows = interfaces.enumerate_interfaces()
    config = migrate_legacy_interfaces(args.config, rows)
    if config["auth"].get("mode") not in ("password", "none"):
        parser.error("auth.mode 只能为 password 或 none")
    if args.no_auth:
        config["auth"]["mode"] = "none"
    from bridge.features.network.access import validate_connections, public_urls, public_url
    legacy_addresses = config.get('lanAddresses')
    raw_interfaces = config.get('lanInterfaces',
                                interfaces.migrate_addresses_to_interfaces(rows, legacy_addresses))
    preferences = {'connections': config.get('connections', []) if connections is None else connections,
                   'lan': args.lan, 'port': args.port,
                   'lanAddresses': network.selected_addresses(legacy_addresses),
                   'lanInterfaces': interfaces.selected_interfaces(raw_interfaces)}
    entries = validate_connections(preferences)
    preferences['connections'] = entries
    if config['auth']['mode'] == 'none' and any(c['enabled'] and c['accessMode'] == 'tailscale' and c['tailscaleMode'] == 'funnel' for c in entries):
        parser.error('Funnel 公网入口需要启用网关登录验证；手机仍可扫码配对。')
    args.tunnel = args.tunnel or any(c['enabled'] and c['accessMode'] == 'quick' for c in entries)
    if entries:
        config['publicUrl'] = public_url(preferences)
    origins = config.get("origins", []) + args.origin + public_urls(preferences)
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            parser.error("额外 origin 必须是完整 HTTPS 源且不能有路径")
    if args.lan:
        hosts = ['127.0.0.1', 'localhost'] + network.bindings(preferences, rows)[1:]
    else:
        hosts = ['127.0.0.1', 'localhost']
    config["origins"] = sorted(set(origins + [f"http://{host}:{args.port}" for host in hosts]))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bridge = Bridge(args.codex_home, args.config.parent, ipc_path=args.ipc_path, codex_bin=args.codex_bin)
    servers = []
    tailscale_port = None
    try:
        # Auto-LAN and selected adapters are owned by the runtime monitor, so
        # DHCP changes replace listeners without a gateway restart.
        startup_bindings = ['127.0.0.1'] if args.lan else network.bindings(preferences, rows)
        for address in startup_bindings:
            servers.append(GatewayServer((address, args.port), bridge, config, ROOT / 'web', args.config.parent,
                                         **({'shared': servers[0]} if servers else {})))
        if any(c['enabled'] and c['accessMode'] == 'tailscale' for c in entries):
            # A pre-existing loopback SSH forward can shadow a LAN
            # listener on args.port. Give Tailscale an unambiguous local owner.
            private = GatewayServer(('127.0.0.1', 0), bridge, config, ROOT / 'web', args.config.parent, shared=servers[0])
            servers.append(private)
            tailscale_port = private.server_address[1]
    except OSError:
        if servers:
            servers[0].harness.close()
            servers[0].desktop_sessions.close()
        for listener in servers:
            listener.server_close()
        bridge.close()
        raise
    server = servers[0]
    listener_manager = DynamicListenerManager(
        server, args.port, bridge, config, ROOT / 'web', GatewayServer,
    )
    listener_threads = []
    pid_file = args.config.parent / "gateway.pid"
    pid_file.write_text(str(os.getpid()), encoding='utf-8')
    control = GatewayControl(args.config.parent)
    tunnel = None
    tunnel_thread = None
    ssh_tunnels = []
    route_rechecks = []
    monitor = RuntimeNetworkMonitor(args.config.parent, preferences, args.port, listener_manager,
                                    server.instance_id, pid=os.getpid(),
                                    enabled=adaptation_settings(args.config.parent)['enabled'],
                                    route_enumerator=None if args.tunnel else lambda rows: {})
    adaptation = NetworkAdaptation(args.config.parent, monitor)
    for listener in servers:
        listener.network_adaptation = adaptation
    def notification_urls():
        if not gateway_ready(args.port, server.instance_id):
            return []
        from bridge.features.notifications.channels import read_json
        external = {}
        for entry in entries:
            status = read_json(args.config.parent/('ssh-status-'+entry['id']+'.json'), {})
            if status.get('pid') == os.getpid():
                external[entry['id']] = status
        if tunnel and not tunnel.entry_stable():
            return []
        return entry_urls(preferences, hosts, ready_url(tunnel, args.port, server.instance_id), external,
                          lan_addresses=monitor.addresses())
    address_notifications = AddressNotifications(args.config.parent, notification_urls, server.instance_id)
    notifications = Notifications(bridge, args.config.parent, lambda: server.origins, lambda: config.get('publicUrl', ''), desktop_sessions=server.desktop_sessions)
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
    from bridge.features.network.discovery import ConnectionDiscovery
    from bridge.features.network.access import phone_urls
    discovery = ConnectionDiscovery(args.config.parent, server, lambda: phone_urls(
        {**preferences, 'port': args.port}, monitor.addresses(),
        tunnel.url if tunnel and tunnel.entry_stable() else ''))
    listener_manager.configure = lambda listener: setattr(listener, 'connection_discovery', discovery)
    try:
        monitor.scan(initial=True)
    except Exception:
        monitor.close()
        raise
    print("Codex App 手机网关已启动", flush=True)
    for origin in sorted(server.origins):
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
            from bridge.features.network.server_connection import managed
            if not managed(entry, args.config.parent):
                ssh_tunnel = SSHTunnel(entry['sshTarget'], entry['sshRemotePort'], args.port, args.config.parent, entry['id'])
            else:
                from bridge.features.network.server_connection import ManagedForward
                ssh_tunnel = ManagedForward(entry, args.port, args.config.parent, connection_secrets.get(entry['id']))
            ssh_tunnels.append(ssh_tunnel)
            ssh_tunnel.start()
        for entry in entries:
            if entry['enabled'] and entry['accessMode'] == 'tailscale':
                from bridge.features.network.tailscale import TailscaleTunnel
                def tailscale_target_is_current(value):
                    parsed = urlsplit(value)
                    try:
                        port = parsed.port
                    except ValueError:
                        return False
                    return (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                            and parsed.path in ('', '/') and not parsed.query
                            and not parsed.fragment and port is not None
                            and gateway_ready(port, server.instance_id))
                tailscale = TailscaleTunnel(entry, tailscale_port, args.config.parent,
                                            target_validator=tailscale_target_is_current)
                ssh_tunnels.append(tailscale)
                route_rechecks.append(tailscale.network_changed)
                tailscale.start()
            if entry['enabled'] and entry['accessMode'] == 'cloudflare':
                from bridge.features.network.named_tunnel import NamedTunnel
                from bridge.features.network.server_connection import credentials
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
            # Let the existing tunnel recover first. Its origin remains valid
            # until a replacement is actually ready, not merely on a route event.
            route_rechecks.append(tunnel.network_changed)
            monitor.network_cancelled = tunnel.cancel_network_check
            def connect_tunnel():
                try:
                    url = tunnel.start()
                    print("外网地址：" + url, flush=True)
                except (RuntimeError, OSError) as exc:
                    tunnel.status('failed', '临时 HTTPS 连接失败，请检查网络并查看 Cloudflare 日志；局域网仍可使用。')
                    print(str(exc), flush=True)
            tunnel_thread = threading.Thread(target=connect_tunnel, daemon=True)
            tunnel_thread.start()
        if route_rechecks:
            monitor.network_changed = lambda: notify_route_rechecks(route_rechecks)
        for listener in servers:
            listener.connection_discovery = discovery
        notifications.start()
        address_notifications.start()
        monitor.start()
        bridge.accounts.monitor.start()
        control.start(server.shutdown, server.pairing.control, server.instance_id, server.auth, bridge.account.control, server.notifications.control, bridge.accounts_control, server.harness.control, server.desktop_sessions.control, bridge.cli_access_control, network_adaptation=adaptation.control)
        for listener in servers[1:]:
            thread = threading.Thread(target=listener.serve_forever, kwargs={'poll_interval': 0.5}, daemon=True)
            thread.start()
            listener_threads.append((listener, thread))
        server.desktop_sessions.start()
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for listener, thread in listener_threads:
            listener.shutdown()
            thread.join()
        server.harness.close()
        server.desktop_sessions.close()
        address_notifications.close()
        monitor.close()
        bridge.accounts.monitor.close()
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
