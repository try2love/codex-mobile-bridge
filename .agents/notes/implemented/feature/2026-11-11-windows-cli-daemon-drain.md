# Agent Note: Windows 共享 CLI 正常排空

Status: implemented

## 问题

Windows 原生 daemon 的 `stop` 和 `restart` 先请求排空，到达期限后可能调用 `TerminateProcess`。原账号跟随在 Windows 缺少只等待正常退出的通路；仅凭连接断开或 socket 消失也不能确认共享会话的写入者已退出。

## 决策

Windows 本机受管 CLI 的已核验版本为 `0.167.0-alpha.3.2`。网关沿用[原待闲恢复事务](2026-11-11-cli-recovery-waits-for-idle.md)，只在原接入需要重建或用户明确确认维护时使用该通路。只读历史、通知、远端配置与未核验版本不启用它。服务确认原生 home、进程拥有者 SID、映像、PID 出生时间和受管关闭标记；控制 socket 的系统 peer PID 必须属于同一仍存活的进程句柄。

确认重建或释放时使用同 home 的网关维护锁；Windows 原生排空还使用 [Codex 同一个操作锁](https://github.com/openai/codex/blob/c40729ea5cf8e947481b130c59807273eda2f8e9/codex-rs/app-server-daemon/src/backend/windows.rs#L38-L66)与字节范围，以便和原生 stop/restart/updater 串行。持原生锁完成最后闲态核验，随后在该 home 的私有 Bridge 目录持久化此代已经请求关闭，再对[原生本机 WebSocket](https://github.com/openai/codex/blob/c40729ea5cf8e947481b130c59807273eda2f8e9/codex-rs/app-server-daemon/src/client.rs#L63-L97)最多发送一次 PID。共享 home 但数据目录不同的网关也读同一标记。原生锁被占用、观察到同代 `serverShuttingDown` 或已有同代派发标记时只观察，回执丢失不重发。原生锁持有到观察退出或该轮观察结束；释放锁后才可调用会重新取得该锁的原生 start。

WebSocket 回显只确认收到了请求，旧进程退出由原句柄确认。退出后仍需核对没有另一代原生写入者；Windows PID 文件残留只有在确证对应进程已退出、PID 不存在或已被别的出生代次复用时才可视为过期。通过 FILETIME 和配置文件时间判断网关没有基线时的账号变更。

## 备选方案

直接调用 `daemon stop` 或 `restart` 会引入超时强制兜底，不能用于默认维护。POSIX 的 `SIGHUP` 是原生明确区分的正常排空信号，Windows 没有相同的信号通路；Windows shutdown 文件仅供 updater 使用。原生 socket 的第二个关闭请求会升级为强制，故不能以重试解决未知回执。临时开新 daemon 或只退订会话无法证明原生写入者已解除。

## 验证

140 项定向回归通过，覆盖准确版本、FILETIME、进程和 socket 代次、原生锁占用只观察、已知排空状态、同 home 派发回执、错误或丢失 PID 回显、未确认退出时不启动替代服务，以及 macOS/Linux 既有维护入口。模块边界、打包静态导入和决策笔记检查通过。

Windows 实机的现有 Python 3.11.1 已创建并关闭数值 `AF_UNIX` 套接字。两个隔离尝试都用同一已核验签名的 `0.167.0-alpha.3.2`，均停在原生 `daemon start`。首轮只得到退出码 1；诊断轮证明进程虽使用 session 1 普通令牌，仍在任务计划程序的 Windows Job 内。原生程序明确报告启动器限制后台进程存活并返回 Win32 拒绝访问 5。事后检查时未发现隔离 daemon PID 记录或 socket，没有模型请求，现场确认自有进程均已退出。证据见 `.tmp/feedback-20261111-10/windows-w07-{socket-preflight,r1-result,r2-result}.json`。

在普通桌面启动上下文核验原生 daemon 后，仍须实测 socket peer PID、同 home 同代次关闭回执和持句柄正常退出。上述 Job 启动失败没有进入 Bridge 维护，不能据此判断关闭协议成功或失败，也不使用同一受限启动器反复重试。Windows 实机维护仍待验收。

## 影响

已核验的 Windows 本机 CLI 可以复用账号恢复和共享释放逻辑。Bridge 在原生互斥下发一次请求，超时不调用 OS 强杀。原生协议没有独立的 Windows `GracefulOnly` 事件或原子只读排空查询。正常原生命令在持锁时不会和 Bridge 同时派发；直接绕过原生锁的外部客户端，或发出关闭请求后释放锁但原进程仍在排空的外部崩溃，超出了该互斥所能证明的范围。已有可见排空证据时只观察。未知写入保持待核对，不在重建时重发原工作。

## 相关记录审计

- [CLI 跟随同一原生配置的账号与接入](2026-11-11-cli-follows-native-account.md)：保留同 home 与 provider 准入规则；此处补齐原 note 提及的 Windows 正常排空通路。
- [已确认恢复等待共享任务](2026-11-11-cli-recovery-waits-for-idle.md)：沿用同一个待闲事务，不为 Windows 新建任务队列。
- [CLI 释放与安全恢复](../bug-fix/2026-11-10-cli-release-and-safe-recovery.md)：等待原生写入者退出，保留未知派发证据。后续显式强制与批量释放另行决定。
