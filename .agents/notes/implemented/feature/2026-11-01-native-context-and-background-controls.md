# Agent Note: 共用上下文维护与后台任务控制

Status: implemented

## 问题

各客户端提供不同的原生上下文维护接口，远程界面需要复用同一入口；DSH 与 ZCode 的后台任务也需要独立停止能力。另建客户端专属弹窗会重复已有上下文及子智能体面板，也容易把缓存状态误当作可写依据。

## 决策

ContextUsageControl 继续服务全部客户端，按原生数据展示自动压缩阈值、最近输入及缓存 Token；缺失字段不显示，不将字符组成估算成 Token。客户端通过可选 compact 能力与回调开放手动压缩，断连、缓存或写操作期间禁用。ZCode 使用 V4 compact；DSH 使用 commands 注册的 compact 指令，不向模型发送伪装的 slash 提示词。忙碌、待审批或暂停队列不自动排入维护命令。

后台状态复用 AgentsPanel 和现有工作台标签。只在面板可见时沿用十秒刷新，停止是独立写请求，复用 DesktopSessions 的幂等回执，不自动重放。ZCode 从原会话 backgroundWorks 取运行状态，停止前核对 workId、running 与 cancellable。DSH 仅读取当前会话拥有的 jobs 和直接子任务目录；不展示无归属或其他会话的任务，不调用会消耗输出游标的 read。子任务未驻留时显示未知，不能推断为已完成。停止分别使用 jobs.kill 的会话权限和 subagents.interrupt 的 user/parentSessionId 权限。

Codex App 经原 owner 的 thread-follower-compact-thread 调用，CLI 与 WSL 经原 daemon 的 thread/compact/start 调用。原生命令可能中断活动回合，因此只允许已连接目标通过新鲜空闲检查后提交；不唤醒历史会话，也不扫描或干预其他聊天。Codex 本机、SSH 和 WSL 复用 CompactCommands 的持久化请求记录，先保存未知状态再发送；相同请求直接返回原回执，未知结果不自动重发。

Claude App、CLI、SSH 与 WSL 复用原生命令检查。CLI、SSH 与 WSL 要求已注册的 builtin compact；Claude App 会投影 SDK 命令并丢弃 builtin，因而仅对 App 兼容已核实的 compact 原生描述及参数提示。显式 builtin=false、仅有名称、自定义描述与项目来源后缀均拒绝；未来原生描述变更会关闭此兼容路径，不能退回仅按名称分派。App 通过原会话 sendMessage，CLI 经既有 stream-json 输入；它们都是原生 slash 分派，不用自然语言模拟压缩。命令目录只在显式操作或既有 CLI 初始化时读取，不在被动刷新中启动查询。原生接收只表示已提交，只有 compact_boundary 等原生结束证据才能确认实际压缩完成。

这些接口为 revision 9 的可选能力增量，保持现有协议版本；旧插件不报告能力时隐藏入口。旧源码 LF/CRLF 指纹保留在受控兼容清单，不将未知来源视为可升级插件。

## 既有记录核对

- [共享上下文](2026-10-31-zcode-shared-capabilities.md)：部分重叠，沿用当前窗口与最近输入的区别，新增手动操作。
- [原生上下文折叠](2026-10-29-native-context-folding.md)：互补，消息记录的折叠和阅读位置保持原行为。
- [共享会话菜单](2026-10-28-shared-session-menu.md)：互补，仍由各原生适配器验证写目标。

## 备选方案

- 专用任务弹窗便于针对某个客户端开发，但会复制 Codex 已有工作台、可见性刷新和错误反馈。采用可选能力扩展 AgentsPanel。
- 直接调用压缩引擎省去命令层，但绕过 DSH 原生命令记录与失败语义。采用已注册 commands.execute。
- 扫描所有子会话能够补齐更多历史状态，但每次刷新会增加磁盘读取且容易跨过父子权限边界。只读直接目录和当前已驻留状态。
- 对 App 也强制要求 builtin 标志能够统一格式，但实机接口移除了该字段，会拒绝原生命令。只按 compact 名称判断则可能运行同名自定义命令；采用仅限 App 的已核实描述兼容，CLI 的来源校验保持。
- 重启所有 CLI 后台进程能立即更新代理环境，但会中断既有任务；仅用隔离实例验证新代理，活动 daemon 保持运行。

## 验证

201 项相关 Node 检查通过，包含任务归属过滤、过期任务拒绝、原生压缩、共享弹窗与消息动作；47 项 ZCode/DSH Python 检查及27项共享元数据检查通过。390/1280 像素浏览器生产组件各9项通过，确认断连禁用压缩、一次点击一个请求、仅可取消任务显示停止入口。

ZCode 独立 Flash/low 分支的 compact 回执106ms，随后原生状态 completedSuccess、无运行项、队列为空、无错误；未把回执本身当作完整压缩效果证明。DSH 独立空会话通过原生命令返回 No compactable history yet，连同任务读取22ms；没有为此产生模型消息。本轮在新建独立 DSH 会话中补齐真实验证：非空历史压缩返回 Compacted 5 history items (~2459 tokens)，耗时 8643ms。随后同时启动两条原生后台 sleep 任务，第一条停止回执 24ms，原生状态 killed，第二条仍 running；再单独停止第二条并确认 killed，测试任务均已清理。没有操作原有会话。

