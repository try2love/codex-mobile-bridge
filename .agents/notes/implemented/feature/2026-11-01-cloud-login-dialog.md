# Agent Note: 云登录使用独立模态表单

Status: implemented

## Problem

网络设置需要简洁的云摘要，并把登录凭据、服务器选择与桌面总设置表单隔离。

## Decision

面板通过 `Sign In to Cloud` 打开原生 modal。官方服务为首项，新配置默认选择 `https://bridge.waken.cloudns.be:8787`，已有自建 origin 优先；选择自建才显示可编辑 HTTPS 地址。官方 hostname/端口只在内部使用，界面显示名称。

打开、换服或换语言不调用云 API。有效 origin 改变时清除未发密码，保留其他草稿、已保存作用域、公钥和授权。独立 form 使 Enter 只提交一次登录；按钮、Escape、背景关闭都会清密码，等待操作禁止换服和关闭，未知响应不重试。登录回执与当前账号一致才关闭对话框，业务授权与服务资格仍分别判断。静态和动态文案使用共享词典，TLS、精确 origin 和 IPC 白名单不改变。

原帮助抽屉中的注册/找回入口已由[可见账号入口](2026-11-01-cloud-account-entry-forms.md)部分取代；本篇继续持有模态表单与登录边界，并取代[官方服务默认值](2026-11-01-official-cloud-default.md)中的内联地址按钮。

[桌面赞助入口](2026-11-11-official-cloud-sponsor.md)位于模态框外的云摘要中，未登录时跟随官方云选项显示，购买按钮独立打开外链并保留本篇的表单隔离规则。

## Alternatives considered

内联地址框和 `Use Official Cloud` 按钮能直接编辑地址，但不满足简洁摘要与独立登录表单的交互，因此改为模态选择，保留其精确 origin 和草稿隔离规则。

## Consequences

登录与设置提交相互隔离，官方入口更简洁；焦点、关闭和等待状态需要共同维护，重绘不能消费凭据或重放写入。

## Testing

2026-11-01 的 1,322 项 Node、26 项 Edge 宽窄合成回归通过，覆盖选项顺序、密码/服务器隔离、提交、焦点、背景/Escape、未知写入和回执一致性，并检查宽窄截图。当时刷新 Windows 面板验证中文入口，原网关继续运行；未验证 WebKit、真实账号、配对或推送。独立缺依赖问题后来由[运行依赖修复](../bug-fix/2026-11-01-cloud-runtime-dependencies.md)处理。
