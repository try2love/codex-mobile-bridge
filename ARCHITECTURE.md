# 实现说明

代码目录、修改入口及平台边界见 [开发架构与修改导航](docs/architecture/README.md)。本文继续记录运行机制和安全约束。

## 目标与执行边界

手机网页控制 Codex App 聊天，并可在已保存项目中新建空聊天。Codex 页面内的 WSL 接入支持直接创建及恢复原生线程；明确创建或继续时可用已安装 Codex 的原生 daemon start 启动共享服务。网关不替换已有执行器或聊天。APP 来源聊天的执行与审批操作绑定 `hostId + conversationId + ownerClientId`，由 App 中相应 owner 执行；CLI 来源直接使用所选主机和配置的原生 CLI。重命名是独立的元数据操作，使用所选主机的短期官方运行时调用 `thread/name/set`，不加载执行任务、不调用 `turn/start`，也不改变 provider、认证或审批策略。

默认继承已有会话的设置。显式选择模型时更新 `model`、`effort` 或速度档位。账号切换是独立的全局操作：仅桌面端添加凭据，双端选择已保存接入，退出官方桌面后替换本机认证及默认 provider，再启动核验。已有聊天的 provider 和审批策略不直接迁移；详情见 [账号与接入](docs/account-switching.md)。

## 原生 Goal 控制

App 执行的本地聊天，其 Goal 元数据通过受限的官方运行时连接读写，只允许 `initialize` 和 `thread/goal/get/set/clear`；该连接不加载或恢复聊天，不调用 `turn/start`，也不修改 provider、认证或审批设置。原桌面 owner 继续拥有实际执行和审批。已连接原生 CLI 的本地或 SSH 聊天通过其同一 daemon 读写 Goal，能力经原生接口探测；两条路径共用操作 ID 与结果确认规则。当前 follower IPC 没有 Goal 写接口，因此 SSH 上由 App owner 执行的聊天仍不支持 Goal 控制；不能把此通路当作任意 App Server RPC 代理。

创建和每一次恢复有独立的持久化操作 ID；该操作的重试沿用 ID 和启动消息，后续恢复不能复用历史启动 ID。写入断连、超时和重启遗留的 sent/pending 均作为结果未知处理，只依据可确认的原生状态核验，不自动重放写入。手机提交所见目标的身份和状态，拒绝对已经变化的目标执行新操作；原生接口尚无原子条件更新，桌面与手机同时修改仍应避免。

修改目标要求 paused，通过官方 set 替换内容，重置统计、保留预算且保持 paused。暂停、修改和关闭撤销未发送的启动消息；队列派发前再次要求匹配的 active 目标。暂停/关闭本身不发送 turn interrupt，用户需要立即中断当前回复时使用独立的停止操作。

## 会话发现

Windows 可在应用管理的 Codex 组内显式扫描并启用 WSL 接入，详见 [WSL 说明](docs/wsl-codex.md)。默认 Codex 列表按全部 / CLI / APP / WSL 分类；独立 Codex 配置以独立接入 ID 展示，账号、项目和会话按配置隔离。共享管理页面和导航契约见[客户端外壳](.agents/notes/implemented/architecture/2026-11-02-shared-client-shell.md)。WSL 保留 `source=cli / runtime=wsl`，不将展示分类作为执行能力。原生 APP / CLI 与各 WSL 子项分别启停，顶层状态聚合；只有 WSL 可用时也可列出环境。应用 ID 沿用 `wsl:<摘要>` 作为内部路由和缓存身份，原 IPC owner、账号、cwd、连接世代及操作回执继续隔离。列表与历史读取不恢复线程。WSL 账号由原工具或 Magpie 管理，不回退到 Windows 账号；HTTP 沿用登录、Origin、CSRF 和文件边界。WSL 停用与写入共用门锁，旧请求在停用后不能继续写文件或创建聊天，关闭接入不退出原 CLI。

