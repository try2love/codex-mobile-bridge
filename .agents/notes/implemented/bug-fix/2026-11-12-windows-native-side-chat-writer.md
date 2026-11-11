# Agent Note: Windows 侧边聊天复用原 App 的原生 writer

Status: implemented

## 问题

Windows 原 App 已持有父聊天时，另起元数据运行时会触发 `already has an active writer`。Mac 的共享 Unix transport 不能直接照搬：Windows App 关闭 stdio 子进程时会直接结束适配器，Python 的 `finally` 不保证执行，独立原生服务可能遗留。

## 决策

Windows 默认本机 Desktop 初始化复用现有账号面板、任务核验和正常重启流程。原 App 通过已支持的 CLI 路径配置启动透明适配器；适配器持有原生 stdio 的唯一输入写端。原 App 保留唯一初始化、审批和任务控制权，原始 cwd、账号、provider 与 CLI 参数保持不变。Bridge 仅通过私有命名管道访问既有侧边聊天和元数据操作，不建立第二个 writer。

`DesktopStdio` 将 App 和 Bridge 请求映射为互不冲突的内部 ID，原生输出只读一次。通知与服务端审批请求继续交给 App，Bridge 回包只交给原调用；超时、迟到或未知回包不会被重放。消息容量核验包含 ID 扩展，单条超限只拒绝该请求，不断开原 App。Bridge 白名单仅包含 fork、list、inject_items、name/set、read、backgroundTerminals/list 和 delete。

命名管道复用 `local_rpc` 与 Windows `local_service` 的私有记录、令牌、同用户与桌面会话以及精确进程身份校验。每次调用重新核对 App、适配器、原生进程和配置目录。原生错误保留完整 RPC 结构，继续使用共享错误转换。只有首个业务请求明确尚未发送才允许安全重试；此前已经成功或结果未知的请求不会因后续连接拒绝而整体重放。

适配器退出后，操作系统关闭唯一 native stdin 写端，原生收到 EOF 执行连接和线程清理。正常路径关闭管道并等待原生退出；超时保留运行记录，不强杀。App 直接结束适配器时遗留的记录必须按原生进程出生时间验证；已退出但 PID 仍存在的 Windows 进程句柄不被误判为活动运行时。

初始化在停止 App 前核验冻结启动器以及安装登记的 MSIX package family。启动时恢复原 App 因自定义 CLI 路径而移除的已核验 sandbox family，不接受任意调用者提供的替代值。网关可执行文件路径改变后，需要重新初始化，不能静默使用另一程序。Windows 配置记录放在 home 的专用子目录，避免私有目录权限操作影响整个原配置目录。

创建、权限、幂等账本和成功后的 attached 查询缓存失效继续使用共享业务。Bridge 连接关闭不关闭原 App 或原生运行时，未初始化时明确提示，不回退到独立 writer。

## 备选方案

原生 UDS 加独立 watcher 能复用多客户端协议，但 0.167 第一次 shutdown 进入 drain，等待任务和已接纳请求结束；待审批任务可能一直不结束。再次 shutdown 是强制路径，不能当安全重试。watcher 崩溃也不会给 UDS 服务产生 EOF，因此未采用。

独立 stdio broker 可以继续记录收尾，但本次不需要扩大进程拓扑；适配器自身持有唯一 stdin 已能通过 OS 句柄关闭触发原生清理。原生 follower 未提供 attached fork；修改 App 私有代码、伪造 owner、改历史或用普通 fork 冒充附属关系均不采用。

## 验证

Windows 实机使用原生 `0.167.0-alpha.3.2`、全新隔离 home 和 Node 模拟 App，经过产品初始化转发、私有命名管道和元数据客户端，最终 r5 的 17 项检查通过：创建原父聊天、附属 fork/关系、边界写入、Bridge 断开后 App 保持连接、列表刷新后 App 读取并恢复子聊天、正常 EOF 后原生退出码 0、模拟 App 消失后的原生退出码 0、遗留记录核验和下一代重连。没有使用真实账号、调用模型或修改用户聊天。Windows 外层 Job 下的独立生命周期夹具同样观察到退出码 0；不是仅以 PID 消失判断正常退出。

定向回归覆盖请求 ID、App 审批透传、迟到回包、容量、原生错误、明确未发送与未知写入、启动器预检、Windows 进程出生时间以及默认初始化门。原有 Mac 共享运行时和侧边服务 36 项通过；本次没有改变其 transport。独立复审发现的错误类型丢失、扩展后超限及未发送状态丢失均已修正并补回归。

隔离模拟 App 证据不等于真实 Windows GUI App 或最终冻结程序验收。实际 App 初始化、最终包和双端界面仍需单独验证；当前正在承载开发任务的 Mac 正式 App 没有被重启。

## 影响

Windows 增加一个由原 App 持有的适配器和请求 ID 路由，初始化需要正常重启一次。未初始化不运行适配器，不增加常驻扫描。仅支持已核验的默认本机 Desktop；CLI、SSH、独立配置和 Mac transport 保持原契约。原生 EOF 清理不承诺固定时间完成，超时必须保留真实未知状态。

## 历史笔记核对

[共享运行时与数据删除](../architecture/2026-11-08-codex-shared-runtime-data-deletion.md)部分重叠，保留其共享业务、Mac 实现及历史验收，本篇增加 Windows 的 stdio 所有权方案。[初始化任务核验](2026-11-10-desktop-initialization-task-audit.md)和[核验不可用时明确重试](2026-11-12-desktop-initialization-confirmed-retry.md)仍决定是否允许重启，本篇不扩大退出权限。旧共享运行时提案保留历史对照；未跟踪提案及 CLI 恢复方案不在本次修改范围。
