# Agent Note: ZCode 原生会话协议原型

Status: implemented

## 问题

ZCode 接入必须操作原 App 的 owner、会话、模型、账号与工作区。另起同名 CLI、复制历史或只证明模型能读到路径，都不能证明原生会话接入。桌面 host 使用内部 MessagePort，尚未发现可直接供 Bridge 长期使用的稳定本机外部入口。

## 决策

原型从 v2.1.7 的 d65a610 建立，分支为 feature/zcode-native-prototype。没有使用 agent-connectors-preview 的代码或接入通道。为保留原工作区分支与未提交成果，开发副本位于原仓库 .tmp/zcode-native-prototype/repo；源码由独立 Git 分支管理，临时运行数据不提交。

`bridge/clients/zcode/` 实现原生测试会话协议适配器，`scripts/zcode-prototype.py` 是手动验证入口。早期协议环境为 macOS ZCode 3.17.1、Node 26.12.1；当前传输复用网关运行时，Windows 的平台扩展与证据见下文。它通过临时 loopback CDP 进入已有 renderer，取得原有 service accessor，调用 App 自己持有的 host；不创建替代 CLI、不修改 app.asar、不读取账号凭据。平台端检查端口仅绑定 127.0.0.1、可执行文件、同用户与进程世代。

命令使用原 renderer 的 clientId 和可信 connection。自造 clientId 会被原生服务明确拒绝为 `fault.command.clientMismatch`，不能静默改写连接身份。绑定同时固定 workspace、sessionId、host connection 和 runtime generation；漂移后拒绝写入，要求显式接入。

原型复用 `DesktopSessions` 的持久化写入回执、并发只读合并和 provider 锁；复用 `Workspace` 的目录句柄、禁止符号链接与 20 MiB 文件边界。项目文件在发送时重新校验并通过原生 attachment begin/chunk/commit 生成引用，用户正文不拼入文件提示词，文件不会先下载到手机。单次原型附件总量也限制为 20 MiB。

被动历史只调用 `readSession(runtimePolicy='existing-only')`。不使用可能隐式 resume 的 `getTaskSnapshot`。原型没有周期轮询、后台重连计时器或第二套聊天 UI；只有手动请求触发原生读取。停止绑定当前 `activeWorks[].foregroundExecutionId`；审批绑定仍在等待的 interactionId，并使用原生给出的权限选项。结果未知的写入绝不自动重放，原生 command query 负责核对接收事实。

## 验证证据

2026-10-31，在独立 `.tmp/native-workspace` 与新建原生测试聊天中完成以下验证。原 App 主进程、共享执行器和工作区身份在收发与断线恢复过程中保持一致。

| 能力 | 实际结果 |
| --- | --- |
| 收发 | 原生 sendText 回执约 50.4 ms；完整回复约 4.06 s；原 App 能搜索并打开同一聊天 |
| 停止 | 精确执行标识的 stop 回执约 13.8 ms；原生 turn 为 completedInterrupted，桌面显示“已停止” |
| 交互 | AskUserQuestion 答案回执约 13.1 ms，模型确认收到选项 |
| 权限 | 实际 Bash 权限请求经 resolveInteraction 选择 deny；待决项清空，目标临时文件不存在，模型返回拒绝标记；没有修改永久权限 |
| 附件 | 原生 userInput 保留 zcode-artifact 引用；模型读到独立文件校验内容；原 App 可见 attachment.md 附件标签。未宣称内置侧栏预览已通过 |
| 断线 | 发出真实命令后、Bridge 回执返回前关闭 CDP；共享回执记 unknown；再次提交相同 ID 的实际派发次数仍为 1；新连接从原生 query 找回 accepted，并回读唯一用户消息和完整回复 |

18 项针对性回归覆盖只读不订阅、原 owner 漂移与显式重新绑定、过期命令、快照超时释放监听器、精确停止、过期审批、权限选项、原生附件、端点身份、项目越界与目录替换、附件总量、共享 unknown 回执与稳定消息 ID。模块边界检查通过，未增加例外。无关且源码未变的三客户端完整测试不重跑。

