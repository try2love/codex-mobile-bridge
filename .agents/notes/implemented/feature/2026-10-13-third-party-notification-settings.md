# Agent Note: 手机端第三方通知配置

Status: implemented

## 问题

手机端原先只能配置 PushPlus，Bark 和 ntfy 需要回到电脑端。三个通道实际共用电脑网关的通知配置与发送器，因此手机端缺少入口会让同一份设置出现不同的操作能力。

## 决策

手机网页和 App 的「第三方通知」列出 Bark、ntfy、PushPlus，各通道详情只编辑自己的开关、服务地址、主题或密钥。通用区编辑 `includeTitle`、`clickBase`、`securityEnabled`、`addressEnabled` 和 `addressName`；全会话请求与完成通知继续由现有设置控制，原生手机通知权限与收件箱保持独立。

HTTP 在已认证与 CSRF 校验之后，通过 `/api/notifications/channels/{bark,ntfy,pushplus}` 和 `/api/notifications/channels/common` 分别限制可写字段。它复用 `bridge/features/notifications/channels.py` 的持久化、校验和发送器，读取密钥只返回是否已保存。留空密钥沿用原配置；更换 Bark 或 ntfy 服务地址时清除旧密钥。`/test` 只发送该通道已经保存的配置，只有用户按下测试按钮才会发送。旧 `/api/notifications/pushplus` 路由继续可用。

通用区读取成功之前不能保存；关闭、重新打开或进入通道详情后，旧读取结果不得回填当前表单。服务列表显示未配置、已关闭、已开启或读取失败，便于判断下一步操作。

网关未保存的手机收件箱与聊天标题字段默认开启，已保存的关闭值保持不变；默认值和系统卡片边界见[手机通知按事件独立保留](../bug-fix/2026-11-08-independent-mobile-notification-events.md)。

## 备选方案

可以在手机端维护另一份通知配置，但会产生与桌面网关状态冲突的密钥和开关。也可以直接开放桌面设置快照，然而其中包含更多与手机端无关的网关选项，扩大可写范围。按通道与通用字段分开限制，更贴近现有权限边界。

## 影响

手机端与电脑端修改同一份网关配置；所有已登录设备可修改这些通道。通道测试使用现有外部发送器，页面确认服务接受请求不等于手机已收到。自动入口通知仍依赖网关运行和原有入口发现逻辑。回归测试用本地模拟响应和受控 loopback HTTP，未发送真实第三方通知。