本地通过只读 SQLite 连接读取 `$CODEX_HOME/state_*.sqlite`，筛选桌面聊天并排除子代理。尚无 IPC 快照时，可读取原始会话记录作为历史展示。WAL 模式且 `-wal` 缺失时，先复制到私有临时目录，并核对复制前后主文件的身份、大小和修改时间以及 WAL 仍不存在，再读取副本；并发变化时丢弃副本并等待重试。已有 WAL 时直接只读查询，保留最新提交。发现与历史读取不直接写入原数据库或会话文件；用户新建聊天时由官方运行时持久化新记录。

发现兼容 `Codex Desktop`、`codex_work_desktop`、网关创建来源 `codex_mobile_bridge`，以及 `originator` 为空且 `source=vscode` 的旧桌面记录。列表与单会话读取使用同样的来源和子代理限制。

HTTP 阅读请求先返回，保存历史和实时连接在后台加载，通过原有 SSE/长轮询更新页面。每条会话只发起一个后台连接任务，失败后间隔重试，手动重新连接可跳过重试间隔。SSH/SQLite 元数据读取不占用会话总锁；写操作仍须确认原生 owner 已连接。只读超时与写入结果未知使用不同提示。

网页点击或打开 APP 来源会话时，并行读取渐进历史并通过带 CSRF 的重连接口自动连接原生 owner。首屏不等待激活完成，切换会话取消旧页面请求，首次历史读取失败会自动重试。后台阅读、轮询和通知监听本身只订阅状态。打开会话、发送、处理授权或点击“重新连接”时，优先等待后台订阅；仍无 owner 才通过已有 `codex://threads/…` 链接让原 App 加载聊天，最多等待约 20 秒。该兜底可能切换桌面当前聊天，不调用第二个 app-server，不改 provider。连接失败时操作尚未发送，草稿保留。原 App 因 provider 缺失等配置问题无法恢复的聊天，网关也不会擅自迁移。

APP 来源使用原 App owner，CLI 来源直接使用所选主机及 CODEX_HOME 的原生 CLI daemon；CLI 不探测或复用 App owner，只有显式继续才连接或冷恢复。CLI 原生接入失败时保持失败，不切换到 App。IDE 保留先核验原 VS Code owner、确认缺席后才允许显式原生接续的边界。原生 writer 锁仍由官方运行时维护，网关不清锁、不终止持有者，也不保证 App 与 CLI 可同时写同一聊天。详情见 [CLI 接入说明](docs/codex-cli.md)及[来源路由决定](.agents/notes/implemented/bug-fix/2026-11-11-codex-source-execution-routing.md)。

模型、权限、Skill 与工作模式使用独立能力字段，不借用会同时开放重命名等操作的 `canConfigure`。APP 与已连接原 VS Code owner 的聊天使用各自既有通路；CLI 的目录、设置、Skill、计划请求和本机/SSH Goal 使用同一原生 daemon。读取目录不激活会话，显式设置只更新所选模型/推理强度或经策略检查的权限字段，不替换 provider、认证或 cwd。原生 CLI 权限以 resume 的实际配置与 thread/settings/updated 事件为准，不把请求值投影为已生效；权限策略读取来自执行端，不依赖网关电脑的桌面开关。

项目与 SSH 主机映射来自 App 的 `.codex-global-state.json`。远端只读取 App 已保存且有关联项目/会话的 SSH 别名；只读辅助脚本使用 `ssh` 和远端 Python，不向远端安装文件。

SSH 参数包含非交互认证、严格主机指纹检查和禁用额外转发。远端模型/Skill 目录由远端 Codex 运行时读取，不复用 本机文件路径。

SSH 传输失败按主机采用带抖动的指数退避，约从 30 秒增至最多 10 分钟；账号管理可手动提前重试，在途探测合并。普通操作按主机隔离，账号变更和应用启停仍全局互斥。聚合列表、活动及通知的 SSH 元数据在后台读取；本机断连会话继续只读刷新保存历史，发送仍须连接原生 owner。此机制不修改 Codex 原生应用自身的 SSH 重连策略，验证边界见 [SSH 离线隔离记录](.agents/notes/implemented/bug-fix/2026-10-30-ssh-offline-isolation-and-backoff.md)。

