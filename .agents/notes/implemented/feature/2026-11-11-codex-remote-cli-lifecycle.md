# Agent Note: SSH 与 WSL 复用目标端 CLI 生命周期

Status: implemented

## 问题

SSH 的 `codex app-server proxy` 与 WSL 字节 relay 连接的是目标 home 的共享 daemon。退出代理或取消订阅不能证明 writer 已释放。Windows 网关还不能用本机路径、信号或版本门槛解释 Linux 目标。此前目标 Linux worker 已验证原生身份与单次回执，但尚未接入共享范围操作。

## 决策

现有 `CliScope` 注入 SSH/WSL 的目标身份、只读 probe 与退出操作，沿用 `CliConnections` 的范围选择、确认、等待、取消和历史回执，不增加另一套队列或轮询器。稳定 scope 仍按 Codex、注册 host、真实目标 home、UID/user 分组；配置、进程代次与连接 token 进入单独确认摘要。

`LinuxCliControl` 通过既有严格 SSH reader 或所选发行版/用户的 WSL reader，原样加载 [Linux worker](2026-11-11-linux-codex-lifecycle-worker.md)、既有身份 reader 和共享 WebSocket framing。目标端先 inspect，连接同一 canonical socket 并核验 SO_PEERCRED，只读 initialize，再 inspect 确认代次未变。返回实际 home/user/PID/出生时间/映像/socket 与该代的原生版本；配置与认证文件只返回变更摘要。initialize 不可用时，正常退出能力保持未核验，已证明的 force 身份仍可用；版本读取失败不改变物理进程身份，不阻挡新的明确 force。

列表只投影已有缓存，不执行目标扫描或启动 daemon。缓存刷新由明确连接或预览触发。单条及全部范围预览都展示整个 home 的影响，确认前重新核验身份、配置与连接。正常释放复用完整原生闲态检查，以显式 Linux 目标平台和 POSIX home 语义执行；停止/答复原任务仍可使用，新输入不能在等待释放期间静默派发。

取消与最后派发检查沿用原维护锁。远端写入请求开始前持久记录意图，进入该阶段后不再承诺取消。传输或回执未知只 check 原请求和原代，不重新 dispatch；实际退出才显示 released。新的明确 force 可以升级等待或未知状态，取消相应旧队列且保留文字，未知业务记录不清除、不重放。旧回执不关闭或解除新 token 的连接。

SSH 的影响、pending 和队列取消共用同一来源集合：优先使用新提交持久保存的原运行时/home；旧账本通过已核验目标 home 的原生保存元数据判定 CLI、其它来源或未知，不因断连、重启或 origin 缓存缺失漏掉未决项。默认等待对 CLI 与未知来源保守阻挡；force 只直接取消已核验 CLI 的旧 queued key，已经证明的 App 队列不动。

暂不能归属的旧 queued key 留在本次 force 回执，并在原消息记录持久标记暂停派发；正文和 queued 状态保留，未知写入状态不变。既有队列派发前再次核验：后来证明属于 CLI 时只取消这些旧 key，证明为 App 时移除标记并保留原发送路径，仍未知时继续暂停。新明确输入的新 key 不带旧标记，不被旧项无限挡住；暂停旧项仍可通过原取消入口撤销，无需原生身份在线。

force 先记录 draining，再用原 submit_lock 取消或标记已接受队列。新的 CLI 提交在实际写账本的同一短锁内复核维护阶段与已准入的连接 process/token；先入账的请求归入取消集合，后入账的旧请求被拒绝，已 released 的原 token 仍不能追加队列。远端来源读取不持此锁，其间出现的未核验 key 持久暂停；正常退出后新明确连接的新 token 可接受新输入。原任务异步答复与 App 提交保持原路径。

