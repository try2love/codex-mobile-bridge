# 从云账号网站访问桌面

先在桌面“云服务 → 网页云服务”开启“允许网页访问本电脑”，再打开 `https://bridge.waken.cloudns.be:18787`，用同一云账号登录并选择在线电脑。此总开关默认关闭，升级不会自动授予；开启后浏览器无需逐一获得桌面批准。托管账号仍需要有效服务天数。入口只提供云连接。

开关按精确服务器、账号和本机身份保存，重启保留；关闭会断开已有网页连接并销毁网页本地会话，手机连接权限独立。若关闭状态保存失败，本次网关运行继续拒绝网页访问，界面提供重试保存关闭入口；重启前应修复存储问题并保存。已执行的写入不会撤回，未知结果不重放。

电脑必须运行包含 account-Web 通道的新桌面版本，并登录同一云账号。网页、脚本、样式、字体、API、文件和预览来自电脑当前的网关 HTTP 服务。更新桌面网页后刷新浏览器即可，不需要向云端复制聊天页面。电脑离线时无法打开它的网站。

此入口通过 HTTPS 和桌面主动建立的 WSS 代理连接，云服务器会处理 Web 内容明文。手机仍使用原 8787、Noise 加密、手机身份批准和可选 P2P。网页会话不会变成手机设备，也不会批准手机身份。

## 网页复用

`bridge/api/cloud_web.py` 适配现有固定资源、CSP、manifest 和认证响应，将路径放入 `/d/{gateway_id}/`。`web/hosts/cloud-web.js` 在原网页启动前提供现有 URL 钩子，将草稿与偏好按账号和电脑隔离，并把返回电脑入口接到云目录。同一电脑的多标签页仍能同步偏好，其他电脑的存储事件被过滤。原网站的会话和客户端控制逻辑继续使用。

HTML 与 Mermaid 沙箱仍保持隔离。Mermaid 壳通过随机 CSP nonce 内嵌桌面的两个固定脚本，避免跨沙箱子资源 Cookie 缺失；用户提供的预览内容不参与脚本适配。终端采用已有的 HTTP 轮询，聊天支持 SSE。

`bridge/features/network/cloud_web.py` 使用注入的私有回环监听器与 API 回调，禁止导入 API 层或接受任意本地地址。按 Web 会话创建临时内部授权；退出、换账号、撤销、到期和断线都会清理。会话内请求不改变原来的 provider、host、owner、账号或工作目录；读取不启动编码会话；未知结果的写入不重放。

## 验证与部署

Python `test_cloud_web.py` 覆盖实际私有 HTTP、通用方法与正文透传、业务鉴权、实时资源版本和会话清理。接口是否存在和是否允许执行统一由网关 HTTP 处理器判断，各通道不维护接口白名单或黑名单。`tests/browser/cloud-web.spec.cjs` 使用真实 Go/Python 代理及生产网页，在 Chromium/WebKit 的宽、窄视口验证登录、资源、图表、HTML 预览、PDF 模块、跨电脑和多标签存储、退出。

先启动服务端仓库的 `internal/webproxy/testdata/fixture.go`（需隔离 PostgreSQL），再运行 `python -B tests/browser/cloud-web-fixture.py <fixture.json>` 和 `playwright test -c tests/browser/cloud-web.config.cjs`。这些测试使用合成账号和客户端列表，不代表生产或真实编码客户端已验收。

服务端仓库 `docs/web-client.md` 包含 18787 Compose/Caddy 配置、已有 8787 证书的保留方式与回退步骤。必须同时升级桌面和服务端；仅发布云端不能使旧桌面具备 Web 透传能力。

18787 已上线；总开关加入前，现有云账号已在 Edge 验证真实聊天列表和当前聊天读取。新版需由用户在桌面主动开启网页权限后连接，其他电脑也需分别更新。生产验收不发送消息、审批或写入用户文件，也不代表手机真机已回归。

决定和替代方案见 [云账号网站记录](../.agents/notes/implemented/feature/2026-11-07-cloud-web-proxy.md)与[网页授权记录](../.agents/notes/implemented/feature/2026-11-08-cloud-web-access-switch.md)。
