# Agent Note: CLI 网络诊断与原生系统代理跟随

Status: implemented

## 问题

原生 CLI 服务继承的回环代理端口失效后，系统代理可能已经可用。将环境变化诊断作为继续会话的前置拒绝，会挡住仍可工作的本地 provider、NO_PROXY、已有连接和原生认证刷新；只更换一次启动环境，又会在代理下次换端口时复发。

## 决策

普通继续会话把环境诊断作为参考，由既有原生执行器裁决接续和请求。旧代理端口失效或凭据文件变化本身不再前置阻断。尚未核对的配置写入只阻止重复配置和服务维护，仍允许既有原生接续；排空、停止和启动的生命周期回执继续阻止新操作。原 owner、provider、auth、cwd 及原生身份校验保持。

macOS 明确确认的失效回环代理恢复增加一项 Codex CLI 用户设置。通过同一原生服务的 `config/read(includeLayers=true)` 取得所选 home 的有效、非 profile user layer version，再以 `config/value/write` 只写 `features.respect_system_proxy=true`，使用 `mergeStrategy=replace` 与 `expectedVersion`。这是持久的 CLI 原生配置，确认文案说明它影响同一 home 后续加载的会话；不修改系统代理、provider、认证或目录。其他 feature 是否配置不影响该项默认值判断。

版本冲突、策略拒绝或配置写入未知时，不开始服务退出，也不自动重放写入。未知写入保留 `configuringUnknown` 回执，显式检查只核验配置，确认已保存后仍须重新确认恢复。`okOverridden` 只证明文件已写：旧 daemon 保存的 false session flag 可在后续普通 fresh Start 清除，其他管理或 profile 覆盖不能绕过。写后再次核验用户值与版本，启动后读取实际生效值。

首次旧服务恢复沿用[明确确认的共享服务维护](2026-11-10-cli-release-and-safe-recovery.md)：核验该配置所有任务与原进程代次，正常排空，确认退出后执行普通 `daemon start`，再核对原线程身份。若外部已经启动新服务，普通 Start 只加入并核验，不追加 Restart。启用后原生新 HTTP 请求按系统代理选路，受原生缓存期限约束；代理后续换端口不触发 Bridge 停止共享服务，也不重发已发送或结果未知的消息。Linux 沿用原确认恢复入口，本次不新增系统代理跟随设置。

## 备选方案

- 自动退出整个 daemon 能刷新环境，但已接纳的 turn 可在排空期间创建后台终端，最终退出会清理该终端。因此保留明确确认，不自动停止共享服务。
- `unsubscribe → resume config` 只重建目标线程，影响面较小；但两次空闲、无后台快照后，另一个合法客户端仍可创建并留下后台终端再退订。原生重建会杀掉该终端，故不采用。跨客户端同线程 RPC 串行不能覆盖这一组请求之间的窗口。
- `daemon start -c features.respect_system_proxy=true` 看似是进程级最小改动，但 0.164.0 CLI 不把参数传给 child。`restart -c` 能生效，却持久化 daemon settings，且可能终止预读后由外部启动的新进程，不能替代普通 Start。
- 热加载配置可保留任务，但既有 ModelClient 的代理策略不变。已订阅线程的 `thread/resume.config` 覆盖也被忽略；均不能作为现有坏连接的无损热更新。

## 验证

精确 Codex 0.164.0 的隔离原生实验确认：版本冲突不写文件；生产 helper 仅保存该项，保留其他 feature；即使旧 saved featureOverrides=false，普通 fresh Start 也会清除旧覆盖并读取用户配置 true。另一个真实快照含多个无关 feature、但未配置 respect_system_proxy，生产 helper 正常启用。两个实验均无模型调用，最终正常停止自有 daemon。helper 的 17 项定向测试及模块边界检查通过。

隔离 HTTPS/CONNECT 夹具使用自有 0.164.0 副本、测试证书、固定模型响应和仅注入测试进程的系统代理解析输入，未修改全局系统代理。测试副本仅调整测试签名以加载解析 shim，`__text` 哈希与原版一致，正式二进制及签名未改。同一线程经历两次代理端口变化，均经新代理产生实际 CONNECT 并完成回复，分别耗时 75.68、75.78 秒；daemon PID、原后台终端 ID 和另一个活跃 turn 保留。夹具与清理回执位于 `.tmp/cli-network-live-20261110/`。这是受控原生网络验证，不等同于正式 App、真实账号、手机或其他平台验收。

原生热加载无效、单线程重建杀后台进程、Start 参数未转发与单项配置 CAS 的证据在 `.tmp/feedback-followup-20261110/cli-network-options/`。产品事务回归由同批 `test_cli_network_recovery.py` 覆盖；不以提交回执或静态读取代替完整模型回复。

## 影响

普通接续保留原生可恢复机会；明确启用后，后续代理端口变化可由原生处理，减少共享服务维护。代价是该 home 的 CLI 用户配置新增持久选项，且首次旧运行时仍受原有排空边界限制。原生成功系统路由缓存为 60 秒，活跃连接另有生命周期，不承诺瞬时切换或无条件无损迁移。

## 相关记录审计

[CLI 释放与恢复修复](2026-11-10-cli-release-and-safe-recovery.md)部分重叠：保留不自动停止共享服务、确认、任务核验和未知回执，只细化普通诊断与明确启用后的原生跟随。[独立 Codex 管理](../feature/2026-11-02-independent-codex-management.md)部分重叠：保留配置隔离与原环境身份，补充后续端口变化不再依赖旧启动变量。[CLI 继续聊天](../feature/2026-10-14-codex-cli-resume.md)部分重叠：普通继续仍不覆盖配置，单项设置只属于明确确认的网络恢复。[上下文与后台控制](../feature/2026-11-01-native-context-and-background-controls.md)部分重叠：保留旧代理故障和活动任务不受干预的历史证据，不把隔离成功改写为原服务已恢复。[CLI 模型目录环境](../feature/2026-10-18-local-cli-daemon-model-environment.md)管理目录端点和凭据读取，与本次系统代理选择无关，保持不变。泛“CLI 恢复”命中的 Desktop、Claude、WSL、归档、通知及初始化记录不改变；现有 proposed TCC 笔记不在本次范围。
