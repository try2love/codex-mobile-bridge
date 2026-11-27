# 实现说明

## 目标与执行边界

手机网页可创建 Codex App 新聊天，并控制已有聊天。网关不执行模型任务。发送消息、更新设置和审批均绑定 `hostId + conversationId + ownerClientId`，由 App 中相应 owner 执行。

默认继承已有会话的设置。只有用户显式选择模型时，才更新 `model` 和 `effort`；provider、认证和审批策略不随网关操作迁移。

## 新建聊天

登录后通过 `GET /api/projects` 选择桌面保存的本机或 SSH 项目，兼容近期本机聊天的工作目录；也可选择独立对话，工作目录位于 Codex 数据目录的 `mobile-workspaces/<request-id>`。网页不直接提交任意路径或运行时参数。

`POST /api/sessions` 沿用登录、Origin 和 CSRF 校验。`creator.py` 启动短期桌面运行时，仅调用 `initialize`、`thread/start`、`thread/name/set`，创建持久化的空会话并退出，不调用 `turn/start`。新会话使用当前 Codex 配置，不覆盖模型、provider、认证或权限。辅助进程以桌面 originator 保存会话，并选择现有历史读取器支持的 legacy 历史格式。

创建后通过 `codex://threads/<id>`（SSH 带 `hostId`）打开电脑上的 Codex，由桌面恢复聊天并提供 IPC owner；手机在 owner 连接后才允许发送。打开桌面失败时返回已创建的 ID 和提示，不会把创建成功误报为需要重建。

新建请求 ID 和结果写入私有 `new_threads.json`，重复请求返回同一会话；超时且结果未知时不重放。仅明确未创建的错误允许重试。SSH 创建使用桌面已保存的 SSH 别名和远端运行时，不安装远端文件。桌面接管和 SSH 兼容性仍依赖所安装的 Codex 版本。

## 会话发现

本地通过只读 SQLite 连接读取 `$CODEX_HOME/state_*.sqlite`，筛选桌面聊天并排除子代理。尚无 IPC 快照时，可读取原始会话记录作为历史展示。不会写入原数据库或会话文件。

发现兼容 `Codex Desktop`、`codex_work_desktop`，以及 `originator` 为空且 `source=vscode` 的旧桌面记录。列表与单会话读取使用同样的来源和子代理限制。

HTTP 阅读请求先返回，保存历史和实时连接在后台加载，通过原有 SSE/长轮询更新页面。每条会话只发起一个后台连接任务，失败后间隔重试，手动重新连接可跳过重试间隔。SSH/SQLite 元数据读取不占用会话总锁；写操作仍须确认原生 owner 已连接。只读超时与写入结果未知使用不同提示。

项目与 SSH 主机映射来自 App 的 `.codex-global-state.json`。远端只读取 App 已保存且有关联项目/会话的 SSH 别名；只读辅助脚本使用 `ssh` 和远端 Python，不向远端安装文件。

SSH 参数包含非交互认证、严格主机指纹检查和禁用额外转发。远端模型/Skill 目录由远端 Codex 运行时读取，不复用 本机文件路径。

本地与远端列表在合并后按最近交互时间排序、分页。项目优先采用显式归属，其次按最长工作目录前缀匹配。项目键带主机 ID，避免同名项目相互混淆。

## 原生 IPC

macOS 使用 `$CODEX_HOME/ipc/ipc.sock`，Windows 使用 `\\.\pipe\codex-ipc`，两者均为 4 字节小端长度前缀，后接 UTF-8 JSON 帧。`--ipc-path` 可覆盖地址。

Windows 使用 CPython 标准库 `_winapi` 的 overlapped I/O 读取原始字节流，不使用 multiprocessing 的消息封装。关闭时先取消未完成 I/O，等待其结束后再释放 handle；发送有超时，空闲读取可被停止操作唤醒。不会另起 agent 替代桌面 owner。

| 操作 | 当前实现 |
| --- | --- |
| 初始化 | `initialize`，获得当前 IPC client ID |
| 本地 owner 查询 | `thread-owner-discovery` |
| 会话订阅 | `thread-stream-following-changed` |
| 状态 | `thread-stream-state-changed` 的 snapshot / patches |
| 新消息 | `thread-follower-start-turn` |
| 补充与停止 | `thread-follower-steer-turn` / `thread-follower-interrupt-turn` |
| 模型设置 | `thread-follower-update-thread-settings` |
| 历史加载 | `thread-follower-load-complete-history` |
| 授权与回答 | 对应的 command/file/permissions/user-input/MCP follower 方法 |

SSH owner 在已验证版本中不能总由本地 owner 查询获得。网关先广播带 `hostId` 的订阅，从匹配会话的快照识别 owner，后续只接受该 owner 的状态。远端 follower 操作携带外层 `hostId`，协议版本按当前 App 约定调整。

这些方法来自当前安装版本的协议适配，不代表 OpenAI 对该接口稳定性的承诺。仓库仅分发网关实现，不分发 App 包或提取出的 App 源文件。

