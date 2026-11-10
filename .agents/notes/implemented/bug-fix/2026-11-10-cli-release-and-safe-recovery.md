# Agent Note: CLI 释放回执、原生占用与明确确认的恢复

Status: implemented

## 问题

等待释放的连接不能升级为停止释放；任何读取异常都成为永久阻挡的 unknown。原生已确认退订后，最后一次读取失败还会丢失退订事实。成功 resume 后的 provider 校验拒绝也可能留下没有管理入口的订阅。弹窗期间提示被顶层模态遮挡，慢请求还会禁用关闭按钮。

## 决策

等待操作可通过新 ID 明确升级为停止释放。worker 在操作锁内先将 waiting 改为 releasing 并保存，再调用适配器；仅仍 waiting 的旧操作可被 superseded，新 worker 不与旧 worker 重复派发。只读预检失败为 failed/notSent，可显式重试；发生过原生写入的未知结果继续禁止重放。

退订确认与原生卸载分别记录。最后读取失败保留 detached/unsubscribed，停止自动接入；check 仅核验原线程、原连接世代，不重复停止、退订或 resume。成功 resume 后校验或初始化失败时，清理同一连接对原线程的订阅；未确认清理的连接保持可管理、禁止发送及再次恢复，不关闭其他线程共用的代理。

原生 0.164 上游失败后可能长期保留 `systemError`，不能仅因该枚举拒绝正常退订。正常释放与账号空闲检查共用额外终态证据，要求最新轮次已结束且有完成时间，再核对当前状态，并保留后台终端、Goal 与请求保护；只读 check 仍只报告 loaded / detached，不把错误状态当作已经卸载。真实 `.7` 失败现场、原生源码依据、范围限制及回归见[受管 CLI 账号同步](2026-11-10-managed-cli-auth-sync.md)。

普通释放不退出共享 daemon。detached 回执在后端提供 canUnload 时显示独立的“释放原生占用”确认，使用 unload 操作；它退出整个配置的共享 CLI 服务，要求所有任务结束且其他客户端在操作期间不发起任务。后端仍核验全部已加载线程、审批、Goal、后台终端和未知/排队操作，只对同一代进程发送一次 SIGHUP。仅精确识别且已验证的 Codex 0.164.0 本机 macOS/Linux 运行时提供此能力。Windows、SSH 和 WSL 不借用宿主信号实现。

继续会话仅自动检测失效代理和配置/凭据变化，不自动退出或重启 daemon。普通变化与诊断失败不再提前拒绝原生接续：NO_PROXY、本机 provider、存量连接和原生凭据刷新可能使这些变化与当前会话无关；实际 provider、身份和未知发送的原有保护仍生效。已进入 draining/stopped/starting 的服务生命周期仍阻止新操作，并采纳锁内返回的最新 maintenancePending 状态，避免并发维护开始后被旧快照放行。配置基线由已核验进程的出生时间与文件 mtime/ctime 建立，不以 Bridge 启动时的 hash 冒充原生已绑定配置；环境仍来自原进程的受限读取。

macOS 检测到失效旧回环代理且当前系统代理可用时，用户明确确认网络恢复后，才通过官方 config/value/write 将此 CODEX_HOME 的 features.respect_system_proxy 保存为 true，再正常重建服务。预览明确说明设置会持久影响使用该配置的其他客户端今后加载的会话，不修改系统代理；确认摘要包含这项意图，旧版本的确认不能复用。只接受同 home、有效非 profile 用户层的非空版本，用 expectedVersion 比较后单项 replace，不覆盖其他设置；已有 true 不重复写。策略/其他高层限制、版本冲突和写入未知均不继续退出服务。仅旧 sessionFlags 覆盖允许在正常 fresh start 后消除，且必须核验新服务实际启用该值。保留原 home、provider、凭据环境和 cwd；真正的 provider 不匹配继续明确拒绝迁移。Linux 保留启动环境刷新，不保存此开关或宣称动态系统代理跟随；Windows 不新增信号恢复。

[CLI 网络诊断与原生系统代理跟随](2026-11-10-cli-system-proxy-following.md)细化普通继续的诊断边界：旧代理或文件变化本身不再前置拒绝，未核对的维护和未知写入仍阻断。macOS 明确确认的网络恢复可通过原生版本检查只启用该 home 的 respect_system_proxy，确认旧服务退出后仍使用普通 Start，并读取生效值；不提供自动停止共享服务或目标线程的路径。

共享服务退出/启动的阶段与代际摘要保存到 Bridge 私有回执。排空超时不重复发信号，启动结果未知不重放；后续只能先核验。原服务环境仅留内存，不把密钥保存进恢复回执；Bridge 重启后无法重核环境时要求在电脑正常启动原配置。

单项设置派发前先持久化 configuringUnknown，避免进程在发送后、回执落盘前退出导致重放。明确原生拒绝或成功核验才解除该阶段；它只阻止配置重放与共享服务维护，不妨碍仍在运行的原生连接继续。显式检查只读取用户层：确认已保存后解除该回执，要求重新检查和确认恢复；未确认时不重复写入。设置成功后其他核验或排空失败，明确提示“设置已保存，服务尚未恢复”，不声称回滚。新服务已运行但首次配置核验超时，可由下一次显式检查只读核验同 home、新代际和有效开关后完成 starting 回执，不再次 start 或发送退出信号。

CLI 面板初次读取与提交期间始终可关闭，关闭仅关闭视图，迟到响应不重新打开面板。网络诊断进度和结果显示在当前菜单；共用 toast 在活动 modal 内呈现，使其他功能也能正常显示提示。

## 原生证据与边界

