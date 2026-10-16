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
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from bridge.auth import password_record
from bridge.httpd import GatewayServer
from bridge.lifecycle import GatewayControl
from bridge.service import Bridge
from bridge.tunnel import QuickTunnel

ROOT = Path(__file__).resolve().parent


def addresses():
    result = {"127.0.0.1", "localhost"}
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            result.add(item[4][0])
    except OSError:
        pass
    if os.name == "posix" and Path("/sbin/ifconfig").exists():
        output = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True, check=False).stdout
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) > 1 and parts[0] == "inet":
                result.add(parts[1])
    return sorted(result)


def save_config(path, config):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(path)


def main():
    # Redirected Windows streams may use a codec that cannot encode Chinese.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description="Codex App 手机网关")
    parser.add_argument("--config", type=Path, default=ROOT / ".local/config.json")
    parser.add_argument("--lan", action="store_true", help="监听局域网；默认只监听本机")
    parser.add_argument("--tunnel", action="store_true", help="同时启动 Cloudflare 临时 HTTPS 外网入口")
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
    origins = config.get("origins", []) + args.origin
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            parser.error("额外 origin 必须是完整 HTTPS 源且不能有路径")
    hosts = addresses() if args.lan else ["127.0.0.1", "localhost"]
    config["origins"] = sorted(set(origins + [f"http://{host}:{args.port}" for host in hosts]))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bridge = Bridge(args.codex_home, args.config.parent, ipc_path=args.ipc_path, codex_bin=args.codex_bin)
    server = GatewayServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), bridge, config, ROOT / "web")
    pid_file = args.config.parent / "gateway.pid"
    pid_file.write_text(str(os.getpid()), encoding='utf-8')
    control = GatewayControl(args.config.parent)
    tunnel = None
    def stop_signal(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop_signal)
    print("Codex App 手机网关已启动", flush=True)
    for origin in config["origins"]:
        print("  " + origin, flush=True)
    print("登录方式：" + ("免密（已显式启用）" if config["auth"]["mode"] == "none" else "账号密码"), flush=True)
    if first_login.exists():
        print("首次登录凭据：" + str(first_login), flush=True)
    try:
        if args.tunnel:
            print("正在建立临时 HTTPS 外网连接…", flush=True)
            def allow_origin(origin):
                server.origins.add(origin)
                host = urlsplit(origin).netloc
                server.hosts.add(host)
                server.secure_hosts.add(host)
            tunnel = QuickTunnel(args.cloudflared, args.port, args.config.parent, allow_origin)
            try:
                url = tunnel.start()
                print("外网地址：" + url, flush=True)
            except RuntimeError as exc:
                print(str(exc), flush=True)
        control.start(server.shutdown)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.close()
        bridge.close()
        server.server_close()
        if pid_file.exists() and pid_file.read_text(encoding='utf-8').strip() == str(os.getpid()):
            pid_file.unlink()
        control.close()


if __name__ == "__main__":
    main()