## 状态与重连

网页接收规范化后的会话快照。原生 patches 只有在 `baseRevision` 与本地一致时应用；失配时重新订阅完整快照。历史、工具输出、文件差异和待回答请求来自该状态。

局域网默认使用 SSE，Cloudflare Quick Tunnel 使用有登录校验的长轮询；SSE 连续失败后也会回退。切换聊天时用代次标识排除旧连接回包，浏览器草稿和 Skill 选择按主机与会话隔离。

## 消息与审批

每条手机消息携带唯一提交 ID；发送前把状态写入私有记录。确认失败时保留 unknown 状态，不自动重放。排队消息在原会话空闲时发送，用户可撤回。SSH 主机分别保存提交记录。

审批回应绑定当前仍存在的 request ID，不允许通过 HTTP 发起任意 RPC。命令、文件和权限请求仅支持实现中明确允许的决定；过期或不支持的请求不会转发。

App 的异步问题通过原生 questionItemId 格式回答。若原任务仍在运行，则补充当前任务；否则在同一聊天启动回答该问题的新一轮。

## 模型与 Skill

Windows 自动发现用户目录中的 App 运行时、常见安装目录、MSIX 包及 PATH；`--codex-bin` 可明确选择桌面对应版本。仅本地目录查询使用该覆盖，SSH 目录仍由远端运行时读取。

目录辅助进程只允许 `initialize`、`model/list`、`skills/list`。它不会调用 `thread/start`、`thread/resume` 或 `turn/start`。

网页选择的 Skill ID 来自目录，服务端按 ID 解析受信目录项，再转为原生 `{type: "skill", name, path}` 输入；网页不能直接传入任意技能文件路径。

## HTTP 与文件

账号密码采用 PBKDF2-HMAC-SHA256，登录 Cookie 使用 HttpOnly、SameSite，HTTPS 入口加 Secure。Host 和 Origin 必须在允许列表；写操作还需 CSRF 令牌。

聊天内容、事件流、长轮询和附件接口均需要登录。文件访问仅允许当前聊天引用的本地工作目录或 visualizations 文件，并校验解析后的真实路径及大小。远端文件不映射到 本机文件系统。

## 模块

网关停止使用带随机实例令牌的本地控制文件，由服务主动调用 `shutdown`，再关闭隧道、IPC 和 HTTP。`stop.py --config` 与启动配置对应，不依赖 Unix 信号或 Windows PID 强制终止。文件统一使用 UTF-8；Windows 权限继承目录 ACL。

| 模块 | 职责 |
| --- | --- |
| `run.py` / `stop.py` | 启动参数、进程记录、停止 |
| `bridge/ipc.py` | 原生帧传输、请求白名单和事件分发 |
| `bridge/transport.py` | Unix socket / Windows 命名管道字节流 |
| `bridge/lifecycle.py` | 跨平台停止请求与实例令牌校验 |
| `bridge/service.py` | 会话同步、项目选择、创建去重、操作路由和队列 |
| `bridge/creator.py` | 创建空会话、保存 ID、调用桌面链接接管 |
| `bridge/store.py` / `remote.py` | 只读发现、SSH、项目与主机映射 |
| `bridge/model.py` | 历史和请求的规范化 |
| `bridge/catalog.py` | 模型与 Skill 元数据 |
| `bridge/auth.py` / `httpd.py` | 登录、同源校验、HTTP、SSE、轮询 |
| `bridge/files.py` | 引用文件的范围校验与解析 |
| `bridge/tunnel.py` | 临时 HTTPS 隧道生命周期 |
| `web/` | 手机浏览器界面 |
| `tests/` | 合成数据与模拟 App IPC 回归 |

## 1.5：恢复、聊天管理与桌面偏好

`POST /api/creations` 立即返回请求回执，后台创建；GET 查询进度，POST recover 按 `thread_source=codex-mobile:<request-id>` 精确查找持久化结果。请求记录跨网关重启保存，结果未知不重放；最近 15 条记录可在手机找回。旧客户端同步创建接口仍保留。

`runtime.py` 将短期进程限制为创建、读取与聊天元数据 RPC，不执行模型任务。聊天管理校验桌面来源；归档前确认实时空闲且无待处理消息，删除仅允许已归档且确认 ID 匹配的聊天。归档/恢复通过既有 IPC 广播通知桌面。手机置顶保存在独立的 `phone_chats.json`，不更改桌面排序。所有新增 HTTP 操作沿用登录、Host/Origin 与 CSRF 检查。

连接诊断区分手机网络、入口不可达、桌面通道和任务错误；入口不可达时不凭空断言电脑离线或隧道故障。不会自动重发模型消息。

`desktop_support.py` 用 Win32 托盘和当前用户 Run 启动项提供可选后台运行。默认关闭仍退出；只有用户选择托盘选项才改变关闭行为。更新源由用户配置，检查时仅请求 HTTPS JSON；缺省明确标为未配置，不自动下载或安装。启动项指向持久化 EXE，不指向 PyInstaller 临时目录。