SSH 控制 probe 使用被核验的目标 executable、socket 和 home，冷启动固定已选 `remote_home`；WSL 使用注册发行版、用户、home 与已核验 canonical endpoint。两者都不接入本机默认账号或代理跟随，也不传递网关认证环境。退出后只允许用户再次明确继续原 ID；启动与 native provider admission 保持原生结果，不改历史、不换 ID、不用 force 绕过跨 provider 限制。

原逐条 `_release` 与退订检查保持独立。新增控制核验失败不会禁用旧退订，也不会把 detached 改写成 writer 已退出。Linux 之外的目标在本点没有新增 OS 控制支持，原退订路径仍保留。

## 备选方案

直接终止 SSH/WSL wrapper 能很快断开界面，但目标 writer 仍可能运行。把宿主信号发给目标 PID 则会操作错误进程。复用本机默认账号恢复会把网关配置注入远端，两者均不采用。另写目标端等待队列可以减少 RPC，但会和原网关队列竞争；目标 worker 只保留身份、单次派发与回执，共享业务继续决定等待和明确意图。

## 验证

定向回归覆盖无已安装 bridge 包时的源码注入、Windows 网关的 Linux 版本/路径语义、真实目标 home 的 saved identity 和 SSH 冷启动命令、被动缓存不探测、未知版本保留 force、未知传输只查原回执、等待升级、过期确认不覆盖旧等待、配置漂移结束等待、最后取消、WSL 旧退订不受控制失败影响、未知消息/队列文字保留、新 token 不被旧回执关闭，以及新输入受阻时旧任务 stop 仍可用。

来源集合回归另覆盖断连/重启后的 unknown 阻挡、旧 CLI 队列无 origin 时按保存来源取消且正文保留、App 队列不取消、未归属 key 的暂停标记跨账本重读仍阻止实际派发、新 key 可以继续，以及后续来源核验的 CLI/App 分流与无需原生身份的取消。

两个事件控制的交错回归覆盖来源读取期间新增队列 key，以及已过前置检查但在 force 取消后才尝试入账的提交；后者分别验证 draining 与 released，保留原草稿而不创建账本或业务写入，并确认新连接的新输入仍可入队。目标接线小组共 23 项通过。

目标 worker 的 26 项回归与真实 WSL 五项证据沿用前一独立提交；本点不将该证据当作 adapter 全链已通过。adapter 实机结果按下述 SSH 和 WSL 阶段分别记录，正式账号、88 与固定样本保留到最终包验收。

首轮真实 SSH 在首次 inspect 因原生 0664 PID/lock 文件被拒，未创建线程、turn 或派发控制信号。只读确认同用户 0700 父目录及完整进程/peer 身份后，另以独立修复限定原生文件权限兼容；本点最初读取 PID 也复用该 `_native_record`，不再先走自有回执的严格 reader。现场未修改权限，自有原代随后一次正常退出并独立确认无进程和端点。

首轮真实 WSL 已通过两条 writer 的单/全范围预览与 scopeWait 原代正常退出。后续明确同 ID 冷恢复没有启动新代；只读确认 current 可执行且原生首次启动回执将其报告为 managedCodexPath，但合成 PATH 无 CLI、releases 不存在，原 WSL 候选列表为空。另以独立一行修复补 current 候选，不修改夹具布局。该次冷续的精确 stderr 未被原 helper 保留，不将其归因为 native start 报错；完整冷恢复仍待补测，已过的范围释放结果保留。

修后 r3 的真实 SSH adapter 已完成四项隔离验收：单条/全部预览均展示两个 writer，scopeWait 正常释放原代，用户明确继续后两个原 ID 的 cwd/provider 保持，新代正常收尾。目标为 Ubuntu 的 fresh synthetic home，网关使用私有 `-F` 连接配置和隔离目标环境；不宣称验证了用户默认 SSH 配置。两个空会话、零 turn、零 force；夹具未主动发起模型请求，未据此作进程级零请求证明。原生 0.164.0 经同代 initialize 核验，原代 PID112178/startTicks13368530，冷恢复新代 PID113437/startTicks13382235，boot 均为 26ed67c3-8b7f-4abd-9709-af2d60d2184f。

