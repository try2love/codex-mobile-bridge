# Agent Note: CLI 只读观察接入共用通知链路

Status: implemented

## 问题

CLI 来源已经直接使用原生 CLI，但全会话通知发现仍先连接 App IPC，监听也只跟随 App owner。纯 CLI 没有 App 时，已有原生任务的完成、失败与待确认状态无法进入共用通知链路。通知读取不能为此启动 daemon、恢复线程或授予聊天写权限。

## 决策

CLI 通知连接所选主机和 home 的现有原生服务，只调用 initialize、thread/loaded/list 和 thread/read。线程未加载或服务不可用时保持未连接，不执行 start、resume、订阅、导航或模型任务。原生 home、保存的 CLI 来源、cwd 与当前 proxy 代次在只读观察中核验。列表发现不再以 App IPC 在线为前提，首次未知来源的元数据判定仍在原有有界后台探针中完成，不让 SSH 读取阻塞扫描器。

观察状态保存在独立的 cli-observer 中，不替换 live 执行会话；通知对象明确只读，打开网页仍从原执行状态决定能力。最多四次观察并行，同一对象不重复派发；活跃观察间隔五秒，空闲沿用共用通知的退订及元数据再发现规则。取消关注、手动释放 CLI、停用或断连使观察代次失效，迟到结果不能恢复旧状态。退订通知只停止读取，不伪称原生 writer 已释放。

完成与失败沿用原有首次快照基线、终态识别、目的地账本及重连去重。实际 CLI 事件也进入 notification_dirty，已完成的短任务可唤醒休眠观察。原生只读状态的 waitingOnApproval / waitingOnUserInput 转为专供收件箱的通知标识，绑定原生 turn、等待类型及已观察到的等待轮次；同一等待期间追加 item 不改变身份，读到等待结束后再次出现同类等待才开始新轮次。轮次保存在 Bridge 私有的 cli-notification-waits.json 中，重启后仍识别同一可见等待；未知、离线或未加载不作为等待结束的证据。此记录不写原生账本，不包含消息内容或认证资料。

每次异步重读先清除旧连接快照，排队任务保留原观察对象和代次，投递器在实际发送前再次核对该对象仍被关注、代次未变、观察已完成且请求仍存在。释放移除或重新关注后的旧任务也会失效，因此旧请求不会在刷新期间向重试或新目的地派发。原生读取没有提供完整的跨客户端待处理请求 ID，这些标识不伪装成可回答 RPC 请求，也不会因用户后来接入并取得真实 requestId 再提醒同一可见等待。审批操作仍须显式连接原生执行通路并重新核验请求。若等待结束与下一次等待完全发生在两次轮询之间，观察器无法区分，只提供同一可见等待的状态提醒。

通知继续使用同一 Notifications / MobileEvents 及通道投递器，host 保留本机、独立 codex 实例或 SSH 身份；相同 thread ID 不跨作用域去重。不新增默认开启的外部通道，不从错误消息推断付费模型或自动重试。

## 备选方案

自动 resume 能直接取得事件与审批请求，却会激活线程并取得执行订阅，违反只读通知边界。跟随 App owner 可复用原通路，但不能支持无 App 的 CLI。直接解析 rollout 能获得持久历史，却不能可靠证明当前待审批状态；因此选择只读原生状态并复用已有通知策略。

## 验证

新增 14 项回归覆盖独立只读状态、UI 不获写权、离线/未加载/身份变化不恢复、异步来源判定、取消及释放后的迟到回包、未加载观察按期休眠、完成/失败/审批去重，以及同 ID 在本机/独立配置/SSH 的不同收件箱身份。等待轮次覆盖无关 item、同 item 再次等待及 Bridge 重启；刷新覆盖旧请求投递失败重试、新收件箱目标、排队发送前核验，以及发送线程取出任务后释放移除或同对象重新关注的交错。23 项现有共用通知回归通过，队列载荷调整后另有 6 项投递隔离与 10 项终态回归通过，覆盖普通通知路径不受影响。

macOS 使用本机已有 0.164.0 与 0.167.0 完整运行组件、全新隔离 home 和自有回环 Responses fixture 运行实际 daemon。两版实际 schema 的 activeFlags 均只有 waitingOnApproval / waitingOnUserInput；真实审批状态也读回 waitingOnApproval。每版收件箱实际捕获 completion、attention、request、completion，重复读取不重复提醒；observer 仅记录上述三个只读方法、joined 为空。停止观察后原 owner 仍持有线程。共 8 个模型协议 POST 全部进入本机合成服务，外部 CONNECT 被拒绝，既有第三方通知未发送、真实付费模型请求为零。两版自有 daemon 均正常停止；结果位于 `.tmp/feedback-20261111-10/cli-notify-native-result.json`。

评审修正后只用现成 0.167.0 和另一个全新隔离 home 重测审批：持续等待不重复，解除审批后阻塞下一次读取，既有失败通知不在刷新期间重试；实际收件箱为 request、completion。只读方法仍限上述三种、joined 为空，两个模型协议 POST 均进入自有回环 fixture，外部通知与付费请求仍为零，自有 daemon 正常停止。结果位于 `.tmp/feedback-20261111-10/cli-notify-native-approval-result.json`。

## 影响

纯 CLI 可复用全会话通知、每会话策略和手机收件箱，不需要 App 在线。代价是状态轮询延迟、首次历史不补发及原生只读请求身份的粒度限制；不可用的服务不会被通知功能唤起。真实 native 证据的上游为回环 fixture，不等于真实模型、外部推送、Windows/Linux 或远程主机验收。

## 相关记录审计

- [原生终态提醒](../bug-fix/2026-10-28-terminal-attention-notifications.md)：部分重叠，复用终态和恢复去重，补充未显式接入的 CLI 观察。
- [通知投递隔离](../bug-fix/2026-10-28-notification-delivery-isolation.md)：沿用同一扫描、收件箱、发送和目的地账本，不复制通道。
- [聊天通知入口](2026-11-09-chat-notification-entry.md)：入口和策略保持不变，新增 CLI 只读数据来源。
- [来源执行路由](../bug-fix/2026-11-11-codex-source-execution-routing.md)：执行通路与只读边界保持不变。
- 账号、代理、强制恢复和 macOS TCC 提案不在本决定范围内，保持不动。
