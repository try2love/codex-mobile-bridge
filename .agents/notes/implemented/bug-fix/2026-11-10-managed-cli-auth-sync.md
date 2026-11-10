# Agent Note: 受管 CLI 账号同步与未知回执核验

Status: implemented

## 问题

运行中的 Codex 0.164 AuthManager 缓存凭据。仅替换同一 home 的 auth.json，或让独立短命进程验证该文件，不会让现有 CLI 会话采用所选 API 账号。把磁盘匹配或 account/read 的 apiKey 类型当成成功，会在界面中显示新账号却继续使用旧凭据。

## 决策

受管 Codex 账号选择复用 Accounts.switch 的任务确认、请求 ID、账号操作门和同 home 约束。仅在 Desktop 未运行、当前受管 API 配置的 provider 为 bridge_api、URL/model/文件认证定义未变，且没有 env_key、实验 bearer token 或 HTTP header 注入时，允许向该 home 的受管 0.164 服务派发一次官方 account/login/start。其余分支沿用既有明确维护路径，不扩大停止信号范围，也不为 ChatGPT 跨账号复制 token。

已连接但空闲的会话保留连接，不将 joined 等同运行任务。切换前保留本侧连接中、审批、待发送与 unknown 保护，并即时检查所有已加载线程的运行、后台终端及目标状态。用户确认还要求切换完成前其他客户端不发起任务。多个只读快照不是原子排他，不宣称能阻止外部客户端在检查后开新任务。RPC 在 CLI 事件锁外执行，避免 account/updated 通知先于响应时阻塞读取线程。

原生 `systemError` 保留为真实状态，不伪装为 `idle`。账号空闲检查与正常 CLI 释放共用 `clients/codex/release.py` 的终态核验：仅在同一当前连接的握手身份属于已核验 0.164.0 时显式启用，最新原生轮次必须明确 `completed` / `interrupted` / `failed` 且带整数 `completedAt`，随后重新读取同一线程仍为 `systemError`，没有已知待处理请求、活动标志或进行中轮次。缺少轮次/时间、状态变化、已知审批、后台终端、活跃 Goal 或未知任务信息仍拒绝；其他已加载子线程仍逐一检查。账号调用原 `check_archive_runtime` 的显式 `allow_terminal_error` 分支，原身份、请求和轮次校验保留，默认归档规则不变。只读释放核验仅将它识别为仍加载，已确认退订返回 `detached`，不能称 `released`。