本地与远端列表在合并后按最近交互时间排序、分页。Codex 聊天列表每批读取 100 条，接近底部时自动续载；失败保留已加载内容，再次向下滚动可重试。项目优先采用显式归属，其次按最长工作目录前缀匹配。项目键带主机 ID，避免同名项目相互混淆。

项目路径比较按路径本身识别 Windows 语法，不依据网关所在系统推断 SSH 目标。Windows 盘符、UNC 和扩展路径前缀在比较时统一分隔符与大小写，POSIX 路径保留大小写；归类不改写聊天原始 `cwd` 或 App 的项目成员关系。未匹配已登记项目时，分组名与 CLI 项目列表按目标路径语法取末级目录名；Windows SSH 路径不会显示为整条路径，POSIX 目录名中的反斜杠保持原义。

Codex 列表下拉刷新使用浮层提示与按动画帧更新的位移，不改变滚动区域高度；拖动期间暂停自动刷新。成功提示停留 500 毫秒后淡出，失败保留点击重试。列表展示内容未变化时保留已有聊天节点。

## 新建聊天

登录后的 `GET /api/projects` 返回 App 已保存的本机及 SSH 项目；`POST /api/sessions` 接受项目键、名称和请求 UUID。服务端重新解析已保存项目，创建线程接口不接受客户端任意目录、主机、程序路径或配置覆盖。用户先用 POST /api/projects/browse 的目录浏览器选择目录或在当前目录中新建，再使用带登录、Origin 和 CSRF 校验的 POST /api/projects 明确登记：主机必须已登记，在目标系统验证绝对路径、可读写性并规范化；勾选创建时仅在已存在的父目录下创建最后一层。网关自己的 projects.json 保存 canonical cwd 与目录身份，创建前再次核对；此登记只保存目录选择。显式创建 Codex App 项目聊天时，才通过原生项目接口创建或复用项目并关联新线程，不直接改写 App 全局项目配置文件。创建接口沿用 Host、Origin、登录和 CSRF 校验。

`bridge/features/sessions/create.py` 使用所选主机的官方运行时完成 `initialize → thread/start → thread/name/set → thread/read(includeTurns=true)`，随后关闭 stdio 并等待进程退出。本机新目录先通过仅含 path 的 `codex://threads/new` 让原 App 完成原生项目登记并打开独立的空白输入区，不携带 prompt 或消息。确认 App 的 local-projects 已保存该目录后才创建线程；没有确认时不会报告成功。登记与线程创建阶段分别落盘：登记结果未知时仅重新读取原生记录，不重复打开登记链接；线程创建开始后结果未知则不再创建。支持原生项目归属的运行时先分页读取 `project/list`，匹配已登记项目，再把原生 `projectId` 传给 `thread/start`，并核对 start 与持久化 read 返回的归属；不会单独创建原生侧栏不可见的 app-server 项目。旧运行时保留已登记项目的 cwd 创建通路。项目列表按主机与规范目录去重，不重新分配已有线程。尚未核验 SSH 新目录的原生侧栏登记通路，因此这类请求在创建聊天前提示先在 App 添加项目；已有 SSH 项目和 CLI 创建沿用原通路。读取完整空历史是必需步骤：仅设置名称会留下目录元数据，却未必落盘可恢复的 rollout。整个辅助进程不调用 `turn/start`，不会发送模型任务。新线程使用该主机、工作目录的运行时默认配置；不克隆已有聊天的临时设置。

创建结果按 UUID 写入网关自己的 `creations.json`，重试复用同一聊天；请求可能提交但结果未知时不自动重放。辅助进程退出后，通过 `codex://threads/<id>?hostId=...` 打开原桌面 App，桌面接管后才允许发送和审批。此步骤会切换桌面当前页面；打开失败保留已创建 ID。SSH 创建借助已有非交互 SSH 通道执行相同辅助代码，不在远端安装网关。

DSH 项目聊天通过原生 workspace registry 的 `resolveByPath/create` 复用或登记工作区，再以 `workspaceId` 调用 `sessionController.create`。单独传入 cwd 不产生原生项目成员关系，因此不用于新建项目聊天。Claude Desktop Code 准确匹配已核验的原生 start 工作区信任拒绝时，标记为已阻止创建，界面保留目录、名称和首条消息，引导用户在 Desktop Code 亲自确认工作区信任后显式重试；网关不调用信任写接口。只有准确匹配的原生 start 信任拒绝可以解除该请求的未确认写入标记；断连、超时和其他错误仍保持结果未知，不自动重放。

