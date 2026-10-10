# 用自己的域名访问 Codex

只有域名、没有公网服务器，也可以使用 **Cloudflare 固定隧道**。配置一次后，手机始终使用同一个 HTTPS 地址；电脑、Codex App 和网关需要保持在线。

访问链路：**手机 → 自己的域名 → Cloudflare → 电脑上的隧道 → 本地网关 → Codex App**。

本文中的用户名 `try2love`、域名 `try2love.com` 和地址 `https://codex.try2love.com` 仅用于配置演示，不代表可访问的演示站点。请替换为自己的用户名和域名。桌面 App 已内置 cloudflared，无需安装 Python、Node.js 或另一个 Cloudflare 客户端，也不需要服务器 SSH 账号。

## 1. 把域名添加到 Cloudflare

登录 [Cloudflare 控制台](https://dash.cloudflare.com)，添加根域名 `try2love.com`，按需要选择套餐。已有网站或邮箱时，先核对导入的 DNS 记录，保留原有站点和邮件记录。

Cloudflare 会分配两条名称服务器（Nameservers / NS）地址。复制自己账号中显示的值，每个域名可能不同。

## 2. 在域名注册商修改 DNS 服务器

如果在阿里云购买域名，进入阿里云的**域名管理 → 对应域名 → DNS 修改 / 修改 DNS 服务器**，将名称服务器替换为 Cloudflare 提供的两条地址。其他注册商也在域名管理中完成这一步。

这里修改的是域名的 **DNS 服务器 / NS**，不是在解析记录中随便新增两条 NS，也不需要把域名转移到其他注册商。若原先开启了 DNSSEC，先按 Cloudflare 指引在注册商关闭旧 DNSSEC，域名激活后再通过 Cloudflare 配置。

回到 Cloudflare 等待域名状态变为 **Active / 有效**。名称服务器变更可能需要数小时，官方提示可等待最多 24 小时；不同网络的缓存更新时间可能不同。激活后，后续解析记录在 Cloudflare 管理。

## 3. 创建固定隧道，取得 Tunnel Token

在 Cloudflare 控制台进入 **Networking → Tunnels**；部分界面显示为 **Zero Trust → Networks → Connectors / Tunnels**。创建 **Cloudflared** 类型的隧道，取一个便于识别的名字，例如 `my-codex`。

在连接器安装页面找到运行命令中的 **Tunnel Token**。它通常是命令最后的一长串字符：App 只需要这段 Token，不需要整条命令，不需要 Cloudflare 全局 API Key，也不需要在终端重复安装服务。

Token 相当于这条隧道的连接凭据，只在自己的 App 中填写；不要放入公开截图、教程或仓库。

## 4. 在 App 填写并启动

1. 打开“网络与登录”，添加 **固定域名 · Cloudflare Tunnel**。
2. 手机访问地址填写 `https://codex.try2love.com`，不带额外路径。
3. 填写上一步的 **Cloudflare Tunnel Token**，保留账号密码登录保护。
4. 启用该连接，保存配置，再点击“启动网关”。局域网连接可同时保留。

回到 Cloudflare，确认隧道连接器显示 **Healthy / 已连接**。如果页面仍停留在安装向导，可在连接器上线后继续下一步。

## 5. 为隧道添加公开访问地址

在这条隧道的 **Routes → Add route → Published application** 中添加路由；旧界面可能叫 **Public Hostname / 公开主机名**。

| 字段 | 示例填写 |
| --- | --- |
| Subdomain / 子域名 | `codex` |
| Domain / 域名 | `try2love.com` |
| Path / 路径 | 留空 |
| Service Type / 服务类型 | `HTTP` |
| Service URL / 服务地址 | `localhost:8787` |

若界面只有一个完整服务地址输入框，填写 `http://localhost:8787`。这里是**同一台电脑上的网关地址**；默认端口为 8787，修改过网关端口时请使用 App 显示的实际值。不要把手机访问的 HTTPS 域名填到服务地址中。

手机的请求会经过“固定域名 → Cloudflare → 电脑主动建立的隧道 → 本机网关”。因此服务地址填写 `localhost` 也可以供外网访问，不要求手机和电脑处于同一局域网，也不需要重定向到临时 Cloudflare 地址。

保存路由后，Cloudflare 会创建指向 `<隧道 ID>.cfargotunnel.com` 的 DNS 记录。若提示同名记录已存在，先核对用途，再处理冲突，避免覆盖原有网站。此方案不需要把子域名的 A 记录指向电脑 IP 或另购服务器。

### 让 HTTP 自动跳转到 HTTPS

公开路由配置完成后，还需配置 HTTPS 跳转。否则直接输入域名时，部分浏览器可能通过 HTTP 显示登录页，点击“连接电脑”才提示“不允许跨站请求”。

在 Cloudflare 的该域名页面，进入 **规则 → 概述 → 创建规则 → 重定向规则**：

1. 选择“自定义筛选表达式”，填写 `(http.request.full_uri wildcard r"http://codex.try2love.com/*")`，将示例域名替换成自己的网关子域名。
2. URL 重定向选择“动态”，目标表达式填写 `concat("https://codex.try2love.com", http.request.uri.path)`，同样替换域名。
3. 状态代码选择 **301**，勾选“保留查询字符串”，然后部署。

这条规则只升级指定子域名的 HTTP 请求，保留路径和查询参数。重新打开 `http://codex.try2love.com`，确认地址自动变为 `https://codex.try2love.com` 后再输入密码。已经打开的 HTTP 登录页需要刷新或重新打开；不要把 HTTP 地址加入网关允许来源来绕过检查。

## 6. 分别验证电脑和手机

1. 在 App 点击“检测固定入口”，确认域名连接到当前网关。
2. 在电脑浏览器打开 `https://codex.try2love.com`，确认可以看到登录页并登录。
3. 手机关闭 Wi-Fi，使用蜂窝网络访问同一地址，验证登录、读取聊天、发送消息和回复同步。
4. 可在“手机通知”中配置并测试入口通知，方便再次找到地址。

隧道显示 Healthy 只说明连接器已连接 Cloudflare，仍需完成上述网页检查。固定地址不会因普通重连而改变；电脑关机、休眠或停止网关时，这个地址暂时无法访问。

## 打不开时，先看卡在哪一步

| 现象 | 优先检查 |
| --- | --- |
| 浏览器提示找不到域名、DNS 错误 | Cloudflare 域名是否 Active；注册商的 NS 是否正确；公开路由和 DNS 记录是否存在；换手机蜂窝网络对比，排查旧 DNS 缓存 |
| Cloudflare 显示 1033 / 隧道未连接 | App 网关是否运行、Token 是否属于这条隧道、电脑网络是否能连接 Cloudflare；查看 App 运行日志 |
| 显示 502 | 本机网关是否启动；服务类型是否 HTTP；服务地址和端口是否与 App 一致 |
| 显示 404 或其他网站 | 公开主机名、路由和域名是否一致，是否存在冲突记录 |
| 首页能显示，点击“连接电脑”提示“不允许跨站请求” | 检查地址是否以 `https://` 开头；配置上面的 HTTP → HTTPS 跳转，重新打开登录页后再输入密码 |
| 从其他页面点击链接进入首页时显示“不允许跨站请求” | 请求已到达网关。使用修复了首页导航校验的网关版本后重启网关；可先在浏览器地址栏直接输入完整 HTTPS 地址，不要关闭跨站保护 |
| 能打开登录页，但无法登录 | 使用网关账号密码或 App 的扫码登录；不是 Cloudflare 密码或 SSH 密码 |
| 只看到顶部标志、描述或语言切换，没有登录正文 | HTML 可能已收到，但页面脚本或认证初始化尚未完成。检查静态资源与 `/api/auth` 的加载情况；当前开发版会显示加载状态与重试提示。电脑上单个 API 请求成功不能替代手机蜂窝网络的首屏验证 |

不要仅凭“记录已保存”或“SSH 检查通过”判断手机入口可用；以真实浏览器和手机蜂窝网络的结果为准。

## 自有服务器 + SSH 可以不用 root 吗？

可以。**日常建立 SSH 隧道不要求 root，也不会自动执行 sudo。** 例如普通用户 `try2love`，只要能登录服务器，并被允许建立到 `127.0.0.1:18787` 的远程端口转发，就可以使用。

| 操作 | 所需权限 |
| --- | --- |
| 登录服务器、建立高位回环端口的 SSH 转发 | 获准转发的普通 SSH 用户 |
| 首次安装 Caddy、写入系统站点配置、管理系统服务 | root 或具备相应 sudo 权限的用户 |
| 已有 HTTPS 站点后的日常连接 | 普通 SSH 用户，保存配置后启动网关即可 |

在 App 中将“SSH 用户名”填为自己的普通用户名，选择该用户的密码、私钥或 Agent。服务器安装、防火墙和 SSH 权限由用户或管理员手动完成，App 不接收 sudo 密码、不执行远端安装命令。按[服务器 SSH 分步教程](server-ssh.md)准备 HTTPS 站点后，在 App 检查 SSH 登录、启动连接并检测固定入口。

服务器还必须有手机可以到达的 HTTPS 入口。能通过校园网、VPN 或跳板登录 SSH，不代表手机在公网能访问该服务器；需要单独核对。若服务器已有网站，请接入现有反向代理，保留已有服务。

## 官方参考

- [Cloudflare：修改名称服务器](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/)
- [Cloudflare：创建远程管理隧道](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)
- [Cloudflare：创建重定向规则](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/create-dashboard/)
- [OpenSSH：服务器转发权限](https://man.openbsd.org/sshd_config#AllowTcpForwarding)


## 访问慢或出现 ICMP 日志

`Failed to send ICMP reply` 来自 cloudflared 的 ICMP 回包路径；单独这条日志不能判断公开 HTTP 页面是否不可用，也不能证明慢速就是 ICMP 导致。当前网关不会再仅凭这条日志将已连接的固定隧道标为连接异常；其他隧道错误和进程退出仍会显示异常。

排查时分别确认本机网关、固定域名的 DNS/TLS/首字节时间和手机蜂窝网络。Cloudflare 显示已连接只说明连接器已注册，不保证手机到边缘节点的路径通畅。不要通过关闭 TLS 校验、开放无认证入口或增加高频重试处理延迟。

如果希望不经过 Cloudflare，可使用[自有服务器 + SSH](server-ssh.md)，或在当前开发版“网络访问 → 自建中继 · 内测”配置[自己的 relay](shared-relay.md)。在阿里云购买域名不会自动使用阿里云服务器线路；需要部署服务器并将子域名解析到该服务器。迁移前保留原入口，用实际手机网络验证新入口再决定是否停用原隧道。

## 无自建服务器的固定入口

可选择 [Tailscale 固定 HTTPS](tailscale.md)：Funnel 允许普通手机浏览器访问，Serve 仅限已接入 Tailscale 的设备。首次需登录官方客户端并授权；固定地址不代表国内线路已验收。
