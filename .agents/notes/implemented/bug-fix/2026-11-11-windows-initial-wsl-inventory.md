# Agent Note: Windows 首次完整发现包含已运行 WSL

Status: implemented

## 问题

桌面首次发现发送 `inventoryOnly`，共享管理器因此关闭 WSL 扫描；已有本机安装缓存还会在进入 WSL 注册器前返回。停止网关时，已有缓存也让 `loadClients` 直接跳过首次发现。Windows 的 Codex 与 Claude Code 即使已在 WSL 中存在，也只能由用户再次手动扫描才能出现。

## 决策

Windows 默认管理器在本网关进程的首次完整 inventory 中复用既有两个 WSL 注册器一次。普通 `clients` / `clients_status` 不探测；本轮尚未尝试时仅把共享 `inventoryAvailable` 投影为 false，驱动现有 `loadClients → scan(inventoryOnly)` 入口。首次发现发生在本机 inventory 缓存的早返回之前。独立 Desktop 子实例、macOS 和 Linux 不进入此通路。

尝试标记在调用注册器前写入内存。两个注册器分别尝试，错误或异常沿既有 `errors` 形状缓存返回；成功方候选保留。本轮失败后 inventoryAvailable 仍完成这一尝试，不因开页或状态刷新反复探测。用户手动扫描和既有 `scan-wsl` 明确重试，并替换本轮结果。桌面扫描反馈显示真实错误，不把部分失败称为完成，也不移除已返回客户端。

注册器继续只探测正在运行的发行版，并在实际调用前复核。发现只更新内存候选，不登记或启用配置，不启动发行版、CLI 或 daemon，不恢复会话、不改变已有任务。已保存 profiles/enabled 和原连接对象保持；没有运行的发行版时只得到空候选。候选按现有设计在进程内缓存，新管理器的首次完整发现可以再次探测，不增加持久扫描调度。

## 备选方案

每次读取状态都扫描能补全新安装环境，但会持续执行 WSL 子进程，且把展示读取变成发现操作。永久持久化“已经扫描”能减少重启后的读取，却会让仅存在内存的候选在下次运行中消失。只调整 inventoryOnly 参数改动更少，但无法越过已有安装缓存的早返回，也无法使首次开页进入发现。因此保留既有显式 inventory 入口，只加本轮首次门禁和候选缓存状态。

## 验证

真实管理器与两个注册器使用合成 WSL Python 响应，覆盖首次无缓存、已有本机缓存、启动时完整发现、同进程重复 inventory/状态读取、手动重扫、停止发行版、部分失败与异常只尝试一次、不同进程重建候选，以及保存文件字节和活动 owner/任务对象不变。既有显式与内部扫描边界、非 Windows WSL 导入保护通过针对性复验。

真实 `clients_status` 与 scan 响应另接入生产桌面 DOM 的 `loadClients`，核对首次门禁确实触发 inventoryOnly、部分失败仍显示 Codex 候选和错误、普通刷新保留错误、手动重试恢复两个种类。上述先运行于 Mac 的隔离夹具。冻结私有源码包经 Windows 完整 279 成员与 SHA 核验后，另一次真实只读探针在一个已运行发行版中各发现一个 Codex / Claude Code 候选：首次各执行一次配置探针，后续 inventory/status 不再执行，手动重扫各增加一次；错误为零，候选未启用，无登记或原生 owner，运行集合不变。探针 PID12400 正常 exit0，同一 Terra 完成事实轻核。真实的是 WSL 元数据与两个注册器；本机安装 inventory 刻意使用隔离缓存夹具，不代表已安装 CLI 执行、正式 App 或模型端到端验收。

## 影响

Windows 首次完整发现可显示当时运行中的 WSL Codex 和 Claude Code，之后普通开页/刷新复用候选。代价是每个管理器进程增加一次受既有超时和运行状态复核约束的发现；晚些启动的发行版或失败后需由用户手动重扫。现有发行版未运行时不为发现唤醒，不新增 macOS/Linux 的 WSL 选项，也不改变手动登记或目录权限。

## 相关记录审计

- [停用客户端停止后台观测与缓存](2026-11-06-disabled-client-observation-and-cache.md)：部分重叠，保持普通读取不扫描，仅补首次完整 inventory 的 WSL 候选。
- [WSL Codex 统一入口](../architecture/2026-10-24-codex-wsl-unified.md)：部分重叠，沿用发行版、Linux 用户、home 身份与不启动边界，不新增独立页面。
- [SSH 与 WSL CLI 生命周期](../feature/2026-11-11-codex-remote-cli-lifecycle.md)：职责不同，本点不执行接续或生命周期控制。
- 路径示例、手动 WSL 登记、手机绑定和 TCC 提案均不属于本点，保持原样。