新建入口与原生 IPC 均受 App/运行时版本影响。Mac 空聊天创建和实际 App 接管已验证；本次新增原生项目接口及归属检查已通过安装版本的只读协议核验与合成回归，尚未执行真实项目创建或核验原生侧边栏刷新。Windows 及 SSH 创建目前只有模拟路径检查，不能视为实机通过。

## 原生 IPC

macOS 使用 `$CODEX_HOME/ipc/ipc.sock`，Windows 使用 `\\.\pipe\codex-ipc`，两者均为 4 字节小端长度前缀，后接 UTF-8 JSON 帧。`--ipc-path` 可覆盖地址。

Windows 使用 CPython 标准库 `_winapi` 的 overlapped I/O 读取原始字节流，不使用 multiprocessing 的消息封装。关闭时先取消未完成 I/O，等待其结束后再释放 handle；发送有超时，空闲读取可被停止操作唤醒。不会另起 agent 替代桌面 owner。

| 操作 | 当前实现 |
| --- | --- |
| 初始化 | `initialize`，获得当前 IPC client ID |
| owner 识别 | 匹配 host / conversation 的订阅快照来源 |
| 会话订阅 | `thread-stream-following-changed` |
| 状态 | `thread-stream-state-changed` 的 snapshot / patches |
| 新消息 | `thread-follower-start-turn` |
| 补充与停止 | `thread-follower-steer-turn` / `thread-follower-interrupt-turn` |
| 模型设置 | `thread-follower-update-thread-settings` |
| 历史加载 | `thread-follower-load-complete-history` |
| 授权与回答 | 对应的 command/file/permissions/user-input/MCP follower 方法 |

本地和 SSH 均先广播带 `hostId` 的订阅，从匹配会话的快照识别 owner，后续只接受该 owner 的状态。首次短等待结束后保留订阅，以接收延迟到达的快照；不把尚未加载的聊天当作历史读取错误。远端 follower 操作携带外层 `hostId`，协议版本按当前 App 约定调整。

这些方法来自当前安装版本的协议适配，不代表 OpenAI 对该接口稳定性的承诺。仓库仅分发网关实现，不分发 App 包或提取出的 App 源文件。

## 状态与重连

网页接收规范化后的会话快照。原生 patches 只有在 `baseRevision` 与本地一致时应用；失配时重新订阅完整快照。历史、工具输出、文件差异和待回答请求来自该状态。

桌面分页快照只含最近若干轮时，展示层按首个重合 turn ID 保留保存历史的前缀，再接上实时内容；完整快照仍具有最终权威，可正确反映回退或删除。此合并不改变原生 patch 基础，也不从保存历史推断新的待授权请求。

局域网默认使用 SSE，Cloudflare Quick Tunnel 使用有登录校验的长轮询；SSE 连续失败后也会回退。切换聊天时用代次标识排除旧连接回包，浏览器草稿和 Skill 选择按主机与会话隔离。

## 消息与审批

项目归类按路径语法比较，只有存在歧义的纯 `//` 路径借助实际执行主机的平台区分 UNC 与 POSIX。本机使用本机平台，SSH 列表在同一次只读请求中返回目标平台标记，展示前移除标记。Windows 的盘符、UNC 与扩展路径大小写归一化，单斜杠 POSIX 路径保留大小写；不修改会话原 cwd、原生项目登记或 host 身份。

每条手机消息携带唯一提交 ID；发送前把状态写入私有记录。确认失败时保留 unknown 状态，不自动重放。排队消息在原会话空闲时发送，用户可撤回。SSH 主机分别保存提交记录。

运行中的普通发送、任务结束后的补充请求在写入提交记录前拒绝，HTTP 返回 `400` 与 `code=send_not_submitted`。网页仅据这一明确结果释放对应的未确认标记，保留草稿，允许用户修改发送方式；断网、其他错误和结果未知继续保留原提交 ID，不自动重发。这是对 `ba10283` 持久化草稿保护误拦明确拒绝请求的回归修复，恢复切换发送方式后重新提交的既有体验。

