# Agent Note: Tailscale 入口接管与健康恢复

Status: implemented

## 问题

Tailscale 固定入口在先前连接成功后可能突然失效并反复恢复。现场同时出现两种状态：

- `tailscale funnel status` 仍显示目标入口已启用。
- 当前网关的 `ssh-status-*.json` 却是 `blocked`，且系统中没有持有该入口的 `tailscale.exe` 子进程。

旧实现把所有同端口 Web 规则都视为其他服务。即使规则精确指向当前网关，新网关也会在 `port_in_use` 后直接退出，不再重试或监控。于是流量实际依赖 tailscaled 中遗留的旧规则；规则一旦消失，网关不会重新建立入口。

## 决策

`route_state` 区分 `none`、`same`、`other`。只有入口主机、端口、目标代理、访问范围（Funnel/Serve）都匹配时才允许接管。目标代理不是当前私有监听端口时，必须通过当前实例的 `/api/health` 验证目标确实是本网关；其他同主机或同端口配置仍保持 `blocked`，不覆盖。

接管旧规则后每 10 秒检查一次 Tailscale 身份和 Serve 状态；连续读取失败或入口消失会回到启动流程。前台会话现在由独立 stdout reader 和健康循环共同管理，进程仍存活但入口失效时也会重建。网络监听到默认路由变化时，同时唤醒 Tailscale 与 Quick Tunnel 的恢复检查。

关闭网关时，`gateway-control.json` 的删除增加短重试。Windows 扫描器或并发读取者造成的短暂 `WinError 32` 不再让整个进程退出；无法删除时保留诊断文件，交给下一次启动覆盖。

HTTP 长轮询或静态响应期间客户端主动中断产生的 `ConnectionAbortedError` 视为正常断开，不再记录为网关操作失败。

## 影响

- 精确匹配且可验证归属的遗留入口会被接管并持续监控。
- 其他服务、不同目标、不同 Funnel/Serve 范围仍不会被覆盖。
- 被接管的非前台入口不会在本次关闭时主动删除，避免清理用户或旧实例的配置；下次启动可以继续验证并接管。
- 该修复不更改网关认证、Origin、CSRF、Tailscale 登录状态或用户 tailnet 配置。

## 验证

- 使用现场真实 `tailscale serve status --json` 验证旧入口状态为 `same`，目标主监听端口通过当前实例健康检查确认。
- 定向测试覆盖精确接管、不同目标拒绝、Funnel/Serve 范围拒绝、无子进程监控和 `gateway-control.json` 短暂占用重试。
- Tailscale、网关状态和网络适配共 45 项通过。
- 完整后端源码回归执行 2897 项；本轮相关测试全部通过。其余失败为当前 Windows 会话缺少符号链接权限、既有 SSH 回环夹具抖动和既有 403/500 断言，不涉及本轮改动。

本轮只构建 Windows 包，不安装或替换现有应用。
