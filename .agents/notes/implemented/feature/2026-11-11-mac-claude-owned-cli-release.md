# Agent Note: 本轮自有的 Mac Claude CLI 退出与明确强制

Status: implemented

## 问题

Claude stdio 执行器是每个会话独占的原生进程。正常 interrupt 与 EOF 可以沿用，但 wrapper 退出不能证明原生 writer 已解除；任务状态读取失败时也不能让用户永远无法明确结束本轮自己创建的 CLI。相反，扫描同名进程或凭历史 PID 退出会误伤其他会话与 PID 的新代次。

## 决策

仅本机 macOS 的明确创建或恢复捕获所选 profile 的目录身份、已解析 executable、原 native ID/cwd，以及实际 Popen PID 的 Mac Native 身份。绑定要求同 UID/ruid、真实映像吻合、非 zombie，内核 birth 与 started 在捕获前后保持。initialize 返回的 pid 若存在必须吻合；未返回 pid 或零 turn 未产生 registry 不否定已证明的直接原生 spawn。catalog 和 registry 不作为凭名称接管外部进程的入口。

正常释放保持 active 等待、明确 stop 的原生 interrupt、最后 stdin EOF，随后检查原内核代次。已发 EOF 的未知结果只检查，不重发 interrupt 或 EOF；Popen 的 exit0 本身不冒充原生退出。缺少内核绑定时正常控制保留，结果明确待核验。

单连接 forcePreview/force/check 复用 `CliConnections` 回调和持久操作账本。预览只读、不读取认证或重新选 profile，确认摘要绑定完整原进程与 spawn 证据。显式 force 可以越过未知任务或审批状态，但仍须再次核验同用户、映像和出生代次，使用既有 Mac audit-token 只向原代派发一次 SIGKILL，不使用进程组或按名称退出。原代已消失或 PID 换代只回退出事实；同代映像等身份变化保持 unknown，不写成 released。

每个随机连接 token 在 Bridge 私有 0700 目录保存一份 0600 原绑定，force 派发前以独占创建和 fsync 持久写入意图。同代已有意图只检查，包含调用返回丢失、信号失败或写入后异常；不凭新的操作 ID 自动再发信号。网关重启只接受原操作保存行的 provider/host/sessionId/token 对应的精确绑定，不扫描进程或目录接管旧 owner。存储不可用时保留内存中的正常退出证明，禁用未持久化的 force。

释放成功只移除原连接 token，旧回调不删除后来恢复的新 owner。`DesktopSessions` 只薄转发本机 Claude 回调；SSH/WSL 与其他系统保持原通路，本点不新增远端强制原语或集合管理器。正常退出不删除原生 registry/history，不换 session ID、账号、provider 或 cwd。

连接列表的只读 `configurationKey` / `configurationLabel` 来自已绑定 profile 的 canonical 目录、UID 与目录 device/inode，跨会话和进程代次稳定，目录身份改变时分组改变。仅展示用途，不是共享 daemon 的 `scopeKey`，不增加 `sharedHome` 或 `_scope_*` 回调。同配置的普通批量释放仍由现有 `CliConnections` 接收明确的 exactKeys 后逐个 `_Owned` 正常退出；force 仍只确认一个进程。元数据不足或来源非本机时不猜同组，不为列表新增文件扫描、profile 选择或认证读取。

## 备选方案

仅凭 stdin wrapper 的退出码实现最小，但不能证明 native writer 已结束。把 registry/catalog 必有字段作为硬门槛可增加会话佐证，却会阻挡已有直接内核绑定的自有进程，因此只接受正向一致性核对，不补发首句取证。按名称扫描便于找回丢失 owner，但不能证明属于本轮明确创建；只恢复保存的精确可信绑定。另建 Claude 释放队列会复制共享业务，本点沿用既有操作与回执。

## 验证

新直接回归覆盖缺 catalog pid、相反 pid、错 UID/ruid/映像、错确认摘要、同 PID 新出生代、同代映像变化、EOF 后只检查、正常 interrupt、错误任务仍可明确 force、持久意图后的未知/重复/重启不重发、私有文件损坏和外链拒绝，以及旧 token 不移除新 owner。与既有 `CliReleaseTests` 共 33 项通过；Claude 合成用例 48 项通过，跳过会调用已安装 CLI 的 effort 用例。模块与平台边界 8 项通过，未增加跨 provider 依赖或例外。需求复审指出未持久绑定读取意图会误伤普通控制后，新增真实 `_Owned.write`/审批响应/control interrupt 回归：存储失败不读意图，正常控制可用；持久绑定的意图不可读仍阻断新写入。

已安装 Mac 2.1.292 的单次隔离 initialize 预检证明 catalog.pid 与直接 Mach-O PID 一致，零 user frame 也产生本轮 sessions registry，EOF 后原代已退出。该预检没有发模型首句、没有信号。

随后新建一个独立 0700 HOME/config/project 的真实 Mach-O 执行器，bare 与白名单环境仅使用合成 key 和回环拒绝端点。通过真实 `_Owned` 单次 initialize 后，仅在夹具中登记其目标行及合成未知任务状态，未调用会发送首句的创建流程。真实 `CliConnections` 默认 wait 返回 unknown、零信号；明确 forcePreview/force 调用一次 audit-token，PID86026、UID501、birth21929933/34122238 的原代退出，Popen 回收为 -9。相同操作 ID 和精确旧绑定恢复后都只回执，原 unknown 历史保留，连接移除。八项检查全通过；另一次独立 Native 观察确认该 native 和夹具父 PID 均 absent，产品/夹具字节与执行 manifest 一致。全程零用户输入、未主动发起模型请求，回环服务未收到 HTTP/POST；此计数不作进程级零外发证明。证据 `.tmp/feedback-20261111-10/mac-claude-owned-force/run1/result.json` 与 `independent-observer.json`；正式 App、账号与用户会话未纳入。

只读配置投影另有三项直接回归，覆盖同 profile 跨 native/token/出生代稳定、不同目录身份分离、缺字段与远端来源不补猜值，以及同组两个独占进程经已有 exactKeys wait 分别退出、其它 profile 的连接保持。该增量没有重跑真实 force 或共享 daemon 原语。

## 影响

用户可以在安全正常释放之外，明确结束一个已证明为本轮自有的 Mac Claude CLI，并核验原代真正退出。未知结果保留、不自动重放；无法证明的旧连接继续如实显示能力不可用，不通过扫描扩大权限。持久绑定与意图保留在 Bridge 数据目录，带来少量文件存储；本点不宣称 SSH/WSL/Windows/Linux 已获得同样原生控制能力。

## 相关记录审计

- [主动释放 CLI 连接](2026-11-01-cli-connection-release.md)：部分重叠，保留正常 EOF 与明确激活，只补本轮 Mac 内核退出证明及单条明确 force。
- [CLI 释放与安全恢复](../bug-fix/2026-11-10-cli-release-and-safe-recovery.md)：部分重叠，沿用未知结果不重放、原身份与真实退出语义，不改变 Codex 共享 daemon。
- [显式强制退出 CLI 服务](2026-11-11-cli-explicit-force.md)：复用共享确认/操作回执与 Mac Native audit-token，Claude 独占进程不伪装成共享 home 服务。
- [Claude 会话账号边界](../bug-fix/2026-11-02-claude-session-account-boundary.md)：保留原 profile/session/cwd 选择，本点退出不重新选择账号。
- 其他 proposed TCC、远端目录预检及手机绑定记录与本点无关，保持原样。