官方 [0.164.0 配置 schema](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/core/config.schema.json) 与实现说明默认卸载延迟为 1800 秒，按线程加载时解析。退订后仍 idle 的默认配置线程，在独立执行器 resume 时仍报 active writer，不能把 detached 称为已接管。

隔离 managed daemon、独立 CODEX_HOME、固定回环响应和真实 rollout 验证：普通 Bridge 释放保留 daemon 且返回 detached；单独确认原生占用释放后，原进程正常退出，第二个独立 app-server 以相同 thread ID、provider 和 cwd 恢复并完成下一轮回环响应，另一空闲线程仍保存。此证据不代表操作过正式 App 或完成手机真机验收。

官方 [0.164.0 shutdown 实现](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/lib.rs) 在 SIGHUP 后拒绝新 turn、等待已接纳的 turn；重复 SIGHUP 不升级强制退出。隔离竞态验证这一行为，但也证明：已接纳 turn 可在排空期间创建后台终端，turn 完成后服务退出会清理该后台任务。因此预检不能保证外部客户端继续操作时后台绝对安全，恢复和原生占用释放必须单独明确确认并保持其他客户端静默，不提供全自动共享服务退出。

本地证据位于 `.tmp/feedback-followup-20261110/cli-native/`：`product-handoff-result.json` 为最终显式维护路径；`safety-result.json` 和 `race-result.json` 保留保护范围及反例。真实模型、正式客户端和用户会话未参与。

本次代理跟随的精确 [0.164.0 模型客户端实现](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/core/src/client.rs) 将 HTTP factory 绑定在线程加载时；config 热重载或有订阅的 loaded thread/resume 覆盖不会重建它。独立原生实验证明：改配置后旧线程仍访问旧 endpoint、新线程访问新 endpoint，背景终端不变。loaded 线程先退订再覆盖 resume 会走 shutdown/reload，并可清理预检后另一个客户端新建的后台终端；cold 检查与 resume 也无原子条件接口，因此不采用自动单线程重建。官方 daemon start 的 -c 参数未传给子服务，restart 虽传递却会再次停止可能由其他客户端复活的服务，因此不以 restart 替换安全 start。

[原生 HTTP 路由实现](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/http-client/src/outbound_proxy.rs) 默认 respect_system_proxy=false，system_proxy_fallback 不是模型 POST 的动态恢复接口。启用 respect_system_proxy 后，模型每次新请求按系统代理路由，成功路由缓存 60 秒。隔离 HTTPS CONNECT 实验用同一 daemon、同一 thread，两次关闭旧代理监听后分别由下一代理完成同一逻辑 turn，耗时 75.68/75.78 秒（缓存和原生退避）；另一长 turn 保持 active，背景 processId 始终一致。模型响应明确 Connection: close，目标线程每轮均新 CONNECT；独立长 turn 的存量隧道刻意保留，不能误算目标请求复用了旧代理。旧默认配置的同类故障没有访问新代理，仅产生原生网络重连。

代理实验不修改系统代理或证书：仅 .tmp 中同版本 native 测试副本添加测试 entitlement，代码段 SHA-256 前后相同，并注入其自有进程的系统代理解析输入，全部 endpoint 为合成回环服务。证据等级是原生 0.164.0 代码加隔离代理输入，不是正式 App 原样签名与真实账号网络的验收。`.tmp/cli-network-live-20261110/dynamic-result.json`、`runtime-copy-proof.json` 和 `cleanup-result.json` 记录结果、边界和自建进程/端口退出；`.tmp/feedback-followup-20261110/cli-network-options/config-helper-native-result.json` 验证实际产品 helper 的单项比较写入与普通 fresh start 生效。

## 备选方案

单线程加载时设置 thread_unload_delay_secs=0 能在退订后迅速解除 writer，隔离执行器接管也成功；但真实测试发现：活跃 turn 和待审批可保留，已经完成 turn 的后台终端会在最后订阅断开后被提前卸载。该方案撤回，不改变线程或全局默认卸载时间。

自动整 daemon 排空可在外部客户端静默时完成真实交接，但已有 turn 在 drain 期间创建后台终端的竞态不能由 Bridge 两遍预检排除，因此自动退出/重启方案撤回。保留单独确认的维护出口及真实 detached 提示。归档再恢复会改动聊天状态，不采用。

## 影响

释放、核验与共享服务维护有独立语义；未知写入继续保持可追溯，普通阅读和连接重建不发模型请求。旧 loaded factory 仍需一次明确确认、全部任务空闲且其他客户端静默的迁移；不宣称可在既有任务运行时无损热修复旧服务。macOS 完成迁移后，之后系统代理端口变化由同一原生服务动态跟随，最坏延迟受原生缓存和退避影响；完整自动首次迁移和无条件即时 App 接管仍未实现。

定向回归覆盖状态机升级、无写预检、退订确认后读取失败、provider 清理、单独维护确认、出生时间基线、Windows 拒绝信号、排空超时不重放，以及保留运行任务的停止入口。Chrome/WebKit 宽窄屏验证模态通知实际命中可见、GET/POST 挂起可关闭、迟到回包不复活面板及维护确认。原生实测仅覆盖本机 0.164.0；其他系统和真实手机不据此宣称通过。

## 相关记录审计

[CLI 主动释放](../feature/2026-11-01-cli-connection-release.md) 部分重叠：保留默认退订与原生延迟边界，补充显式维护出口和回执核验。[独立 Codex 管理](../feature/2026-11-02-independent-codex-management.md) 部分重叠：保留配置隔离、全部线程任务核验与原环境读取，收窄为明确确认后恢复。其他 proposed 笔记保持不变。
