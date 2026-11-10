# Agent Note: CLI 网络诊断与原生系统代理跟随

Status: implemented

## 问题

原生 CLI 服务继承的回环代理端口失效后，系统代理可能已经可用。将环境变化诊断作为继续会话的前置拒绝，会挡住仍可工作的本地 provider、NO_PROXY、已有连接和原生认证刷新；只更换一次启动环境，又会在代理下次换端口时复发。

## 决策

普通继续会话把环境诊断作为参考，由既有原生执行器裁决接续和请求。旧代理端口失效或凭据文件变化本身不再前置阻断。尚未核对的配置写入只阻止重复配置和服务维护，仍允许既有原生接续；排空、停止和启动的生命周期回执继续阻止新操作。原 owner、provider、auth、cwd 及原生身份校验保持。

macOS 明确确认的失效回环代理恢复增加一项 Codex CLI 用户设置。通过同一原生服务的 `config/read(includeLayers=true)` 取得所选 home 的有效、非 profile user layer version，再以 `config/value/write` 只写 `features.respect_system_proxy=true`，使用 `mergeStrategy=replace` 与 `expectedVersion`。这是持久的 CLI 原生配置，确认文案说明它影响同一 home 后续加载的会话；不修改系统代理、provider、认证或目录。其他 feature 是否配置不影响该项默认值判断。

版本冲突、策略拒绝或配置写入未知时，不开始服务退出，也不自动重放写入。未知写入保留 `configuringUnknown` 回执，显式检查只核验配置，确认已保存后仍须重新确认恢复。`okOverridden` 只证明文件已写：旧 daemon 保存的 false session flag 可在后续普通 fresh Start 清除，其他管理或 profile 覆盖不能绕过。写后再次核验用户值与版本，启动后读取实际生效值。

首次旧服务恢复沿用[明确确认的共享服务维护](2026-11-10-cli-release-and-safe-recovery.md)：核验该配置所有任务与原进程代次，正常排空，确认退出后执行普通 `daemon start`，再核对原线程身份。若外部已经启动新服务，普通 Start 只加入并核验，不追加 Restart。启用后原生新 HTTP 请求按系统代理选路，受原生缓存期限约束；代理后续换端口不触发 Bridge 停止共享服务，也不重发已发送或结果未知的消息。Linux 沿用原确认恢复入口，本次不新增系统代理跟随设置。

## 账号、provider 与注入定义的明确恢复

本轮扩展继续复用上述确认与回执。受管接入先核验同 home 当前选择、保存凭据来源与配置；旧 activeId 的文件已被原 App/CLI 改变时，明确回退为“按此 home 当前原生配置继续”，不借旧保存账号授权。外部采用路径不改 auth/config，绑定原 id/cwd/provider、当前目标、文件身份和原生配置层版本；keyring/auto 不能仅凭 auth.json 充当已核验新账号，保持准确的能力限制。

openai/bridge_api 仍明确映射所选同一接入时保留旧 provider。确需变更时只给本次目标线程传 modelProvider，保留 id、历史、cwd、模型与权限的其他原生语义；普通 attach 不允许覆盖。恢复在已确认维护后的 cold 状态执行，loaded 会被拒绝，不自动退订重建。notLoaded 检查与 resume 仍不是跨客户端原子操作，其他客户端必须在明确恢复期间保持静默；不能将这个路径变成普通自动重连。

目标环境仅来自已选择接入或同 home 原生进程中目标明示的变量。清除旧通用 API key/base URL 与已知旧绑定，覆盖目标所需变量；缺新 env_key 或 header 变量时准确报缺来源，不猜网关或新终端 shell，也不拿旧 key 兜底。配置读取不证明 loaded 的 factory 已更新，注入定义变化仍需这一次维护。

resuming 在实际 resume 派发前持久化。只读核对要求同 home、代际、配置、目标摘要与 id/cwd；同 provider 必须见证先 cold 后当前 loaded，不能把写前回执等同执行。A→B 已持久为 B 时，即使当前 notLoaded 也只解除维护阻挡；后续仅普通无 override 接续。核对均保留原 resume ACK 为 unknown，不授予 factory 来源证明。connected 早返回但未派发时不会报目标已完成。

只有本 Bridge 在上述明确 quiet 恢复后实际 cold 接续成功、且订阅/metadata 仍属于同一原生代际时，才给该线程记无密钥的 file-auth factory 标记。此时 API-file 与 getAuthStatus 缓存可在内存比较，证明不符才阻新 send，保留阅读/停止。release、卸载或未验证的重新 attach 失去标记。来源未知的外部 loaded 线程不能凭 current config 得到该标记；也不以 ChatGPT token 字节变化误判正常同账户刷新。

原生 userAgent 前缀由首次 initialize 的 client_name 决定，并不代表可执行程序换版。真实连续恢复实验中，第一次产品核验连接锁定为 `codex-mobile-bridge-network-verify/0.164.0 (Mac OS 15.6.1; arm64) dumb (codex-tui; 1)`，仅认 codex-tui 会误拒第二次恢复及账号同步。因此共享版本判定仅增加当前代码已有的 Bridge client_name，仍精确限制 0.164.0，并继续核验 home/进程；未来版本和任意新后缀不据此放行。账号 `_api_runtime` 复用这一判定。

