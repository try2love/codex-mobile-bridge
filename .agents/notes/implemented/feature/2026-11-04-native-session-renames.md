# Agent Note: 共享重命名只确认原生标题，不再回退为显示别名

Status: implemented

## 问题

用户要求 Claude、DSH 和 ZCode 的重命名同步到宿主机原会话。此前共享管理器在列表未声明 `rename` 能力时，直接写 Bridge 显示别名并返回成功；ZCode 已接续的列表还丢失了会话投影外层的能力字段，因此原生通路存在也会走别名。真实只读核对发现同一独立测试会话在 Bridge 显示用户指定名称，原 App 的任务索引仍保留原名。

## 决策

`DesktopSessions._session_metadata` 只通过原适配器派发原生改名。能力未就绪时明确拒绝，不把本地别名当成已完成。已有未知请求仍可只读确认；换请求 ID 不能绕过未知结果保护，也不能在发现旧修改已经成功后再派发相同标题。

共享 `RenameCommands` 在派发前保存目标、原身份与请求 ID。派发后只读核对标题和身份；原生事件或文件写入尚在收敛时允许最多两秒的短暂读回重试，不重复写入。这个窗口不是弱网发送超时，也不表示失败可以安全重发。身份变化立即拒绝，未确认的写入仍保留未知回执。

原生能力已确认的列表条目显示权威原名，不再被旧 Bridge 别名遮盖。旧别名数据保留；只有一次新的原生改名读回确认后，才清理该会话对应的别名，不批量迁移或回写历史别名。项目与分区的原有 Bridge 分类不变。

### 各原生通路

- Claude Desktop 的 Code/Cowork 沿用已核验的 `updateSession(id, {title, titleSource: 'user'})` 与 `getSession` 读回，不加载聊天、不写模型设置。
- Claude CLI 只对网关已经持有、原生初始化同时公布 `ui_surface_v1` 和 `rename` 命令的连接开放。复用同一 `_Owned.control`，发送官方 `rename_session` 控制请求，携带 `title`、`source: host` 和预期 `session_id`。本机、SSH、WSL 共用该函数和原生标题事件读取；不发送 `/rename` 用户消息，不新起执行器，也不由 Bridge 追加或重写 JSONL。冷会话先由用户显式连接。
- DSH 通过已有 `ctx.get('sessions')` 动态服务入口取得 Session，使用原 `sessionTitle.rename` 服务，对原 Session store 中仍然加载的同一 Session 生效；不要求同时存在 Agent。缺少已加载 Session 时明确要求先连接，不调用会隐式恢复 Agent 的 `sessionController.rename`。派发前读取原服务当前 `config.maxTitleBytes`，明确拒绝会被原服务折叠、去控制或按 UTF-8 字节截断的标题；无法核验上限时不写入，不猜默认值。接受回执后再核对列表标题、cwd 与同一 Session 对象。
- ZCode 使用原 `zcodeTaskService.renameTask`。该服务经原生 `renameSession`、CLI 事实对账和任务索引持久化后返回任务元数据；Bridge 再读原项目树确认 ID、标题、cwd。它不需要聊天订阅，列表独立传递原生改名能力，避免丢失外层能力后退回别名。归档或尚未持久化的草稿不被承诺为可原生改名。

## 原生依据与验证