此判定来自精确 0.164.0 源码：[ThreadWatchManager](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/thread_status.rs#L174-L181)记录 system error 时清除 running/待审批/待输入计数，状态计算先考虑 [active 标志](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/thread_status.rs#L447-L468)，错误标记保留到下一轮次。但 [Error 事件](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/bespoke_event_handling.rs#L1044-L1066)先于 TurnComplete，因此不能只看 systemError 就允许切换；[TurnComplete 处理](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/bespoke_event_handling.rs#L190-L205)中止该轮次的待处理服务端请求并完成终态。官方 [Thread DTO](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs#L204-L294)没有 `requests` 字段：缺失字段不作为“审批列表为空”的证据，必须依赖上述状态及带完成时间的原生轮次。StreamError 的重试通知不等同终态。

派发前保存 authUnknown 回执和请求账本，绑定目标账号、服务代次、配置摘要及保存凭据文件的 stat 版本；回执不保存凭据或其派生摘要。重复请求只允许核验同一回执，绝不再次 login。getAuthStatus(includeToken=true, refreshToken=false) 返回该代 AuthManager 的缓存凭据，API 仅在进程内与已选保存行比较；不写入日志、回执或前端。只有缓存、磁盘、目标定义和代次均核验后才标记 active。成功后同步 CLI 的认证/配置基线，避免下一次网络检查误把已完成热同步当作新维护；该基线仅使用本次已核验的配置摘要和认证原始字节摘要，不吸收其后的并发文件变化。未知阶段即使磁盘已变也不显示已激活；统一账号清单仅为原目标提供“核验账号同步”，复用原 requestId。

恢复目标只读解析复用当前 activeId/activeStamp。保存记录与当前 home 不匹配时返回未选择，交由明确的“按当前 home 原生配置”确认处理，不复用旧账号密钥。已匹配的受管接入若有来源冲突则拒绝。受管文件认证的后续启动显式清除旧 OPENAI_API_KEY/OPENAI_BASE_URL；不猜测终端新环境。provider 迁移和外部来源采纳由 CLI 恢复事务另行绑定目标，不放宽普通会话身份检查。

## 验证

macOS 上官方 0.164 隔离 home 与合成本机 Responses 服务验证：login A 后缓存为 A；login B 后同 PID 的缓存及原线程真实请求均为 B；只改磁盘为 C 后，只读缓存和真实请求仍为 B。测试服务正常退出，未使用真实账号、上游模型或付费请求。该证据不等于真实 ChatGPT 跨账号登录、其他系统或打包界面已验收。

当前源码网关另完成真实 HTTP 集成 smoke：通过合法登录、CSRF、UUID 与任务确认提交 A→B，响应为 cliAuth/complete/verified；同 PID、同线程的后续合成本机 Responses 请求使用 B 且不再使用 A，同请求回读完成状态且账本只有一条回执。真实 joined A→热切 B 后只读检查 CLI 网络不再误报需要共享维护。流程经过真实 Accounts.switch/LocalCliSync，核心任务检查、缓存读取和原生 RPC 均未 mock。正常 HTTP 的 all/cli 清单能列出原生空闲 seed；测试网关、native、夹具进程和三个回环监听均正常关闭。此项不等同打包产物或浏览器操作验收。

定向 Python 覆盖派发前回执、未知结果不重放、磁盘与缓存不一致、服务换代、保存凭据版本变化、进程中断、任务阻挡、注入冲突及通知先于响应。Node 夹具覆盖共享确认、未知阶段仅原目标可核验、原 requestId 保留和正常完成后释放。后续打包浏览器验收单独记录，不以源码测试代替。

2026-11-11，真实 `.7` 独立 home / native 0.164 的 A 请求因上游 `503/no_available_candidate` 在原生重试后失败：最新 turn=`failed` 且有 `completedAt`，UI 已空闲，原生 `thread/read.status` 仍为 `systemError`。浏览器切 B 被“聊天或子智能体正在运行、等待确认或状态未知”阻挡；同一现场正常释放又显示“尚未释放，可重试 · 无法确认原 CLI 会话状态，未释放连接”。根因是空闲和释放的旧状态白名单均排除 systemError，并非仍有模型任务。只读证据位于 `.tmp/real-cli-browser-20261111/isolated-real-api-a-readonly.json`；本次源码修复不重发 A、不启动或退出该 daemon。新增协议夹具以同一 failed/completedAt 形状，修前复现账号与释放拒绝，修后允许账号预检和一次退订；真实 `.8` 浏览器复验另行记录，不能由源码夹具代替。

## 备选方案

仅更新 auth.json 成本最低，但同代原生缓存实测不变。用 account/read 类型或新独立进程验证能证明磁盘可读，不能证明现有服务采用新 key。自动重启共享 daemon 能重建缓存，但会影响其他客户端及后台任务，不能替代用户确认与维护边界。调用私有 chatgptAuthTokens 虽能导入 token，却不是公开受管账号同步契约，本实现不用。

将 systemError 直接归为 idle 可以消除误阻，却会越过 Error 到 TurnComplete 的窗口，因此采用额外原生终态证据。继续把所有 systemError 当未知可保持保守，却会让上游失败后的账号修正及退订长期不可达，也不采用。

## 影响

同 provider 的受管 API key 轮换可同步活服务，无需重启或迁移历史。无法核验时保留真实未恢复状态；未知回执可能需要用户恢复原服务/配置后再核验。ChatGPT 同账号凭据核验按缓存 token 中的账号和 subject 判断，不按 token 字节判断；真实 ChatGPT 登录未纳入本轮原生验收。

systemError 的额外读取仅用于当前账号维护的既有 all-loaded scope 和显式释放目标；不扩展到未 loaded、其他执行器中的关联子任务。`check_archive_runtime` 本身只校验调用方传入范围，归档/删除的完整生命周期 scope 来自其原有调用方，不能把本次账号检查称为跨执行器完整遍历。未核验版本及未显式启用该终态能力的 WSL 释放入口继续保守拒绝 systemError；idle / active / notLoaded 原有行为不变。

## 相关笔记审计

- [统一本地 Codex 账号](../feature/2026-10-26-unified-codex-accounts.md)：部分重叠，保留单一清单与主动选择；本决定补充活服务缓存同步。
- [本机 CLI 账号同步](../feature/2026-10-14-local-cli-account-sync.md)：部分重叠，保留文件事务、回滚和平台证据；活服务一律阻止的旧限制由本决定在严格 API 条件下补充。
- [CLI 释放与安全恢复](2026-11-10-cli-release-and-safe-recovery.md)：部分重叠，沿用不自动停止共享服务和未知写入不重放。
- [独立 Codex 配置接入](../feature/2026-11-01-independent-codex-home.md)：部分重叠，继续按 home 隔离账号及执行身份。
- proposed 中的 macOS TCC 修复与本次认证缓存无关，保留原提案。