DSH 首次正常退出请求仅让插件端点消失，GUI 与后端仍在。25秒恢复期限到达后停止等待，核实原生退出对话框在未知状态时也显示任务中断提示；在本次重启前所有任务空闲的证据和既有正常重启授权下，通过原 App 正常退出确认完成退出，再启动恢复新端点。没有强杀或改 App 安装包。

安装后的 Mac Test zcode11 在 HTTPS 原生会话中显示 8,401 Token、1,000,000 容量、Flash/low 与可用压缩入口；共享子智能体标签实际返回当前会话没有后台任务。只核验读取和入口，未重复压缩、发送模型消息或取消不存在的任务。

Codex／Claude 新增 15 项 Python 定向检查及 3 项共享入口检查通过，覆盖原生分派、空闲与 owner 变更拒绝、持久化未知回执、HTTP 认证和 CSRF、WSL 路径以及切换聊天后的晚回执隔离。模块边界检查通过。CLI 实测在独立新会话中发现环境阻塞：Claude 内置 compact 已确认，但首条请求返回 401 Invalid access key；Codex 原 daemon 连接上游 WebSocket 被拒绝，首条请求未在 80 秒内完成。两次测试均止损，Claude 测试执行器正常关闭，Codex 仅中断新测试回合；没有以这些测试声称真实压缩完成。Claude App 独立新聊天的原生日志也报告 ECONNREFUSED；超过等待上限后仅停止此测试回合，并确认 idle。没有调整账号、模型服务地址或系统代理来掩盖环境问题。

最终 Mac Test zcode12 已编译、签名、安装并启动，375 个 Web 文件及新增冻结模块匹配，正式网关、所有原生 App 进程、草稿、账号和网络配置保持。Codex App 独立新会话使用原生目录中的 Luna/low 完成首条消息；压缩回执 222ms，原生 contextCompaction 记录 source=manual、completed=true，开始到结束 1339ms，最终 idle。LAN 与 HTTPS 读取同一压缩结果，不重复执行。Claude App 的共享上下文弹窗显示原生压缩入口，但其模型请求受上游连接拒绝阻塞；CLI 的真实压缩也未宣称完成。

另对本轮变更影响的 Claude 本地／远端和 Codex CLI 模块执行 141 项检查：140 项首轮通过，1 项旧替身缺少真实运行时必有的 catalog 字段，补齐替身后单独复测通过；未重复其余通过项。iOS／Android 壳源码本轮未变，不重新打包。

### 账号与代理故障定位

上述上游失败发生在账号与代理修复前。Claude CLI 当前选中的 Cockpit 实例保留失效旧凭据；同一供应商的旧凭据读取模型目录返回 401，现有 App 账号凭据返回 200。按用户授权对同一 CLI 账号及实例设置同步有效凭据，保留供应商、账号标识和加密格式，模型固定为 claude-sonnet-4-5-20250929。独立会话完成原生压缩，compact_boundary 的 trigger=manual，41,401 Token 降至 1,613，原生耗时 33,741ms，最终 success／idle。

Claude App 指向 Cockpit 本地映射留下的无监听回环端口。开启 Cockpit 后新端口出现并属于该进程，证实其生命周期依赖。改为同一供应商及凭据的原生直连配置，恢复模型目录，将指定 Sonnet 放在默认位置并移除旧 Haiku；Cockpit 账号元数据与原生保存结果同步。重启仅在所有 App 会话即时确认空闲后正常执行。Cockpit 已退出时，独立 App 会话仍以指定 Sonnet 返回测试标记，证明请求不再依赖临时中转进程。

Codex CLI 常驻 daemon 的启动环境仍指向旧代理端口；当前系统代理和 Codex App 已使用另一端口，旧端口无监听。原 daemon 尚有活动回合，未重启、终止或改写其会话。使用同版本原生二进制、原有账号及当前代理的隔离实例完成非空历史压缩，原生 contextCompaction 完成事件、compacted 历史及 task_complete 均落盘，耗时 2,961ms；测试进程正常关闭，临时认证副本删除。该结果不代表原 daemon 已恢复，仍须在其任务空闲后正常重启以更新启动环境。

App 实测另发现 getSupportedCommands 的原生投影移除了 builtin 字段。读取已安装本体的投影函数及实际 IPC 回包确认原因；零模型调用的独立同名自定义命令检查证实存在覆盖，且目录带项目来源。新增针对投影和自定义覆盖的两项回归，连同受影响的五项 Claude 压缩检查共七项通过，没有重跑无关通过项。

Mac Test zcode13 已构建、签名和安装，冻结代码中的压缩识别函数与当前源码一致。原生 App、正式网关进程和 Test 草稿、绑定、网络配置均通过安装保护检查。最终 Test 对已建立历史的同一独立 Claude App 会话提交一次压缩，回执 2,640ms；原生日志 compact_boundary 确认 trigger=manual，77,008 Token 降至 3,784，原生耗时 32,311ms，随后状态 idle、无错误，模型仍为指定 Sonnet。没有重复发送建立历史的消息，也没有以回执耗时代替压缩完成耗时。

## 影响

共享 UI、写入回执和可见性刷新不分叉；新增接口只服务原生提供能力的会话。DSH 非空压缩和真实后台任务独立取消已验证；实际模型服务耗时须与快速提交回执区分。共享入口不新增定时请求，压缩只在点击后执行。账号和路由修复仅作用于用户授权的本机既有配置，有私有备份；原生 App 安装包不修改。原 Codex CLI 活动 daemon 的代理更新仍有待其任务结束。