另一项原生字段差异是 ThreadResumeResponse 顶层 modelProvider 表示当前 SessionConfigured，nested thread 与后续 thread/read 却可能长期保留旧历史摘要，短等待不能纠正。首次明确迁移以预原身份、明确目标及已确认顶层实际 provider 为依据；当前连接只接受这个唯一历史旧值，输出实际 runtime 身份，第三 provider/id/cwd 或卸载仍撤销访问。普通无 override 接续只有在当前原生 Store 持久 provider 与 resume 顶层实际 provider 一致时，才重建该历史摘要解释；不改 DB/历史，不把任意 mismatch 当成迁移授权。释放清除旧连接标记，再接续仍需重新取得这份双原生依据。

## 备选方案

- 自动退出整个 daemon 能刷新环境，但已接纳的 turn 可在排空期间创建后台终端，最终退出会清理该终端。因此保留明确确认，不自动停止共享服务。
- `unsubscribe → resume config` 只重建目标线程，影响面较小；但两次空闲、无后台快照后，另一个合法客户端仍可创建并留下后台终端再退订。原生重建会杀掉该终端，故不采用。跨客户端同线程 RPC 串行不能覆盖这一组请求之间的窗口。
- `daemon start -c features.respect_system_proxy=true` 看似是进程级最小改动，但 0.164.0 CLI 不把参数传给 child。`restart -c` 能生效，却持久化 daemon settings，且可能终止预读后由外部启动的新进程，不能替代普通 Start。
- 热加载配置可保留任务，但既有 ModelClient 的代理策略不变。已订阅线程的 `thread/resume.config` 覆盖也被忽略；均不能作为现有坏连接的无损热更新。

## 验证

精确 Codex 0.164.0 的隔离原生实验确认：版本冲突不写文件；生产 helper 仅保存该项，保留其他 feature；即使旧 saved featureOverrides=false，普通 fresh Start 也会清除旧覆盖并读取用户配置 true。另一个真实快照含多个无关 feature、但未配置 respect_system_proxy，生产 helper 正常启用。两个实验均无模型调用，最终正常停止自有 daemon。helper 的 17 项定向测试及模块边界检查通过。

隔离 HTTPS/CONNECT 夹具使用自有 0.164.0 副本、测试证书、固定模型响应和仅注入测试进程的系统代理解析输入，未修改全局系统代理。测试副本仅调整测试签名以加载解析 shim，`__text` 哈希与原版一致，正式二进制及签名未改。同一线程经历两次代理端口变化，均经新代理产生实际 CONNECT 并完成回复，分别耗时 75.68、75.78 秒；daemon PID、原后台终端 ID 和另一个活跃 turn 保留。夹具与清理回执位于 `.tmp/cli-network-live-20261110/`。这是受控原生网络验证，不等同于正式 App、真实账号、手机或其他平台验收。

原生热加载无效、单线程重建杀后台进程、Start 参数未转发与单项配置 CAS 的证据在 `.tmp/feedback-followup-20261110/cli-network-options/`。产品事务回归由同批 `test_cli_network_recovery.py` 覆盖；不以提交回执或静态读取代替完整模型回复。

后续产品 HTTP 集成证据位于 `.tmp/cli-recovery-integration-20261110/`。`run-16261/result.json` 验证缺新变量准确拒绝且无退出/请求，以及 file-auth 已知 factory 下外部文件 B/缓存 A 阻新 send、view/interrupt 仍成功；`run-24662/result.json` 验证同 provider 维护后 KEY_B/staticB/headerB 实际请求、连续第二次 provider 迁移到 other-only 端点，以及正常 release→普通无 override reattach 后仍使用 other-only，保留同 ID/cwd 与四轮历史，均正常清理。原生摘要旧值原样保留在证据中，没有改写它伪装成功。

## 影响

普通接续保留原生可恢复机会；明确启用后，后续代理端口变化可由原生处理，减少共享服务维护。代价是该 home 的 CLI 用户配置新增持久选项，且首次旧运行时仍受原有排空边界限制。原生成功系统路由缓存为 60 秒，活跃连接另有生命周期，不承诺瞬时切换或无条件无损迁移。

## 相关记录审计

[CLI 释放与恢复修复](2026-11-10-cli-release-and-safe-recovery.md)部分重叠：保留不自动停止共享服务、确认、任务核验和未知回执，只细化普通诊断与明确启用后的原生跟随。[独立 Codex 管理](../feature/2026-11-02-independent-codex-management.md)部分重叠：保留配置隔离与原环境身份，补充后续端口变化不再依赖旧启动变量。[CLI 继续聊天](../feature/2026-10-14-codex-cli-resume.md)部分重叠：普通继续仍不覆盖配置，单项设置只属于明确确认的网络恢复。[上下文与后台控制](../feature/2026-11-01-native-context-and-background-controls.md)部分重叠：保留旧代理故障和活动任务不受干预的历史证据，不把隔离成功改写为原服务已恢复。[CLI 模型目录环境](../feature/2026-10-18-local-cli-daemon-model-environment.md)管理目录端点和凭据读取，与本次系统代理选择无关，保持不变。泛“CLI 恢复”命中的 Desktop、Claude、WSL、归档、通知及初始化记录不改变；现有 proposed TCC 笔记不在本次范围。
