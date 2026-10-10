"""Fixed-domain setup files and validation; never installs or configures a server."""
import http.client
import ipaddress
import json
import re
import shlex
from urllib.parse import urlsplit

from bridge.features.auth.tls import client_context
from bridge.api.validation import FieldError, at_field


DEFAULTS = {'accessMode': 'lan', 'publicUrl': '', 'proxyUpstream': '',
            'sshTarget': '', 'sshRemotePort': 18787, 'sshAuth': 'config',
            'sshHost': '', 'sshPort': 22, 'sshUser': '', 'sshKeyPath': '',
            'tailscaleMode': 'funnel', 'tailscalePort': 443, 'tailscaleNodeId': ''}


def origin(value, scheme='https'):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError('地址不能包含空白字符')
    parsed = urlsplit(value)
    if (parsed.scheme != scheme or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError(f'请填写完整 {scheme.upper()} 地址，不含路径、账号或参数')
    host = parsed.hostname
    try:
        ipaddress.ip_address(host)
        if '%' in host:
            raise ValueError('地址不能包含 IPv6 区域标识')
    except ValueError:
        if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', host):
            raise ValueError('请使用域名或 IP 地址；国际化域名请填写 Punycode')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('地址端口不正确')
    authority = '['+host+']' if ':' in host else host
    if parsed.port is not None and parsed.port != (443 if scheme == 'https' else 80):
        authority += ':'+str(parsed.port)
    return f'{scheme}://{authority.lower()}'


def server_ip_url(preferences):
    """Use a literal public SSH address; never guess an IP from DNS or SSH aliases."""
    from bridge.features.network.server_connection import endpoint
    host = endpoint(preferences)['host']
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise FieldError('手机访问地址留空时，请将 SSH 服务器地址（或别名的 HostName）填写为公网 IP；使用域名时请填写完整 HTTPS 访问地址。', 'publicUrl') from None
    if not address.is_global or address.is_multicast or address.is_reserved or '%' in host:
        raise FieldError('手机访问地址留空时需要公网 IP，不能使用内网、回环或特殊用途地址。', 'publicUrl')
    authority = f'[{address.compressed}]' if address.version == 6 else str(address)
    return 'https://' + authority


def validate(preferences):
    p = {**DEFAULTS, **preferences}
    if p['accessMode'] not in ('lan', 'quick', 'cloudflare', 'server', 'nas', 'tailscale'):
        raise FieldError('请选择有效的外网连接方式', 'connection-kind')
    if p['publicUrl']:
        p['publicUrl'] = at_field('publicUrl', origin, p['publicUrl'])
    if p['proxyUpstream']:
        p['proxyUpstream'] = at_field('proxyUpstream', origin, p['proxyUpstream'], 'http')
    if p['accessMode'] == 'tailscale':
        if p['tailscaleMode'] not in ('funnel', 'serve'):
            raise FieldError('请选择有效的 Tailscale 访问范围。', 'tailscaleMode')
        if type(p['tailscalePort']) is not int or p['tailscalePort'] not in (443, 8443, 10000):
            raise FieldError('Tailscale HTTPS 端口只能为 443、8443 或 10000。', 'tailscalePort')
        if not isinstance(p['tailscaleNodeId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{0,128}', p['tailscaleNodeId']):
            raise FieldError('Tailscale 设备身份无效，请重新检测。', 'publicUrl')
        if p['publicUrl']:
            parsed = urlsplit(p['publicUrl'])
            if (not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net', parsed.hostname)
                    or (parsed.port or 443) != p['tailscalePort']):
                raise FieldError('请通过检测读取此电脑的 Tailscale 固定地址。', 'publicUrl')
    if (not isinstance(p['sshTarget'], str) or len(p['sshTarget']) > 253
            or (p['sshTarget'] and not re.fullmatch(r'[\w][\w.@-]*', p['sshTarget']))):
        raise FieldError('SSH 目标请填写已有 Host 别名或 user@hostname，不含空格和命令参数', 'sshTarget')
    port = p['sshRemotePort']
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise FieldError('服务器回环端口须为 1024–65535', 'sshRemotePort')
    if p['accessMode'] in ('nas', 'cloudflare') and not p['publicUrl']:
        raise FieldError('请填写手机访问的固定 HTTPS 地址', 'publicUrl')
    if p['accessMode'] == 'server':
        if p['sshAuth'] == 'config' and not p['sshTarget']:
            raise FieldError('请填写服务器的 SSH 目标', 'sshTarget')
        if urlsplit(p['publicUrl']).port not in (None, 443):
            raise FieldError('服务器 HTTPS 入口使用 443 端口；已有其他入口请选择 NAS / 已有反代', 'publicUrl')
    if p['accessMode'] == 'nas':
        if not p['lan'] or not p['proxyUpstream']:
            raise FieldError('NAS 方式需要开启局域网访问并填写 NAS 可达的电脑 HTTP 地址', 'proxyUpstream')
        parsed = urlsplit(p['proxyUpstream'])
        if (parsed.port or 80) != p['port']:
            raise FieldError('电脑上游地址的端口须与网关监听端口一致', 'proxyUpstream')
        if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
            raise FieldError('NAS 上游请使用电脑的局域网地址，不能使用回环地址', 'proxyUpstream')
    if p['sshAuth'] not in ('config', 'password', 'key', 'agent'):
        raise FieldError('请选择 SSH 认证方式', 'sshAuth')
    if isinstance(p['sshPort'], bool) or not isinstance(p['sshPort'], int) or not 1 <= p['sshPort'] <= 65535:
        raise FieldError('SSH 端口须为 1–65535', 'sshPort')
    for key in ('sshHost', 'sshUser', 'sshKeyPath'):
        if not isinstance(p[key], str) or len(p[key]) > 4096 or any(c in p[key] for c in ('\x00', '\n', '\r')):
            raise FieldError('SSH 配置格式不正确', key)
    if p['accessMode'] == 'server' and p['sshAuth'] != 'config':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', p['sshHost']):
            raise FieldError('请填写服务器 IP 或主机名，不含协议或路径', 'sshHost')
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', p['sshUser']):
            raise FieldError('请填写有效的 SSH 用户名', 'sshUser')
        if p['sshAuth'] == 'key' and not p['sshKeyPath']:
            raise FieldError('请选择本地 SSH 私钥文件', 'sshKeyPath')
    if p['accessMode'] == 'server' and not p['publicUrl']:
        p['publicUrl'] = server_ip_url(p)
    if p['accessMode'] == 'cloudflare' and urlsplit(p['publicUrl']).port not in (None, 443):
        raise FieldError('Cloudflare 固定隧道请使用标准 HTTPS 域名', 'publicUrl')
    p['tunnel'] = p['accessMode'] == 'quick'
    return p


def connections(preferences):
    """Migrate the previous single-entry preferences without changing saved files."""
    if 'connections' in preferences:
        return preferences['connections']
    mode = preferences.get('accessMode', 'quick' if preferences.get('tunnel') else 'lan')
    if mode == 'lan':
        return []
    return [{**DEFAULTS, **{k: preferences[k] for k in DEFAULTS if k in preferences},
             'id': 'legacy-'+mode, 'name': '', 'enabled': True, 'accessMode': mode}]


def validate_connections(preferences):
    rows = connections(preferences)
    if not isinstance(rows, list):
        raise ValueError('连接配置须为列表')
    result, ids, urls, forwards = [], set(), set(), set()
    quick = tailscale = False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', row['id']):
            raise ValueError('连接配置 ID 不正确')
        if row['id'] in ids:
            raise ValueError('连接配置 ID 重复')
        ids.add(row['id'])
        if not isinstance(row.get('enabled'), bool) or row.get('accessMode') not in ('quick', 'cloudflare', 'server', 'nas', 'tailscale'):
            raise ValueError('连接类型或开关格式不正确')
        if not isinstance(row.get('name', ''), str) or len(row.get('name', '')) > 100:
            raise FieldError('连接名称最多 100 个字符', 'name', row['id'])
        current = {**DEFAULTS, **{k: row[k] for k in DEFAULTS if k in row},
                   'id': row['id'], 'name': row.get('name', '').strip(), 'enabled': row['enabled']}
        for key in ('publicUrl', 'sshTarget', 'proxyUpstream'):
            if isinstance(current[key], str):
                current[key] = current[key].strip()
        if current['enabled']:
            try:
                checked = validate({**current, 'lan': preferences.get('lan', False), 'port': preferences.get('port', 8787)})
            except FieldError as exc:
                exc.validation['connectionId'] = row['id']
                raise
            for key in DEFAULTS:
                current[key] = checked[key]
            if current['accessMode'] == 'quick':
                if quick:
                    raise FieldError('每个网关只需启用一个临时 Cloudflare 入口；可同时启用其他连接方式', 'enabled', row['id'])
                quick = True
            elif current['publicUrl']:
                if current['publicUrl'] in urls:
                    raise FieldError('已启用的连接不能使用相同 HTTPS 地址', 'publicUrl', row['id'])
                urls.add(current['publicUrl'])
            if current['accessMode'] == 'tailscale':
                if tailscale:
                    raise FieldError('每个网关只需启用一个 Tailscale 入口。', 'enabled', row['id'])
                tailscale = True
            if current['accessMode'] == 'server':
                forward = (current['sshTarget'] if current['sshAuth'] == 'config' else (current['sshHost'], current['sshPort']), current['sshRemotePort'])
                if forward in forwards:
                    raise FieldError('同一 SSH 目标的回环端口不能重复', 'sshRemotePort', row['id'])
                forwards.add(forward)
        result.append(current)
    return result


def public_urls(preferences):
    return [c['publicUrl'] for c in connections(preferences)
            if c['enabled'] and c['accessMode'] in ('server', 'nas', 'cloudflare', 'tailscale') and c['publicUrl']]


def public_url(preferences):
    return next(iter(public_urls(preferences)), '')


def select_connection(preferences, value, allow_disabled=False):
    selected = next((c for c in connections(preferences) if c['id'] == value.get('id')), None)
    if not selected or (not selected['enabled'] and not allow_disabled) or selected['accessMode'] not in ('server', 'nas', 'cloudflare', 'tailscale'):
        raise ValueError('请先保存并启用需要操作的固定连接配置')
    return validate({**selected, 'lan': preferences['lan'], 'port': preferences['port']})


def deployment(preferences):
    p = validate(preferences)
    if p['accessMode'] == 'cloudflare':
        raise ValueError('固定 Cloudflare 隧道请在 Cloudflare 配置公开主机名，并在 App 保存 Tunnel Token。')
    if p['accessMode'] not in ('server', 'nas'):
        raise ValueError('请先选择并保存自有服务器或 NAS 配置')
    ssh_example = shlex.join(['ssh', p['sshTarget']] if p['sshAuth'] == 'config' else ['ssh', '-p', str(p['sshPort']), p['sshUser']+'@'+p['sshHost']])
    url = p['publicUrl']
    try:
        ipaddress.ip_address(urlsplit(url).hostname)
        ip_entry = True
    except ValueError:
        ip_entry = False
    upstream = f'http://127.0.0.1:{p["sshRemotePort"]}' if p['accessMode'] == 'server' else p['proxyUpstream']
    common = f'''# Codex 手机网关 · 固定入口部署

手机地址：{url}/
电脑网关端口：{p['port']}（保持原值）
反代上游：{upstream}

电脑、网关和原 Codex App 需要持续运行。Docker 仅部署 HTTPS 入口。
本包不包含账号密码、Token、Codex 数据或 SSH 私钥。
网关登录密码从电脑 App 的“查看首次登录凭据”获取，或由用户自行设置。
域名必须指向服务器/NAS，手机使用独立子域名根路径，不支持 /codex/ 子路径。
登录建议使用账号密码。不要将电脑端口直接映射到公网。

'''
    if p['accessMode'] == 'server':
        files = {
            'compose.yaml': '''services:
  codex-https:
    image: caddy:2-alpine
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
volumes:
  caddy_data:
  caddy_config:
''',
            'Caddyfile': f'''{url} {{
    reverse_proxy {upstream} {{
        header_up Host {{http.request.host}}
        flush_interval -1
    }}
}}
'''}
        steps = f'''## 自有 Linux 服务器（手动配置参考）

安装软件、配置防火墙和 SSH 权限、启动 Docker 等操作由用户或管理员亲自在终端完成。App 不接收 sudo 密码、不执行远端命令。优先接入已有反向代理；以下 Docker 配置为可选参考。

1. 可使用 `{ssh_example}` 检查服务器连接，也可在 App 内使用“检查 SSH 登录”。首次连接需核对主机指纹；密码、私钥或 SSH Agent 在 App 配置，凭据不包含在此部署包内。
2. 服务器须允许远程端口转发（AllowTcpForwarding remote 或 yes），GatewayPorts 使用 no 或 clientspecified。回环端口 {p['sshRemotePort']} 须空闲。不要使用 GatewayPorts yes，以免强制向公网绑定。
3. 将域名 DNS 的 A/AAAA 记录指向这台服务器。将本包解压到一个新目录；服务器须已安装 Docker Compose，且公网 TCP 80、443 可达并空闲。已有网站占用这两个端口时，不要停止旧网站；使用已有反代，将上游设为 {upstream} 并保留公网 Host 即可，无需启动本包的 Caddy。
4. 在电脑 App 保存配置并启动网关。电脑主动建立 SSH 回程连接，不要求服务器能直接访问电脑的局域网 IP。
5. 在服务器解压目录执行：

```sh
docker compose config
docker compose up -d
docker compose logs --tail=50
```

Caddy 自动申请和续期 HTTPS 证书，证书保存在 Docker 卷中。此包的 host 网络仅面向 Linux Docker Engine；SSH 转发和 Caddy 必须位于同一台主机。不要在 Mac/Windows 的 Docker Desktop 运行此服务器包。
'''
    else:
        files = {
            'compose.yaml': '''services:
  codex-entry:
    image: nginx:stable-alpine
    restart: unless-stopped
    ports:
      - "${BIND_ADDRESS:-127.0.0.1}:18787:8080"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
''',
            'nginx.conf': f'''server {{
    listen 8080;
    server_name _;
    client_max_body_size 512000;
    access_log off;
    location / {{
        proxy_pass {upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }}
}}
''',
            '.env.example': '# 外层反代运行在 NAS 主机上时保持默认。若反代也在容器内，填写 NAS 局域网 IP，并限制仅反代可访问。\nBIND_ADDRESS=127.0.0.1\n'}
        steps = f'''## NAS / 已有 HTTPS 反代

1. 先在 NAS 确认可以访问 {upstream}/。电脑与 NAS 必须网络互通；家中 NAS 无法直接访问校园网电脑时，改用 App 的“自有服务器 + SSH”或先建立可用的 VPN 路由。
2. 推荐直接在群晖、威联通或 Nginx Proxy Manager 创建 HTTPS 反代：公网入口 {url}，目标 {upstream}。使用已有证书，保留外部 Host、Origin、Cookie、X-CSRF-Token，关闭缓存和缓冲，读写超时设为 300 秒。
3. 若希望用 Docker 统一管理 HTTP 转发，将本包放在 NAS 新目录执行：

```sh
docker compose config
docker compose up -d
docker compose logs --tail=50
```

然后把 NAS 的 HTTPS 反代上游改为 http://127.0.0.1:18787。每层都需保留外部 Host。若外层反代也在容器内，复制 .env.example 为 .env，将 BIND_ADDRESS 填为 NAS 局域网 IP，外层上游也用该 IP:18787；限制仅该反代可访问，禁止公网映射此端口。
4. Docker 容器只提供 HTTP 中转，证书和公网 443 由 NAS 已有反代负责。没有现成 HTTPS 入口时，可选择自有 Linux 服务器方案。
'''
    files['部署说明.md'] = common + steps + f'''
## 验收与维护

电脑 App 点击“检测固定入口”，然后手机关闭 Wi-Fi，用蜂窝网络打开 {url}/，验证登录、聊天同步和 ntfy 跳转。检测仅验证这台电脑经 HTTPS 访问的是当前网关，不替代手机外网测试。
502/504：检查反代上游或 SSH 隧道；403：检查每层保留 Host，以及电脑配置保存后是否重启；证书错误：检查 DNS、80/443 和证书日志。
启动/更新容器：docker compose up -d；日志：docker compose logs --tail=50；停止入口：docker compose down（不删除证书卷，不停止电脑网关）。

## 给部署 Agent

项目：https://github.com/try2love/codex-mobile-bridge
仅使用用户授权的服务器/NAS；执行前确认现有站点、端口和 DNS，禁止覆盖已有服务。部署依赖缺失时先说明。不要复制 Codex 凭据或私钥，不更改电脑网关端口。完成后返回固定 HTTPS 地址、原局域网地址、密码获取方式、部署目录、启停命令、已通过和未通过的验收项。没有权限或真实入口时，说明尚未部署，不声称连通。
'''
    if p['accessMode'] == 'server':
        english_steps = f'''All server software installation, firewall and SSH permission changes, and Docker operations must be performed manually by the user or administrator. The app does not accept sudo passwords or execute remote commands. Prefer an existing reverse proxy; Docker is an optional reference.

1. Check `{ssh_example}` from your computer, or use Check SSH sign-in in the App. Verify the host fingerprint. Configure password, key or agent authentication in the App; credentials are not included in this package.
2. The server must allow remote forwarding (`AllowTcpForwarding remote` or `yes`). Keep `GatewayPorts no` or `clientspecified`, never `yes`. Port {p['sshRemotePort']} must be free and restricted to loopback.
3. Point the domain's A/AAAA records to this server. Have the user or administrator manually install Docker Compose if needed and ensure public TCP 80/443 are reachable and free. Extract this bundle into a new directory. The Caddy service uses host networking on Linux Docker Engine, on the same host as SSH; do not run this bundle on Mac/Windows Docker Desktop.
4. Start the computer gateway. It connects out to the server over SSH, including behind NAT or on a campus network.
5. On the server run `docker compose config`, then `docker compose up -d`. Caddy obtains and renews certificates, persisted in the named Docker volumes. If an existing proxy already owns 80/443, keep it and use {upstream} as its upstream, preserving the public Host header; do not start another Caddy instance.
'''
    else:
        english_steps = f'''1. From the NAS, verify that {upstream}/ is reachable. It must be the computer's address, not the NAS loopback address. A home NAS cannot directly reach an isolated campus computer without a working network route; use the SSH server option or an existing VPN route in that case.
2. Prefer an existing NAS HTTPS reverse proxy directly to {upstream}. Preserve Host, Origin, Cookie and X-CSRF-Token. Disable caching/buffering and use 300-second read/send timeouts.
3. To manage an optional HTTP intermediary in Docker, extract this bundle and run `docker compose config`, then `docker compose up -d`. Set the NAS HTTPS proxy's upstream to http://127.0.0.1:18787. The container does not issue certificates: the existing NAS proxy handles HTTPS.
4. If the outer proxy is also containerized, copy .env.example to .env and set BIND_ADDRESS to the NAS LAN IP. Point the outer proxy there, restrict access to that proxy, and do not expose port 18787 publicly.
'''
    files['DEPLOYMENT_EN.md'] = f'''# Codex Mobile Bridge — fixed entry deployment

Phone URL: {url}/
Computer gateway port: {p['port']} (keep unchanged)
Proxy upstream: {upstream}

Keep the computer, gateway and original Codex App running. Docker hosts the access entry; it does not replace the desktop App. This ZIP contains no passwords, tokens, Codex data or SSH keys. Obtain gateway credentials from the computer App or the user's configured login. Use a dedicated domain root, not a subpath such as /codex/.

## Setup

{english_steps}
## Verification and maintenance

Click Check fixed entry in the desktop App, then disable phone Wi-Fi and verify HTTPS login, chat synchronization and ntfy links over mobile data. The built-in check verifies the current gateway instance from this computer only. A 502/504 suggests an unreachable upstream or SSH forwarding failure; a 403 suggests the public Host is not preserved or the gateway needs a restart after saving its allowlist. Check DNS and Caddy logs for certificate failures.

Start/update: `docker compose up -d`. Logs: `docker compose logs --tail=50`. Stop the public entry: `docker compose down` (keep certificate volumes; the computer gateway stays running).

## Instructions for deployment Agents

Project: https://github.com/try2love/codex-mobile-bridge
Use only authorized servers or NAS devices. Check existing sites, DNS and ports before deploying; do not overwrite existing services. Explain missing dependencies before installing them. Keep the computer gateway port, model authentication and SSH keys unchanged. Return the fixed HTTPS URL, original LAN URL, credential retrieval method, deployment directory, start/stop commands, verified results and remaining checks. If access is unavailable, deliver the prepared configuration and clearly say deployment is pending.
'''
    if p['accessMode'] == 'server':
        files['部署说明.md'] = files['部署说明.md'].split('## 给部署 Agent')[0] + '''## 手动配置边界

此内容仅供用户检查与手动配置，不是自动部署指令。涉及管理员权限的命令必须由用户自行执行；不要让 App 或部署 Agent 代输 sudo 密码、安装服务或修改系统权限。保留已有站点、Codex 凭据、SSH 私钥及电脑网关端口。
'''
        files['DEPLOYMENT_EN.md'] = files['DEPLOYMENT_EN.md'].split('## Instructions for deployment Agents')[0] + '''## Manual setup boundary

This is a reference for user review and manual setup, not an automatic deployment instruction. Administrative commands must be executed by the user. Do not ask the app or an Agent to enter sudo passwords, install services or change system permissions. Preserve existing sites, Codex credentials, SSH keys and the computer gateway port.
'''
        if ip_entry:
            files['Caddyfile'] = files['Caddyfile'].replace(
                f'{url} {{\n', f'{url} {{\n    tls /etc/caddy/tls/fullchain.pem /etc/caddy/tls/privkey.pem\n')
            files['compose.yaml'] = files['compose.yaml'].replace(
                '      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n',
                '      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n      - ./tls:/etc/caddy/tls:ro\n')
            files['部署说明.md'] = f'''# 公网 IP HTTPS 入口（手动配置参考）

手机地址：{url}/
反向代理上游：{upstream}

无需配置域名 DNS。服务器必须提供受手机和电脑信任、且 Subject Alternative Name 包含该公网 IP 的有效证书；域名证书和未受信任的自签名证书不能代替 IP 证书。App 不申请证书、不跳过验证、不降级为 HTTP。

1. 优先复用服务器现有 HTTPS 反向代理，将上述 IP 入口转发到 {upstream}，保留 Host，关闭缓存和响应缓冲。
2. 如使用本包的可选 Caddy 示例，由管理员将证书完整链和私钥放在服务器上的 tls/fullchain.pem 与 tls/privkey.pem（不随本包提供），限制私钥权限并负责证书续期。不要将私钥传回网关或提交到代码仓库。
3. 服务器公网 TCP 443 需可达。SSH 需允许远程转发，保持 GatewayPorts no，{p['sshRemotePort']} 仅监听 127.0.0.1，不向公网开放。Docker 示例仅适用于同一 SSH 服务器上的 Linux Docker Engine；已有服务占用端口时不要另启容器。
4. 保存电脑配置、检查 SSH 登录并核对指纹，再启动网关。若使用 Docker 示例，在服务器执行 docker compose config 验证后，再由管理员执行 docker compose up -d。
5. 点击“检测固定入口”，再用手机蜂窝网络测试。证书错误时修正证书，502 时检查 SSH 隧道；SSH 登录成功不代表 HTTPS 入口已经可用。

管理员权限的命令必须由用户自行执行。App 不修改服务器配置、安装服务或接收 sudo 密码。本包不含凭据，保留已有服务及电脑网关端口 {p['port']}。
'''
            files['DEPLOYMENT_EN.md'] = f'''# Public IP HTTPS entry (manual reference)

Phone URL: {url}/
Proxy upstream: {upstream}

No domain DNS record is needed. Provision a valid certificate trusted by the phone and computer with this public IP in its Subject Alternative Name. A domain-only certificate or untrusted self-signed certificate is insufficient. The app does not issue certificates, bypass verification or fall back to HTTP.

1. Prefer an existing HTTPS reverse proxy. Forward this IP entry to {upstream}, preserve Host and disable caching and response buffering.
2. For the optional Caddy example, place the full certificate chain and private key on the server at tls/fullchain.pem and tls/privkey.pem. They are not included. Restrict key permissions and arrange renewal. Never send the private key to the gateway or commit it.
3. Make public TCP 443 reachable. Allow SSH remote forwarding with GatewayPorts no and restrict port {p['sshRemotePort']} to 127.0.0.1. The Docker example requires Linux Docker Engine on the SSH server; do not start another proxy over existing listeners.
4. Save the computer configuration, check SSH sign-in and the host fingerprint, then start the gateway. For the Docker example, validate with docker compose config before the administrator runs docker compose up -d.
5. Check the fixed entry, then test on phone cellular data. Fix certificate errors at the server; for 502 check the SSH tunnel. Successful SSH authentication alone does not prove HTTPS access.

Administrative commands must be executed by the user. The app does not configure the server, install services or accept sudo passwords. This bundle contains no credentials. Preserve existing services and computer gateway port {p['port']}.
'''
    return files


def read_auth(url):
    parsed = urlsplit(url)
    if parsed.scheme == 'https':
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=8, context=client_context())
    else:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=8)
    try:
        connection.request('GET', '/api/auth', headers={'Cache-Control': 'no-cache'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f'入口返回 HTTP {response.status}；检查反代上游、Host 和 HTTPS 配置')
        value = json.loads(response.read(65536))
        if not isinstance(value, dict):
            raise ValueError('入口返回的不是网关响应，请检查反代目标')
        return value
    finally:
        connection.close()


def check_entry(preferences, instance_id=None):
    url = public_url(preferences)
    if not url:
        raise ValueError('请先保存固定 HTTPS 入口配置')
    if instance_id is None:
        instance_id = read_auth(f'http://127.0.0.1:{preferences["port"]}').get('instanceId')
    remote = read_auth(url)
    if not instance_id or remote.get('instanceId') != instance_id:
        raise ValueError('入口没有连接到当前网关；请检查反代目标，旧版网关须先更新并重启')
    return {'message': '固定 HTTPS 入口已连到当前网关。请再用手机蜂窝网络验证登录与聊天。', 'url': url}