本机原生会话记录中已有 `agent-bridge-zcode` hook。原型没有调用该 hook 的接入服务，也没有安装、更新或修改它；测试环境不是“无插件安装”。这项环境事实不作为原型拥有其他分支能力的证据。

## Windows 平台接入与验证

Windows 与 macOS 共用 `adapter.py`、`transport.py`、`renderer.mjs` 和共享会话管理器。`installation.py` 只选择安装、端口身份与启动原语；Windows PE 版本读取及 TCP owner 查询放在 `bridge/platforms/windows/`，平台层不导入客户端。Windows 精确核对 `ZCode.exe`、相邻 `resources/app.asar`、3.17.1 产品版本、同用户 SID、同桌面 session、进程创建时间和单一 IPv4 loopback 监听，拒绝公网监听、重复监听、错误程序或其他用户/桌面。复用已有进程查询句柄，明确不申请终止权限。启动复用桌面启动器的参数入口，不借系统 Node 启动第二个接入程序。

2026-11-01 在 Windows 10 x64 笔记本、官方 ZCode 3.17.1.790 中，使用独立项目和原生聊天完成以下直接协议验收。所有模型请求均先核对 `GLM-5.3-Flash` 与 `low`；只发送一条简短标记、一条附件读取及一条可停止的空等待命令。

| 能力 | 实际结果 |
| --- | --- |
| 安装与进程身份 | 原 App 正常升级、登录；真实 Windows 端口、SID、session 和 PE 版本查询通过 |
| 收发与附件 | 接入约 906 ms；附件发送接受回执 312 ms；原生 `zcode-artifact` 引用、模型文件标记和桌面附件卡片均确认 |
| 一次性审批 | 原生等待项提供的 `allowOnce` 回执 63 ms；未改变项目永久权限 |
| 停止 | 仅停止对应 `foregroundExecutionId`，回执 78 ms；原生为 `completedInterrupted`，无 activeWorks，桌面显示“已停止” |
| 断开与恢复 | 显式 detach 后 resume 500 ms；同一会话、消息、附件与 logEpoch/revision 不变，没有重发或重启 App |
| 收尾 | 全部可枚举运行会话为空闲后正常退出；17742 无监听；测试驱动退出，删除约 199 MB 的已使用安装程序 |

以上耗时为本机适配器调用单次观测，不是手机端或公网时延分布。Windows 原生 6 项针对性单测通过，另在 macOS 完成 15 项传输/协议回归及 3 项 Windows 身份拒绝夹具；模块边界保持原有 22 条例外。没有重复无关且源码未变的完整测试。

Windows Test `2.1.7-test.20261101.zcode-windows1` 本地构建通过：网关 45.95 秒、Electron 23.16 秒，冻结模块包含新平台原语，Web 资源与源码快照一致。使用空白独立数据目录启动桌面包，主界面、数据目录与实际版本可见，随后正常退出；未启动第二个 Web 网关、未接管现有服务。清理本轮可重建源码副本与压缩包，保留 Test 产物、日志和原生测试项目。此启动检查与前述源码适配器验收分开计证，不代表 Windows 手机端到端链路已通过。

首次 Windows 验收的冷首页 attach 失败，原因不是首页缺少 service accessor：旧注册表探针只从已打开聊天的 `layer.acquire` 取模块作用域，首页没有该对象。现在也允许从持有原生 services 的组件函数取模块作用域，仍要求注册表唯一匹配，不调用组件函数或模拟点击桌面。

显式 attach 先核对原生 task index 的 session/cwd/local host，再复用原 App 的工作区注册表与 `SessionDataLayer.acquire`。原生 V4 订阅负责按需启动工作区并恢复历史；Bridge 等待 store 事件达到 live 后核对原 client、runtime identity 与 snapshot.sessionId。不得在订阅前、或 V4 历史恢复后强制调用旧 `session/read`：冷历史投影不保证旧会话 runtime 已 resident，该额外读取会错误返回 `Session runtime is not ready`。被动 history/detail 仍不触发订阅；等待有截止时间，失败释放本次租约与监听器，不重放发送。

