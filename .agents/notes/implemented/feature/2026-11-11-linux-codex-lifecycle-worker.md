# Agent Note: Linux 目标端 Codex 生命周期回执

Status: implemented

## 问题

SSH 和 WSL Codex 连接控制同一 home 的共享原生 daemon。取消订阅、关闭代理和断开传输之后，原生 writer 可能仍在。网关当前操作系统的 PID 和信号接口不能直接控制目标 Linux；传输超时也不能证明目标没有收到退出动作。已有本机身份与信号实现需要在目标端复用，同时保存可以跨 worker 进程核对的操作回执。

## 决策

`bridge/platforms/linux/codex_cli_control.py` 提供 `inspect`、`dispatch` 和 `check`。目标侧原样复用 `linux/codex_daemon_env.py` 的身份 reader，额外核验 managed-daemon 角色、目标 home、executable 与 socket 的设备/inode、同用户 SO_PEERCRED。适配器负责把同一运行时已核验的版本传给 worker；正常 SIGHUP 目前限于既有 POSIX 已核验表中的 0.164.0 和 0.167.0。force 依靠已核验进程身份与明确操作模式，任务或认证配置不可读不改变其原生身份检查。

worker 在 home 下 `bridge-cli-maintenance/linux` 保存私有回执，持跨进程 flock。一次派发同时取得原生 daemon.lock，持 pidfd 再次核验原代，原子替换并 fsync 意图文件后才发一次 SIGHUP 或明确 SIGKILL。回执尚未更新就断连时保持 unknown，同 requestId 或同进程同模式的新 ID 只核对既有动作。正常排空与明确 force 有不同请求 ID，允许用户在原正常意图仍未知时另行明确 force。原 force 已存在时，后续普通意图观察该动作。

`check` 只观察原代的 bootId、出生时间和 pidfd 退出状态。新的 PID 记录、原生 socket 或重新启动的 daemon 不会成为旧回执的信号目标。worker 不加载线程、不保存任务或队列、不启动服务、不删除 endpoint；影响范围、待闲和恢复继续由共享业务层协调。远端资源清单同时携带既有 Linux reader 与本模块，避免复制原有身份算法。

0.164.0 的 home 入口是指向 `/tmp/codex-daemon-UID` 的一跳符号链接。仅接受原生规则计算的路径：规范化入口父目录后拼接文件名，对原始路径字节取 SHA-256；共享私有目录须为当前用户且权限 0700，目标须为实体私有 socket。代次保存实际 socket 的路径和 inode，每次 inspect 同时重验 home 别名与 peer；外链、多跳、别名变化均拒绝。[原生 Unix socket 布局](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server-transport/src/transport/unix_socket.rs#L307-L348)

## 既有笔记审计

[CLI 明确强制退出](2026-11-11-cli-explicit-force.md)与本次部分重叠，继续负责网关明确意图、预览与共享恢复边界，本次只增加目标端执行和持久回执。[WSL Codex 统一入口](../architecture/2026-10-24-codex-wsl-unified.md)继续保持发行版、用户、home 与原会话身份。本次没有接线 SSH/WSL adapter，也没有替代其写操作门锁。现有 [shared desktop runtime 提案](../../proposed/architecture/2026-11-08-codex-shared-desktop-runtime.md)仍是独立架构讨论，当前 worker 未吸收其方案。

## 备选方案

- 复制原生身份读取到单文件能减少远端注入模块数，但会产生第二套 `/proc` 与 PID record 规则。复用已有 Linux reader，注入侧携带该原样模块。
- 只在网关保存派发记录能复用本机操作账本，但多个网关或传输断连后的 worker 无法共同识别已经发送的信号。目标 home 保存原子回执，并由同 home 文件锁串行化。
- 自动重试超时退出可以减少手动核对，但原生重复 Forceable 动作可能升级强制，PID 也可能已被替代。持 pidfd 单次发送，未知结果查询原代。

## 测试

本地合成回归覆盖目标 home/用户/boot/出生时间/映像、原生角色、socket 权限与 peer、legacy PID 字段、原生一跳别名与 canonical 身份、外链/多跳/路径变更拒绝、进程调度状态变化、同 requestId 与同代重复、明确 force 升级、丢回执、意图持久化失败、并发和原生锁、跨 scope 回执、只读检查。系统进程与信号均 mock，socket 文件位于项目临时目录。该测试不替代目标 Linux 的真实信号与原生 CLI 验收。

首轮隔离 WSL 0.164.0 在首次 inspect 拒绝原生 home 符号链接，尚未派发信号或模型 POST。只读确认入口、私有目录、实际 socket 和同 PID peer 后，以冻结旧 worker 对实际路径派发一次正常 SIGHUP；持 pidfd 和独立观察均确认自有进程、endpoint 已退出，原失败保留。针对该已复现布局补了别名规则和定向红绿；修后真实验收使用另一 fresh home，不覆盖首轮。

修后 22 项 worker 合成回归与 4 项模块边界通过。已运行 WSL 的 0.164.0 原生隔离验收也通过五项：活跃 turn 收到 SIGHUP 后继续排空；调用方完整接收后丢弃回执，独立 worker check 与重复请求只观察；明确 force 通过同代 pidfd 退出；旧回执不影响替代代；替代代一次 SIGHUP 正常退出。两代均由同 runtime initialize 核对 home/版本。只有一个回环合成模型 POST，未出现非预期 POST；代理拒绝了九次 CONNECT，此结果不证明整进程零外发。独立观察确认两代 PID、scope 进程及 advertised/canonical 端点均不存在。完整接收后丢弃回执不等同真实传输中途断线。

官方 0.164.0 的 PID record 显式接受 `linuxProcessIdentity` 旧字段；worker 只在调用同一 reader 前将其规范为当前字段。[原生 PID record](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server-daemon/src/backend/pid.rs#L40-L51)

## 影响

本单点提供 Linux 目标控制原语和至多一次回执。SSH / WSL adapter、作用域 UI、共享业务与账号恢复接口保持当前实现，后续独立接线。回执永久保留未知意图；身份、权限或持久化失败会保留可核对错误，不能据此默认进程空闲。真实 WSL 验收覆盖目标 Linux worker 和原生控制，尚未验证 SSH 传输或网关 adapter 的释放全链路。