官方 [TypeScript SDK 会话元数据文档](https://code.claude.com/docs/en/agent-sdk/typescript#renamesession)提供独立非模型改名。另核对官方发布包 `@anthropic-ai/claude-agent-sdk` 0.3.318 的类型与实现：控制请求为 `rename_session`，支持 `source: host` 和预期 `session_id`。本机已安装 CLI 2.1.292 的初始化公布对应能力；运行时按能力开放，不锁定版本。

Mac 原生 CLI 的隔离实验复制已有独立验收记录的完整字节，保留原 ID 与原 cwd，使用独立配置和仅回环的假 API。共享函数完成改名、恢复原标题；CLI 在两次操作后仍运行，EOF 正常退出后原生文件仍显示恢复后的标题。发送用户消息数、网络请求数、模型请求数均为零，原始记录哈希未变。证据在本机 `.tmp/preview3-rename/`，不提交记录副本或聊天内容。

Windows 已安装 CLI 2.1.303。使用 F 盘既有独立验收记录的字节副本、原 ID/cwd、隔离配置与回环假 API，验证同一共享控制函数完成改名和恢复，原生进程身份校验通过；两次操作后进程仍在，EOF 正常退出，持久 `custom-title` 事件与原标题恢复均确认。原始记录 SHA-256 不变，用户消息、网络和模型请求数均为零。辅助结果采集曾以 Windows 默认 GBK 读取 UTF-8 JSONL 失败；随后只读补齐 UTF-8 结果，没有重复改名或重启。主流程与持久读回已通过，诊断采集错误不计作产品失败。证据为 `.tmp/preview3-rename/windows-rename-result.json`。

DSH 当前安装内核 0.2.1-rc.5 的标题服务明确要求 `ctx.sessions.get(id)` 与传入对象一致，并追加用户来源的 `session/title` 事件。ZCode 的固定官方源码中，`renameTask` 调用 `taskFacts.mutate`，随后回源并更新任务索引；不是只改当前窗口投影。

定向测试覆盖：不支持时不创建别名；旧别名数据保留但不遮盖权威名；一次派发与短暂只读收敛；身份变化不继续；断线后未知结果只读确认且新请求 ID 不重放；Claude 本机/远端共用控制、冷会话不启动、原生标题跨独立 worker 读取；DSH 无 Agent 的已加载 Session 可改名；ZCode 不订阅聊天的任务改名及原身份核对。真实新版 Desktop/DSH/ZCode 的最终会话改名与恢复仍分别记录，不能以模拟通路或 CLI 副本实验代替最终包全部通路的实机结论。

WSL 环境只读核对中，Ubuntu 发行版处于 Stopped，正在运行的是另一个 Nvidia SDK 发行版。本轮未启动 Ubuntu，也未进入无关发行版；共享 SSH/WSL 控制与独立 worker 读取通过合成测试，WSL 已持有会话的真实改名不计作已通过。

## 最终本机验收

Mac Test 128.12 的 ZCode 独立 Fork 原生改名约 1082 ms、恢复约 990 ms，SQLite 任务标题与 API 双读回一致，原 cwd 和 Flash/low 不变。Claude Desktop 独立 Code 聊天改名约 2013 ms、恢复约 2169 ms，原 App JSON 与 API 双读回一致；原生异步落盘曾使即时文件断言早于写入，只追加只读确认，没有重发改名。原生 titleSource 按用户改名变为 user。后续 128.14 未修改这两条通路，未重复测试。

Mac Test 128.14 / DSH 修订 11 在指定独立聊天中先显式接续（43 ms），再原生改名和恢复（各 98 ms）。原生压缩日志新增两个 session/title 事件，与 API 标题一致，原标题已恢复；user/message 保持 12 条、request/header 保持 2 条，原目录和 deepseek-flash/low 不变。此检查不切前台、不发送用户消息或模型请求。真实框架中直接访问未注入 sessions 的错误已由服务查询接口修复。

这些结果证明所列版本的独立测试聊天及原生写入通路，不泛化为任意平台、版本或冷会话均已实机通过。具体证据保留在 `.tmp/preview3-rename/`，不提交原始聊天数据。

## 备选方案

继续使用 Bridge 别名能覆盖没有原生接口的冷会话，但不符合本轮“必须修改宿主机原会话”的决定。直接改写日志或数据库看似简单，却绕过原客户端写入者、原生通知和持久化规则，不采用。安装完整 Agent SDK 可使用独立会话元数据函数，但本机没有该依赖；当前复用已持有 CLI 的官方控制流即可，不为改名引入另一套执行器或文件定位实现。

DSH 的高层 `sessionController.rename` 能处理冷会话，但会解析或恢复 Agent；保留“先显式连接，再改名”的边界。ZCode 直接向当前投影发送改名命令可修改原会话，但缺少任务索引的权威对账，因此采用原 App 侧栏也使用的任务服务。

## 影响

名称、回执与宿主机存储保持一致，失败不再伪装为本地显示成功。代价是尚未连接或未公布原生能力的 CLI/DSH 会话暂不能重命名；原生写入结果未知时，需要读回确认，不能反复提交。原生改名可将标题标记为用户选择，后续自动命名行为由原客户端管理。没有模型调用、账号切换、项目迁移或隐式启动其他会话。

## 既有记录审计

- [原生命令显示](../bug-fix/2026-11-11-claude-native-command-display.md)：默认标题跳过内部 meta 与 synthetic 记录；原生 custom-title 优先级和原生日志保持不变。
- [显示元数据与别名](2026-10-28-desktop-session-display-metadata.md)：部分取代。本轮撤销改名的别名回退，旧方案理由保留，Bridge 项目/分区仍有效。
- [原生会话元数据](2026-10-28-native-session-metadata.md)：部分重叠。保留非激活和原 cwd 约束，补充 CLI 控制通路、DSH Session store 与 ZCode 持久任务服务。
- [共享会话菜单](2026-10-28-shared-session-menu.md)：交互外壳继续复用，能力与原生结果由本文约束；不新增独立客户端菜单实现。