最终源码经 Windows 正常退出再启动验证：原 App 保持首页，cached registry=false、聊天 context=0、Bridge lease=0；显式 resume 500 ms 返回 ready，detail 63 ms 回读 6 条消息、原生附件、上下文用量与 Flash/low。正文和附件与重启前一致；分支/编辑目标随原生日志世代更新，不把旧 epoch 当作恒定值。本轮没有模型调用，没有点开桌面聊天。24 项协议回归覆盖首页入口、冷 V4 历史、被动不启动、等待超时清理及既有 owner/回执边界。未覆盖原 App 升级或 Windows 手机端到端操作。正常退出后临时端口关闭，未留下启动项。

## 备选方案

官方手机中继能复用桌面 host，但引入官方登录、中继与单手机连接约束，不作为 Bridge 默认入口。另起 `zcode --web` 容易获得 HTTP 服务，但不证明原会话 owner，故不采用。修改 app.asar 能暴露接口，但破坏签名、更新与维护边界，故不采用。直接复制现有客户端 UI 或回执逻辑会再次产生共性行为分叉；本原型复用已有管理器和文件服务，不添加 UI。

## 影响

外部 Node 传输现由[网关内置运行时传输](../architecture/2026-11-01-zcode-bundled-transport.md)替代；此处保留早期原型环境证据，不再表示用户需要安装 Node。

本文记录手动协议阶段。手机 Test 后续实现与订阅方案调整见 [ZCode 共享会话手机原型](2026-10-31-zcode-mobile-test.md)，原来的独立订阅限制由同一原生 SessionDataLayer 的共享租约解决；不再要求手机读取时关闭桌面聊天。

已证明原 App 协议可实现核心控制能力，尚不是可在手机应用管理中启用的第四个正式客户端。实验入口不注册到生产网关、不修改资源白名单或启动扫描，也没有覆盖安装任何 Test/正式版。

CDP 与 React service accessor 只是短期验证入口。它复用 renderer 的 connection，V4 同会话订阅可能被桌面 UI 替换，因此仅用于独立测试会话：协议操作结束先 detach，再在桌面打开检查。订阅失效时不自动抢回。正式接入仍需独立、可恢复的原 host 连接，随后才能验证桌面与手机同时打开、冷会话恢复、更新后的路由重建和多窗口。

协议原型详情按需取有界快照，不实现连续流式投影及历史分页；它不能代替现有共享聊天状态、滚动保留、附件预览与权限卡片。接入正式共享 UI 时继续沿用这些组件，不能把此实验入口包装成另一套产品界面。Windows 的直接协议证据见上文，手机与 HTTPS 后续证据见共享手机原型记录；Linux 原生接入、SSH 和 App 重启后的恢复不由本篇证明。这里的恢复证据仅为 Bridge 到原 App 的连接中断。

回执 query 当前提供原生核对结果，但不会将共享数据库中既有 unknown 自动改写为 accepted；因此相同 ID 仍保持不可重放。正式交互需复用共享发送状态并明确呈现核对结论。

临时调试入口仅在本机本轮验证期间开启；收尾即时核对全部原生任务无活跃/排队/审批/后台作业后正常退出，不强杀。不推送、不发布、不改变 v2.1.7 版本号。

## 相关记录审计

[共用原生任务状态](2026-10-29-shared-chat-progress.md) 部分重叠，继续沿用共享状态入口；[原生项目附件](../bug-fix/2026-10-29-native-project-attachments.md) 部分重叠，继承“模型读到不等于原生 UI 可见”的证据边界。两篇既有决定不变，无完全吸收或需要替换的记录。

模型、Skill、提醒和私有账号快照的后续开放见 [ZCode 共享能力](2026-10-31-zcode-shared-capabilities.md)，该部分由新记录取代；原型 owner、版本和会话范围继续保留。

当前同项目多会话与原生工作模式接入见[共用原生工作模式与 ZCode 新建聊天](2026-11-01-native-work-modes-and-zcode-create.md)；该决定部分取代本文的固定单会话范围，保留原 owner、模型与未知回执约束。