多标签页共享的 Codex 提交需要逐次登记发送尝试，只有同一提交的全部尝试均明确未提交时才允许释放；较早的拒绝不能清除另一页仍在途或结果未知的尝试。浏览器协调只锁本地状态更新，不跨网络等待。

审批回应绑定当前仍存在的 request ID，不允许通过 HTTP 发起任意 RPC。命令、文件和权限请求仅支持实现中明确允许的决定；过期或不支持的请求不会转发。

App 的异步问题通过原生 questionItemId 格式回答。若原任务仍在运行，则补充当前任务；否则在同一聊天启动回答该问题的新一轮。

## 模型与 Skill

Windows 自动发现用户目录中的 App 运行时、常见安装目录、MSIX 包及 PATH；`--codex-bin` 可明确选择桌面对应版本。仅本地目录查询使用该覆盖，SSH 目录仍由远端运行时读取。

目录辅助进程只允许 `initialize`、`model/list`、`skills/list`。它不会调用 `thread/start`、`thread/resume` 或 `turn/start`。

网页选择的 Skill ID 来自目录，服务端按 ID 解析受信目录项，再转为原生 `{type: "skill", name, path}` 输入；网页不能直接传入任意技能文件路径。

## HTTP 与文件

账号密码采用 PBKDF2-HMAC-SHA256，登录 Cookie 使用 HttpOnly、SameSite，HTTPS 入口加 Secure。Host 必须在允许列表；提供 Origin 的请求须与当前 Host 的协议和完整域名／端口对应，写操作还需 Origin 与 CSRF 令牌。不能仅因 Host 与 Origin 各自在允许列表，就接受二者交叉配对。

HTTP 监听队列显式设为 128，以容纳 HTTPS 代理将首屏资源集中转发到本机时的连接突发；各监听器共享的 48 个活动连接上限保持不变。GET 不读取请求体，遇到分块编码或非零／无效长度时在响应后关闭连接，避免 Cloudflare 转发的空分块结束标记被当作下一条请求。无请求体的 GET 仍支持连接复用，写操作仍拒绝分块请求体。

聊天内容、事件流、长轮询和附件接口均需要登录。文件访问仅允许当前聊天引用的本地工作目录或 visualizations 文件，并校验解析后的真实路径及大小。远端文件不映射到 本机文件系统。

## 模块

桌面控制器通过白名单 Electron IPC 调用 `desktop.py` 的独立 JSON 工作进程；网关启停、更新和访问范围配置不监听网络。网络恢复布尔开关独立保存在 network-adaptation.json，通过本地控制通道及已登录且启用密码保护的 /api/network-adaptation 修改；写入沿用 Host、Origin、CSRF 校验，不提供网卡或域名范围修改。PushPlus 全局配置与测试允许通过手机 HTTP 操作，必须经过 Host、Origin、登录及写入 CSRF 校验，仅接受 PushPlus 字段，不返回 Token。该配置影响整个网关的关注聊天，不按登录设备隔离。打包版本执行随包提供的 PyInstaller 运行时，网关以独立后台进程运行，退出控制器不会停止网关。Windows 使用 NSIS 安装包或完整 ZIP 目录，避免临时自解压目录随控制器退出被删除而破坏后台运行时。

Windows `desktop/shell/tray.cjs` 管理托盘状态、手机地址和两种退出动作；仅显式停止才调用网关的协作停止协议。主窗口关闭时隐藏，第二次启动恢复原窗口；退出前销毁托盘并允许窗口关闭。状态请求合并，菜单操作避免重复提交；停止失败保留控制器并显示错误。macOS 使用同一菜单提供菜单栏入口，关闭控制面板后仍可从菜单栏或 Dock 重新打开。登录自启动由用户在高级设置中启用：Windows/macOS 使用系统登录项，Linux 已安装的桌面包使用用户 XDG autostart；AppImage 注册原始文件路径，不注册临时挂载目录。Windows/macOS 登录启动隐藏控制面板，Linux 保持窗口可见。启用登录自启动同时启用网关自动启动；关闭登录项保留后者。

