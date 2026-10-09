# 文件操作与客户端上下文用量

本轮基于 `refactor/platform-feature-layout` 的 `9f9a0fb`，源码修改覆盖共享网关、桌面设置、Web、Android/iOS 下载器。未构建、安装、推送或发布应用。

## 使用方式

- 聊天文件链接、文件预览中的下载入口：先选择“定位并打开所在文件夹”或“下载到本地”。定位使用原会话的文件标签页，选中目标文件，保留草稿、标签和分屏。
- 默认点击下载阈值为 **20 MiB**，统一原先工作区 20 MiB 和聊天文件 50 MiB 两种规则。网关“应用与维护”、Web/手机设置中的“文件传输”均可保存新阈值，无需重启网关。范围为 1–1048576 MiB。
- 超阈值普通点击只提供定位。文件列表长按、鼠标右键或 Shift+F10 可明确下载文件/文件夹，不受该阈值限制。文件夹生成 ZIP64，准备完成后显示总字节数；沿用下载浮窗的进度、速度、暂停和继续。
- 图片查看器仍保留原有点击缩放；上传大小和图片在线预览大小不随下载阈值改变。
- 目录下载拒绝链接、Windows reparse point 和特殊文件，避免夹带工作区外内容。空间不足仍会失败；“不限此阈值”不表示设备存储无限。
- 超出项目根目录的已授权 Codex 产物仍可按阈值下载，但不会扩展工作区文件浏览范围。

## 修改导航

| 功能 | 位置 |
| --- | --- |
| 实时阈值与保存 | `bridge/features/workspace/preferences.py`；数据文件 `file-transfer.json` |
| HTTP/本机 IPC | `bridge/api/httpd.py` 的 `/api/file-transfer`；`bridge/app/desktop.py` 的 `transfer_settings` |
| 下载、ZIP、受控路径及快照 | `bridge/features/workspace/workspace.py` |
| 受控聊天文件链接 | `bridge/features/workspace/files.py`；`bridge/clients/manager.py` |
| 选择弹窗、长按、设置表单 | `web/features/workspace/file-actions.js` |
| 目录定位、文件标签 | `web/features/workspace/workbench.js` |
| 浏览器下载暂存 | `web/features/workspace/downloads.js` |
| 原生磁盘下载 | Android `ArtifactDownload.java`；iOS `DownloadManager.swift` |
| 权限按钮、上下文圆环 | `web/features/clients/desktop-sessions.js`、`desktop-session-controls.css` |
| Claude 用量接口 | `bridge/clients/claude/connector.js`、`adapter.py` |
| DSH 权限及用量 | `bridge/clients/deepseek/host.mjs` |

文件相关 API 仍验证登录、Origin/CSRF（修改设置）、会话归属和工作区路径。`explicit=1` 是用户界面的下载方式选择，并非新增身份权限；它只跳过普通点击的大小阈值。中继仅增补固定设置/元信息路由，没有扩大其 HTTP 目的地址或单帧上限。

浏览器通过 IndexedDB 保存分块，避免把整个大文件保留为 JS 字节数组；浏览器存储不可用时，原 50 MiB 内存兜底仍在，并提示改用原生 App。取消和正常离开页面清理暂存；浏览器进程崩溃仍可能留下 IndexedDB 数据，受浏览器站点存储清理约束。

本机大文件和目录最多保留两个私有磁盘快照，空闲 30 分钟后释放，进程正常退出也释放。大文件每次请求重新验证路径、类型和源文件版本；文件夹快照表示下载开始时的内容，后续源文件变化不会拼入下载。快照过期/被替换后，原校验值不一致会要求重新下载。SSH 源码以独立进程运行，不能复用本机的进程内快照缓存；大目录打包仍可能较慢，仍受现有 SSH 子进程 60 秒超时约束。

## Claude Desktop 接口调查

只读检查了本机 **Claude 2.31226.0** 的安装包，没有读取账号凭据、修改安装包、改变签名或启动调试端口。证据是该版本的打包代码，不是稳定的官方公开 API 合约。

