# Agent Note: ZCode 原生会话协议原型

Status: implemented

## 问题

ZCode 接入必须操作原 App 的 owner、会话、模型、账号与工作区。另起同名 CLI、复制历史或只证明模型能读到路径，都不能证明原生会话接入。桌面 host 使用内部 MessagePort，尚未发现可直接供 Bridge 长期使用的稳定本机外部入口。

## 决策

原型从 v2.1.7 的 d65a610 建立，分支为 feature/zcode-native-prototype。没有使用 agent-connectors-preview 的代码或接入通道。为保留原工作区分支与未提交成果，开发副本位于原仓库 .tmp/zcode-native-prototype/repo；源码由独立 Git 分支管理，临时运行数据不提交。

`bridge/clients/zcode/` 实现显式绑定单条原生测试会话的协议适配器，`scripts/zcode-prototype.py` 是手动验证入口。原型只核验 macOS ZCode 3.17.1，Node 26.12.1。它通过临时 loopback CDP 进入已有 renderer，取得原有 service accessor，调用 App 自己持有的 host；不创建替代 CLI、不修改 app.asar、不读取账号凭据。平台端复用 bundle 与内核进程身份读取器，检查端口仅绑定 127.0.0.1、可执行文件、同用户与进程世代。

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

## 备选方案

官方手机中继能复用桌面 host，但引入官方登录、中继与单手机连接约束，不作为 Bridge 默认入口。另起 `zcode --web` 容易获得 HTTP 服务，但不证明原会话 owner，故不采用。修改 app.asar 能暴露接口，但破坏签名、更新与维护边界，故不采用。直接复制现有客户端 UI 或回执逻辑会再次产生共性行为分叉；本原型复用已有管理器和文件服务，不添加 UI。

## 影响

已证明原 App 协议可实现核心控制能力，尚不是可在手机应用管理中启用的第四个正式客户端。实验入口不注册到生产网关、不修改资源白名单或启动扫描，也没有覆盖安装任何 Test/正式版。

CDP 与 React service accessor 只是短期验证入口。它复用 renderer 的 connection，V4 同会话订阅可能被桌面 UI 替换，因此仅用于独立测试会话：协议操作结束先 detach，再在桌面打开检查。订阅失效时不自动抢回。正式接入仍需独立、可恢复的原 host 连接，随后才能验证桌面与手机同时打开、冷会话恢复、更新后的路由重建和多窗口。

协议原型详情按需取有界快照，不实现连续流式投影及历史分页；它不能代替现有共享聊天状态、滚动保留、附件预览与权限卡片。接入正式共享 UI 时继续沿用这些组件，不能把此实验入口包装成另一套产品界面。Windows/Linux、SSH、手机、HTTPS 和 App 重启后的恢复尚未验证。这里的恢复证据仅为 Bridge 到原 App 的连接中断。

回执 query 当前提供原生核对结果，但不会将共享数据库中既有 unknown 自动改写为 accepted；因此相同 ID 仍保持不可重放。正式交互需复用共享发送状态并明确呈现核对结论。

临时调试入口仅在本机本轮验证期间开启；收尾即时核对全部原生任务无活跃/排队/审批/后台作业后正常退出，不强杀。不推送、不发布、不改变 v2.1.7 版本号。

## 相关记录审计

[共用原生任务状态](2026-10-29-shared-chat-progress.md) 部分重叠，继续沿用共享状态入口；[原生项目附件](../bug-fix/2026-10-29-native-project-attachments.md) 部分重叠，继承“模型读到不等于原生 UI 可见”的证据边界。两篇既有决定不变，无完全吸收或需要替换的记录。