网关停止使用带随机实例令牌的本地控制文件，由服务主动调用 `shutdown`，再关闭隧道、IPC 和 HTTP。`stop.py --config` 与启动配置对应，不依赖 Unix 信号或 Windows PID 强制终止。文件统一使用 UTF-8；Windows 权限继承目录 ACL。

| 模块 | 职责 |
| --- | --- |
| `run.py` / `stop.py` | 启动参数、进程记录、停止 |
| `bridge/clients/codex/ipc.py` | 原生帧传输、请求白名单和事件分发 |
| `bridge/clients/codex/transport.py` | Unix socket / Windows 命名管道字节流 |
| `bridge/app/lifecycle.py` | 跨平台停止请求与实例令牌校验 |
| `bridge/app/service.py` | 会话同步、操作路由、去重和队列 |
| `bridge/features/sessions/store.py` / `remote.py` | 只读发现、SSH、项目与主机映射 |
| `bridge/features/sessions/model.py` | 历史和请求的规范化 |
| `bridge/clients/codex/catalog.py` | 模型与 Skill 元数据 |
| `bridge/features/sessions/create.py` | 创建并持久化空聊天，退出辅助运行时，交给桌面接管 |
| `deploy/nas/` | NAS HTTPS 反代教程及可选 Docker 入口 |
| `bridge/features/auth/auth.py` / `httpd.py` | 登录、同源校验、HTTP、SSE、轮询 |
| `bridge/features/workspace/files.py` | 引用文件的范围校验与解析 |
| `bridge/features/network/tunnel.py` | 临时 HTTPS 隧道生命周期 |
| `web/` | 手机浏览器界面 |
| `tests/` | 合成数据与模拟 App IPC 回归 |

## 固定 HTTPS 入口

桌面 `connections` 保存可独立启用的 Quick Tunnel、自有服务器 SSH 和 NAS 配置，局域网监听由 `lan` 控制。`lanInterfaces: null` 表示自动内网地址：只发布非虚拟网卡上的 RFC1918 地址，Parallels/WSL/Docker/VPN 等虚拟网卡和公网地址不会成为入口或通知；数组保存网卡身份，同一网卡 DHCP 地址变化后由网关运行时替换 listener 并同步通知，空数组只保留回环，未选网卡不会自动开放。`lanAddresses` 仅兼容旧配置。各监听器共享同一个认证、配对、通知和会话状态。`localAccess: false` 关闭本机网页入口，回环仅允许无会话信息的私有 `/api/health` 检查和保留 Host 的已授权 HTTPS 隧道；停机关闭全部监听器。地址和默认路由签名需连续 6 秒稳定才应用；旧 HTTP 网卡来源不会跨启动继续信任。临时 Quick Tunnel 在默认路由签名稳定变化后由监视器主动重建并先移除旧来源，公网域名会变化；重建期间旧地址不再返回，新地址就绪后补发通知。旧单入口配置在读取时转换，保存前不改写文件。`bridge/features/network/access.py` 验证地址，生成不含凭据的 Caddy/Nginx Compose 部署包；所有启用的固定 URL 自动加入允许源，第一个启用的固定 URL 优先用于通知链接（显式 clickBase 仍优先）。停用或删除配置时移除对应托管固定源，保留其他手动 HTTPS 源。

`accessMode: external` 只保存用户自管穿透或同机 HTTPS 反代的 `publicUrl`，要求可信 HTTPS 根地址，可包含端口；上游由用户指向电脑网关的实际回环端口。该模式不管理 SSH、frpc 或反代进程，即使 `lan: false`、`localAccess: false` 仍保留受公开 Host 约束的回环上游。跨设备 NAS 继续使用 `nas`，要求启用 LAN 并填写端口匹配、非回环的 `proxyUpstream`。固定入口和扫码前的探测不发送凭据、不跟随重定向、不跳过证书验证；仅核对本次网关实例，不用 `instanceId` 迁移或授予登录凭据。

