# Agent Note: 官方云作为新配置的默认服务器

Status: implemented

## Problem

新配置需要可直接选择的官方服务，同时不能覆盖已有自建地址、用户草稿或凭据作用域。

## Decision

官方预设使用精确 HTTPS origin `https://bridge.waken.cloudns.be:8787`。已有自建 origin 优先，用户编辑或主动清空的草稿不会被状态/语言刷新覆盖。切换有效 origin 清除未发送密码，不修改已配置账号、使用凭据、启动中继或授权手机；hosted/selfhost 仍由服务器 capability 决定。

原内联地址和 `Use Official Cloud` 按钮只修改草稿，后来由[登录模态框](2026-11-01-cloud-login-dialog.md)取代；预设与精确 origin、证书和不重定向规则继续适用，服务端和手机配置不因预设改变。

[桌面赞助入口](2026-11-11-official-cloud-sponsor.md)沿用官方预设与精确 origin 区分购买入口的显示范围，购买后的服务天数仍通过原兑换流程确认。

## Alternatives considered

无；原记录未保存关于默认值的其他备选方案。

## Consequences

官方入口减少初次地址输入，自建配置继续隔离；预设名称不能作为服务资格或授权依据，切服必须清除未发密码。

## Testing

2026-11-01 的 IPC 校验接受该 HTTPS origin 并拒绝 HTTP，1,321 项 Node 和 16 项 Edge 宽窄合成浏览器回归通过，覆盖地址优先、显式重设、清密码、无被动请求及草稿保留。当时无凭据 `/v1/capabilities` HTTPS 请求返回 200/hosted，不证明登录、配对或推送；未验证 WebKit 和手机。独立缺包问题后来由[运行依赖修复](../bug-fix/2026-11-01-cloud-runtime-dependencies.md)处理。
