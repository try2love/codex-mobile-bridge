# Agent Note: Antigravity 原生历史与受限控制接入

Status: implemented

## 问题

Antigravity App、IDE、CLI 将历史保存在不同目录。把端口日志、任意同名进程和固定模型目录直接拼成连接，会丢失原会话来源；根据展示状态自动派发排队消息，也可能在结果未知后重复提交。PR #150 提供了 SQLite/JSONL 和 Hook 的接入基础，本地整合需要保留已有共享聊天界面及原执行器归属。

## 决策

- `bridge/clients/antigravity/` 负责原生格式、RPC 和 Hook；系统进程与监听核验在 `bridge/platforms/antigravity.py`，不反向依赖客户端业务。
- App、IDE、CLI 历史均按目录来源生成网关 UUID。API 不接受可能跨来源碰撞的裸原生 UUID。展示及模型目录读取不启动 CLI、不创建任务、不派发队列。
- 写入只发送到同用户、同进程世代、官方安装映像、匹配 `--app_data_dir` 且独占 IPv4 loopback 监听的原服务。日志仅提供候选端口；每次 RPC 前后重新核验，不跟随重定向，也不使用代理或重试未知写入。
- 原生发现同时覆盖官方 App 的 `language_server` 与 `.gemini/bin/agy` 的内嵌 LS。新版 IDE hub 的前端端口不替代原生 RPC 监听，资料归属仍由实际 `app_data_dir` 决定。三类程序通过共享默认应用表单补录；安装清单缓存、官方启动形态和无账号真实只读验证见[发现与添加修复](../bug-fix/2026-11-10-client-inventory-and-agy-discovery.md)。
- SQLite 使用 `mode=ro`。不分别复制 main/WAL/SHM 作为快照；历史不可读时该来源暂不可用。发送前直接查询原库，缺行、空状态、查询失败均不能证明空闲。
- 新建聊天携带已登记目录的 file URI；只在原服务返回相同 `projectEnvInfo.workspaceUris` 和新会话 ID 后发送首条消息。绑定回复缺失或不符保留未知回执，不重试创建或首条发送。
- 模型仅取原服务目录，目录缓存和设置校验包含来源。思考强度由完整原生模型条目表达，不提供猜测的独立 effort 或额度数据。
- `accounts=true` 开放共享账号页面；原生身份按 APP、CLI、IDE 分别读取，单来源失败不会遮住其他来源，不依赖先有聊天历史。账号列表仅投影身份白名单和已保存副本的元数据，不读钥匙串或令牌。模型目录按来源分组，不将同名模型合并为另一来源的权限。未保存的原生身份不提供切换操作，额度按钮遵循能力门。
- 默认本机 Google 账号支持保存、改名、删除副本和显式应用。已安装 App 2.29.1、CLI 1.5.3 的二进制静态核验证实普通 OAuth 的 `auth_method=consumer`、空活动 `wif_provider`、非空 `token.refresh_token` 契约；`id_token_email` 只作展示及跨副本一致性核验，不证明登录有效。完整保留原始 JSON 和未知字段；GCP、WIF、旧式无方法记录及自定义 home 不推断为默认登录。
- 三类入口共用系统凭据 `service=gemini/user=antigravity`。默认回退文件是 `.gemini/jetski-standalone-oauth-token`、`.gemini/antigravity-cli/antigravity-oauth-token`、`.gemini/antigravity/antigravity-oauth-token`，保存时保留存在/缺失状态。使用同一系统登录的自定义入口也会受影响，确认文案明确说明；不复制 Electron 数据、聊天、插件秘密或其他目录。平台模块操作系统凭据，客户端处理 Go 编码：Mac 兼容 Base64/旧 hex 前缀，通过 `security -i` stdin、`-U` 更新，不先删后写、不加 `-A`；Windows 使用 `gemini:antigravity`、UTF-8 blob、Persist=2；Linux 选择 login collection，仅不存在时采用 default，锁定或多匹配项不静默绕过。
- 保存和应用前后重新核验原生三类进程均已退出，不自动退出、启动或强杀 AGY。Linux 更新后已删除但仍运行的映像继续视作占用；无关非 dumpable 的 sd-pam 不阻塞，无法核验的原生候选仍拒绝。私有恢复副本记录变更前后值；失败仅回滚本次仍匹配的值，全量读回确认原配置后才移除备份，外部变化不覆盖。写入成功只报告“登录配置已应用，尚未验证登录”，活跃账号仍来自原生状态，不用最后切换记录伪造成功。
- 桌面仍保留 `management` 会话配置区域，并与共享账号面板组合。App、CLI、IDE 分开显示历史和原生服务状态；模型、审批配置仅作用于当前选中的来源及会话，保存复用现有操作记录和模型目录身份校验，不新增账号或审批默认值。读取失败后禁用陈旧配置，重新加载成功才恢复写入。
- `backend` 是历史记录来源的展示分类，`surfaceDirKey` 是实际目录及服务 owner。App 目录内标记 CLI 的记录仍只向 App owner 写入；独立 CLI 目录中的同名原生会话使用另一网关 UUID。新建默认环境优先使用 `surfaceDirKey`，不能由展示标签重选执行器。
- 共享列表识别 APP、CLI、IDE；共享输入区沿用 [已有展示契约](2026-11-04-shared-account-editor-and-zcode-readiness.md) 保留模型、Skill、审批、上下文、附件和工作模式的位置。`composerCapabilities` 仅保留槽位，真实 `capabilities` 决定可操作性；未提供的 Skill、计划和目标模式保持禁用，不进入发送参数。离线仍保留草稿和同一控件结构，并明确提示原生服务未连接。
- 消息时间从原生带时区 ISO 转为共享渲染器要求的 epoch 毫秒；缺失的时间不补造。历史列表仍保留原有秒值格式，由列表的统一转换处理。
- Hook 只在用户启用接入后启动，使用用户资料级单 owner 锁。来源通过会话目录中的 transcript 路径核验。steer 仅在 `PostInvocation` 消费；queue 仅在已确认 `fullyIdle=true` 的正常 `model_stop` 消费。已消费但回执不明的注入不重放；未知/异常停止保留队列。
- 上传的图片及文件保留网关验证后的本机路径，通过共享 `file_mentions` 交给原服务。历史图片继续使用经过会话/项目边界检查的共享预览。这里不宣称原生视觉输入已经实机验证。
- 沿用共享聊天、时间戳、资源、通知、下载和手机跳转；不引入原 PR 的 Light 主题、排队控制条或独立聊天布局。官方图标直接取自 [Antigravity press](https://antigravity.google/press) 的品牌 PNG，文件哈希为 `e0cd08ccd10cd8d08ccf0ba449823ee88495825c0841619618100d3ab089f51e`。
- 在不改变只读与来源隔离契约的前提下补齐原生运行态增强：通过只读 `GetAllCascadeTrajectories` 与按来源隔离的 `agy-wait:{surface}:{cid}:{tid}:{step_index}` / `HandleCascadeUserInteraction` 原生回应 `askQuestion`、`runCommand`、`filePermission`、`elicitation`、`deploy` 等等待交互及子智能体交互，并在 `send` 前拦截未处理等待步骤；模型目录过滤 `disabled`/`Unavailable`/`-disabled` 条目，仅在真实 `quotaInfo` / `planStatus` 存在时透出配额标签、`usageSummary` 与详细步骤 `contextUsage`（`cacheWriteTokens` / `outputTokens`），缺省时保留原纯文本提示；`transcript_full.jsonl` 水合与 `read_image` 执行分支对齐、全文件扫掠、`transcript_full.jsonl` 回退与允许根目录内的符号链接拦截；`list_tasks` / `stop-task` 核验当前运行中子智能体后停止；`HookIPCServer` 在 socket 写入失败时按顺序无重复回补未送达队列。

## 备选方案

原 PR 的后台空闲轮询可以在 Hook 缺席时自动发送，但展示快照和独立 CLI 无法证明原 owner 已释放任务，未知结果重排也会重复写入，因此不保留该机制。复制 SQLite 主库和 WAL/SHM 可以绕过部分打开失败，但复制间隙存在并发更新，不能作为一致快照或写前安全证据。固定模型/额度表容易展示完整选择项，但不能证明当前账号有权限，故使用原客户端目录。只读账号页足以显示身份，但不能满足保存及应用账号；静态核验原生契约后，扩展为停止状态下的有限快照事务，仍不开放未知认证类型。自动停启三类客户端会扩大生命周期改动，因此保留原客户端正常退出后应用的边界。PR #150 更新 `f25d787f` 提供账号快照思路，但将安装当作连接、跨平台套用 Linux 目录、macOS 删除旧钥匙串项后以 `-A` 重建、恢复写入失败未上抛等行为不能沿用。

## 影响

离线来源仍可查看历史；实时发送、新建、停止、模型和审批依赖可核验的原服务或 Hook。停用只关闭手机访问并禁用本插件，不退出/强杀 Antigravity，不改写原生历史和任务。账号页支持原生身份、分来源模型及默认 Google 账号副本；聊天改名/归档或独立 effort 仍不开放。保存/应用可能触发系统凭据授权，普通刷新不会读凭据；写入成功不能保证登录、地区和上游权限可用。

原生手机白名单及图标资源同步到 Android 和 iOS 源码。既有已安装手机包不会自动获得这些源码改动。网关冻结入口支持 `antigravity-hook`，普通源码入口直接调用 Hook 脚本。

## 验证

`test_antigravity_adapter.py` 覆盖三个来源历史、工具/思考/图片归一化、分支对齐、只读不派发、未知空闲拒绝、一致性失败关闭、新建项目确认、Windows drive/空格/UNC URI、来源模型缓存/设置隔离、单 owner 锁、RPC 不重试和队列单次消费。以上为隔离夹具与合成 RPC，不代表真实客户端写入或三系统联调通过。本机正式客户端仅允许只读发现，不创建/继续/停止正式任务。

后续管理与共享输入区修复采用合成原生服务：`tests/test_antigravity_adapter.py` 23 例通过；相关两个 Node 文件 270 例通过、1 例基线失败。失败是重新打开文件工作区后 `active` 从 `files` 变为 `chat`，在 `c22abfce` 的独立静态文件快照复现，不在本次修复范围。Chrome/WebKit 的宽窄聊天、模型空目录/错误、来源筛选、连接/断连几何与草稿检查及通知菜单保存均通过；桌面管理按原 App 最小窗口 820px 及 1280px 检查模型、审批保存和错误禁用。390px 桌面管理模拟不属于原 App 可用窗口，不宣称其可用。

本机 App、CLI、IDE 均无法登录，本轮没有登录、原生上游调用或真实聊天发送。浏览器截图和请求均为生产静态资源加合成数据，不能作为真实 AGY 接入成功证据。初始笔记之外，相关共享输入区决策为部分重叠并继续保留；历史/控制拆分约束仍沿用架构文档。

后续发现修复通过额外沙箱启动专用无账号官方 LS，验证生产发现、监听归属、401 CSRF 拒绝及 200 无账号状态读取，随后退出该专用进程。未登录并非发现/只读 RPC 不可验证的理由；真实模型收发仍未验证，隔离措施与脱敏回执位置见上述修复记录。

2026-11-12 的共享账号入口覆盖：来源身份隔离、无历史身份、单来源失败、返回字段不泄露令牌、拒绝未开放写操作及分组模型；模型来源读取失败沿用共享缓存的 stale 路径，保留上次成功结果并展示来源错误。相关 Python 28 例、共享账号 UI 43 例通过。两个 Hook socket 用例在允许 loopback 的环境单独完成。生产桌面组件的浏览器合成夹具验证账号与会话配置并存、无额度按钮及来源选择；不代表登录或真实账号切换完成。


默认账号快照补验：14 项目标 Python 回归覆盖无凭据轮询、原始字段/缺失文件、活动或自定义入口拒绝、不一致身份、条件回滚、外部变化及未确认回滚保留副本、无关秘密不变、符号链接/路径穿越、认证类型和 Go 编码。共享账号 UI 共 45 项已覆盖；新增名称编辑用例在修前失败，修后及相关子集通过。不支持添加 API 不再隐藏官方账号名称编辑，关闭后也不暴露 API 表单。生产桌面页面、实际 manager 和快照存储配合合成凭据，在浏览器完成保存、应用、改名和删除，页面不出现令牌。

Mac 原生 Keychain、Ubuntu Secret Service、Windows Credential Manager 的随机测试项均完成新增、读回、更新和删除，没有读取真实账号。Windows SSH 会话返回错误 1312；同用户 Interactive/Limited 的一次性后台任务验证成功后移除，不能把 SSH 的不可用当作桌面凭据格式错误。Ubuntu 原生 sleep 副本在运行中 unlink 后仍被停止核验识别，随后正常退出。AGY 实际账号无法登录，未做真实账号切换或模型请求；这些结果证明存储及流程兼容，不能替代登录有效性和新版冻结包验证。

2026-11-14 基于 `v2.2.1-preview.1` 的运行态增强验证：覆盖原生等待交互与子智能体交互传播/跨来源隔离、disabled/Unavailable 模型过滤与真实 `quotaInfo`/`planStatus`/`contextUsage`、`CodeContent`/`ERROR_MESSAGE`/`CHECKPOINT`/工具图片、回滚 `transcript_full.jsonl` 水合与 `read_image` 符号链接拦截、`stop-task` 子任务归属核验及 `HookIPCServer` socket 写失败不重放。以上基于隔离夹具与合成 RPC/socketpair；模块边界与桌面 UI 相关 Node 用例通过，真实 Antigravity 桌面客户端实机联调、真机手机 WebView 与 Playwright 浏览器套件仍待端到端验证。

## Hook 写回未知结果

队列由原生Hook事件在原有锁内一次性消费。socket写回失败可能发生在部分内容已送出后，也可能与其他Hook消费、用户撤回/编辑交错，因此不恢复派发前的队列快照。保留未选中的排队项，不推断已消费消息送达成功，也不自动重发；用户应核对原客户端结果后决定是否重新发送。恢复“看似未送达”队列能减少掉线时的丢消息，却无法证明未送达，故不采用。定向socketpair测试覆盖成功消费交错、撤回/编辑、半包错误、未选中项持久化及下一Hook不重放。

## 等待交互的即时核验

列表和详情沿用短时展示缓存，读取失败保留历史；发送及回答前重新读取原生等待摘要，失败不能按空闲处理。回答只使用本次摘要仍列出的请求及父会话关系，不能从用户传入的旧请求 ID 重建授权。当前摘要仅缺少步骤详情时仍可读取完整步骤。刷新只替换同来源的等待记录，APP、CLI、IDE 互不清除；子任务及后台活动同步影响原生父会话的忙碌状态。沿用展示缓存可少一次 RPC，但可能回答桌面已处理的问题，故仅展示使用。九项定向回归覆盖新等待、已处理、读取失败、父关系变化、来源隔离与步骤详情补读；原有提问和子智能体交互用例亦通过。