| 原生表面 | 观察与本轮处理 |
| --- | --- |
| `LocalSessions`（Code） | 具备会话、消息、权限、模型、思考强度、队列、Git、终端、侧会话、子任务等 IPC 方法。仅已有允许列表与本轮确认的只读用量查询对外提供，不暴露通用 IPC。 |
| `getContextUsageSummary(sessionId)` | 主进程调用活跃会话查询；没有对应进程或查询失败时返回 `null`。确认数值字段为 `totalTokens` 和 `rawMaxTokens`，转换为当前已用/容量，非累计计费用量。 |
| `LocalAgentModeSessions`（Cowork） | 有消息、权限、模型、文件交付、MCP、文件夹授权等方法；未发现同名上下文汇总接口。本轮显示“暂未提供”，不伪造为 0%。 |
| 调试启动 | `index.pre.js` 的发布包启动逻辑拒绝 `remote-debugging-port`/`remote-debugging-pipe` 等参数。不能靠添加参数假定 CDP 可用。 |
| IPC 可达性 | `mainView.js` 中的接口通过 renderer 的 `ipcRenderer.invoke` 调用，并有来源校验；它们不是可以从网关直接访问的通用 HTTP 服务。观察到的 localhost 服务不能据此认定为可替代会话接入的端点。 |

没有验证出可安全替代当前首次 Console 初始化、同时覆盖 Code/Cowork 的无界面接入方式。既有签名 mailbox 连接仍优先复用；没有连接时仍可能需要桌面初始化和系统授权。Windows/macOS 原生初始化方式保留，Linux 的 Claude 自动接入仍未变成支持。后续应逐项适配实际能力及确认流程，不能将原生方法目录直接透传到公网。

用量查询与会话/历史在一个受限 `mobileDetail` 请求中取得，有单独超时，失败不阻断聊天读取。连接协议修订 Claude 4→5、DSH 3→4，便于识别旧加载脚本；产品版本号不变。尚在运行的旧连接器需要按现有受保护流程更新，本轮未重启真实客户端。

DSH **0.2.0-rc.2** 的 `contextPressure` 投影区分 `pressureTokens`、`projectedTokens`、`contextWindow`。使用其当前投影；有未发送内容估算时显示说明。没有容量/用量则未知，不使用累计 `tokenUsage` 替代。

## 验证与限制

- Node 全套源码测试此前 431 通过，1 项 Windows 环境测试跳过；最终增补权限写入/会话切换保护后，相关界面、Claude/DSH 协议、资源清单 65 项回归通过。
- 文件与工作区早期回归 22 通过；最终下载/设置 HTTP、相对路径、Windows reparse 属性模拟 15 项通过，消息/文件引用与存储 34 项通过、2 项 Windows 专属检查跳过。新增设置认证/CSRF/实时生效检查通过。Claude/会话适配回归初次 115 项中协议修订夹具 1 项失败，修正夹具后该项重新通过。
- 浏览器真实 DOM、隔离 HTTP：小文件选择；大文件仅定位；目标高亮；草稿保留；宽屏文件分屏；390px 手机宿主用量弹窗；长按文件、右键目录、移动取消长按、英文文件与用量弹窗。阈值由 20 改为 60 MiB 后，55 MiB 文件立即出现下载选项，无需重启。
- 55 MiB 合成文件在 1 MiB 后暂停再继续，IndexedDB 暂存，最终 57,671,680 字节。浏览器及源文件 SHA-256 均为 `cff99b70f18ca3032e0576b0c4f4cf7ee1b471c9519c4679eec37952c74d2242`。
- ZIP 下载有正确总量、文件名、Range 和归档内容；普通文件被替换、回退修改时间、符号链接、越界路径继续拒绝或触发安全重下载。
- Android/iOS 原生下载器去除旧本地上限需要后续重新构建安装，当前已安装的旧 App 不会因本轮源码编辑自动获得该能力。
- 当前主机是 macOS；没有执行 Windows/Linux 桌面实机、Android/iOS 新版原生包、真实 Claude/DSH 会话或睡眠/锁屏回归。手机宿主 CSS 模拟不等同真机验证。
- 尚无设备功耗测量，不宣称具体续航提升。没有证明完全免 Console/免解锁初始化。
