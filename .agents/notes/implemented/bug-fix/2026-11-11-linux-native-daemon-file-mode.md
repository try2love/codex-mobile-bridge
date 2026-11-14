# Agent Note: Linux 原生 daemon 文件权限兼容

Status: implemented

## 问题

真实 SSH 的隔离 Codex 0.164.0 在当前用户所有、权限 0700 的 home 与 app-server-daemon 目录内，原样创建了 0664 的 daemon.pid 和 daemon.lock。旧 worker 把这两种原生文件与 Bridge 自有回执统一按 022 写权限拒绝，因此首次 inspect 就失败，尚未创建线程或派发信号。只读核验确认 PID、bootId、出生时间、映像、home、managed-daemon 角色和 socket peer 一致；不能修改现场文件权限来把该失败算作通过。

## 决策

仅对原生 PID record 与 daemon.lock 显式启用兼容：已持有的父目录 descriptor 必须是当前用户所有的目录，且权限恰为 0700，文件才可保留 group-write。该私有目录禁止其它用户遍历。文件仍须是当前用户所有的普通实体文件，禁止 world-write、符号链接与多硬链；目录不是 0700 时保持原 022 拒绝。

worker 的 `_native_record` 统一读取 daemon.pid 或原有兼容 app-server.pid，供 inspect 与接线层复用。派发仍取得原生锁，并完整核验 home、用户、boot/出生时间、映像、socket/peer 与持有的 pidfd；本次不改这些身份规则。Bridge 的 control.lock、requests.json 和临时回执不启用兼容，仍严格拒绝 group-write。适配器调用新 helper 的接线修改另属原集成提交。

## 备选方案

修改原生文件权限会改变被测条件，也无法解决其它真实 home 的同类问题。全局放宽文件权限会削弱 Bridge 自有回执信任边界。仅凭 UID 相同忽略目录权限则允许其它组成员经可遍历目录写入原生文件，均不采用。

## 验证

7 项定向合成检查通过，覆盖私有目录内原生 0664 record/lock 的 inspect 与一次派发、公开可遍历目录拒绝、world-write/多硬链拒绝、自有回执 0664 拒绝、原有符号链接/peer/进程身份、原生互斥与旧 PID 字段。进程和信号均 mock；测试 socket 仅在项目临时目录内。初次沙盒禁止 socket bind，获执行权限后同一组检查通过。

首次真实 SSH 失败及原生文件保持原样，证据在本轮 adapter 隔离记录中；本次不将合成通过称作 SSH adapter 全链已通过。正常释放和同 ID 冷恢复须由接线点的后续独立实测确认。

## 影响

本次纠正原生文件和 Bridge 自有持久回执的权限边界，不改用户目录权限、不新增操作、不放宽进程身份、不重发未知意图。

## 相关记录审计

- [Linux 目标端 Codex 生命周期回执](../feature/2026-11-11-linux-codex-lifecycle-worker.md)：保留其身份、锁、持久回执与单次信号合同，仅补原生私有目录内实际文件模式的兼容。
