# Agent Note: CLI 释放回执、原生占用与明确确认的恢复

Status: implemented

## 问题

等待释放的连接不能升级为停止释放；任何读取异常都成为永久阻挡的 unknown。原生已确认退订后，最后一次读取失败还会丢失退订事实。成功 resume 后的 provider 校验拒绝也可能留下没有管理入口的订阅。弹窗期间提示被顶层模态遮挡，慢请求还会禁用关闭按钮。

## 决策

等待操作可通过新 ID 明确升级为停止释放。worker 在操作锁内先将 waiting 改为 releasing 并保存，再调用适配器；仅仍 waiting 的旧操作可被 superseded，新 worker 不与旧 worker 重复派发。只读预检失败为 failed/notSent，可显式重试；发生过原生写入的未知结果继续禁止重放。

退订确认与原生卸载分别记录。最后读取失败保留 detached/unsubscribed，停止自动接入；check 仅核验原线程、原连接世代，不重复停止、退订或 resume。成功 resume 后校验或初始化失败时，清理同一连接对原线程的订阅；未确认清理的连接保持可管理、禁止发送及再次恢复，不关闭其他线程共用的代理。

普通释放不退出共享 daemon。detached 回执在后端提供 canUnload 时显示独立的“释放原生占用”确认，使用 unload 操作；它退出整个配置的共享 CLI 服务，要求所有任务结束且其他客户端在操作期间不发起任务。后端仍核验全部已加载线程、审批、Goal、后台终端和未知/排队操作，只对同一代进程发送一次 SIGHUP。仅精确识别且已验证的 Codex 0.164.0 本机 macOS/Linux 运行时提供此能力。Windows、SSH 和 WSL 不借用宿主信号实现。

继续会话仅自动检测失效代理和配置/凭据变化，不自动退出或重启 daemon。配置基线由已核验进程的出生时间与文件 mtime/ctime 建立，不以 Bridge 启动时的 hash 冒充原生已绑定配置；环境仍来自原进程的受限读取。用户明确确认网络恢复后才重新建立服务，保留原 home、provider、凭据环境和 cwd。真正的 provider 不匹配继续明确拒绝迁移。

共享服务退出/启动的阶段与代际摘要保存到 Bridge 私有回执。排空超时不重复发信号，启动结果未知不重放；后续只能先核验。原服务环境仅留内存，不把密钥保存进恢复回执；Bridge 重启后无法重核环境时要求在电脑正常启动原配置。

CLI 面板初次读取与提交期间始终可关闭，关闭仅关闭视图，迟到响应不重新打开面板。网络诊断进度和结果显示在当前菜单；共用 toast 在活动 modal 内呈现，使其他功能也能正常显示提示。

## 原生证据与边界

官方 [0.164.0 配置 schema](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/core/config.schema.json) 与实现说明默认卸载延迟为 1800 秒，按线程加载时解析。退订后仍 idle 的默认配置线程，在独立执行器 resume 时仍报 active writer，不能把 detached 称为已接管。

隔离 managed daemon、独立 CODEX_HOME、固定回环响应和真实 rollout 验证：普通 Bridge 释放保留 daemon 且返回 detached；单独确认原生占用释放后，原进程正常退出，第二个独立 app-server 以相同 thread ID、provider 和 cwd 恢复并完成下一轮回环响应，另一空闲线程仍保存。此证据不代表操作过正式 App 或完成手机真机验收。

官方 [0.164.0 shutdown 实现](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/app-server/src/lib.rs) 在 SIGHUP 后拒绝新 turn、等待已接纳的 turn；重复 SIGHUP 不升级强制退出。隔离竞态验证这一行为，但也证明：已接纳 turn 可在排空期间创建后台终端，turn 完成后服务退出会清理该后台任务。因此预检不能保证外部客户端继续操作时后台绝对安全，恢复和原生占用释放必须单独明确确认并保持其他客户端静默，不提供全自动共享服务退出。

本地证据位于 `.tmp/feedback-followup-20261110/cli-native/`：`product-handoff-result.json` 为最终显式维护路径；`safety-result.json` 和 `race-result.json` 保留保护范围及反例。真实模型、正式客户端和用户会话未参与。

## 备选方案

单线程加载时设置 thread_unload_delay_secs=0 能在退订后迅速解除 writer，隔离执行器接管也成功；但真实测试发现：活跃 turn 和待审批可保留，已经完成 turn 的后台终端会在最后订阅断开后被提前卸载。该方案撤回，不改变线程或全局默认卸载时间。

自动整 daemon 排空可在外部客户端静默时完成真实交接，但已有 turn 在 drain 期间创建后台终端的竞态不能由 Bridge 两遍预检排除，因此自动退出/重启方案撤回。保留单独确认的维护出口及真实 detached 提示。归档再恢复会改动聊天状态，不采用。

## 影响

释放、核验与共享服务维护有独立语义；未知写入继续保持可追溯，普通阅读和连接重建不发模型请求。当前原生限制仍意味着完整自动连接恢复和无条件即时 App 接管没有实现。

定向回归覆盖状态机升级、无写预检、退订确认后读取失败、provider 清理、单独维护确认、出生时间基线、Windows 拒绝信号、排空超时不重放，以及保留运行任务的停止入口。Chrome/WebKit 宽窄屏验证模态通知实际命中可见、GET/POST 挂起可关闭、迟到回包不复活面板及维护确认。原生实测仅覆盖本机 0.164.0；其他系统和真实手机不据此宣称通过。

## 相关记录审计

[CLI 主动释放](../feature/2026-11-01-cli-connection-release.md) 部分重叠：保留默认退订与原生延迟边界，补充显式维护出口和回执核验。[独立 Codex 管理](../feature/2026-11-02-independent-codex-management.md) 部分重叠：保留配置隔离、全部线程任务核验与原环境读取，收窄为明确确认后恢复。其他 proposed 笔记保持不变。
