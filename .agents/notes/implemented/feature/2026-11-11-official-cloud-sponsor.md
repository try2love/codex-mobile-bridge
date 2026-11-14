# Agent Note: 桌面官方云赞助与激活码购买入口

Status: implemented

[购买二维码弹窗与服务端下发](2026-11-11-sponsor-dialog-and-web-address.md)部分取代本篇的外链、双按钮和本地生成购买图决定；[登录后显示赞助支持](2026-11-14-sponsor-after-official-login.md)部分取代未登录时跟随官方云选项显示的决定。以下保留初版取舍和已交付产物证据，精确官方 origin 与手动兑换边界继续有效。

## Problem

桌面云服务已有服务天数兑换入口，但用户无法从面板找到官方云 30 天激活码的购买渠道。购买提示还需要区分官方云与自建服务器，避免把官方激活码误用于其他服务。

## Decision

云服务状态下方、账号区域上方展示“赞助支持”，按钮为“购买官方云服务 30 天激活码（闲鱼）”，说明购买后登录官方云账号并在本页兑换。未登录时根据官方云选项显示，已登录时根据账号的精确官方 origin 显示，与服务是否激活、到期或状态查询失败无关。

按钮通过既有 `bridgeDesktop.open()` 的固定目标 `official-cloud-sponsor` 打开 `https://m.tb.cn/h.jjD7PcA?tk=gr8Q6d3Taty`，主进程继续校验来源并保留完整链接参数。使用系统默认浏览器，失败显示中英文提示；不提交设置、登录或兑换请求。

“手机扫码购买”按钮展开同一链接的二维码，使用既有本地二维码库生成 PNG，经来源校验的 `bridge:sponsor-qr` 返回图片。链接只在 `sponsor.cjs` 定义一份；接口不接受 URL 或凭据。二维码按需生成并保存在当前页面内存，切换语言复用图片，切换自建服务器时收起；显示和折叠不提交云请求。

## 记录归属

检索 proposed、implemented、rejected 中的官方云、云登录、赞助、购买与闲鱼，未发现重复购买方案。[官方云默认值](../../implemented/feature/2026-11-01-official-cloud-default.md)与[云登录对话框](../../implemented/feature/2026-11-01-cloud-login-dialog.md)部分重叠，继续持有精确 origin、选项与表单隔离规则并互链；账号入口表单、持久中继 v2、上游集成记录分别属于注册布局、传输与通知默认值，与购买入口无关，无需替换或归档。

## Alternatives considered

所有云服务场景都显示入口可以让自建用户购买官方服务，但容易让人误以为自建服务器需要购买官方激活码。用户选择仅在官方云场景显示。

## Testing

二维码扩展后，140 项桌面 Node 定向回归通过，实际 PNG 解码得到完整闲鱼链接；新增 IPC 验证拒绝错误界面来源。12 项 Edge Chromium/WebKit 宽窄浏览器场景通过，覆盖展开／收起、切换语言复用图片、自建账号隐藏、外链与二维码不提交云请求或外层表单。

含二维码的 Windows x64 v2.2.0 完整构建产物另存于 `.local/deliveries/cloud-sponsor-qr-desktop-2026-11-11/`，安装版和 ZIP 均通过完整性检查。云面板、二维码模块、主进程、preload、样式和翻译与当前源码逐字节一致；打包程序烟测通过，新增实际 IPC 展开二维码与 PNG 解码验证，沿用隔离测试目录，不打开真实商品页或修改现有安装。

`node --test tests/cloud-desktop.test.cjs tests/cloud-i18n.test.cjs tests/cloud-panel-ui.test.cjs tests/desktop.test.cjs` 的 139 项回归通过。固定目标保留完整 URL，未授权 IPC 来源和直接传入商品 URL 均被拒绝，打开入口不调用网关工作进程。

Playwright 1.64.1 使用仓库已有的 `.local/tools/browser-tests` 隔离工具链，在本机 Edge Chromium 与 WebKit、1280/390 px 视口中执行赞助、服务器选择、语言草稿、兑换和登录布局定向回归，24 项通过。覆盖未登录／自建／官方有效／未激活／过期／资格未知、打开失败翻译、刷新无额外请求、一次点击一次打开、兑换码保留及设置表单零提交；宽窄截图确认按钮换行和布局无溢出。

2026-11-11 按用户追加要求完成 Windows x64 v2.2.0 打包：原生助手自检、P2P release 与完整 PyInstaller 网关构建通过；使用本机 Electron 44.5.1 和 electron-builder 26.15.3 生成 NSIS 安装程序及 ZIP，产物位于 `.local/deliveries/cloud-sponsor-desktop-2026-11-11/`。包内云面板、主进程、样式和共享翻译与本次源码逐字节一致，两份产物通过 7-Zip 完整性检查，SHA-256 保存在同目录。安装程序的 Authenticode 状态为未签名。

`scripts/smoke-windows.cjs` 对本次打包程序使用独立中文测试目录验证成功，覆盖不依赖系统 Python/Node 的网关启停、HTTP 保护、11 项手机双语页面、托盘与二次启动、语言保存、自启及 820×640 排版；实际打包截图可见赞助入口。验收结果保存在交付目录的 `package-verification.json`，未把 NSIS 安装到用户现有安装目录。

## Consequences

官方云用户可从兑换所在面板找到购买渠道，自建服务器用户不受官方购买提示干扰。入口与现有授权和兑换流程独立；固定商品链接可能随闲鱼商品调整而失效，更新链接需随桌面源码交付。

浏览器回归使用合成账号与打开接口替身，主进程回归模拟系统 shell；未验证真实默认浏览器唤起、商品页面加载或购买。Windows 产物已构建并以隔离数据运行，安装事务、公开发布、自动发码和支付未执行。