`bridge/features/network/ssh_tunnel.py` 管理独立 OpenSSH 子进程，复用已有 SSH 目标和身份，以 `-R 127.0.0.1:服务器端口:127.0.0.1:电脑端口` 回程。启用 BatchMode、StrictHostKeyChecking、ExitOnForwardFailure 与保活，不复用 ControlMaster。失败后台退避重连，不阻塞 LAN 服务。各 SSH 配置独立保存状态与日志，状态关联当前网关 PID；服务器 GatewayPorts 应使用 no/clientspecified，不能强制公网绑定。

`/api/auth` 返回非秘密的本次运行 instanceId。固定入口检测仅从本机管理进程请求本机与已保存 HTTPS 地址，核对实例一致，不携带登录凭据，不跟随重定向。配置包导出、剪贴板和入口检测仅经受保护的本机 Electron IPC 提供，手机 HTTP 不提供这些操作。

临时 Cloudflare 握手在后台进行，避免等待外网隧道拖延 LAN 和其他入口的服务。关闭网关时停止各隧道。

`web/shared/i18n.js` 是电脑与手机共享的简体中文 / English 文案表，无外部翻译服务。静态节点通过 data-i18n 标记，动态界面显式翻译；聊天正文、命令、用户输入及外部模型/Skill 描述不进入翻译流程。语言保存在各界面的 localStorage，切换不重载会话或提交表单。日志按源展示最新记录优先，保留多行错误记录的内部顺序。


## 一次性扫码登录

`bridge/features/auth/pairing.py` 为每个允许源保存一个有效的内存授权：32 字节随机值的 SHA-256 摘要、随机 ID、5 分钟过期时间与消费状态。刷新替换同源旧授权，消费在锁内完成，正确源且未过期的授权只生成一次标准 12 小时登录会话。网关重启不恢复授权和会话。失败兑换限速独立于密码登录。

签发只走本机 stdio/Electron IPC 与 `GatewayControl` 的私有文件通道：每实例随机 `.pairing-*` 目录，独立请求/响应文件和控制令牌，响应读取后删除、服务正常退出时清理目录。Windows 继承本机目录 ACL；数据目录不能放在共享公共目录。HTTP 没有签发接口。桌面签发前检查地址属于当前显示入口，并从该入口读取 `/api/auth` 核对本机 instanceId；公网探测不携带凭据、不跟随重定向。

Electron 主进程使用 qrcode 本地生成 PNG，渲染器接收图片、授权 ID 与有效期。每张地址卡片的折叠区域独立保留状态，收起撤销、刷新替换、轮询显示已使用/已过期；语言和常规状态刷新不重新签发。

手机首次加载时捕获 `#pair=` 片段并立即通过 `history.replaceState` 清除，随后 `POST /api/pair` 换取 HttpOnly/SameSite Cookie 和 CSRF。兑换要求 HTTP Host 对应的完整 Origin 与授权源一致；HTTPS Cookie 使用 Secure。片段不会作为 HTTP 请求路径或查询串发送，接口也不记录请求体。手机失败时提供原有密码登录入口。二维码仅授予与普通登录相同的访问权限，不绕过 Codex 原生人工确认。

## 桌面应用更新

`desktop/features/updates/updater.cjs` 从固定 GitHub Release 源获取签名清单，使用内置 Ed25519 公钥验证原始载荷，再流式下载并核对长度与 SHA-256。自动检查不自动安装；安装按钮只通过受来源校验的桌面 IPC 调用，HTTP 网关无更新管理接口。

`bridge/features/updates/gateway.py` 负责原安装目录旁的准备、替换与恢复。准备阶段拒绝路径穿越和危险链接，核对包版本、平台、macOS 签名和内置运行时。独立复制的旧运行时等控制面板退出、协作停止原网关后替换目录，再等待新面板确认加载并恢复此前运行的网关。失败时尝试回滚；配置目录保持独立。发布签名身份和事务恢复操作见 [桌面更新文档](docs/desktop-updates.md)。

## Android 更新协助