既有调用自然结束，runner exit0，fixture status=passed、四项检查全 true。末次正常收尾回执为 exited，fixture 内的目标独立观察确认两原代 absent、无自有 native PID、advertised/canonical socket 均不存在。网关记录的 33 个子进程均已 poll 结束，包含失败的后台探测退出码，不能写成全部 exit0。原始证据为 `.tmp/feedback-20261111-10/adapter-native-r3/mac/run-ssh/result.json` 与 runner 回执。

用户随后告知 Windows/Linux 不可达并暂停远端访问。暂停后仅等待既有调用自然结束并读取本地回执，没有新连远端。r3 外层 observer 尚未派发，暂停后的远端现状未回查；这不撤销已经完成的 SSH 四项实测，也不能扩展为暂停后的清理确认。四处 r3 源包已暂存并独立校验，但 Windows→WSL 的 cold-only 补验尚未执行，不能将 staging 当作冷恢复通过。原 WSL 两项通过与首轮失败全部保留，等待用户明确告知恢复后再补冷续及外层观察。精确私有 ELF 回收也未执行，保留报告、配置、回执与同 SHA 恢复副本。恢复位置和最后已知代次见 `.tmp/feedback-20261111-10/adapter-native-r3/paused-remote-checkpoint.md`。

用户明确恢复远端访问后，沿原 r3 冻结脚本补齐外层观察及 Windows→WslEntry cold-only，未重复 SSH 已过链或 WSL scope 释放。SSH 外层 Mac 35 条、Ubuntu 11 条原代均已退出，源包复核一致；WSL 两个原 ID 的 cwd/provider 冷恢复通过，原 PID136594/startTicks3342782 换为 PID192098/startTicks4731725，同一 boot8446978a-775c-495a-8924-683e8fc4fe7c，新代一次正常退出。独立 WSL 7 条及 Windows 54 条记录均确认原代已消失或 PID 已换代，自有进程集合为空、两个 socket 均不存在。确认无占用后精确回收四项本轮私有 ELF 目录项，Ubuntu 空闲由 532295680 增至 840441856 字节，WSL 逻辑空闲增加 308142080 字节；Mac 同 SHA 中转恢复副本重新核验保留。报告、合成配置、回执和首轮失败记录保持，最终证据见 `.tmp/feedback-20261111-10/adapter-native-r3/acceptance.md` 与 `final-summary.json`；本段不宣称正式账号或固定样本验收。

## 影响

已核验的 Linux SSH/WSL 配置可以进入同一范围释放与明确 force 流程。正常 SIGHUP 仍限原生已核验版本，force 不依赖任务或认证配置可读，但不能越过用户、home、映像、出生代次、socket 或持久回执身份。实际跨 provider 准入失败继续显示原生真实结果；本点不提供自动迁移或后台消息重发。

## 相关记录审计

- [Linux 目标端 Codex 生命周期回执](2026-11-11-linux-codex-lifecycle-worker.md)：保持 worker 原语与目标 home 持久去重，补齐传输和范围接线。
- [按本机 CLI 配置释放原生写入者](2026-11-11-cli-scope-writer-release.md)：沿用原范围/确认/取消合同，以目标操作扩展已核验 Linux SSH/WSL，保留本机实现。
- [WSL Codex 统一入口](../architecture/2026-10-24-codex-wsl-unified.md)：保留发行版、用户、home、原会话身份和只读不启动约束。
- [Linux 原生 daemon 文件权限兼容](../bug-fix/2026-11-11-linux-native-daemon-file-mode.md)：独立修复原生私有目录内 0664 record/lock 与自有回执的信任边界。
- [WSL 从 current 原生包冷启动](../bug-fix/2026-11-11-wsl-current-package-cold-start.md)：独立补齐所选 home 的已有原生 current 候选，沿用原启动检查。
