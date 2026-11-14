# Agent Note: 原生共享初始化的任务范围与核验期限

Status: implemented

## 问题

共享连接初始化只改变原 App 的运行时启动方式，却复用账号切换的通用任务阻断，独立 native CLI 的活动任务也会阻止初始化。另一路报错来自任务核验：默认配置的历史元数据形成 601 个候选，原实现逐个发现 owner，再等待快照。20 秒被剩余候选数分割后，首个请求只约有 16.6 毫秒，IPC 实际等待仍额外增加一秒。静默的历史候选会拖住后面的真实任务，最后报“仍有会话未检查”。

## 决策

初始化单独使用 Desktop 生命周期范围。native CLI 路由标记、存活 proxy、同 home 握手和 joined token 只标识本轮待核验候选，不证明原 App 没有 owner。缓存中的 `desktop_owner_probe` 无论是 `absent`、`unknown`、空值或 `found` 均不能授予豁免：旧读取超时也可能被记为 `absent`。这些候选即使不在历史集合，也必须进入本轮真实 owner 发现；只有精确 `no-client-found`、查询前后 proxy 与 token 不变，且结束前再次查询仍满足同一条件，才允许在最终任务判断中排除。Desktop owner、发现超时、断连、未知或变化的 proxy、配置不匹配仍保留保护。队列及未知提交缺少持久化的执行归属证据，继续阻断；账号切换的认证、provider 和配置保护不收窄。

任务核验保留原有 Desktop 历史候选、attached/subagent 后代、App 相关远端和已知 Desktop 实时会话，最多并行核验八项，不把网关当前已跟随的会话当成全部会话。每项仍先发现真实 owner，再被动订阅并检查实时快照；忙碌状态、待确认请求、进行中的回合、owner 断连和修订不匹配保持原判断。完成时再次检查全部已取得的状态与提交记录。

IPC 的连接、初始化和 owner 读取共享绝对 deadline。握手也放入核验工作线程，协调线程不会同步卡在 Unix `sendall` 或 Windows 写入等待中。带期限的读取扣除发送耗时，不附加普通请求的一秒回执宽限；正常写请求保留原等待和结果未知语义，不重放。超时或提前失败时关闭本次临时 follower 并唤醒等待者，不等待静默任务逐个结束。`close` 的连接世代同时阻止迟到的 socket 或初始化响应重新发布连接；原有控制连接与原 owner 不受影响。

## 备选方案

只检查 `gateway.live` 可以绕过历史规模，但会漏掉仅在原 App 中运行的任务。当前 Desktop follower 协议没有已验证的完整 loaded-thread 枚举，也没有附属聊天 fork；原 App 的内部 `thread/loaded/list` 和 `thread/fork` 不等同于外部 follower 接口，因此不借此宣称可以免初始化。

统一延长超时或豁免所有 CLI 的改动更少，但前者仍然逐个累积静默等待，后者会漏掉由 App 持有的 CLI 来源会话和归属未知的请求。保持原覆盖并有界并行，只有已证实独立的 native CLI 才排除。

## 验证

新增回归先复现独立 native CLI 被拦、601 个候选中首项静默挡住尾部 busy、静默请求越过总期限，再通过同一用例。生产 `assert_desktop_idle` 路径另覆盖 CLI 与历史候选重合、CLI 不在历史集合、不同缓存 probe 状态、本轮发现超时、结束前第二次发现失败，以及查询中的 token 变化。夹具保留最多八个在途请求、各次提交复核、Desktop owner、proxy 更换、home 不匹配和未知提交阻断。IPC 用可控时钟核对发送耗时扣减、过期不发送和默认写回执宽限；阻塞握手、迟到 connect 和迟到 initialize 响应不能越过核验结束后恢复控制。

复审修正后，`PYTHONPATH=tests python -B -m unittest test_desktop_runtime_accounts test_desktop_idle test_ipc_deadline` 的 59 项定向测试通过。第一版的账号、压缩、会话管理、共享 runtime、客户端生命周期及大帧 IPC 扩展基线曾共通过 260 项；本次只重跑直接受修正影响的 59 项，不将旧基线计作这些新增缺口的再次验证。所有新夹具使用合成状态，无正式 App 连接、模型请求、真实 fork 或 App 重启。

## 影响

独立 native CLI 的任务可在初始化时继续，账号切换仍保留原保护。任务核验增加最多八个短生命周期工作线程和同时订阅，但不新增常驻轮询；海量历史或无法确认的状态仍可能按期限安全拒绝，不能将超时视为空闲。

本改动不改变共享运行时拓扑、原 owner/provider/auth/cwd，也不提供首次连接的无重启迁移。首次启用共享方式仍须用户确认并在受影响任务结束后正常重启原 App 一次；真实主 App 和跨平台验收不由这些源码夹具替代。

## 历史笔记核对

[共享运行时数据边界](../architecture/2026-11-08-codex-shared-runtime-data-deletion.md)部分重叠：保留透明适配器、首次重启、写入事务和原生数据验收，只细化初始化任务范围与核验调度。[共享运行时提案](../../proposed/architecture/2026-11-08-codex-shared-desktop-runtime.md)保留其标签同步限制，不将本次核验修复视为已实现标签同步。[CLI 原 owner](2026-10-14-cli-existing-owner.md)继续要求按实际归属而非来源路由；本次不改其接续规则。账号切换与本次初始化不同，通用 CLI 保护继续有效。