Android 优先直接检查本项目 GitHub Release，查询失败时可通过当前已登录网关查询版本。APK 下载由用户在「下载并安装」后选择手机直连或电脑下载再传送；取消选择不启动下载，下载失败不自动切换路径。下载卡片允许重新选择，暂停后的继续仍沿用原路径。交互取舍见 [下载方式选择记录](.agents/notes/implemented/feature/2026-10-12-android-update-download-choice.md)。`bridge/features/updates/mobile.py` 只接受固定仓库、版本对应的 APK、有效大小和 SHA-256。手机准备请求的版本、大小和摘要须与网关从官方源取得的记录一致；不接受手机指定外部 URL 或磁盘路径。

`GET /api/mobile/update?current=<版本>` 查询版本；带登录和 CSRF 的 `POST /api/mobile/update/prepare` 接受 `{version,size,sha256}`，启动或复用一个后台下载；`GET /api/mobile/update/status/<id>` 返回进度；`GET /api/mobile/update/packages/<id>` 只传送已完成校验的缓存包，并支持 Range/ETag。所有入口要求密码保护的登录会话。共享中继与连接器只放行这些准确的方法和路径，共用同一网关缓存。

网关单次最多下载 512 MiB，只并行下载一个包，最多保留两个任务/缓存；闲置超过 24 小时的缓存按需清理。缓存只属于当前网关运行周期。手机取消准备仅停止等待，电脑任务仍可供已登录手机复用。手机和网关各自校验完整 APK；手机安装恢复复用私有已校验文件，不依赖网关持续在线。网关 Cookie 只发往选定网关，不跟随重定向或发送给 GitHub。详细取舍见 [决策记录](.agents/notes/implemented/feature/2026-10-12-gateway-assisted-android-update.md)。


## PR8 验收反馈调整

Goal 元数据写入后立即读取原生状态确认，不再打开桌面页面或同步调用完整历史 RPC。暂停、修改和清除不依赖 owner 激活；创建与恢复的实际执行仍由原 owner 发送，并保留未知结果不重放和持久化操作 ID。

保存历史从 rollout 末尾按完整轮次读取，首批 2 轮，读到保存历史边缘后按 5 轮扩展；本地缓存按路径、inode、大小和修改时间失效，最多保留 8 个不超过 4 MB 的解析结果。远端沿用相同读取器。历史 IO 与 owner 订阅独立推进；向前补页保留已有消息键、游标和顺序。历史可读不代表 owner 已就绪。

Codex 时间线首屏返回最近 20 条，展示投影初始只整理最近 40 条；用户上翻时按需扩展，取消自动补足 100 条。相同状态序号复用规范化结果，旧正文在进入展示窗口前不计算全文版本或复制到详情索引；已加载窗口不会因新回复到达而向后滑走。原始 Desktop 状态仍保持完整，操作校验不使用展示缓存。当前 Desktop 内部具有 turns/items 分页，但跟随 IPC 只暴露整段历史加载与状态快照，网关不为首屏调用加载全部历史。

网页在内存中最多保留 8 份最近 20 条的展示缓存，按已认证网关身份、主机与会话隔离；退出登录、网关身份变化或已确认的账号切换清理缓存。缓存先画正文，实时读取随后替换；审批、权限、草稿与发送状态不从缓存恢复。完整历史仍只在请求旧页或展开正文时传输。

全会话通知策略保存在 `notification-policies.json`，请求处理默认开启、运行完毕默认关闭，通道本身仍默认关闭。已有显式 watches 保留其选择，每个聊天可对两种事件独立继承或覆盖。通知通过只读列表发现聊天：APP/IDE 沿用原 owner 跟随，CLI 仅从已运行服务读取已加载线程状态，不依赖 App IPC 在线。网关不直接读取 rollout、不启动或激活聊天，观察状态不授予写权限；取消、释放或停用后丢弃迟到回包。首次实时状态作为完成通知边界，不补发旧完成事件。网页关闭后网关继续监听；依赖对应原生服务及 SSH 主机可用。CLI 状态提醒和验证边界见[只读通知记录](.agents/notes/implemented/feature/2026-11-11-codex-cli-passive-notifications.md)。

主页快捷入口属于浏览器显示偏好。最近排序在发送或回复被接受后立即更新，并通过活动查询中的数据库时间校准；仅阅读不更新交互时间。计划意见非空时只允许继续规划，避免执行按钮忽略意见。
