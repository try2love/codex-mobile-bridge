# 使用自己的域名和服务器

以下以 `codex.example.com` 和文档示例 IP `203.0.113.10` 说明，请替换为自己的信息。

1. 域名添加 A 记录 `codex`，指向服务器公网 IP；让服务器 TCP 80、443、SSH 端口可达。
2. 宝塔 → 网站 → 添加站点：域名 `codex.example.com`，纯静态，无需数据库或 FTP。
3. 站点 → SSL：申请并部署有效证书，启用强制 HTTPS，确认自动续期。
4. 反向代理：目标 URL `http://127.0.0.1:18787`，发送域名 `codex.example.com`，目录 `/`，关闭缓存。
5. 助手中选择固定域名，填写 `https://codex.example.com`、服务器 IP 和 SSH 端口，输入自己的 SSH 用户名和密码连接。

建议在宝塔已生成的代理配置内加入以下设置，不要重复新增同路径的 `location`：

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 300s;
proxy_send_timeout 300s;
proxy_buffering off;
client_max_body_size 16m;
```

手机 → Nginx HTTPS → 服务器 `127.0.0.1:18787` → SSH 反向隧道 → 电脑 `127.0.0.1:8787` → Codex Desktop。

无需向公网开放 8787、18787。SSH 服务需允许远程端口转发，`GatewayPorts` 保持 `no`。可使用有相应转发权限的专用 SSH 用户，用户名不是固定值。

第一次连接需核对 SSH 主机指纹。未连接电脑时网站可能返回 502；域名固定不表示任务可以在电脑离线时运行。服务器密码、宝塔密码、手机网关密码是不同凭据。
