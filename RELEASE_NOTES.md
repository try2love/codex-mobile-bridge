## v2.2.1-preview.1 · Antigravity 接入、CLI 连接管理与会话体验改进

本次预览版新增 Antigravity 接入，重点完善 CLI 连接释放与恢复、侧边聊天和文件浏览，并修复 Windows 客户端启动、手机连接恢复及模型列表读取中的问题。

### 新增与改进

- **Antigravity 接入。** 在手机和 Web 查看 Antigravity App、IDE、CLI 的本机历史，使用受支持的原生连接继续聊天、新建项目聊天、切换模型、发送附件和处理审批。增加按来源展示的账号信息，以及受支持默认本机 Google 登录的账号副本管理。[PR #150](https://github.com/try2love/codex-mobile-bridge/pull/150)
- **CLI 连接管理更清晰。** 按当前 Agent、本机／SSH／WSL 和配置分组，支持单条或配置范围释放。共享服务操作前展示实际影响范围，区分等待任务结束、明确强制释放和结果待核验；改善释放后接回原会话的流程。[#122](https://github.com/try2love/codex-mobile-bridge/issues/122)
- **CLI 账号与网络恢复。** 在受支持的本机接入中核验原生账号和代理变化，增加网络恢复入口；需要重建服务时先检查任务与审批，保留未发送内容。SSH 和 WSL 继续使用各自的账号、目录和运行环境。
- **原生与临时侧聊。** 原生侧聊继续保留在父会话工作台中；恢复明确的临时侧聊入口，复用常规聊天的输入、附件、模型和审批界面。修复子标签错位及 Windows 原生侧聊的运行时连接问题。[#149](https://github.com/try2love/codex-mobile-bridge/issues/149)
- **更完整的文件浏览。** 文件页支持经权限校验的宿主机目录、完整路径导航和“返回项目文件夹”；聊天中的文件链接可以定位到对应文件。项目外浏览为只读，上传和发送引用仍遵循原项目范围。[#160](https://github.com/try2love/codex-mobile-bridge/issues/160)
- **共享聊天操作。** 输入框增加 `/` 命令入口，复用当前接入支持的操作；新建聊天名称改为可选，留空时保留原生自动命名行为。[#154](https://github.com/try2love/codex-mobile-bridge/issues/154) · [#161](https://github.com/try2love/codex-mobile-bridge/issues/161)
- **消息展示与通知。** 展示可读取的原生交互日期和时间，支持授权音视频附件播放，精简会话提醒入口并保留独立通知控制。[#148](https://github.com/try2love/codex-mobile-bridge/issues/148) · [#158](https://github.com/try2love/codex-mobile-bridge/issues/158) · [#141](https://github.com/try2love/codex-mobile-bridge/issues/141)
- **连接与桌面体验。** 自有服务器＋SSH 入口支持自定义 HTTPS 端口；macOS 菜单栏采用单色图标。[#145](https://github.com/try2love/codex-mobile-bridge/issues/145) · [PR #146](https://github.com/try2love/codex-mobile-bridge/pull/146) · [#156](https://github.com/try2love/codex-mobile-bridge/issues/156) · [PR #157](https://github.com/try2love/codex-mobile-bridge/pull/157)

### 修复与优化

- 修复 Windows 商店版 Codex 的启动身份、共享运行环境和就绪判断，改善初始化及冷启动恢复。
- 改善 Windows Claude、Codex 正常退出时的窗口行为；修复 ZCode 本地连接的窗口路径匹配问题。[#153](https://github.com/try2love/codex-mobile-bridge/issues/153)
- 改善 DSH 缺失连接器的确认修复及旧连接器兼容，连接器异常时仍可独立核验应用退出。
- 修复 iOS 进入电脑时长时间停留在连接页、后台中断后不能继续，以及连接页“更多”按钮的安全区域问题；完善 Android Cookie 迁移和网关身份保留。[#159](https://github.com/try2love/codex-mobile-bridge/issues/159)
- 修复项目目录或历史文件缺失时模型列表不可用的问题；改善独立 CLI 发现、WSL 首次扫描与显式启动。
- 部分主机读取失败时保留已成功加载的聊天列表，并提供对应错误与重试入口；改善文件页连续返回和迟到响应处理。
- Windows 更新前先检查影响安装的应用占用，减少下载完成后才发现无法更新的情况。

### 使用范围与升级

这是预览版本，欢迎反馈连接恢复、CLI 释放、侧边聊天和 Antigravity 接入的实际体验。桌面和 iOS 覆盖安装时保持原应用标识、签名和数据目录，不要先卸载。Android 本次提供独立的 **Bridge Preview**，与原 App 并存；请保留原 App，并在预览 App 中重新添加电脑连接，原绑定和草稿不会自动迁移。手机原生连接恢复等改动需要更新对应手机 App。

Antigravity、独立配置及 WSL 相关能力仍以当前接入实际支持为准。临时侧聊仅适用于受支持的本机桌面入口，不写入原生历史，网关重启后不会恢复；关闭标签与结束临时会话是不同操作。恢复和释放结果不明时，不自动重发消息或重放操作。iOS 源码与未签名 IPA 仍需自行签名安装。

### 贡献


感谢 [@windowmmk](https://github.com/windowmmk) 在 [PR #150](https://github.com/try2love/codex-mobile-bridge/pull/150) 中提供的 Antigravity 接入基础，以及 [@heiqiu47](https://github.com/heiqiu47) 在 [PR #146](https://github.com/try2love/codex-mobile-bridge/pull/146) 和 [PR #157](https://github.com/try2love/codex-mobile-bridge/pull/157) 中贡献的自定义 HTTPS 端口与 macOS 菜单栏图标改进。

---

### English · Antigravity, CLI connection management and chat improvements

This preview adds Antigravity integration and improves CLI handoff and recovery, side chats, file browsing, Windows startup and mobile connection recovery.

- **Antigravity:** browse local App, IDE and CLI history and use supported native connections for chat continuation, project chats, model selection, attachments and approvals. View source-specific account information and manage snapshots for supported default local Google sign-ins. [PR #150](https://github.com/try2love/codex-mobile-bridge/pull/150)
- **CLI management:** group connections by agent, local/SSH/WSL runtime and verified configuration. Preview the impact of shared-service operations, wait for active work, explicitly force supported releases, and distinguish uncertain results from completed handoffs. [#122](https://github.com/try2love/codex-mobile-bridge/issues/122)
- **Account and network recovery:** check native account and proxy changes for supported local CLI connections. Preserve unsent input while checking tasks and approvals before service recovery. SSH and WSL retain their own accounts and environments.
- **Side chats:** keep native side chats in the parent workbench and restore an explicit temporary side-chat option using shared chat controls. Fix attached-tab layout and Windows native side-chat runtime connections. [#149](https://github.com/try2love/codex-mobile-bridge/issues/149)
- **Files:** browse authorized host directories, navigate by full path, return to the chat project and locate files referenced in messages. Browsing outside the project remains read-only; uploads and send-time references retain project boundaries. [#160](https://github.com/try2love/codex-mobile-bridge/issues/160)
- **Chat controls:** add a shared `/` command menu, optional names for new chats, available native message dates and times, authorized audio/video playback, and simpler notification controls. [#154](https://github.com/try2love/codex-mobile-bridge/issues/154) · [#161](https://github.com/try2love/codex-mobile-bridge/issues/161) · [#148](https://github.com/try2love/codex-mobile-bridge/issues/148) · [#158](https://github.com/try2love/codex-mobile-bridge/issues/158) · [#141](https://github.com/try2love/codex-mobile-bridge/issues/141)
- **Network and desktop:** support custom HTTPS ports for SSH server entries and a monochrome macOS menu-bar icon. [#145](https://github.com/try2love/codex-mobile-bridge/issues/145) · [PR #146](https://github.com/try2love/codex-mobile-bridge/pull/146) · [#156](https://github.com/try2love/codex-mobile-bridge/issues/156) · [PR #157](https://github.com/try2love/codex-mobile-bridge/pull/157)
- **Fixes:** improve Windows Store Codex launch identity and readiness, quiet native exits, ZCode window matching, DSH connector recovery, iOS entry deadlines and foreground recovery, Android cookie migration, model catalogs without saved history, and partial chat-list refreshes. Check Windows update blockers before downloading. [#153](https://github.com/try2love/codex-mobile-bridge/issues/153) · [#159](https://github.com/try2love/codex-mobile-bridge/issues/159)

For desktop and iOS upgrades, retain the existing identity, signature and data directory; do not uninstall first. Android is supplied as a separate **Bridge Preview** app that coexists with the original app. Keep the original app and add your computer connections to Preview; existing bindings and drafts are not migrated automatically. Update the corresponding mobile app for native connection changes. Antigravity, independent profiles and WSL capabilities depend on the selected native integration. Temporary side chats are limited to supported local desktop entry points, do not enter native history and do not survive gateway restart. Closing a tab does not end a temporary chat. Unknown writes are not automatically replayed. iOS source and unsigned IPA packages require your own signing.


Thanks to [@windowmmk](https://github.com/windowmmk) for the Antigravity foundation in [PR #150](https://github.com/try2love/codex-mobile-bridge/pull/150), and [@heiqiu47](https://github.com/heiqiu47) for [PR #146](https://github.com/try2love/codex-mobile-bridge/pull/146) and [PR #157](https://github.com/try2love/codex-mobile-bridge/pull/157).

---

## v2.2.0 · ZCode 接入、多信道连接与原生会话管理

v2.2.0 将 ZCode 加入远程工作台，完善手机多信道切换、原生侧边聊天和账号模型管理，并改善连接恢复、通知与后台资源占用。

## 新增与改进

- **ZCode 远程工作台。** 在手机和 Web 浏览已保存的历史，继续原生聊天，发送附件、回答问题与处理审批；支持新建项目聊天、模型与思考强度、Skill、分支和原生改名。提供本地连接及官方远控接入，具体方式以平台和安装包支持为准。保存自定义 API，查看并手动刷新官方账号按模型提供的额度。[#107](https://github.com/try2love/codex-mobile-bridge/issues/107) · [#116](https://github.com/try2love/codex-mobile-bridge/issues/116) · [#124](https://github.com/try2love/codex-mobile-bridge/issues/124) · [#128](https://github.com/try2love/codex-mobile-bridge/issues/128) · [#129](https://github.com/try2love/codex-mobile-bridge/issues/129)
- **一台电脑，多种连接入口。** 将同一网关的局域网、固定 HTTPS 和临时 HTTPS 整合到一张电脑卡片，支持延迟查看、手动选择与自动切换。改善重新登录后的可信信道恢复，减少可用地址被误判不可达的情况；换线保留聊天、草稿和阅读位置。[#125](https://github.com/try2love/codex-mobile-bridge/issues/125)
- **原生侧边聊天归回主会话。** 根据 Codex 原生父子关系显示已有侧边聊天，在主会话中以子标签打开，复用常规聊天操作。关闭标签不再等同于删除原生聊天；无法关联到可访问父会话的条目仍保留可访问入口。[#137](https://github.com/try2love/codex-mobile-bridge/issues/137)
- **Codex 永久删除。** 在支持的本机 Codex 会话中增加永久删除入口，执行前确认主会话及附属聊天范围，核验任务和未确认操作；结果不明时保留核对入口，不自动重复删除。macOS 默认 Codex 可显式初始化共享原生运行时，以支持原 App 持有会话时的创建和数据删除。[#138](https://github.com/try2love/codex-mobile-bridge/issues/138)
- **新建聊天前选择模型。** Codex、Claude、DSH、ZCode 共用模型与思考强度选择，创建后保留选择。新建入口、账号管理与聊天设置复用同一接入的模型缓存，按电脑、账号和配置隔离。[#131](https://github.com/try2love/codex-mobile-bridge/issues/131)
- **独立客户端与 CLI 管理。** 可登记独立 Codex 配置及 VS Code 扩展环境，实验性支持独立 Claude、DSH 桌面配置。增加本网关持有的 CLI 连接管理，支持等待任务结束后释放，保留原会话与历史。[#106](https://github.com/try2love/codex-mobile-bridge/issues/106) · [#122](https://github.com/try2love/codex-mobile-bridge/issues/122)
- **长会话与问题卡片。** 在受支持接入中查看上下文、手动压缩和管理后台任务；Codex 异步问题支持逐题跳过，其他问题的未提交内容保留。跳过状态在 Bridge 手机和 Web 间共享。[#118](https://github.com/try2love/codex-mobile-bridge/issues/118) · [#119](https://github.com/try2love/codex-mobile-bridge/issues/119) · [#121](https://github.com/try2love/codex-mobile-bridge/issues/121)
- **手机通知与远控入口。** Android 按事件保留通知，同一聊天的待确认与完成提醒不再互相覆盖；重复事件继续去重。增加按电脑配置的 UU 远程入口，可调用已安装的 UU 客户端，实际能力取决于系统及 UU 支持。[#113](https://github.com/try2love/codex-mobile-bridge/issues/113)

## 修复与优化

- 修复第三方 Codex 接入只返回思考正文时无法展示的问题；支持含中文等正常 Unicode 字符的供应商模型 ID。[#110](https://github.com/try2love/codex-mobile-bridge/issues/110) · [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130) · [PR #139](https://github.com/try2love/codex-mobile-bridge/pull/139)
- 修复 Tailscale 遗留入口无法正确接管、规则丢失后不能恢复的问题。核验入口归属后恢复，稳定时降低检测频率，异常时退避重试，不覆盖其他服务的配置。[PR #135](https://github.com/try2love/codex-mobile-bridge/pull/135)
- 改善 Claude 初始化、Windows 商店版识别、DSH 连接器升级与跨客户端原生改名；修复 Codex 原连接被替换后的恢复。[#120](https://github.com/try2love/codex-mobile-bridge/issues/120) · [#127](https://github.com/try2love/codex-mobile-bridge/issues/127)
- 保留长段思考及工具输出的阅读位置，改善首次复制和图片保存；修复手机冷启动样式加载及四客户端导航布局。[#112](https://github.com/try2love/codex-mobile-bridge/issues/112) · [#114](https://github.com/try2love/codex-mobile-bridge/issues/114) · [#115](https://github.com/try2love/codex-mobile-bridge/issues/115)
- 停用未启用客户端的后台观察，按需启动可选服务，复用已核验状态与模型缓存；隐藏或收起二维码后停止无意义的配对刷新，减少空闲开销。

## 使用范围与升级

覆盖安装对应平台 App，无需先卸载。保留原应用标识、签名身份及数据目录，以继续使用账号、配置、手机绑定和草稿。完整使用手机信道、通知和原生菜单改进，需同步更新手机 App；iOS 源码与未签名 IPA 仍需自行签名安装。

独立客户端配置和 WSL 账号管理仍为实验性。原生操作取决于客户端版本及接入能力：共享 Codex 运行时当前仅在 macOS 默认配置开放；永久删除不保证同步关闭原 App 已打开的标签；逐题跳过不代表原生 Codex App 的局部跳过状态双向同步；Codex CLI 释放仍可能受上游延迟卸载限制。

macOS、Windows 和 Linux 正式包提供 ZCode 本地连接；macOS 本地启动器随安装包提供，无需开启 TCP 调试端口。也可使用已配置的官方远控方式。

## 贡献

感谢 [@tiancai123123](https://github.com/tiancai123123) 的第三方思考内容修复（PR #130）、[@laozuzhen](https://github.com/laozuzhen) 的 Tailscale 恢复方案（PR #135），以及 [@qybgh](https://github.com/qybgh) 的 Unicode 模型 ID 支持（PR #139）。感谢所有预览版体验者提供连接、模型、会话管理与跨平台反馈。

---

### English · ZCode, connection recovery and native conversation management

v2.2.0 adds ZCode to the remote workspace and improves mobile channel switching, native side chats, model selection and background resource usage.

- **ZCode workspace:** browse saved history, continue native chats, send attachments, answer questions and handle approvals. Create project chats, select models and reasoning levels, use Skills, fork and rename chats. Production packages on macOS, Windows and Linux include local connections; configured official remote access remains available. macOS ships a verified local launcher without requiring a TCP debugging port. [#107](https://github.com/try2love/codex-mobile-bridge/issues/107) · [#116](https://github.com/try2love/codex-mobile-bridge/issues/116) · [#124](https://github.com/try2love/codex-mobile-bridge/issues/124) · [#128](https://github.com/try2love/codex-mobile-bridge/issues/128) · [#129](https://github.com/try2love/codex-mobile-bridge/issues/129)
- **Multiple routes, one computer:** combine LAN, fixed HTTPS and temporary HTTPS entries, inspect latency, select a route or recover automatically. Renew trusted routes after login and preserve drafts and reading position when switching. [#125](https://github.com/try2love/codex-mobile-bridge/issues/125)
- **Native side chats and deletion:** show Codex side chats under their parent, with shared chat controls. Closing a tab preserves the native chat. Supported local Codex sessions offer explicit permanent deletion with scope confirmation and pending-operation checks. Unknown outcomes are not automatically replayed. [#137](https://github.com/try2love/codex-mobile-bridge/issues/137) · [#138](https://github.com/try2love/codex-mobile-bridge/issues/138)
- **Models and independent clients:** choose a model before creating a chat and reuse catalogs scoped to the computer, account and configuration. Manage supported independent client profiles and release idle CLI connections owned by this gateway. [#106](https://github.com/try2love/codex-mobile-bridge/issues/106) · [#122](https://github.com/try2love/codex-mobile-bridge/issues/122) · [#131](https://github.com/try2love/codex-mobile-bridge/issues/131)
- **Questions and notifications:** skip individual Codex async questions without losing other answers. Android preserves separate notifications for separate events. Add a per-computer shortcut to an installed UU remote client. [#113](https://github.com/try2love/codex-mobile-bridge/issues/113) · [#118](https://github.com/try2love/codex-mobile-bridge/issues/118) · [#119](https://github.com/try2love/codex-mobile-bridge/issues/119) · [#121](https://github.com/try2love/codex-mobile-bridge/issues/121)
- **Fixes:** display reasoning-only responses from third-party Codex providers and accept Unicode model IDs; recover owned Tailscale routes with bounded retries; improve Claude initialization, Windows Store discovery, DSH connector upgrades, native renaming, copy/save actions and mobile cold starts. Reduce idle polling and reuse verified caches. [#110](https://github.com/try2love/codex-mobile-bridge/issues/110) · [#112](https://github.com/try2love/codex-mobile-bridge/issues/112) · [#114](https://github.com/try2love/codex-mobile-bridge/issues/114) · [#115](https://github.com/try2love/codex-mobile-bridge/issues/115) · [#120](https://github.com/try2love/codex-mobile-bridge/issues/120) · [#127](https://github.com/try2love/codex-mobile-bridge/issues/127)

Upgrade over the existing installation without uninstalling. Keep the same application identity, signing identity and data directory to retain accounts, settings, phone bindings and drafts. Update the mobile app for native route and notification changes. iOS source and unsigned IPA distributions still require your own signing.

Independent profiles and WSL account management remain experimental. Shared Codex runtime initialization currently applies only to the default macOS profile. Data deletion does not guarantee closing an already open native tab. Individual question skips synchronize between Bridge clients, not the native Codex app. CLI release may remain subject to upstream delayed unloading.

Thanks to [@tiancai123123](https://github.com/tiancai123123) for [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130), [@laozuzhen](https://github.com/laozuzhen) for [PR #135](https://github.com/try2love/codex-mobile-bridge/pull/135), and [@qybgh](https://github.com/qybgh) for [PR #139](https://github.com/try2love/codex-mobile-bridge/pull/139).

---

## v2.2.0-preview.3 · ZCode 双通道接入、新建模型选择与独立客户端配置

本次预览版继续完善 ZCode 接入、账号与模型管理，让新建聊天前的设置更明确，并修复会话改名、模型缓存和网关启动中的问题。

### 新增与优化

- **ZCode 支持本机连接与官方远控两种方式。** 在桌面网关完成官方账号登录和远控链接配置后，可在手机和 Web 的账号管理中选择已准备好的连接方式。官方模式可正常启动尚未运行的 ZCode，并接回原聊天；切换方式保留输入草稿、附件和阅读位置，不自动重发消息。首次配置增加分步引导，登录状态加密保存。原 TCP 兼容端口入口由这两种方式替代。[#107](https://github.com/try2love/codex-mobile-bridge/issues/107) · [#124](https://github.com/try2love/codex-mobile-bridge/issues/124) · [#128](https://github.com/try2love/codex-mobile-bridge/issues/128)
- **新建聊天时选择模型和思考强度。** Codex、Claude、DSH、ZCode 共用模型选择入口，覆盖支持的桌面、CLI、SSH 和 WSL 接入。Claude 和 ZCode 创建聊天时，首条消息直接使用所选模型；思考强度以当前接入实际支持的档位为准。[#131](https://github.com/try2love/codex-mobile-bridge/issues/131)
- **模型列表更充分地复用缓存。** 新建聊天、聊天设置和账号管理共享同一接入下已核验的模型目录；一处刷新后，其他入口同步更新。不同电脑、账号、项目和独立配置继续隔离，较晚返回的旧结果不会覆盖较新的目录，也不会改变其他聊天已选的模型。
- **账号配置界面统一，ZCode 可保存自定义 API。** 复用共同的地址、密钥和模型选择表单，支持在保存前读取上游提供的模型。ZCode 可保存原客户端支持的 Anthropic Messages、OpenAI Chat Completions 和 OpenAI Responses 接入；保存配置与实际应用分开，应用时保留现有任务检查。
- **ZCode 额度支持主动刷新。** 通过原客户端查询当前官方账号各模型的每日剩余额度，显示更新时间并复用缓存；查询失败保留同账号仍有效的旧值，同时说明未更新。额度查询不调用模型。[#129](https://github.com/try2love/codex-mobile-bridge/issues/129)
- **独立客户端配置，实验性开放。** 在已有 Codex 独立配置入口基础上，增加 Claude、DSH 的独立桌面配置，与默认接入并存。分别管理账号、会话、附件、草稿、模型缓存和通知，启动、重连及退出按当前实例执行。ZCode 可手动指定未被自动发现的安装位置，仍使用默认配置，不新增第二套独立 ZCode。

### 修复

- **会话改名同步到原客户端。** 完善 Claude Desktop、Claude Code CLI、DSH 和 ZCode 的原生改名与结果确认；不再把仅修改 Bridge 显示名称当作原 App 改名成功。未连接或原生版本不支持时给出明确提示。[#120](https://github.com/try2love/codex-mobile-bridge/issues/120)
- **第三方 Codex 思考内容能够显示。** 当兼容 Responses API 只返回思考正文、未返回摘要时，使用上游已提供的正文补充展示，并支持 CLI 思考增量；有摘要时仍优先显示摘要。[#110](https://github.com/try2love/codex-mobile-bridge/issues/110) · [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130)
- **可选登录恢复不再卡住整个网关。** 修复系统钥匙串未就绪时，ZCode 官方登录恢复阻塞主窗口的问题。未配置官方登录时不读取其钥匙串；已保存的登录在后台恢复，失败可单独重试。
- **补齐平台与升级兼容。** 改善 Linux ZCode AppImage 的发现和原安装位置识别；修复 macOS 中文路径下的进程识别，以及 Windows 打包参数过长导致的构建失败。修复 DSH 旧连接插件升级与可选服务读取问题，并完善独立 Claude 开启开发者模式、自行重启后的显式连接恢复。

感谢 [@tiancai123123](https://github.com/tiancai123123) 在 [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130) 中提供第三方 Codex 思考内容修复及 Windows、Android 验证。

---

### English · ZCode connection modes, model selection and independent client profiles

This preview improves ZCode connectivity and account management, adds model selection before starting a chat, and fixes native renaming, model cache reuse and gateway startup.

- **Choose local or official ZCode remote access.** Configure the official login and remote link in the desktop gateway, then choose a prepared connection mode in account management on mobile or the web. Official mode can start ZCode when it is closed and reconnect to the original chat. Switching modes keeps drafts, attachments and reading position without replaying messages. Setup guidance and encrypted login persistence are included. These two modes replace the previous TCP compatibility-port option. [#107](https://github.com/try2love/codex-mobile-bridge/issues/107) · [#124](https://github.com/try2love/codex-mobile-bridge/issues/124) · [#128](https://github.com/try2love/codex-mobile-bridge/issues/128)
- **Choose a model before creating a chat.** Codex, Claude, DSH and ZCode share a model picker across supported Desktop, CLI, SSH and WSL connections. Claude and ZCode use the selected model for the first message. Reasoning levels follow the capabilities of the selected connection. [#131](https://github.com/try2love/codex-mobile-bridge/issues/131)
- **Reuse model catalogs across screens.** Chat creation, conversation settings and account management share verified catalogs for the same connection. Refreshes propagate between screens while computers, accounts, projects and independent profiles stay separate. Late responses cannot replace newer catalogs or change another conversation's model selection.
- **Use a shared API account editor.** Reuse the endpoint, key and model-selection form, including upstream model discovery before saving. ZCode can save providers using its supported Anthropic Messages, OpenAI Chat Completions and OpenAI Responses protocols. Saving and applying remain separate operations, with existing task checks preserved.
- **Refresh official ZCode quotas.** Query the original client for the current account's per-model daily allowance, with timestamps and caching. A failed refresh preserves valid values for the same account and reports that they were not updated. Quota queries do not make model requests. [#129](https://github.com/try2love/codex-mobile-bridge/issues/129)
- **Try independent client profiles.** Experimental Claude and DSH Desktop profiles can coexist with the default connection, extending the existing independent Codex entry. Accounts, history, attachments, drafts, model catalogs, notifications and lifecycle operations are scoped to the selected instance. ZCode supports manually selecting an installation missed by discovery, but continues to use its default profile.

**Fixes**

- Native renames in Claude Desktop, supported Claude Code CLI connections, DSH and ZCode are verified against the original client. Changing a Bridge-only display alias is no longer reported as a successful native rename. [#120](https://github.com/try2love/codex-mobile-bridge/issues/120)
- Codex third-party Responses providers that return reasoning text without a summary can display that text, including CLI reasoning deltas. An available summary still takes precedence. [#110](https://github.com/try2love/codex-mobile-bridge/issues/110) · [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130)
- Optional ZCode login restoration no longer blocks the gateway window when the system keyring is unavailable. Unconfigured logins do not access the keyring; saved logins restore in the background and can be retried separately.
- Improved Linux ZCode AppImage discovery, macOS process identification under Unicode paths, Windows packaging with long resource argument lists, DSH connector upgrades and optional-service access, and explicit reconnection after an independent Claude instance relaunches to enable developer mode.

Thanks to [@tiancai123123](https://github.com/tiancai123123) for the reasoning-content fix and Windows/Android validation in [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130).

---

## v2.2.0-preview.2 · ZCode 历史、桌面接入与模型额度

**这次预览版把「看历史」和「操作桌面」分开了。** 手机和 Web 可以直接打开 ZCode 已保存的聊天；要继续发送或处理审批时，Bridge 再确认连接的是原来的桌面会话。

- **看历史不用切换桌面窗口。** 在 macOS、Windows 和 Linux 桌面网关上，可以直接读取已保存的 ZCode 聊天。如果 ZCode 只保留了列表条目，仍可打开空会话；原 App 已找不到的聊天不会取得发送权限。读取失败可以原地重试。打开聊天先看最新消息，向上翻阅时，后台刷新会保留阅读位置。[#128](https://github.com/try2love/codex-mobile-bridge/issues/128)
- **看清每条回复用的模型，也能找回附件。** 如果原记录提供了模型和思考强度，聊天会按回复显示当时的值。历史图片可以点缩略图看原图；来自当前项目、且来源可核实的附件可以点击文件名定位。原记录缺少信息时不会用当前模型或同名文件代替。
- **从手机管理 ZCode 或新建项目聊天。** 可以在网关、手机或 Web 启动 ZCode、正常退出，或只关闭远程接入，让电脑上的任务继续运行。Windows 支持从原生托盘正常退出。「第三方通知」可以直接从列表打开；原 App 的项目服务就绪后，即可新建项目聊天，不必先找一个可发送的旧会话。[#128](https://github.com/try2love/codex-mobile-bridge/issues/128)
- **弱网下接回原聊天。** 如果手机没有收到接续结果，恢复连接后会继续查询同一次请求，无需反复点启用，也不会重发聊天消息。接续期间不会反复加载整份历史。
- **按模型查看 ZCode 每日额度。** 账号页可分别显示 GLM-5.3、GLM-5.3-Flash 等模型的剩余额度及更新时间。数据来自 ZCode 官方账号已取得的记录；没有记录时不会估算，点击刷新也不会消耗模型额度。[#129](https://github.com/try2love/codex-mobile-bridge/issues/129)
- **继续 VS Code 里正在使用的 Codex 聊天。** 对于单独配置了 `CODEX_HOME` 的 VS Code 扩展，Bridge 优先把发送、审批和停止交给原来持有会话的扩展，减少电脑和手机争用同一聊天的问题。修复了 Windows 上同一目录因路径写法不同而未被识别的情况。可用操作取决于扩展版本。[#106](https://github.com/try2love/codex-mobile-bridge/issues/106)
- **连接与更新。** 会话连接重建失败后仍可重试；网关设置会指出尚未保存的字段。修复同一台电脑装有多份网关时，更新重启可能用错数据目录的问题。

**兼容范围：** Windows 和 Linux 桌面默认通过本机私有管道接入，不必开启 TCP 调试端口。网关按原进程身份和实际协议能力开放操作，不再只看版本号。已在 Windows x64 的 ZCode 3.17.1 和 Ubuntu 22.04 x64 已登录图形桌面验收。macOS 公开包普通启动即可看历史；要继续聊天，可在「应用管理」启用本机兼容端口。Mac Test 的锁屏无端口启动器不包含在公开包中。

**从 preview.1 更新：** macOS 和 Windows 网关可在「检查更新」中选择「更新并重启」；Android 可在 App 内检查更新、下载并确认安装。Linux 请覆盖安装对应架构的 DEB，或替换原 AppImage；iOS 可在 App 内检查更新并打开发布页，仍需使用与原安装一致的签名身份和应用标识覆盖安装。无需先卸载 preview.1。使用稳定版的用户不会收到此次预览更新。

---

### English · ZCode history, desktop access and model quotas

You can now open saved ZCode chats on mobile and the web without switching the desktop window. Before sending a message or handling an approval, Bridge verifies that it is connected to the original desktop session.

- **Read saved chats.** macOS, Windows and Linux desktop gateways read ZCode history locally. An indexed chat with no transcript opens as an empty conversation. A chat missing from the original app stays read-only. Failed reads can be retried in place. Chats open at the latest message and keep your place while you read older replies. [#128](https://github.com/try2love/codex-mobile-bridge/issues/128)
- **Inspect replies and attachments.** Replies show the model and reasoning effort recorded at the time, when available. Tap image thumbnails to see the original, or select a verified project attachment to locate its file.
- **Manage the desktop or start a project chat.** Start ZCode, quit normally (including via its native Windows tray), or turn off remote access while leaving local work running. Open third-party notifications from the list or start a project chat once ZCode’s project service is ready, without first opening a writable chat. [#128](https://github.com/try2love/codex-mobile-bridge/issues/128)
- **Resume over a slow connection.** If a phone misses the response, it can check the original request after reconnecting. It does not resend chat messages or repeatedly reload the full history.
- **See daily quota by model.** The account page shows the remaining allowance and update time for models such as GLM-5.3 and GLM-5.3-Flash when ZCode has recorded an official quota response. Refresh rereads those records; it does not make a model request or estimate missing values. [#129](https://github.com/try2love/codex-mobile-bridge/issues/129)
- **Continue the original VS Code Codex chat.** For extensions registered with a separate `CODEX_HOME`, Bridge routes sending, approvals and stop requests to the extension that owns the session when supported. Windows paths that refer to the same directory are now recognised consistently. [#106](https://github.com/try2love/codex-mobile-bridge/issues/106)
- **Connection and update fixes.** Retry a failed reconnection, identify unsaved gateway settings and keep the correct data directory when an update restarts one of several gateway installations.

**Compatibility:** Windows and Linux desktop gateways use a private local pipe by default, without a TCP debugging port. Controls are enabled according to the identity and capabilities of the running app, rather than its version alone. Verified with ZCode 3.17.1 on Windows x64 and a signed-in graphical desktop on Ubuntu 22.04 x64. The public macOS build reads saved chats without special setup; enable the local compatibility port in app management to continue a chat. The port-free locked-screen Mac Test launcher is not included.

**Upgrading from preview.1:** Use **Check for updates → Update and restart** on macOS or Windows. Android can check, download and prompt to install in the app. On Linux, install the matching DEB over the previous package or replace the AppImage. On iOS, the update check opens the release page; sign and install over the existing app using the same identity and bundle ID. Stable installations will not be offered this preview.

---

## v2.2.0-preview.1 · ZCode 原生会话与手机连接切换

这次预览版把 ZCode 原生聊天带到手机和 Web。手机 App 也开始按网关整理电脑。同一个网关的局域网、固定 HTTPS 和临时 HTTPS 地址可以放在一张卡片里，在家和外出时都能继续原来的聊天。

### ZCode 原生接入预览

- **继续桌面上的聊天。** 在已配置 ZCode 接入的 macOS 或 Windows 网关上，可以从手机和 Web 浏览历史、继续原会话、发送附件、回答问题和处理审批。ZCode 重启后，即使桌面还停在首页，也可以从 Bridge 主动恢复旧聊天。接入使用网关自带运行时，无需另装 Node.js 或 Homebrew。[#107](https://github.com/try2love/codex-mobile-bridge/issues/107) · [#123](https://github.com/try2love/codex-mobile-bridge/issues/123) · [#124](https://github.com/try2love/codex-mobile-bridge/issues/124)
- **在项目里新建和整理会话。** 可以选已有项目，也可以登记新目录，用第一条消息创建原生聊天。支持改名、从指定回复分支，以及编辑原消息后重新发送。[#116](https://github.com/try2love/codex-mobile-bridge/issues/116) · [#120](https://github.com/try2love/codex-mobile-bridge/issues/120)
- **控制下一步怎样执行。** 可切换模型和思考强度、引用 Skill、选择计划或目标模式，以及调整访问权限。任务进行中可选择「完成后发送」或「补充当前任务」。如果 ZCode 将补充内容排入队列，Bridge 会显示「已入队」。[#107](https://github.com/try2love/codex-mobile-bridge/issues/107) · [#117](https://github.com/try2love/codex-mobile-bridge/issues/117) · [#126](https://github.com/try2love/codex-mobile-bridge/issues/126)

### 手机连接与会话管理

- **一台网关，一张电脑卡片。** 进入电脑时，App 会使用最先通过网关身份验证的已启用地址。自动模式下，回到同一局域网后优先走局域网，离开后可切回可用的公网入口。也可以查看延迟、手动选线。换线时保留当前聊天、草稿和阅读位置。[#125](https://github.com/try2love/codex-mobile-bridge/issues/125)
- **接入独立的 VS Code Codex 配置。** 在桌面网关添加扩展使用的 `CODEX_HOME` 后，手机和 Web 会把它显示为独立客户端，分别管理会话、账号和草稿。[#106](https://github.com/try2love/codex-mobile-bridge/issues/106)
- **用完 CLI 后，把连接交还给电脑。** 可在会话菜单中释放单条连接，也可在设置里集中管理本网关持有的本机、SSH 和 WSL 连接。默认等任务结束再释放，原聊天和历史保留。Codex 原生服务有时会延迟释放，电脑端接手可能需要稍候。[#122](https://github.com/try2love/codex-mobile-bridge/issues/122)
- **管理长聊天与后台任务。** 支持的 Codex、Claude、DSH 和 ZCode 会话可查看原客户端提供的上下文用量，并在空闲时发起手动压缩。DSH 和 ZCode 还可查看当前聊天的后台任务，单独停止其中一项。[#118](https://github.com/try2love/codex-mobile-bridge/issues/118) · [#119](https://github.com/try2love/codex-mobile-bridge/issues/119) · [#121](https://github.com/try2love/codex-mobile-bridge/issues/121)

### 修复与阅读体验

- **Claude Desktop 连接。** 修复 Windows 商店版（MSIX）已开启开发者模式却被误判的问题。macOS 和 Windows 上的首次初始化可在已信任目录完成握手，避免空消息触发模型调用。登录数据库暂时被占用时，仍可打开原生聊天历史，待账号身份重新确认后再恢复模型设置和对应账号的草稿。[#127](https://github.com/try2love/codex-mobile-bridge/issues/127)
- **阅读、复制与图片保存。** 展开 Claude 或 DSH 的长段思考或工具输出后，后台刷新会保留阅读位置。会话菜单中的复制操作首次点击即可生效，并给出反馈。图片预览新增保存原图入口，文件保存到当前手机或电脑。[#112](https://github.com/try2love/codex-mobile-bridge/issues/112) · [#114](https://github.com/try2love/codex-mobile-bridge/issues/114) · [#115](https://github.com/try2love/codex-mobile-bridge/issues/115)
- **Ubuntu 网关启动。** 修复部分使用 Python 3.11 之前运行时的打包网关因缺少 TOML 读取组件而无法启动的问题。

**预览范围。** ZCode 接入目前面向已有接入配置的 macOS 和 Windows 网关，已验证 ZCode 3.17.1。手机多地址切换需要配套的 Android 或 iOS 预览 App。欢迎重点反馈 ZCode 冷启动后继续旧聊天、手机在 Wi-Fi 与蜂窝之间切换，以及 Claude 登录库被占用时打开历史这三个场景。

感谢 [@SOFdian](https://github.com/SOFdian) 提出独立 VS Code 配置的接入需求，感谢 [@xtayavij](https://github.com/xtayavij) 提供 Windows 商店版 Claude 的复现步骤。[#106](https://github.com/try2love/codex-mobile-bridge/issues/106) · [#127](https://github.com/try2love/codex-mobile-bridge/issues/127)

---

### English · Native ZCode chats and mobile connection switching

This preview brings native ZCode chats to mobile and the web. The mobile app can group LAN, fixed HTTPS and temporary HTTPS addresses for the same gateway under one computer entry.

- **Native ZCode chats.** On a configured macOS or Windows gateway, read history, resume a desktop chat, send attachments, answer questions and handle approvals. Chats can also be resumed after ZCode restarts at its home screen. The gateway includes the connection runtime, so no separate Node.js or Homebrew installation is required. [#107](https://github.com/try2love/codex-mobile-bridge/issues/107) · [#123](https://github.com/try2love/codex-mobile-bridge/issues/123) · [#124](https://github.com/try2love/codex-mobile-bridge/issues/124)
- **Projects and chat controls.** Choose an existing project or register a directory, then create a native chat with its first message. Rename chats, branch from a reply, or edit and resend a message. Choose the model, reasoning effort, Skills, work mode and access permissions. Follow-up input is shown as queued when ZCode accepts it into the queue. [#116](https://github.com/try2love/codex-mobile-bridge/issues/116) · [#117](https://github.com/try2love/codex-mobile-bridge/issues/117) · [#120](https://github.com/try2love/codex-mobile-bridge/issues/120) · [#126](https://github.com/try2love/codex-mobile-bridge/issues/126)
- **One computer entry per gateway.** The mobile app opens the first enabled address that proves it belongs to the paired gateway. Automatic mode prefers a verified LAN connection when it becomes available and can fall back to a public address when LAN is lost. You can also view latency and choose a route manually. Switching keeps the current chat, draft and reading position. [#125](https://github.com/try2love/codex-mobile-bridge/issues/125)
- **Independent VS Code configurations.** Add the Codex extension’s `CODEX_HOME` in the desktop gateway to manage its chats, accounts and drafts separately on mobile and the web. [#106](https://github.com/try2love/codex-mobile-bridge/issues/106)
- **Release CLI connections.** Release a single connection from the chat menu, or manage local, SSH and WSL connections held by the gateway in Settings. Release waits for the task to finish by default and preserves chat history. Codex’s native service can take additional time before another terminal can resume the chat. [#122](https://github.com/try2love/codex-mobile-bridge/issues/122)
- **Context and background tasks.** Supported Codex, Claude, DSH and ZCode chats show the context usage reported by the client and offer manual compaction when idle. In DSH and ZCode, you can inspect background tasks for the current chat and stop an individual task. [#118](https://github.com/try2love/codex-mobile-bridge/issues/118) · [#119](https://github.com/try2love/codex-mobile-bridge/issues/119) · [#121](https://github.com/try2love/codex-mobile-bridge/issues/121)
- **Claude Desktop recovery.** Fixed developer-mode detection for Windows MSIX installations. On macOS and Windows, initialization in a trusted directory completes without sending an empty model message. Native history stays readable if the sign-in database is temporarily locked. Account-specific drafts and model settings become available once account identity can be verified again. [#127](https://github.com/try2love/codex-mobile-bridge/issues/127)
- **Reading, copying and saving images.** Expanded Claude and DSH activity keeps its reading position during refresh. Copying from a chat menu works on the first tap and gives feedback. The image viewer can save the original image to the device you are using. [#112](https://github.com/try2love/codex-mobile-bridge/issues/112) · [#114](https://github.com/try2love/codex-mobile-bridge/issues/114) · [#115](https://github.com/try2love/codex-mobile-bridge/issues/115)
- **Ubuntu startup.** Fixed a missing TOML reader that prevented packaged gateways using Python runtimes older than 3.11 from starting.

**Preview coverage.** Native ZCode access has been tested with ZCode 3.17.1 on configured macOS and Windows gateways. Mobile connection switching requires the matching Android or iOS preview app. Feedback on cold-start chat recovery, Wi-Fi/cellular switching and reading history while Claude’s sign-in database is locked is especially welcome.

Thanks to [@SOFdian](https://github.com/SOFdian) for the independent VS Code configuration request and [@xtayavij](https://github.com/xtayavij) for the Windows MSIX reproduction. [#106](https://github.com/try2love/codex-mobile-bridge/issues/106) · [#127](https://github.com/try2love/codex-mobile-bridge/issues/127)

---

## v2.1.7 · 手机通知、会话整理与连接恢复

<a id="v217"></a>

本次更新完善手机通知和会话管理，增加项目文件引用与历史图片预览，并修复弱网发送、SSH 离线和部分桌面会话连接异常。桌面与手机 App 版本为 **2.1.7**，Android/iOS 构建号为 **40**。

### 新增与优化

- **Android 后台任务通知**：可手动开启后台接收，在离开 App 后继续接收任务提醒。点击通知返回聊天时复用已有页面和草稿，减少重复加载。默认关闭，可随时暂停。[Issue #47](https://github.com/try2love/codex-mobile-bridge/issues/47) · [PR #85](https://github.com/try2love/codex-mobile-bridge/pull/85)
- **通知来源与手机外观**：按保存的连接地址分别启停任务提醒，减少多个入口重复通知；Android 与 iOS 统一支持浅色、深色及跟随系统。改善 iOS 前台通知，重新整理 Android 电脑选项菜单的分组、触摸区域和返回操作。[PR #85](https://github.com/try2love/codex-mobile-bridge/pull/85) · [PR #95](https://github.com/try2love/codex-mobile-bridge/pull/95)
- **统一会话菜单与分类整理**：长按、右键或标题入口集中提供详情、改名、置顶、复制、项目和分区整理。支持原生操作时同步原客户端；Bridge 显示分类明确标注“仅在 Bridge 中生效”。菜单返回和下拉关闭保持一致；分类结果未确认时不重复提交。[PR #92](https://github.com/try2love/codex-mobile-bridge/pull/92) · [Issue #77](https://github.com/try2love/codex-mobile-bridge/issues/77)
- **引用宿主机项目文件**：附件入口可选择当前项目内的文件或文件夹，追加到草稿后发送，无需下载到手机再上传。Codex、Claude 和 DSH 使用各自的附件或引用格式；支持的文件链接可进入已有文件页面定位和预览。[Issue #102](https://github.com/try2love/codex-mobile-bridge/issues/102)
- **历史图片预览**：Claude 与 DSH 的历史图片支持按需加载、预览和缩放，避免历史刷新反复传输原图。[Issue #99](https://github.com/try2love/codex-mobile-bridge/issues/99)
- **聊天阅读与任务状态**：代码块增加语言标签和语法高亮，保留原文复制；DSH 已识别的注入上下文默认折叠。聊天统一显示运行中、等待审批及异常状态；本版不将这些状态提示描述为逐字流式输出。[Issue #104](https://github.com/try2love/codex-mobile-bridge/issues/104) · [Issue #101](https://github.com/try2love/codex-mobile-bridge/issues/101) · [Issue #98](https://github.com/try2love/codex-mobile-bridge/issues/98)
- **项目创建与信任确认**：支持的 Codex App 和 Claude Desktop Code 接入可在远端确认工作区信任，明确展示环境与完整路径；完善 Codex SSH 项目登记和列表归类。取消后保留输入，信任确认不会自动发送首条消息。[Issue #100](https://github.com/try2love/codex-mobile-bridge/issues/100)
- **DSH 双路线接入**：增加通用插件及显式实例绑定，手机 App 也可添加原生 DSH Mobile 链接。原生 Mobile 页面与 Bridge 统一聊天界面独立使用；兼容范围以客户端和插件版本核验为准。[Issue #91](https://github.com/try2love/codex-mobile-bridge/issues/91)
- **可选 Codex 重置动态**：增加第三方重置动态查询和可选提醒，默认关闭并标明数据来源；不代表官方承诺或个人账号的实际额度。[PR #86](https://github.com/try2love/codex-mobile-bridge/pull/86)

### 问题修复

- 修复 Safari 中部分无关脚本异常触发“工作台尚未加载完成”的问题，改善失败资源重试，同时保留正常请求的缓存复用。[Issue #87](https://github.com/try2love/codex-mobile-bridge/issues/87) · [PR #88](https://github.com/try2love/codex-mobile-bridge/pull/88)
- 隔离离线 SSH 主机的慢请求，避免拖慢本机会话连接、账号查询和通知；增加失败退避与手动重试。改善 SSH 模型目录与 Skill 的读取和缓存复用。[Issue #90](https://github.com/try2love/codex-mobile-bridge/issues/90)
- 为部分 Codex Desktop 异常提供“重建连接”入口；失败后可进入应用管理，在任务保护和明确确认下处理原生应用。原客户端内部路由异常仍有恢复限制，继续由 [Issue #103](https://github.com/try2love/codex-mobile-bridge/issues/103) 跟踪。
- 改善已提交消息的确认恢复：依据发送回执或匹配的原生持久记录核对，不自动重发；只清理对应已提交草稿，保留用户后续编辑。
- 修复 Windows WSL Codex 的部分时间线与写入互相等待问题，以及跨轮次回答问题卡片后其他未答卡片消失的问题。
- 修复 Claude 模型设置中部分错误的“账号已切换”提示，保留模型来源、账号隔离及思考强度信息；完善支持原生设置时的手动模型入口。
- 修复紧接正文的块公式解析和渲染，保留 TeX 原文。[Issue #94](https://github.com/try2love/codex-mobile-bridge/issues/94) · [PR #96](https://github.com/try2love/codex-mobile-bridge/pull/96)
- 改善附件选择取消后的恢复、Claude 配置目录识别、DSH 接入组件升级兼容性和回复模型信息显示。[Issue #99](https://github.com/try2love/codex-mobile-bridge/issues/99) · [Issue #102](https://github.com/try2love/codex-mobile-bridge/issues/102)
- 增加任务明确失败或阻塞时的“需要你处理”提醒，复用现有通知策略与去重机制。[Issue #47](https://github.com/try2love/codex-mobile-bridge/issues/47)

### 升级与使用范围

覆盖安装对应平台的原 App，不要先卸载或清空数据。Android 沿用原签名；iOS 沿用原应用标识，仍需使用自己的签名身份安装。建议同时更新网关与手机 App，并刷新已打开的共享页面；独立 Test 与正式版保持各自的数据目录。各平台验收范围见 [验证记录](VERIFICATION.md#v217)。

Android 后台通知需主动开启，并受通知权限及系统省电策略影响。**iOS 本次完善前台提醒，不新增 APNs 后台推送。** DSH 需要兼容的客户端与运行中插件；共享原生连接器的正式版和 Test 应使用兼容版本，更新接入前确认任务已经停止。Bridge 显示分类不会同步到原客户端。

部分原生归档限制继续由 [#77](https://github.com/try2love/codex-mobile-bridge/issues/77) 跟踪；[连接重建](https://github.com/try2love/codex-mobile-bridge/issues/103)不能保证修复 Codex 内部所有路由异常，也不等于消除公网网络故障。WSL 账号管理继续作为实验性功能提供。

### 贡献与反馈

- 感谢 [@heiqiu47](https://github.com/heiqiu47) 的 Android 后台通知与设置、可选重置动态、共享会话菜单及 Android 电脑选项菜单：[PR #85](https://github.com/try2love/codex-mobile-bridge/pull/85)、[#86](https://github.com/try2love/codex-mobile-bridge/pull/86)、[#92](https://github.com/try2love/codex-mobile-bridge/pull/92)、[#95](https://github.com/try2love/codex-mobile-bridge/pull/95)。
- 感谢 [@mc373906408（waken）](https://github.com/mc373906408) 的 Safari 启动错误识别与失败资源恢复：[PR #88](https://github.com/try2love/codex-mobile-bridge/pull/88)。
- 感谢 [@littltstream](https://github.com/littltstream) 的块公式渲染修复：[PR #96](https://github.com/try2love/codex-mobile-bridge/pull/96)。
- 原作者提交完整保留，整合调整另行记录。感谢相关 Issue 的建议和复测，以及 [@SevenPwZ](https://github.com/SevenPwZ) 对 [#103](https://github.com/try2love/codex-mobile-bridge/issues/103) 的故障反馈。

### English

**v2.1.7 improves mobile notifications, session organization and connection recovery.** Desktop/mobile version: **2.1.7**, Android/iOS build **40**.

- **Android background notifications:** optional reception while away from the app, with pause controls and improved notification navigation that preserves existing pages and drafts. Disabled by default. [#47](https://github.com/try2love/codex-mobile-bridge/issues/47), [PR #85](https://github.com/try2love/codex-mobile-bridge/pull/85).
- **Mobile preferences:** choose notification sources per saved connection; unify light, dark and system themes on Android/iOS, improve iOS foreground notifications and refine the Android gateway menu. [PR #85](https://github.com/try2love/codex-mobile-bridge/pull/85), [PR #95](https://github.com/try2love/codex-mobile-bridge/pull/95).
- **Session organization:** shared long-press, right-click and title menus for details, renaming, pinning, copying, projects and sections. Use native operations where supported and clearly label Bridge-only categories. Unknown writes are not replayed. [PR #92](https://github.com/try2love/codex-mobile-bridge/pull/92), [#77](https://github.com/try2love/codex-mobile-bridge/issues/77).
- **Project references and images:** reference files/folders within the current host project without downloading them to the phone; preserve drafts and use each client's native attachment/reference format. Preview Claude/DSH history images on demand. [#102](https://github.com/try2love/codex-mobile-bridge/issues/102), [#99](https://github.com/try2love/codex-mobile-bridge/issues/99).
- **Chat reading:** language labels and syntax highlighting with original-text copying; collapsible identified DSH context and shared running/approval/error status. Status indicators do not imply token streaming. [#104](https://github.com/try2love/codex-mobile-bridge/issues/104), [#101](https://github.com/try2love/codex-mobile-bridge/issues/101), [#98](https://github.com/try2love/codex-mobile-bridge/issues/98).
- **Projects and DSH:** remote workspace trust confirmation for supported Codex App/Claude Desktop Code connections, improved Codex SSH project registration/grouping, generic DSH plugin binding and separate native DSH Mobile links. [#100](https://github.com/try2love/codex-mobile-bridge/issues/100), [#91](https://github.com/try2love/codex-mobile-bridge/issues/91).
- **Optional reset outlook:** third-party Codex reset information and optional reminders, disabled by default; not an official guarantee or personal account quota. [PR #86](https://github.com/try2love/codex-mobile-bridge/pull/86).
- **Startup and remote recovery:** fix Safari error attribution and failed-resource retries while retaining normal caching; isolate offline SSH hosts, add backoff/manual retry, and improve SSH models and Skill loading. [#87](https://github.com/try2love/codex-mobile-bridge/issues/87), [PR #88](https://github.com/try2love/codex-mobile-bridge/pull/88), [#90](https://github.com/try2love/codex-mobile-bridge/issues/90).
- **Connection and sending:** rebuild supported Desktop connections; reconcile accepted messages using receipts or matching durable native records without resending or discarding newer draft edits. Codex internal routing limitations remain tracked in [#103](https://github.com/try2love/codex-mobile-bridge/issues/103).
- **Other fixes:** WSL Codex timeline/write deadlocks, unanswered cards disappearing across turns, incorrect Claude model-identity errors, attachment picker recovery and connector compatibility. Restore display math immediately after prose. [#94](https://github.com/try2love/codex-mobile-bridge/issues/94), [PR #96](https://github.com/try2love/codex-mobile-bridge/pull/96), [#99](https://github.com/try2love/codex-mobile-bridge/issues/99), [#102](https://github.com/try2love/codex-mobile-bridge/issues/102). Explicit task failures/blocks can now trigger existing attention notifications ([#47](https://github.com/try2love/codex-mobile-bridge/issues/47)).

Install over the matching app without clearing data. Retain Android signing and use matching iOS identifiers and your own signing identity. Update the gateway and mobile app, then refresh shared pages. Android background notifications require permission and remain subject to power management; iOS improvements cover foreground notifications, not new APNs background push. DSH compatibility depends on the client and running plugin. Bridge-only categories do not modify native clients. Archive limitations ([#77](https://github.com/try2love/codex-mobile-bridge/issues/77)), native routing limitations ([#103](https://github.com/try2love/codex-mobile-bridge/issues/103)) and experimental WSL account management remain. See [verification coverage](VERIFICATION.md#v217).

Thanks to **[@heiqiu47](https://github.com/heiqiu47)** for PRs [#85](https://github.com/try2love/codex-mobile-bridge/pull/85), [#86](https://github.com/try2love/codex-mobile-bridge/pull/86), [#92](https://github.com/try2love/codex-mobile-bridge/pull/92), [#95](https://github.com/try2love/codex-mobile-bridge/pull/95); **[@mc373906408 (waken)](https://github.com/mc373906408)** for [#88](https://github.com/try2love/codex-mobile-bridge/pull/88); and **[@littltstream](https://github.com/littltstream)** for [#96](https://github.com/try2love/codex-mobile-bridge/pull/96). Original author commits are retained. Thanks to the issue reporters and testers, including **[@SevenPwZ](https://github.com/SevenPwZ)** for [#103](https://github.com/try2love/codex-mobile-bridge/issues/103).

---

<a id="v216"></a>

## v2.1.6 · 会话整理、文件阅读与接入体验

本版完善手机 App 与 Web 的会话整理、复制和文件阅读，优化账号管理，并修复跨平台项目分组与列表更新问题。桌面与手机 App 版本为 **2.1.6**，Android/iOS 构建号为 **39**。

### 新增与改进

- **会话置顶**：Codex、Claude 和 DSH 在“按最近交互”列表中支持置顶与取消置顶。操作先在当前设备生效，再同步到网关，减轻弱网等待；这是 Bridge 手机 App 与 Web 的列表偏好，不修改原生 App 或 CLI 的置顶状态。电脑右键、手机长按可打开会话菜单。
- **断开连接并归档**：增加二次确认，左侧“确认归档”、右侧“取消”，两个按钮采用相同样式和大小。在支持的接入中停止所选会话及可核验的子任务，确认停止后再归档；聊天记录保留，可取消归档。无法确认停止或归档结果时不会自动重复提交。原生能力限制见下文，不能保证所有 App/CLI 会话均可归档。
- **复制与会话详情（#74、#75）**：支持复制 Codex 深度链接、DSH Session ID，以及文件和目录的绝对路径；修复旧版 iOS/Android App 中复制未写入剪贴板、缺少结果提示的问题。深度链接入口移至会话详情的“在电脑运行”旁；Windows 普通盘符与 UNC 路径显示不再带扩展路径前缀。
- **文件阅读（#80、#81）**：Markdown 可切换阅读与源码模式，并保留相应阅读位置。增加独立的文件字号设置，应用于文本、代码、Markdown 和 HTML 预览；图片与 PDF 继续使用各自的缩放方式。
- **账号与 API 接入（#78）**：统一 Codex 账号清单，保留桌面网关的“添加账号或 API”和具体额度展示。手机 API 表单沿用现有配色，折叠标题与相邻设置一致，去掉多余外框，补齐输入标识；支持“获取上游模型”并自动填入默认模型。官方账号登录仍在电脑完成。
- **网关与列表**：直接展示全部访问地址（#76），移除“客户端与接入”中多余的 Codex/Claude 分组标题。Mac/Linux 不初始化或展示 WSL 模块。修复 DSH/Claude 列表与置顶状态读取范围导致的更新失败，以及最近会话和项目列表之间的缓存干扰。
- **连接与项目分组（PR #73、#82）**：修复 Windows SSH/CLI 项目目录分组名称；提高 HTTP 监听队列容量，改善并发连接接入，并验证异常 GET 请求关闭连接时不会污染后续请求。

### 归档支持范围与限制

- **Codex**：支持兼容原生运行时上的 CLI/WSL 会话，以及没有活跃写入者的 App 历史。**仍由 Codex App 持有的部分会话暂时不能从 Bridge 释放并归档**，请在 Codex App 中归档；Bridge 不会为此退出整个 App、影响其他会话或直接改写原生数据库。
- **Claude**：Claude Code Desktop 通过支持的原生接口停止并归档。CLI/SSH/WSL 使用可逆文件归档，仅在原生版本、配置身份、会话文件及写入进程检查通过后启用；未验证的版本、Windows 原生 CLI，以及仍检测到其他写入者的环境可能无法归档。**Cowork 暂不支持归档与恢复。**
- **DSH**：需要支持本版归档流程的连接器。停止目标会话及其任务后重新核验，再调用原生归档；无法确认安全状态时不执行。升级运行中的旧连接器需要显式重启接入，启动网关本身不会自动重启 DSH。
- 归档不会删除聊天记录，也不会在取消归档时自动恢复未完成任务。上述未完成范围继续由 [#77](https://github.com/try2love/codex-mobile-bridge/issues/77) 跟踪。

### 升级

覆盖安装对应平台原 App，不要先卸载或清除数据。Android 沿用原签名；iOS 仍需使用相同应用标识与签名身份自行覆盖安装。独立 Test 与正式 App 保持独立安装与数据目录。

更新网关后刷新 Web 或手机共享页面；旧手机 App 可在原电脑连接的“更多 → 刷新页面”加载新版。若正式版和 Test 共用一个 DSH 原生连接器，应一并升级到兼容版本；两套网关的独立安装并不隔离同一个原生连接器。仅在确认原生任务已停止后，通过明确的重启操作完成连接器升级。

### 贡献与反馈

感谢 [@lanchengjiu](https://github.com/lanchengjiu) 的 [PR #73](https://github.com/try2love/codex-mobile-bridge/pull/73)，以及 [@mc373906408（waken）](https://github.com/mc373906408) 的 [PR #82](https://github.com/try2love/codex-mobile-bridge/pull/82)。两位作者的原始提交通过 merge 保留。感谢 #74、#75、#76、#77、#78、#80、#81 的建议与跨平台测试反馈。

### English

**v2.1.6 improves chat organization, file reading, copying and account management.** Desktop/mobile version: **2.1.6**, Android/iOS build **39**.

- Pin Codex, Claude and DSH chats in Bridge's recently active list. Apply changes locally before gateway synchronization; native clients' pin states are unchanged. Context menus support desktop right-click and mobile long-press.
- Confirm before stopping and archiving a supported chat. Confirm archive is on the left and Cancel on the right, with equal styling and size. Verify the selected chat and its descendants have stopped before archival; retain history and never replay uncertain operations.
- Copy Codex deep links, DSH session IDs and absolute file/folder paths. Restore clipboard writes and feedback in older iOS/Android apps; normalize Windows display paths.
- Switch Markdown between reading and source views and use a separate file-text size preference. Images and PDFs retain their own zoom controls.
- Unify Codex account inventory while retaining desktop quota details and the existing add-account/API entry. Improve the mobile API form and fetch upstream models to fill a default model. Official sign-in remains on desktop.
- Show all gateway addresses, remove redundant client headings and keep WSL initialization/UI on Windows. Fix DSH/Claude list refresh scope and recent/project cache interference. PR #73 corrects Windows SSH/CLI project names; PR #82 increases the HTTP listener backlog.

**Archive limitations:** Codex App-owned writers cannot always be released externally; archive those chats in Codex App. Claude Code Desktop needs compatible native support. Claude CLI/SSH/WSL reversible file archival is gated by verified versions, identity, files and writer checks; unsupported versions and Windows native CLI may be unavailable. Cowork archive/restore is unsupported. DSH needs the compatible connector and fresh stop verification. Bridge never quits a shared app or kills unrelated work to archive a chat. Restoring history does not restart unfinished tasks. Issue #77 remains open for these limitations.

Install over the matching existing app without clearing data; retain Android signing and use matching iOS identifiers/signing. Refresh shared pages after the gateway update. If Test and production share one native DSH connector, upgrade both gateways compatibly and explicitly restart the connector only after its tasks have stopped.

Thanks to [@lanchengjiu](https://github.com/lanchengjiu) for PR #73 and [@mc373906408 (waken)](https://github.com/mc373906408) for PR #82; original author commits are retained through merges.

---

<a id="v215"></a>
<a id="v215-unreleased"></a>

## v2.1.5 · WSL CLI、项目创建与聊天管理

本版扩展 Windows 网关的 WSL Codex / Claude Code CLI 接入，统一跨客户端新建项目，并完善回复阅读、消息队列和浏览器导航。桌面与手机 App 版本为 **2.1.5**，Android/iOS 构建号为 **38**。

### 新增与改进

- **WSL Codex 与 Claude Code CLI**：扫描并登记正在运行的发行版，在原 Linux 用户和配置目录中读取历史、显式继续或新建聊天；Codex 共用 APP / CLI / WSL 来源筛选，Claude 标注 CLI · WSL。按原生能力提供模型、推理、权限、Skill、审批、停止、文件、Git、附件、终端和通知；Codex 还支持原生 Goal 与持久队列。保留各环境的账号、工作目录、草稿、历史分页及操作回执；模型目录按身份缓存。被动浏览不唤醒已停止的发行版，关闭接入不退出原 CLI 任务，主题切换不重建 WSL Claude 会话。[WSL 使用说明](docs/wsl-codex.md)
- **WSL 账号与接入（实验性）**：按发行版、用户和配置目录保存、导入及切换支持的 API 接入，并恢复原始文件配置。官方登录仍在原 WSL 完成；Windows 与 WSL 不自动同步凭据。切换前检查任务、审批、队列和未确认操作，空闲执行器正常交接，无法核验或外部工具管理的配置停止切换。此能力不保证所有账号与配置可用，尚未完成真实多账号全流程验证；保存或切换成功不代表上游认证成功。[实验性账号说明](docs/wsl-accounts.md)
- **跨客户端新建项目聊天**：Codex App、本机/SSH CLI、WSL、Claude Desktop/CLI 和 DSH Desktop 共用运行环境及目录选择器，可选已有项目、浏览其他目录或在父目录中新建文件夹。沿用原生项目登记和权限；Claude 明确显示“创建并发送”，浏览目录本身不发送消息。新 SSH 目录、旧运行时及 DSH 连接器的能力边界见[使用说明](docs/project-chats.md)。
- **回复审查、引用与附件**：完整代码审查标记展示为可复制的审查卡，授权文件可打开并高亮对应代码行；后续操作按钮先追加到草稿，再由用户发送。Codex / Claude 支持消息和选中文字引用，保留原草稿并按会话恢复；图片预览可添加到当前聊天附件。文件引用和修改文件摘要提供受权限约束的位置链接，长摘要保留分页。`codex://review` 保留 Desktop 目标，识别到 GitHub PR 时提供网页入口；不承诺在手机复现 Desktop 专属审查状态。Mermaid 继续沿用已有预览能力。[操作说明](docs/output-interactions.md)
- **消息管理与多页面一致性（#71、PR #70）**：在“更多 → 消息管理”中撤回未发送消息、原位编辑并保留附件和队列位置、调整网关实际发送顺序，以及核对或显式忽略未确认记录。已交给 Claude / DSH 原生客户端的队列仍由原客户端管理。修复明确拒绝发送后的残留与旧页面记录互相干扰；不清空其他页面请求，不自动重发未知结果，编辑冲突保留内容并支持重新打开管理器。[消息管理](docs/message-management.md)
- **返回与项目分组（#67、PR #70）**：浏览器返回遵循聊天、工具与全屏层级，避免意外离开工作台；修复 Windows / UNC 项目归类，并按实际执行主机的路径规则分组。
- **按需资源与弱网**：聊天列表启动不再等待可选的文件和图表渲染器；打开对应功能时再加载，复用内容缓存并保留有限恢复。消息和配对等写入不自动重放。窄屏 WebKit 的 WSL 弹窗适配可用视区。
- **客户端与手机生命周期**：改善连接入口恢复、原生聊天输出与分页记录还原，保留客户端任务及接入身份；区分关闭 Bridge 接入、停止任务与退出桌面程序，避免误影响其他原生进程。

### 升级与边界

覆盖安装对应平台原 App，不要先卸载或清除数据。Android 沿用原签名；iOS 仍需自行签名，使用相同应用标识与签名身份覆盖安装。独立 Test 与正式版互不覆盖。更新网关后刷新 Web / 手机共享页面；旧 Android 可在原电脑连接的“更多 → 刷新页面”主动加载新版。

WSL 功能需要已安装且能力兼容的原生 CLI；扫描不安装程序，也不自动启用新入口。账号管理仍为实验性，不将模拟测试、配置写入或模型列表视为真实多账号认证与任务验证。各平台、构建和实测范围见[验证记录](VERIFICATION.md#v215)。

### 贡献与反馈

- 感谢 [@we-worker](https://github.com/we-worker) 在 [PR #64](https://github.com/try2love/codex-mobile-bridge/pull/64) 中贡献 WSL、Magpie 兼容与项目目录能力，全部 **8 个原始提交**保留。
- 感谢 [@lanchengjiu](https://github.com/lanchengjiu) 在 [PR #70](https://github.com/try2love/codex-mobile-bridge/pull/70) 中修复 Windows 项目归类和发送拒绝残留，原始提交 **d62002b** 保留在历史中。
- 感谢 [#67](https://github.com/try2love/codex-mobile-bridge/issues/67)、[#71](https://github.com/try2love/codex-mobile-bridge/issues/71) 及回复阅读相关反馈；[#46](https://github.com/try2love/codex-mobile-bridge/issues/46) 的 Desktop 审查链接按上述边界处理。

### English

**v2.1.5 expands WSL CLI access, project creation, reply interactions and message management.** Desktop/mobile version: **2.1.5**, Android/iOS build **38**.

- **WSL Codex and Claude Code:** register running distributions and use their Linux user, configuration, account and workspace. Browse history, explicitly resume or create chats, and use native-supported models, reasoning, permissions, Skills, approvals, stop, files, Git, attachments, terminals and notifications. Codex also supports native Goal controls and durable queues. Preserve environment-scoped drafts, paging, receipts and identity-bound model caches. Passive reads do not wake stopped distributions; disconnecting Bridge does not quit original CLI tasks. Theme changes keep WSL Claude sessions intact. [WSL guide](docs/wsl-codex.md)
- **Experimental WSL account management:** save, import, switch supported API connections and restore original file configuration per distribution/user/configuration directory. Official sign-in stays in WSL; Windows credentials are not synchronized automatically. Check tasks, approvals, queues and unconfirmed operations before graceful idle-runtime handoff; refuse unverifiable or externally managed configurations. Compatibility is not guaranteed, and real multi-account end-to-end validation remains incomplete. Saving or switching configuration does not establish upstream authentication. [Account guide](docs/wsl-accounts.md)
- **New project chats:** a shared environment/directory picker serves Codex App, local/SSH CLI, WSL, Claude Desktop/CLI and DSH Desktop. Browse existing directories or create a folder, retaining native registration and permissions. Claude explicitly uses **Create and send**. New SSH paths, older runtimes and DSH connector requirements retain the limits in the [project guide](docs/project-chats.md).
- **Reply interactions:** render complete code-review directives as copyable cards; open authorized code locations with line highlighting. Follow-up buttons append to the draft for explicit sending. Codex/Claude quotes preserve and restore scoped drafts, and image previews can add current-chat attachments. File citations and paged edited-file summaries link to authorized locations. Desktop review links retain their target, with a web action for recognized GitHub PRs; Desktop-only review state is not reproduced on mobile. Existing Mermaid preview remains available. [Interaction guide](docs/output-interactions.md)
- **Message management and multiple pages (#71, PR #70):** withdraw unsent messages, edit in place while retaining attachments and queue position, persist actual dispatch order, and reconcile or explicitly dismiss unconfirmed records. Claude/DSH native queues remain with their clients. Fix residual records after explicit rejection and interference from older pages. Preserve other pages' submissions, do not replay unknown outcomes, and retain conflicting edits for reopening. [Message guide](docs/message-management.md)
- **Navigation and grouping (#67, PR #70):** browser Back follows chat/tool/fullscreen navigation without unexpectedly leaving the workbench. Correct Windows/UNC project grouping using the execution host's path rules.
- **Resources and slow links:** keep optional file/diagram renderers off chat-list startup, load them when needed, reuse cached content and retain bounded recovery. Writes are not replayed automatically. Fit WSL dialogs within narrow WebKit views.
- **Client and mobile lifecycle:** improve connection-entry recovery and native-output/paged-history restoration while preserving task and connection identity. Keep Bridge disconnection, task stopping and desktop exit distinct.

Install over the matching app without clearing data. Android retains its signing identity; iOS requires your own matching signing identity and app identifiers. Test apps remain separate. Refresh shared pages after updating the gateway; older Android apps may need **More → Refresh page** in the existing computer connection. WSL needs an installed, compatible native CLI; discovery neither installs nor automatically enables it. Experimental account configuration and simulated checks are not real multi-account authentication evidence. See [verification coverage](VERIFICATION.md#v215).

Thanks to [@we-worker](https://github.com/we-worker) for [PR #64](https://github.com/try2love/codex-mobile-bridge/pull/64), with all **eight original commits** retained, and [@lanchengjiu](https://github.com/lanchengjiu) for [PR #70](https://github.com/try2love/codex-mobile-bridge/pull/70), retaining original commit **d62002b** in ancestry. Thanks to reporters of [#67](https://github.com/try2love/codex-mobile-bridge/issues/67), [#71](https://github.com/try2love/codex-mobile-bridge/issues/71) and reply-interaction issues; [#46](https://github.com/try2love/codex-mobile-bridge/issues/46) Desktop review links retain the limits above.

---

<a id="v214"></a>

## v2.1.4 · 公网启动、长会话与网络恢复

本版改善公网与弱网下的工作台启动，修复超长会话断开连接的问题，并补齐 Web 一键复制和可选的网络变化自动恢复。桌面与手机 App 版本为 **2.1.4**，Android/iOS 构建号为 **37**。

### 修复与改进

- **公网启动与缓存复用（#62）**：修复 Cloudflare 固定入口因附加统计脚本被拦截而停在错误页的问题。必需资源提前下载、按依赖顺序执行；短暂下载失败或初始登录状态读取超时有限重试，重复打开复用未变化的缓存资源。
- **长会话连接与阅读（#65）**：修复超大 Desktop 历史快照超过接收上限，导致当前及其他会话断开的问题。进入时先展示缓存和最近内容，上翻才加载更早记录，保留阅读位置；历史读取与原会话实时控制分离，发送和审批继续由原连接处理。
- **多客户端历史**：为 Codex Desktop/CLI、Claude Code Desktop/CLI 和 DeepSeek Harness 接入各自支持的历史读取方式。改善跨页工具记录、过期响应处理和 Windows Claude 历史文件变化识别，保留账号、工作目录和权限边界。
- **Web 一键复制（#66）**：回复、代码和 Mermaid 源码支持点击复制，补齐局域网 HTTP 与延迟获取长正文的兼容路径；浏览器拒绝时提供重新复制和手动入口。
- **可选网络变化恢复（PR #63）**：默认关闭，可在桌面网关或登录后的手机/Web 中即时启停。使用系统网络事件和 30 秒兜底检查，稳定后更新实际可用的局域网入口；同 IP 续期不重建。修复 Windows 网卡地址读取、多网卡旧配置迁移和失败监听地址误报；Quick Tunnel 先自行恢复，连续明确失败后才有限重建。

### 升级

覆盖安装对应平台的原应用，不要先卸载或清除数据，以保留配置、电脑列表和有效手机绑定。Android 沿用原签名；iOS 沿用正式应用标识，仍需使用自己的签名身份安装。独立 Test 与正式版互不覆盖。浏览器及现有手机 App 可在网关更新后刷新共享页面。

网络恢复不会自动改写手机中保存的旧 IP；地址变化后请使用稳定域名、其他有效入口或重新扫码。各平台与升级验收范围见[验证记录](VERIFICATION.md#v214)。

### 贡献与反馈

- [@qybgh](https://github.com/qybgh) 在 [PR #63](https://github.com/try2love/codex-mobile-bridge/pull/63) 中贡献按网卡管理入口、地址变化通知和网络恢复基础；全部 8 个原始提交保留，默认关闭、监听与恢复策略修订另行提交。
- 感谢 [@P2pfans2000](https://github.com/P2pfans2000) 在 [Issue #62](https://github.com/try2love/codex-mobile-bridge/issues/62) 中报告并复测公网启动问题，以及参与跨平台和手机实测的用户。

### English

**v2.1.4 improves public-entry startup, long-chat connections and reading, Web copying, and optional network recovery.** Desktop/mobile version: **2.1.4**, Android/iOS build **37**.

- **Startup and caching (#62):** fix Cloudflare entries remaining on the error page when an optional injected script is blocked. Preload required resources while preserving execution order, retry transient resource/auth-state reads within limits, and reuse unchanged cached assets.
- **Long chats (#65):** fix oversized Desktop snapshots closing the shared connection. Display cached/recent messages first, load older history on demand and preserve reading position. History display is separate from the original connection used for sending and approvals.
- **Client history:** use supported history sources for Codex Desktop/CLI, Claude Code Desktop/CLI and DeepSeek Harness. Improve cross-page tool records, stale-response handling and Windows Claude history-change detection without changing account, workspace or permission boundaries.
- **One-click Web copying (#66):** copy replies, code and Mermaid source, including LAN HTTP and delayed full-text retrieval. Retry and manual copying remain available if the browser refuses clipboard access.
- **Optional network recovery (PR #63):** off by default; toggle from desktop or signed-in mobile/Web. Native events and a 30-second fallback update working LAN entries after addresses stabilize. Same-address renewals do not rebuild entries. Fix Windows adapter addresses, legacy multi-adapter migration and failed-listener reporting. Quick Tunnel reconnects first and is replaced only after repeated confirmed failures.

Install over the matching existing app without uninstalling or clearing data. Android retains its signing identity; iOS retains official identifiers and requires your own signing. Independent Test apps do not replace release apps. Network recovery does not rewrite old IP addresses saved on phones; use a stable hostname, another working entry or a new pairing QR code.

Thanks to [@qybgh](https://github.com/qybgh) for [PR #63](https://github.com/try2love/codex-mobile-bridge/pull/63), with all eight original commits preserved, and [@P2pfans2000](https://github.com/P2pfans2000) for reporting and retesting [#62](https://github.com/try2love/codex-mobile-bridge/issues/62). See the [verification record](VERIFICATION.md#v214) for platform and upgrade coverage.

---

<a id="v214-preview1"></a>

## v2.1.4-preview.1 · 启动恢复与首次加载优化

这是面向体验用户的预发布版本，重点修复 Issue #62 的工作台启动问题。**v2.1.3 仍是最新正式版**，正式版更新检查不会自动安装本预览版；本轮代码保留在独立预发布分支，尚未合并 main。

### 修复与改进

- **Cloudflare 固定入口**：修复部分 Cloudflare Tunnel 页面因附加统计脚本被浏览器安全策略拦截，而一直停留在“工作台尚未加载完成”的问题。保留原有访问安全限制。
- **首次加载更快**：页面样式加载时提前下载必需脚本，减少弱网和公网入口下的多轮等待；脚本仍按依赖顺序执行。
- **缓存复用**：重复打开继续复用内容版本相同的资源；升级后仅更新发生变化的资源，首页及时读取当前版本。
- **启动自动恢复**：短暂的资源下载失败或初始登录状态读取超时会有限重试；持续失败时显示恢复状态、可展开的详情和重新加载入口，不再只有空白页面。
- **手机与无障碍显示**：改善早期原生剪贴板脚本加载失败后的恢复，以及减少动态效果设置下的启动提示。配对和消息等写入不会自动重发。

### 下载与升级

桌面及手机发布版本为 **2.1.4-preview.1**，Android/iOS 构建号为 **36**（iOS 系统版本字段为 2.1.4）。提供 macOS Apple Silicon/Intel、Windows x64、Linux x64/arm64、Android APK、iOS 未签名 IPA 与 Xcode 源码包。

选择对应安装包覆盖原安装，保留原数据目录；Android 使用原签名，iOS 沿用原应用标识并需自行签名。不要先卸载或清空数据。仅更新 Web 网关也可使用本次共享页面修复。反馈时请注明平台、浏览器或 App、访问方式，以及首次/重复打开的表现；可附启动页展开的详情，请勿包含登录链接或凭据。

感谢 [Issue #62](https://github.com/try2love/codex-mobile-bridge/issues/62) 的问题反馈与参与跨平台实测的用户。验收范围见 [验证记录](VERIFICATION.md#v214-preview1)。

### English

**v2.1.4-preview.1 is an opt-in prerelease focused on Issue #62 startup recovery and first-load performance.** v2.1.3 remains the latest stable release. Stable update checks do not install this preview automatically, and these changes remain on a separate branch pending wider feedback.

- Fix workspaces getting stuck behind “The workspace has not finished loading” when an optional script added by Cloudflare is blocked. Existing security restrictions remain in place.
- Start downloading required scripts while styles load to reduce sequential waits on slow or public connections, while preserving execution order.
- Reuse unchanged, content-versioned resources on repeat visits and after upgrades; the entry page continues to fetch the current version.
- Retry transient resource failures and the initial sign-in-state read within fixed limits. Persistent failures show recovery status, expandable details and a reload action instead of leaving an empty page.
- Improve early native clipboard recovery and startup indicators with reduced motion. Pairing, messages and other writes are not automatically replayed.

Desktop/mobile release version: **2.1.4-preview.1**, Android/iOS build **36** (iOS marketing version 2.1.4). Downloads include macOS Apple Silicon/Intel, Windows x64, Linux x64/arm64, Android APK and unsigned iOS IPA/Xcode sources. Install over the matching existing app without uninstalling or clearing data; Android retains its signing identity and iOS retains its app identifiers but requires your own signing. Updating the gateway also delivers these shared Web fixes to existing mobile apps.

Thanks to the [Issue #62](https://github.com/try2love/codex-mobile-bridge/issues/62) reporter and cross-platform testers. Please report your platform, browser/app, connection type and first/repeat-load behavior. See the [verification record](VERIFICATION.md#v214-preview1) for coverage.

---

<a id="v213"></a>
<a id="unreleased-webhook"></a>
<a id="unreleased-mermaid"></a>

## v2.1.3 · 多目标通知、图表阅读与连接恢复

本版新增多个独立 Webhook 目标和 Mermaid 图表阅读，并改善弱网加载、iOS 连接恢复及手机输入交互。桌面与手机 App 版本为 **2.1.3**，Android/iOS 构建号为 **35**。各平台验收范围以[验证记录](VERIFICATION.md)为准。

### 新增与改进

- **多个 Webhook 接收目标**：每条配置可单独命名、启停、编辑、测试和删除；旧单目标配置仍可见。支持自动识别、通用自定义、飞书、钉钉、Discord 和 Telegram 的可编辑 JSON 模板，飞书与钉钉支持可选签名。修复飞书返回 HTTP 200 但业务拒绝时误报成功的问题。测试只使用已保存配置；保存不会发送通知。普通配置读取隐藏已保存地址、请求头和签名密钥，地址仅在主动查看时返回。每个已启用目标独立投递、去重与重试，不启动会话，也不补发历史通知。详见 [Webhook 配置说明](docs/webhook-notifications.md)。
- **Mermaid 图表阅读**：在 Codex、Claude、DeepSeek Harness 聊天与 Markdown 文件中预览完整 Mermaid 代码块，可切换源码并复制原文。全屏时手机可双指缩放、放大后拖动或双击放大、还原；宽屏可用滚轮、按钮和双击缩放，再拖动图表。流式未完成内容保留源码；后续文字更新复用未变化图表。iOS App 同步修复图表预览子页面被导航策略拦截的问题，需同时更新网关与 App。
- **弱网加载与 Windows 包体**：静态资源按内容生成版本地址，重复访问可使用浏览器缓存与条件请求；带页面专属安全随机数的 HTML 仍不缓存。Windows 包仅保留支持的 Electron 语言资源，以缩减包体。
- **接入与恢复**：修复 DeepSeek Harness 自动折行的 YAML 接入配置无法识别的问题；iOS 网关不可达或遇到隧道错误时提供原生重试、返回电脑列表入口。账号查询在桌面标签可见性变化后恢复，收敛不必要的请求。
- **手机交互**：弹窗打开时隐藏原生“更多”入口；调整手机列表、弹窗与常规输入框的焦点外观。补充回答卡片输入框被键盘遮挡时做一次可见定位，输入中不反复移动。

### 升级

覆盖安装以保留原电脑列表、配对和网关数据，不要先卸载或清除配置。Android 沿用原签名；iOS 保留正式应用标识，仍需使用自己的签名身份安装。Web 页面在网关更新后刷新；iOS 的原生连接恢复和 Mermaid 子页面修复需要配套 App 更新。共享中继部署需同步更新服务。

### 贡献者

- [@wangzheuser](https://github.com/wangzheuser) 提交 [PR #51](https://github.com/try2love/codex-mobile-bridge/pull/51)，修复 Harness 折行配置解析；原提交作者 [@w154594742](https://github.com/w154594742) 的署名保留。
- [@RigerWu](https://github.com/RigerWu) 在 [PR #52](https://github.com/try2love/codex-mobile-bridge/pull/52) 中贡献 iOS 原生连接恢复入口。
- [@heiqiu47](https://github.com/heiqiu47) 在 [PR #40](https://github.com/try2love/codex-mobile-bridge/pull/40) 中提出账号查询改进；本版纳入范围仅为查询生命周期收敛。
- [@jasongzy](https://github.com/jasongzy) 通过 [Issue #60](https://github.com/try2love/codex-mobile-bridge/issues/60) 提出通用 Webhook 通知需求。
- [@tiancai123123](https://github.com/tiancai123123) 在 [Issue #54](https://github.com/try2love/codex-mobile-bridge/issues/54) 中定位静态资源重复下载，并在 [PR #56](https://github.com/try2love/codex-mobile-bridge/pull/56) 中贡献内容哈希缓存与条件请求。

### English

**v2.1.3 brings multiple Webhook destinations, Mermaid reading, faster repeat loads on slow links, iOS connection recovery and mobile interaction refinements.** Desktop and mobile apps use version **2.1.3**; Android/iOS build number **35**. See the [verification record](VERIFICATION.md) for platform-specific coverage.

- **Webhook destinations:** create multiple named endpoints and enable, edit, test or delete each independently. Editable JSON presets cover automatic detection, Custom Webhook, Feishu, DingTalk, Discord and Telegram; Feishu and DingTalk offer optional signing. Feishu responses with HTTP 200 but a rejected business code no longer report success. Tests use saved settings only, and saving does not send a notification. Ordinary settings reads mask saved URLs, headers and signing secrets; a saved URL is returned only on explicit reveal. Enabled endpoints deliver, deduplicate and retry independently, without activating chats or replaying past notifications. See the [setup guide](docs/webhook-notifications.md#english).
- **Mermaid reading:** preview complete Mermaid fences in Codex, Claude and DeepSeek Harness chats and Markdown files, switch to source and copy it. In fullscreen, pinch to zoom, drag when enlarged or double-tap to zoom and reset on mobile; use the wheel, buttons or double-click to zoom, then drag, on wider screens. Incomplete streamed diagrams remain source; unchanged diagrams are reused as text continues. iOS also fixes navigation of the diagram preview subframe, so update both the gateway and iOS app.
- **Slow-link loading and Windows size:** content-versioned static assets use browser caching and conditional requests on repeat visits. The HTML shell still avoids caching because it contains a per-page security nonce. Windows packages retain supported Electron locales only.
- **Connections:** parse DeepSeek Harness YAML settings folded across lines; show native Retry and Back to computers actions when the iOS gateway or tunnel is unreachable. Account queries resume when the desktop tab becomes visible, with narrower request scope.
- **Mobile controls:** hide the native More menu over open dialogs and refine list and dialog focus appearance. When the keyboard covers an answer-card input, bring it into view once without repeatedly moving it during typing.

Install over the existing app to retain saved computers, pairings and gateway data. Android keeps its signing identity; iOS keeps its official bundle IDs and requires your own signing. Refresh Web pages after the gateway update. iOS recovery and Mermaid subframe changes require the matching app; shared-relay deployments should update the relay.

**Contributors:** [@wangzheuser](https://github.com/wangzheuser) submitted [PR #51](https://github.com/try2love/codex-mobile-bridge/pull/51), retaining original commit credit for [@w154594742](https://github.com/w154594742); [@RigerWu](https://github.com/RigerWu) contributed iOS recovery in [PR #52](https://github.com/try2love/codex-mobile-bridge/pull/52); the integrated part of [@heiqiu47](https://github.com/heiqiu47)'s [PR #40](https://github.com/try2love/codex-mobile-bridge/pull/40) is limited to account-query lifecycle improvements; and [@tiancai123123](https://github.com/tiancai123123) reported the static-download issue in [Issue #54](https://github.com/try2love/codex-mobile-bridge/issues/54) and contributed caching in [PR #56](https://github.com/try2love/codex-mobile-bridge/pull/56).

[@jasongzy](https://github.com/jasongzy) requested general Webhook notifications in [Issue #60](https://github.com/try2love/codex-mobile-bridge/issues/60).

---

## v2.1.2 · 终端交互与跨平台兼容性

v2.1.2 重点改善手机与 Web 终端体验，让直接输入、中文输入、触摸滚动、字号调整和断线恢复更顺畅，并补齐 Windows 终端组件的安装与鼠标交互支持。

### 新增与优化

- **终端直接输入**：手机与宽屏 Web 默认直接在终端输入，保留可选的“编写后发送模式”；改善键盘唤起、输入焦点、中文组合输入和光标显示。
- **Enter 与 Tab**：新增独立 Enter 按钮，可确认终端 CLI 菜单和命令；直接模式的 Tab 交给当前 shell 或 CLI，编写模式支持公共前缀补全及候选选择，兼容中文与空格路径。
- **触摸与鼠标交互**：改善 OpenCode 类终端 CLI 的触摸滚动，保留普通终端历史滚动；支持程序启用的鼠标交互，避免用上下方向键代替翻阅。
- **更连贯的终端连接**：持续双向传输减少逐次请求与排队等待；临时断线后自动恢复仍有效的会话。高延迟网络下减少额外等待，远端回显仍受实际网络延迟影响。
- **独立终端字号**：通过加减按钮在 6–24px 调整，当前设备的终端共享该设置；shell 路径移至底部，为终端正文留出更多空间。
- **阅读设置**：主题与语言放在设置顶部，正文字号范围扩大为 10–32px，代码字号扩大为 8–28px。

### Bug 修复

- 修复手机终端调出键盘后输入无效，以及编写模式点击正文出现无效输入焦点。
- 修复中文、英文、粗体与方块字符混排时的网格错位，小字号文字重叠，以及 CLI 分栏背景边界不齐。
- 修复终端退出交互式 CLI、收放键盘或调整尺寸后，内容覆盖底部按钮及右侧滚动条。
- 修复部分环境下终端未使用 UTF-8 导致中文显示异常，以及重新进入终端需要手动重连。
- 修复 Windows 10 系统 ConPTY 丢失鼠标信号；正式 Windows 安装包和 ZIP 携带完整 ConPTY 组件，无需手动补文件或替换系统组件。
- 修复 Windows 安装器遗漏部分终端组件的解压兼容性问题。

### 升级

桌面与手机 App 统一为 **2.1.2**，Android/iOS 构建号 **34**。覆盖安装并保留原数据目录，不要先卸载或清除配置；Android 沿用原签名，iOS 保留正式应用标识，仍需使用自己的签名身份安装。浏览器在网关升级后刷新；建议手机 App 同步更新以获得完整终端体验。

发布包覆盖 macOS Apple Silicon / Intel、Windows x64、实验性 Linux x64 / ARM64、Android，以及需自行签名的 iOS IPA/源码包。Windows 的包内 ConPTY 不替换系统组件；Linux 使用安装包手动升级。使用 `SHA256SUMS.txt` 校验下载。

### English

v2.1.2 focuses on mobile and Web terminals: direct input, Chinese composition, touch scrolling, font sizing and reconnection, together with complete Windows terminal components and mouse interaction support.

#### Features and improvements

- **Direct terminal input:** enabled by default on mobile and wide-screen Web, with optional Compose & send mode. Improve keyboard activation, focus, Chinese composition and cursor visibility.
- **Enter and Tab:** a dedicated Enter button confirms commands and CLI menus. Direct mode forwards Tab to the current shell or CLI; compose mode supports common-prefix completion and candidate selection, including paths with Chinese characters or spaces.
- **Touch and mouse interaction:** improve touch scrolling in terminal CLIs such as OpenCode while retaining normal terminal scrollback. Forward supported mouse interaction without substituting history-navigation arrow keys for scrolling.
- **Smoother connections:** persistent duplex transport reduces request and queueing overhead; reconnect automatically to valid sessions after temporary disconnections. Remote echo still depends on actual network latency.
- **Independent terminal size:** use minus/plus controls from 6–24px, shared across terminals on the current device. Move the shell label to the footer to free content space.
- **Reading settings:** place theme and language first; expand text size to 10–32px and code size to 8–28px.

#### Fixes

- Fix mobile keyboards opening without delivering terminal input, and ineffective terminal focus in compose mode.
- Correct cell alignment for Chinese, Latin, bold and block characters, overlapping small fonts and uneven CLI sidebar backgrounds.
- Keep terminal output inside its viewport after exiting an interactive CLI, resizing or showing/hiding the keyboard, without covering controls or the scrollbar.
- Correct UTF-8 terminal locale selection and automatic reconnection when returning to a terminal.
- Fix lost mouse signals in Windows 10 system ConPTY. Official Windows installers and ZIPs contain all required ConPTY files, without manual copying or system-component replacement.
- Fix installer extraction compatibility that omitted some Windows terminal components.

#### Updating

Desktop and mobile version **2.1.2**, Android/iOS build **34**. Install over the existing app and retain its data directory. Android keeps the existing signing identity; iOS retains the official bundle IDs and requires your own signing. Refresh Web pages after updating the gateway, and update the mobile app for the complete terminal experience.

Packages cover Apple Silicon/Intel macOS, Windows x64, experimental Linux x64/ARM64, Android and self-signed iOS. Bundled ConPTY does not replace Windows system components. Linux uses manual package upgrades. Verify downloads with `SHA256SUMS.txt`.

---

## v2.1.1 · 模型与账号体验、草稿保存及文件定位

v2.1.1 改善 Codex、Claude 和 DeepSeek Harness 的账号与模型操作，减少重复加载，并补齐手机与 Web 的草稿保存、文件定位和导航体验。

### 新增与优化

- **统一账号与模型界面**：三个客户端采用一致的账号卡片和模型选择布局。当前账号卡片底部提供“查看可用模型”、用量更新时间及刷新；模型列表单独显示上次成功更新时间。
- **模型目录缓存**：优先显示缓存；手动刷新、切换接入或网关运行期间每 12 小时更新。按电脑、客户端、账号和有效接入配置隔离；失败保留原目录，不把失败时间当成成功更新时间。
- **API 与 CLI 模型查询**：补齐可核验的环境变量接入及 SSH CLI 上游目录，保留上游或原生运行时提供的具体模型和思考强度。Claude 账号模型查询不再要求先连接 Desktop；不支持的能力会说明原因，不编造型号或推理选项。
- **保留未发送草稿**：手机与 Web 切换聊天、客户端或电脑后，恢复对应输入草稿；按网关、账号与会话隔离，确认发送后清除。
- **更快定位文件**：从聊天链接定位文件或文件夹时平滑滚动到目标，并高亮 3 秒；用户滚动可以中止自动定位。左上角返回直接回聊天列表，文件区的上级目录与系统返回继续保留层级导航。
- **工具截图**：可查看工具返回的截图，并通过显示设置控制预览；限制预览体积及缓存占用。
- **更新与绑定选择**：网关及手机 App 可跳过当前版本的自动提醒，手动检查仍可更新；修改网关密码时可选择保留仍有效的手机绑定。

### Bug 修复

- 修复 Windows 账号切换或恢复时，配置文件被短暂占用导致操作中断；持续占用仍会报错并保留原文件。

- 修复 Codex 当前账号模型列表长时间停留在读取中、Claude 模型设置重复请求，以及账号切换后旧模型结果覆盖新接入的问题。
- 降低账号面板无必要的高频刷新，修复模型列表滚动时自动回到顶部。
- 修复快速从 Codex 滑到 DeepSeek Harness 时被拉回 Claude；Claude/DSH 重进列表优先展示缓存，再同步最新内容；统一筛选框样式。
- 修复非图片附件的显示与上下文信息、ChatGPT 登录经过自定义网关路由时的身份保留，并限制工作台启动资源并发。
- 修复 iOS 场景生命周期及通知跳转重试，避免启动或恢复期间丢失待处理跳转。
- 加强模型目录缓存身份隔离，修复网关直接查询上游模型目录时的环境凭据误用。

### 贡献者

- [@qybgh](https://github.com/qybgh) — [PR #38](https://github.com/try2love/codex-mobile-bridge/pull/38)：非图片附件显示与上下文修复。
- [@heiqiu47](https://github.com/heiqiu47) — 采纳 [PR #40](https://github.com/try2love/codex-mobile-bridge/pull/40) 中的账号卡片、模型选择和模型目录兼容改进，并适配到当前多客户端界面；不包含重置预测功能。
- [@Lovest20018](https://github.com/Lovest20018) — [PR #42](https://github.com/try2love/codex-mobile-bridge/pull/42) 的启动资源并发控制，以及 [PR #43](https://github.com/try2love/codex-mobile-bridge/pull/43) 的 ChatGPT 路由身份修复。
- [@RigerWu](https://github.com/RigerWu) — [PR #44](https://github.com/try2love/codex-mobile-bridge/pull/44)：工具截图预览、显示控制与缓存限制，以及 iOS 场景生命周期和通知跳转改进。

### 升级

网关与手机 App 均为 **2.1.1**，Android/iOS 构建号 **33**。支持 macOS Apple Silicon / Intel、Windows x64、实验性 Linux x64 / ARM64、Android，以及需自行签名的 iOS IPA/源码包。Linux 的 Claude 接入使用 Claude Code CLI；Claude Desktop 自动连接支持 macOS / Windows。

覆盖安装并保留原数据目录，避免先卸载或清除数据；Android 沿用原签名，iOS 请使用相同签名身份和 Bundle ID。浏览器在网关升级后刷新，共享中继部署同步更新服务。使用 `SHA256SUMS.txt` 校验下载。

### English

v2.1.1 improves account and model controls across Codex, Claude and DeepSeek Harness, reduces repeated loading, and preserves drafts while making files easier to locate on mobile and Web.

#### Features and improvements

- **Consistent accounts and models:** matching account cards and model pickers across clients. The current account footer contains available models, quota update time and refresh; model catalogs show their own last successful update time.
- **Cached model catalogs:** show cached results first, update on manual refresh or access changes, and refresh every 12 hours while the gateway runs. Keep computers, clients, accounts and effective access configurations separate; retain the last successful catalog after a failed refresh.
- **API and CLI discovery:** query upstream catalogs for verifiable environment-based access and SSH CLI, preserving model IDs and reasoning options supplied by the upstream or native runtime. Claude account catalogs no longer require Desktop to be connected. Unsupported capabilities are explained rather than guessed.
- **Saved drafts:** restore unsent text when switching chats, clients or computers, scoped to gateway, account and conversation. Clear drafts after confirmed sends.
- **File location and navigation:** smoothly reveal a linked file or folder and highlight it for three seconds; user scrolling can interrupt. The top-left Back button returns directly to the chat list; folder-parent controls and system Back retain hierarchical navigation.
- **Tool screenshots:** display tool-returned screenshots with preview controls and bounded preview/cache sizes.
- **Update and pairing choices:** skip automatic reminders for an exact release on desktop and mobile while retaining manual updates. Choose whether valid phone bindings survive a gateway password change.

#### Fixes

- Retry brief Windows file-sharing conflicts during account changes or recovery; persistent failures still report an error and preserve the original file.

- Fix stuck Codex account model loading, repeated Claude model requests and stale catalog responses after switching access.
- Remove unnecessary frequent account refreshes and preserve model-list scroll position.
- Prevent fast client swipes from jumping back to Claude; restore cached Claude/DSH lists before background updates and unify filter styles.
- Fix non-image attachment visibility/context and ChatGPT identity behind custom gateway routing; bound workspace startup resource concurrency.
- Handle iOS scene lifecycle and retry pending notification navigation during startup or resume.
- Strengthen model cache identity isolation and fix gateway environment credentials being used for direct upstream model-catalog HTTP requests.

#### Contributors

- [@qybgh](https://github.com/qybgh), [#38](https://github.com/try2love/codex-mobile-bridge/pull/38): non-image attachment visibility and context.
- [@heiqiu47](https://github.com/heiqiu47), selected work from [#40](https://github.com/try2love/codex-mobile-bridge/pull/40): account cards, model selection and catalog compatibility, adapted to the current multi-client UI. Reset predictions are not included.
- [@Lovest20018](https://github.com/Lovest20018), [#42](https://github.com/try2love/codex-mobile-bridge/pull/42) and [#43](https://github.com/try2love/codex-mobile-bridge/pull/43): bounded startup resource loading and ChatGPT routing identity.
- [@RigerWu](https://github.com/RigerWu), [#44](https://github.com/try2love/codex-mobile-bridge/pull/44): tool screenshots, display controls, bounded caches, iOS scenes and pending notification navigation.

#### Updating

Desktop and mobile version **2.1.1**, Android/iOS build **33**. Packages cover Apple Silicon/Intel macOS, Windows x64, experimental Linux x64/ARM64, Android and self-signed iOS. Linux uses Claude Code CLI; automatic Claude Desktop connection supports macOS and Windows.

Install over the existing app and retain its data directory. Android keeps its signing identity; iOS requires the same signing identity and Bundle ID to retain data. Reload Web pages and update shared-relay deployments. Verify downloads with `SHA256SUMS.txt`.

---

## v2.1.0 · CLI 接入、文件阅读与连接体验

v2.1.0 将 Codex CLI 与 Claude Code CLI 纳入手机和网页工作台，并改善已有 HTTPS 入口、文件阅读与多客户端连接。感谢通过 Issues 反馈使用体验。

### 新增功能

- **Codex CLI 会话**：发现并阅读本机或 SSH 上已有聊天；原终端关闭后，可显式点击“继续会话”恢复。支持从已知项目目录新建聊天、读取后续回复，并在原生运行时提供的范围内操作模型、推理强度、Skill、权限、计划模式、审批和 Goal。
- **Claude Code CLI 会话**：在本机或 SSH 上阅读历史、恢复已关闭的聊天，从已有项目新建聊天，并使用模型、权限、Skill、上传、审批和中断。各会话沿用执行端配置和工作目录，不接管其他终端中仍在运行的 Claude 进程。
- **独立 CLI 账号配置**：Codex 与 Claude 分别管理 CLI 接入、模型查询与连通性检查；桌面账号切换可选择同步本机 CLI，并分别显示结果。SSH 保留远端配置。
- **已有 HTTPS 入口**：可接入已有穿透或反向代理的 HTTPS 根地址，包括自定义端口；提供固定入口检测和配置引导。详见[配置说明](https://github.com/try2love/codex-mobile-bridge/blob/v2.1.0/docs/external-tunnel.md)。
- **文件阅读**：源码高亮、行号、复制和换行；PDF 连续滑动、缩放、文字选择与全屏；自包含 HTML 的隔离预览和源码切换。

### 体验优化

- Codex 列表按本机与各 SSH 主机逐步呈现，较慢的主机不阻塞已经读到的聊天；失败主机保留已有列表。Claude Desktop 与 CLI 混合列表按实际时间排序，来源标签持续显示在日期上方；客户端切换滑动时列表与标签布局更稳定。
- iOS Safari 保留原有页面布局，修复输入时的大面积空白及滚动抖动。弹窗可点击外部背景关闭。
- Claude 初始化在已确认提交后更早显示连接进度；Windows 与 macOS 的 Console 初始化减少多余等待。Claude 的额度改为手动查询，保留上次成功结果与时间。
- SSH 连接诊断可复制摘要或导出脱敏报告；文件目录逐级返回、全屏先退出，PDF 切换标签后保留阅读位置。所有客户端停用后，网关仍可独立启动和管理。

### Bug 修复

- 修复 Windows 连续终端子进程输出与 Ctrl+C，保留扩展路径中的项目工作目录；CMD 跨盘切换说明给出完整 `cd /d` 示例。
- 修复当前 DeepSeek Harness 主机记录在不同换行格式下的识别；修复 Claude Desktop 普通退出的会话归属判断、首次 Windows 初始化时的错误交接，以及重连与初始化期间的请求状态。
- 修复 Claude Desktop / CLI 混合日期排序，以及 Claude Code CLI 恢复时合成模型名覆盖真实模型的问题；已退出的旧版 Claude Code 写入者可在确认退出后恢复，身份不明的进程仍受保护。
- 修复固定 HTTPS 入口检测后的旧错误残留，并减少 PDF 快速翻阅、缩放时的闪烁。

### 升级说明

提供 macOS Apple Silicon / Intel、Windows x64、实验性 Linux x64 / ARM64、Android APK、iOS 未签名 IPA 和源码包。建议网关与手机 App 一起更新；浏览器用户更新网关后刷新页面，共享中继部署也需同步更新中继服务。Android 请覆盖安装以保留电脑列表和绑定，不要先卸载；iOS 需自行签名，并以相同签名身份覆盖安装以保留 App 数据。可用 `SHA256SUMS.txt` 校验下载。

CLI 需要执行端已安装并配置对应原生程序。Linux 支持 Claude Code CLI，Claude Desktop 自动连接仍只支持 macOS / Windows。PDF 在线预览上限 **20 MiB**，还受普通点击下载限额约束；源码与 HTML 预览支持 **512 KiB 以内 UTF-8 文本**。HTML 预览面向自包含文件，不访问外部网络或网关登录信息。

### English

v2.1.0 brings Codex CLI and Claude Code CLI chats to mobile and Web, together with existing HTTPS entry support, file reading, and more reliable multi-client connections.

#### Features

- **Codex CLI chats:** discover and read local or SSH history, explicitly resume after the original terminal closes, and create chats in known project folders. Supported native runtimes provide model/effort, Skills, permissions, plan mode, approvals and Goals.
- **Claude Code CLI chats:** read history, resume closed chats, create chats in known local or SSH projects, and use available models, permissions, Skills, uploads, approvals and interrupt. The execution host retains its configuration and working directory; live writers in other terminals are not taken over.
- **Separate CLI account settings:** manage Codex and Claude CLI access independently, query available models, test connectivity, and optionally sync a desktop account switch to the local CLI with separate results. SSH hosts retain their own settings.
- **Existing HTTPS tunnels and proxies:** add a managed HTTPS root URL, including a custom port, and check the fixed entry. See the [setup guide](https://github.com/try2love/codex-mobile-bridge/blob/v2.1.0/docs/external-tunnel.md).
- **File reading:** highlighted source with line numbers, copy and wrapping; continuous PDF scrolling, zoom, text selection and fullscreen; isolated preview and source mode for self-contained HTML.

#### Improvements

- Render Codex chat rows progressively as local and SSH hosts respond. Sort mixed Claude Desktop/CLI chats by actual recency and keep source labels visible above dates. Stabilize badges and the client-swipe layout.
- Preserve the original iOS Safari layout and fix large blank areas and scroll jitter while typing. Dismiss dialogs by tapping their backdrop.
- Show Claude connection progress after verified initialization submission and avoid redundant Console waits on Windows and macOS. Query Claude quota on demand while retaining the last successful result.
- Add copy/export SSH diagnostics, preserve file and PDF reading position, and keep the gateway manageable when every client integration is disabled.

#### Fixes

- Restore Windows continuous-terminal child output and Ctrl+C, preserve project cwd for extended local paths, and document a complete `cd /d` drive-switch example.
- Recognize the current DeepSeek Harness host across LF/CRLF records. Correct Claude Desktop quit ownership checks, first-time Windows initialization handoff, and request state during reconnect and initialization.
- Correct mixed Claude Desktop/CLI date sorting and prevent a synthetic model name from replacing the real Claude CLI model on resume. Resume legacy Claude writers only after their exit is confirmed.
- Clear stale fixed-HTTPS check errors and reduce flashes during rapid PDF navigation and zoom.

#### Updating

Packages cover Apple Silicon/Intel macOS, Windows x64, experimental Linux x64/ARM64, Android APK, unsigned iOS IPA and source. Update the gateway and mobile apps together; reload browser pages and update shared-relay deployments. Install Android over the existing app to retain saved computers and pairing. Sign and install iOS with the same signing identity to retain app data. Verify downloads with `SHA256SUMS.txt`.

The execution host needs its native CLI installed and configured. Linux supports Claude Code CLI; Claude Desktop automatic connection remains limited to macOS and Windows. PDF previews are capped at **20 MiB** and the ordinary-download limit; source/HTML previews accept **UTF-8 text up to 512 KiB**. HTML previews are self-contained and cannot access external networks or gateway sign-in data.

---

## v2.1.0-preview.1 · CLI 接入、已有 HTTPS 入口与阅读体验

这是 v2.1.0 的首个预览版，欢迎体验并通过 [Issues](https://github.com/try2love/codex-mobile-bridge/issues) 反馈问题和建议。稳定版继续保留 v2.0.3。

### 新增功能

- **Codex CLI 会话**：在手机与 Web 发现和阅读已有 CLI 聊天，原终端关闭后也可点击“继续会话”恢复；支持从已有本机或 SSH 项目目录新建 CLI 聊天。终端后续回复可持续同步阅读，每轮回复按原始记录显示模型。
- **CLI 完整操作**：Codex CLI 支持模型与推理强度、Skill、普通/计划模式、权限切换、审批与中断；兼容的本机和 SSH 原生 CLI 支持 Goal 的创建、暂停、修改、恢复与关闭。
- **Claude Code 本机与 SSH 接入**：支持历史阅读、已关闭会话恢复、新建聊天、发送消息、模型与权限切换、Skill、文件上传、审批及中断，沿用执行端的配置与工作目录。
- **独立 CLI 账号配置**：Codex 与 Claude 在各自账号管理中配置 CLI，支持查询上游模型和测试连通性，不依赖 Cockpit Tools。桌面账号切换可选“同时应用到本机 CLI”，分别显示生效结果；SSH 保留各服务器自己的配置。
- **已有穿透 / 反向代理入口**：填写已有 HTTPS 根地址即可接入，支持自定义端口；网络连接方式默认展开，并提供固定入口检测和更清晰的配置引导。详见[配置说明](https://github.com/try2love/codex-mobile-bridge/blob/v2.1.0-preview.1/docs/external-tunnel.md)。
- **文件阅读**：源码增加高亮、行号、复制与自动换行；PDF 支持连续滑动、双指缩放、文字选择和全屏，电脑保留翻页与缩放按钮；自包含 HTML 可预览内联交互、动画并切换源码。

### 体验优化

- 根据实际会话类型显示筛选与日期上方的类型标签；对齐 Codex 与 Claude 列表高度，Claude 上下文使用可点击查看详情的圆环。
- 客户端状态在后台更新，保留已有列表；CLI 短暂断线保留草稿与 Skill 选择。
- 设置中提供“SSH 连接诊断”，支持复制摘要和导出脱敏报告；顶部同一 SSH 故障可关闭。
- Claude 额度改为主动点击查询，显示上次成功查询时间，查询失败保留原结果。
- 文件描述与下载、复制、全屏操作并排；目录返回先回上一级，全屏返回先退出预览。PDF 切换标签与全屏时保留阅读位置。

### Bug 修复

- 修复 Windows Codex 保存历史与冷恢复中的长路径识别问题，以及已退出的旧版 Claude Code 会话被误判占用的问题；改善 Windows UTF-8 读取与 Linux 进程识别。
- 修复 CLI 后台读取短暂超时导致有效连接丢失的问题，改善 Goal 运行后的连接恢复。
- 修复固定 HTTPS 入口检测成功后仍显示上一次错误的问题；切换入口后清除旧结果。
- 减少 PDF 快速翻阅、缩放和邻页回看时的黑屏与闪烁，保留已渲染画面直到替换完成。

### 安装与使用

本次提供 macOS Apple Silicon / Intel、Windows x64、Linux x64 / ARM64、Android APK、iOS 未签名 IPA 和源码包。建议网关与手机 App 一起更新，浏览器用户更新网关后刷新页面；共享中继部署也需同步更新中继服务。Android 覆盖安装以保留数据，iOS 需自行签名。下载可用 `SHA256SUMS.txt` 校验。

CLI 需要执行端已安装并配置相应原生程序；不会接管其他终端仍在运行的 Claude 会话。PDF 在线预览上限为 **20 MiB**，同时受当前普通点击下载限额约束；源码与 HTML 预览支持 **512 KiB 以内 UTF-8 文本**，更大文件可下载打开。HTML 预览面向自包含文件，不开放外部网络与网关登录信息。

### English

This is the first v2.1.0 preview. Try the new features and share feedback through [Issues](https://github.com/try2love/codex-mobile-bridge/issues). v2.0.3 remains the stable release.

#### New features

- **Codex CLI chats:** discover and read saved CLI chats on mobile and Web, resume after the original terminal closes, and create chats in known local or SSH project folders. Follow later terminal-written replies and show each reply's recorded model.
- **CLI controls:** model/effort, Skills, default/plan modes, permissions, approvals and interrupt. Compatible local and SSH native Codex CLI runtimes support creating, pausing, editing, resuming and clearing Goals.
- **Local and SSH Claude Code:** read history, resume closed chats, create chats, send messages, select models and permissions, use Skills, upload files, handle approvals and interrupt, preserving the execution environment's configuration and directory.
- **Separate CLI account settings:** configure Codex and Claude independently, list upstream models and test connectivity without Cockpit Tools. Desktop account changes can optionally apply to local CLI, with separate results; SSH hosts keep their own configuration.
- **Existing tunnels / reverse proxies:** enter an existing HTTPS root URL, including a custom port. Connection options open by default, with fixed-entry checks and clearer guidance. See the [setup guide](https://github.com/try2love/codex-mobile-bridge/blob/v2.1.0-preview.1/docs/external-tunnel.md).
- **File reading:** syntax highlighting, line numbers, copying and wrapping for source; continuous scrolling, pinch zoom, text selection and fullscreen for PDFs, with page/zoom buttons on desktop; inline interactions and source view for self-contained HTML.

#### Improvements

- Show chat-type filters and labels above dates when relevant; align Codex/Claude lists and use a tappable context ring for Claude.
- Refresh client status in the background while retaining the list; preserve CLI drafts and selected Skills across brief disconnects.
- Add **SSH diagnostics** with copy/export and dismissible recurring SSH errors.
- Query Claude quota on demand, displaying the last successful query time and retaining results if a refresh fails.
- Place file actions beside the description, navigate Back through parent folders, exit fullscreen first and retain PDF reading position across tabs and fullscreen.

#### Fixes

- Fix Windows Codex extended paths in saved history and cold resume, and false occupied-state detection for exited legacy Claude Code sessions; improve Windows UTF-8 reading and Linux process identification.
- Retain valid CLI connections across brief history-read timeouts, including after Goal execution.
- Replace stale fixed-HTTPS errors after successful checks and clear old results when the entry changes.
- Reduce PDF flashes during rapid navigation and zoom by retaining rendered pages until replacements are ready.

#### Installation and usage

Packages cover Apple Silicon/Intel macOS, Windows x64, Linux x64/ARM64, Android APK, unsigned iOS IPA and iOS source. Update the gateway and mobile apps together; browser users reload after updating the gateway. Update shared-relay deployments as well. Install Android over the existing app to retain data; iOS requires your own signing. Verify downloads with `SHA256SUMS.txt`.

The execution host needs its native CLI installed and configured. Live Claude chats in another terminal are not taken over. PDF previews are capped at **20 MiB** and the ordinary-download limit; source/HTML previews accept **UTF-8 text up to 512 KiB**. Download larger files to open elsewhere. HTML previews are self-contained, without external network access or access to gateway sign-in data.

---

## v2.0.3 · 手机更新、第三方通知与界面优化

- **Android 应用内更新**：检查新版、查看更新说明，在 App 内下载 APK 并交给系统安装器。安装前校验大小和 SHA-256；完成下载后，即使离开 App 授予安装权限或重新打开 App，也可继续安装或保存 APK。
- **更新下载方式自己选**：可选「手机下载」或「电脑下载」。手机无法查询 GitHub 时可通过当前已登录网关查询；选择电脑下载后，由电脑下载并校验，再传给手机安装。下载失败可重新选择方式，或通过「GitHub ↗」在浏览器下载对应 APK，不会自动替换用户选定的下载方式。
- **手机与 Web 配置第三方通知**：从设置或聊天列表进入「第三方通知」，选择 **Bark（iPhone）、ntfy（Android）或 PushPlus（微信）**，分别配置、保存和测试；与电脑网关共用配置，也可修改通知跳转地址、标题显示及网关入口通知。该配置入口用于直连网关，共享中继保留原有权限范围。
- **搜索与筛选更紧凑**：搜索框始终可见，显示「搜索 / Search」；各选择框按当前内容分配宽度，其余空间留给搜索。精简英文按钮，改善手机横屏、平板和展开折叠屏上的 Claude、Codex、DSH 列表布局。
- **聊天列表减少干扰**：Claude 与 DSH 支持手动下拉刷新，保留搜索、筛选和阅读位置；移除常驻的读取与连接成功提示，恢复成功后清除过期错误，连接失败仍提供说明和重试入口。修复刷新失败提示持续停留的问题。
- **设置与工作台更清楚**：底部 App 图标开关、应用管理与第三方通知入口并列，兼顾中英文窄屏显示；文件传输保存按钮明确说明用途，修复未打开设置时遗留「请登录」提示的问题。关闭当前工具标签时切换到可用标签，避免不必要地退出分屏。
- **手机连接恢复**：Android/iOS 安全保存仍有效的扫码绑定凭据，WebView 登录状态丢失时尝试恢复；不保存网关密码，退出、移除电脑或撤销绑定后不再恢复。改善网关登录会话隔离、Claude 已初始化连接的重连入口，以及 macOS Claude 初始化时长 Console 输入的核验与连接效率。

### 致谢

感谢 [@SevenPwZ（des1re）](https://github.com/SevenPwZ) 在 [PR #32](https://github.com/try2love/codex-mobile-bridge/pull/32) 中贡献 Android 应用内更新。本版在该贡献基础上整合电脑协助下载、下载方式选择与安装恢复优化。

### 升级说明

建议电脑网关与 Android/iOS App 一起更新至 v2.0.3，以使用完整的通知配置、绑定恢复和更新改进。网页用户更新网关后重新加载页面即可。旧 Android 版本首次升级可下载 APK 覆盖安装，不要先卸载；后续更新可使用新版应用内更新。电脑协助下载需要配套网关，电脑仍须能访问 GitHub；仅更新手机时也可继续使用手机直连下载。iOS IPA 仍需自行签名，另附 Xcode 源码包；Linux 仍为实验性支持，使用安装包手动升级。正式下载文件可用 `SHA256SUMS.txt` 校验。

### English

- **Android in-app updates:** check releases, read notes, download the APK and open the system installer. Verify size and SHA-256 before installation. Keep a completed download available for installation or export after granting install permission or reopening the app.
- **Choose the download route:** download on the phone or let the signed-in computer gateway download, verify and transfer the APK. Release lookup can use the gateway if the phone cannot reach GitHub. Failed downloads offer route selection and a browser link to the matching APK; they do not silently switch the chosen route.
- **Third-party notifications on mobile and Web:** open **Push alerts**, select **Bark for iPhone, ntfy for Android or PushPlus for WeChat**, then configure, save and test that channel. Settings are shared with the desktop gateway, including notification links, title visibility and gateway address notifications. Configuration is available through direct gateway connections; shared-relay permissions are unchanged.
- **Compact search and filters:** keep Search visible, size selectors to their selected text and give the remaining width to search. Shorter English labels and consistent list layouts improve landscape phones, tablets and unfolded devices.
- **Quieter chat lists:** add manual pull-to-refresh to Claude and DSH, retain filters and reading position, remove persistent loading/connected messages and clear stale errors after recovery. Connection failures still show actionable messages and retry controls.
- **Clearer settings and workbench behavior:** place app-icon visibility, Apps and Push alerts together with narrow-screen English support. Clarify the file-transfer save action and prevent stale sign-in messages in unopened settings. Closing the active tool tab selects an available tab without unnecessarily leaving split view.
- **Connection recovery:** securely retain valid scanned-device credentials on Android/iOS and try to restore lost WebView sign-in state without storing the gateway password. Sign-out, removal and revocation stop recovery. Improve gateway session isolation, reconnection to initialized Claude sessions and long Console input verification during macOS Claude setup.

Thanks to [@SevenPwZ (des1re)](https://github.com/SevenPwZ) for Android in-app updates in [PR #32](https://github.com/try2love/codex-mobile-bridge/pull/32). This release builds on that contribution with gateway-assisted downloads, explicit download choices and installation recovery improvements.

Update both the gateway and mobile app for all improvements; reload browser pages after the gateway update. Older Android versions can install the APK over the existing app once, then use in-app updates. Computer-assisted downloads require a compatible gateway and computer access to GitHub. The unsigned iOS IPA requires your own signing; Linux remains experimental with manual package upgrades. Verify published downloads with `SHA256SUMS.txt`.

---

## v2.0.2 · 文件兼容与聊天列表体验优化

- **聊天列表更连贯**：Codex 列表滚动接近底部时自动加载后续聊天，移除常驻“加载更多”按钮。下拉刷新提示不再挤压列表，手势跟随与回弹更顺滑，成功提示更快收起；内容未变化时保留已有列表节点和阅读位置。
- **Windows 文件链接更可靠**：兼容盘符、反斜杠、file URL 和长路径前缀；同一文件的不同引用共用下载入口。继续使用现有工作区授权与可配置下载上限。
- **手机下载后直接打开**：Android 和 iOS 可通过系统支持的应用打开已下载文件，同时保留保存入口；系统区域颜色随当前页面变化。
- **跨平台登录自启**：Windows、macOS 和已安装的 Linux 桌面包可在高级设置中启用。Windows/macOS 登录后收起控制面板，macOS 可从菜单栏或 Dock 恢复；Linux 保持窗口可见，AppImage 使用原文件路径。
- 保留 v2.0.1 的 macOS 异常应用 plist 扫描修复。

感谢 [@heiqiu47](https://github.com/heiqiu47) 在 [PR #24](https://github.com/try2love/codex-mobile-bridge/pull/24) 中贡献的文件兼容、手机文件打开、系统栏与登录自启改进。本版按现有 main 的文件与认证边界整合，并扩展跨平台适配。

**升级**：建议网关与手机 App 一起更新至 v2.0.2，以使用完整改进。聊天列表优化由网关提供，升级后重新加载页面即可。Android 覆盖安装以保留已保存电脑与登录；iOS IPA 仍需自行签名，另附 Xcode 源码包。Linux 仍为实验性支持，请保留数据目录后手动安装。下载文件可用 `SHA256SUMS.txt` 校验。

### English

- Load more Codex chats automatically near the bottom, remove the persistent load-more button, and make pull-to-refresh smoother with a shorter success notice. Keep unchanged list nodes and reading position.
- Improve Windows drive paths, backslashes, file URLs and long-path aliases while retaining workspace authorization and configurable download limits.
- Open downloaded files through supported system apps on Android and iOS, retain Save, and match system-area colors to the current page.
- Add opt-in login startup for Windows, macOS and installed Linux desktop packages. Windows/macOS hide the control panel on login; Linux keeps it visible. macOS provides a menu-bar entry, and AppImage startup uses its original file path.
- Retain the v2.0.1 macOS malformed-plist discovery fix.

Thanks to [@heiqiu47](https://github.com/heiqiu47) for [PR #24](https://github.com/try2love/codex-mobile-bridge/pull/24). Update both the gateway and mobile app for all improvements. Install Android over the existing app; the unsigned iOS IPA requires your own signing. Linux remains experimental. Verify downloads with `SHA256SUMS.txt`.

---

## v2.0.1 · 修复 macOS 客户端扫描中断

- 修复 macOS 扫描应用时，某个应用的 `Info.plist` 存在 XML 格式错误，导致整轮客户端发现中断、已启用客户端无法继续自动接入的问题。
- 遇到这类异常应用时跳过该候选，继续发现其他客户端，并在网关日志中记录具体文件路径和解析原因，便于定位。
- 受影响的 macOS 用户建议更新电脑端网关；已有账号、会话与连接配置无需重新设置。

## v2.0.0 · 从手机聊天到多客户端远程工作台

v2.0.0 将 v1.4.0 的 Codex 手机网关扩展为 **Codex、Claude Desktop、DeepSeek Harness 的统一远程入口**。这次正式版汇总整个 v2 预览周期的工作台、手机 App、多客户端、网络连接和平台适配改进。任务继续在原电脑或对应 SSH 服务器运行，已有会话的提供商、认证与工作目录保持归属。

### 三个客户端，各自的会话与配置

- **自动发现与接入**：网关扫描本机已安装的 Codex、Claude Desktop 和 DeepSeek Harness。无需先启动网关，即可检查安装与登录状态、管理接入；后续安装客户端后可重新扫描。只有配置就绪的应用才能启用，网关启动后再连接已启用的应用。
- **独立聊天列表**：按应用切换聊天，创建会话时直接归属当前应用；Claude Code 与 Cowork 分组并可折叠。网页和手机共用会话状态标识、重连入口及可用能力提示。
- **受保护的启停**：桌面、Web 和手机可管理已配置客户端。正常退出前检查运行任务、等待授权与待发送内容；任务状态不明时保留明确的恢复指引。退出选项区分仅停用手机接入、正常退出与二次确认的强制退出。
- **恢复已有连接**：改善 Claude 断线重连、隐藏窗口恢复、中文路径与目录信任提示；核验并恢复旧 Harness 接入，处理插件更新和残留实例。发送或授权结果未知时，不自动重放操作。

### 聊天与项目工作台

- **文件标签**：浏览本机或 Codex SSH 会话的项目文件，预览文本、Markdown 和图片，上传文件，在聊天文件链接与目录之间定位。普通点击先选择定位或下载，默认下载阈值为 20 MiB，可从网关、Web 或手机即时调整。
- **大文件与目录下载**：文件列表长按、右键或键盘菜单可明确下载文件及目录，不受普通点击阈值限制；目录打包为 ZIP。下载浮窗显示进度、速度和总量，支持暂停、继续、取消；原生手机使用磁盘下载，浏览器仍受站点存储和设备空间限制。
- **图片缩放**：聊天图片、发送前附件和工作区图片共用查看器。手机双指缩放、放大后拖动；电脑滚轮缩放与拖动；双击切换适应屏幕与两倍，支持 1–8 倍及恢复适应。关闭图片后保留草稿、阅读位置和文件标签。
- **连续终端**：终端保留工作目录、环境和交互状态，支持多行命令、停止、清屏与尺寸变化。macOS/Linux 使用 PTY，Windows 使用 ConPTY，并补齐跨盘 `cd` 的处理；关闭刚创建的终端时也回收其所属进程树，避免后台残留。
- **Git 面板**：查看变更、差异、提交历史图和分支；暂存、提交、切换分支、本地合并与中止合并。保留工作区检查与确认，不提供远程推送或历史重写。
- **Codex 侧边聊天与子智能体**：在工作台查看子智能体历史，创建临时侧边聊天，独立选择模型、推理、Skill、权限和附件；支持排队、补充任务、普通/计划模式与同网关多设备同步。侧边聊天在结束或网关重启后清除，暂不支持 SSH。
- **宽屏分屏**：电脑浏览器、平板与折叠屏可将工具标签放到右侧并调整宽度；窄屏恢复单栏，保留标签、草稿和阅读位置。Claude/DSH 按自身协议提供工具能力，不把 Codex 专属功能伪装成全部客户端可用。
- **上下文与权限**：Codex、Claude Code 和 DSH 在权限附近展示上游提供的上下文用量，点击查看已用量、容量及说明；未提供的数据显示未知。权限按钮显示当前模式，模型与推理选项依照客户端能力。Codex 支持不选择项目创建会话。

### 账号、API、额度与模型

- **按客户端管理**：桌面网关管理 Codex、Claude、DSH 各自的已保存账号或支持的 API 接入，提供导入、修改、重命名和删除。Web/手机展示当前应用的账号信息，可切换已保存接入；凭据的新增与编辑仍留在电脑端。
- **当前接入更清晰**：当前账号置顶并标记，额度和重置卡紧邻账号；切换前核验任务，必要时重启对应客户端，并保留恢复信息。
- **API 使用自己的模型**：Codex 模型选择器与账号页共用当前 API 上游目录，支持主动刷新及手动填写；修复切换 API 后仍用官方地址或旧 GPT 列表的问题，兼容 CC Switch 模型目录。能列出模型不代表第三方协议一定兼容。
- **查询节奏可控**：进入账号页自动查询，可见时每五分钟更新，手动查询立即执行并重新计时；保留上次成功值和时间，失败不把未知余额显示成零。
- **官方额度与重置卡**：查看官方账号额度、恢复时间及服务返回的订阅周期；可为任一已保存的 Codex 官方账号使用重置卡，无需先切换接入。确认页核对账号与到期时间，等待五秒后才可消费，结果不明时复用原请求标识。
- **客户端能力有别**：Claude 支持已登录配置及第三方网关配置，DSH 支持保存当前账号、管理官方 API Key 与查询可用余额，任意第三方提供商需在 Harness 配置；模型、推理和额度以客户端/上游实际返回为准。Codex Fast、重置卡及 Skills 不扩展为其他客户端的通用能力。

### Android、iOS 与手机网页

- **多台电脑与扫码保持登录**：App 保存多台电脑，支持重命名、删除、按最近访问排序和手动刷新在线状态。扫码建立可撤销的可信设备绑定，普通网页登录过期不再迫使手机反复输入密码。
- **更紧凑的聊天列表**：搜索、排序与归档入口收拢；功能栏位于应用切换栏上方。只有一个已启用应用时隐藏切换栏；更多菜单可隐藏底部图标。左右滑动切换应用默认开启，带拖动预览，也可关闭。
- **电脑卡片上的应用通知**：显示已启用应用的原始图标，网关按用户通知选择维护各应用未读数；点击图标直接进入该应用列表。列表加载成功后标记已读，其他应用及新到达提醒保留；已读不等于批准权限请求。
- **通知权限与来源**：首次打开申请通知权限，允许后默认使用横幅和声音，保留用户后续选择。Android 大图标与 iOS 图片附件标明来源应用；iOS 系统小图标仍属于 Bridge，不能逐条替换成其他 App。
- **原生交互修复**：修复 Android 应用图标跳转地址校验、重复更多菜单、输入换行、剪贴板、附件和返回导航等问题；iOS 与 Android 共用页面宿主适配，同时保留各自原生扫码、下载与系统返回逻辑。

### 远程连接与网关设置

- **固定 HTTPS · Tailscale**：检测官方客户端、读取固定 `.ts.net` 地址，支持公网 Funnel 与私有 Serve，连接随网关启停。Funnel 下手机无需安装 Tailscale；Serve 需要同一私有网络权限。首次登录、网络权限及 Funnel 授权由用户在官方界面完成。
- **自有服务器 + SSH**：保留普通用户 SSH 反向转发、主机指纹核验及固定入口检测。手机访问地址可留空并使用服务器公网 IP 的 HTTPS 地址，但服务器仍需已有 HTTPS 反代和与 IP 匹配的受信任证书；不会自动降级 HTTP。
- **保留多种入口**：局域网、内置 Cloudflare 临时 HTTPS、固定 Cloudflare Tunnel、Tailscale、自有服务器与 NAS/已有反代可按需配置。移除桌面端“自建中继 · 内测”邀请码入口，减少普通用户配置负担。
- **网络提示更准确**：Cloudflare 和 Tailscale 公网转发/中继可能经过境外节点，中国大陆直连可能较慢或超时；固定域名不保证国内速度。首次配置后应在实际 Wi-Fi 与蜂窝网络验证。
- **设置重新分区**：概览、客户端与接入、网络访问、登录与设备、消息通知、应用与维护各司其职；概览和客户端页均可启停网关。修复网关实际已启动却被界面误报超时的情况；文件传输设置独立保存，异步加载不再误报网关配置未保存并阻止启动。

### 安全、效率与平台维护

- **请求与文件边界**：加固 DSH 带认证请求的重定向限制；限制中继压缩请求，避免解压后体积绕过；保留登录、CSRF、同源图片入口和受控文件访问。Windows 补齐 junction/reparse point、`.git` 大小写及 Win32 路径别名检查。
- **减少重复工作**：同轮状态刷新共用进程扫描，相同只读请求合并；发送、授权、停止保持独立。后台暂停终端读取并保留 shell，回前台按游标补读；手机通知使用增量游标，离开前台停止巡检。
- **Windows 与 Ubuntu 实测分支整合**：吸纳 Windows 安装发现、进程退出、终端和路径边界调整；改进 Ubuntu 启动器识别、权限能力查询与侧边聊天权限确认。Linux 仍为实验性支持，Claude 自动连接仅限 macOS/Windows。
- **更清晰的源码结构**：共享业务、客户端协议、操作系统实现分别归入 `features`、`clients`、`platforms`；Web 宿主与宽窄布局独立，Android/iOS 保留各自原生入口，共用注入脚本只有一份。便于在目标系统上定向维护。
- **保留 v1.4.0 的基础能力**：登录保护、会话归属、原生 Fast、模型/Skill 独立加载、已安装 Skill 链接、图片路径规范化、临时隧道重试和签名更新检查继续保留。

### 安装、升级与能力边界

- **电脑端**：提供 macOS arm64/x64 DMG 与 ZIP、Windows x64 Setup 与 ZIP，以及实验性 Ubuntu x64/ARM64 DEB 与 AppImage。macOS 使用 ad-hoc 完整性签名、未公证；Windows 无发布者证书签名。没有 Windows ARM64 专用包。
- **升级网关**：macOS/Windows 可在“应用更新”查看正式版，或停止旧网关并退出后手动安装；保留原数据目录。Linux 手动升级。不要让两个网关同时操作同一套客户端与数据。
- **Android**：覆盖安装 APK 以保留电脑列表和登录，不要先卸载。**iOS IPA 未签名**，需自行签名；源码包包含 Xcode 工程，暂无 App Store/TestFlight 分发。使用 `SHA256SUMS.txt` 校验下载。
- **Claude**：接入 Code/Cowork，不包含普通 Chat。首次初始化或丢失连接后的重新初始化可能需要电脑解锁、辅助功能/开发者模式及目录信任确认；已有连接优先在后台复用。不能承诺从锁屏状态完成冷启动。Cowork 未提供可靠上下文用量时显示未知。
- **通知与在线状态**：普通手机包依靠前台同步，不保证后台、锁屏或被系统终止后的送达；可使用 Bark、ntfy、PushPlus。灵动岛在后台可能显示最后状态。电脑睡眠或离线时无法继续远程操作。
- **原版 Codex 更新**：Bridge 可查询支持渠道的版本并引导原生更新器/商店；安装资格与确认仍由原应用处理，不承诺完全无人值守升级。此入口与 Bridge 自身更新独立。
- 客户端内部接口可能随上游更新变化。平台打包、模拟协议检查与真实账号/模型执行是不同验证范围；没有设备耗电测量，不宣称具体续航提升。

### 致谢

感谢参与跨平台真机测试、反馈和贡献的用户。保留 [@qybgh](https://github.com/qybgh) 在 [#15](https://github.com/try2love/codex-mobile-bridge/pull/15) 中的更新交接贡献、[@702165405](https://github.com/702165405) 在 [#16](https://github.com/try2love/codex-mobile-bridge/pull/16) 中的模型目录兼容贡献，以及 MIT 项目 [coding-mobile](https://github.com/2389859005/coding-mobile) 对 Claude/Harness 接入的参考；相关许可与归属随源码保留。

---

### English · v2.0.0

v2.0.0 expands the v1.4.0 Codex mobile gateway into **one remote entry for Codex, Claude Desktop and DeepSeek Harness**. It brings the v2 preview workbench, mobile apps, client management, networking and platform improvements into the stable release. Tasks continue on their original computer or SSH host with their session ownership, provider, authentication and working directory preserved.

#### Clients and sessions

- Discover installed clients, inspect login/setup readiness and manage connections before starting the gateway. Rescan after installing a client. Starting the gateway connects enabled, configured applications.
- Keep separate application chat lists; create chats under the selected client. Claude Code and Cowork have collapsible groups. Share activity indicators, reconnect controls and capability-aware messages across Web and mobile.
- Manage configured clients from desktop, Web and mobile. Check running tasks, approvals and queued input before quitting. Distinguish disconnecting mobile access, a normal quit and an explicitly confirmed force quit.
- Improve Claude reconnection, hidden-window recovery, Unicode paths and trust guidance. Verify and restore existing Harness integrations, including plugin updates and stale processes. Never automatically replay an operation whose outcome is unknown.

#### Chat and workbench

- Browse project files, preview text/Markdown/images, upload and locate chat-linked files in their folder. Ordinary downloads use a live configurable threshold, 20 MiB by default; larger links offer location first.
- Explicit long-press/right-click downloads allow files and ZIP folders above that threshold. A floating panel shows progress, speed and size with pause, resume and cancel. Native apps stream to disk; browser downloads remain subject to browser storage and device space.
- Zoom chat images, pending attachments and workspace images with pinch, wheel, drag or double-click. Scale from fit-to-screen to 8× and retain drafts, reading position and tabs when closing.
- Use persistent terminals with interactive shell state, multi-line input and resizing. macOS/Linux use PTY; Windows uses ConPTY with cross-drive `cd` handling; closing a newly created terminal also reaps its owned process tree.
- Review Git changes, diffs, commit graphs and branches; stage, commit, switch and locally merge branches. Dirty-worktree checks remain; remote push and history rewriting are not exposed.
- Codex temporary side chats support independent model/reasoning/Skills/permissions, attachments, queued input and Default/Plan modes. View saved subagent history. Side chats are cleared on end or gateway restart and do not support SSH.
- Move tool tabs into resizable split panes on wide browsers, tablets and unfolded phones. Smaller layouts retain drafts, tabs and reading position. Claude/DSH expose capabilities their protocols actually support, rather than claiming all Codex-specific tools.
- Show upstream context usage beside permissions for Codex, Claude Code and DSH, with a detail popover; unavailable usage remains unknown. Show current permission mode and supported model/reasoning choices. Create a Codex chat without selecting a project.

#### Accounts, models and usage

- Desktop manages each client's saved accounts or supported API connections, including import, editing, renaming and removal. Web/mobile show the selected client's information and can switch saved connections; credential creation/editing stays on desktop.
- Keep the active connection first with quota/reset credits beside the account. Verify tasks before switching, restart the appropriate client when needed and retain recovery information.
- Codex's model picker follows the active API provider. Refresh the upstream catalog or enter models manually; fix stale official API routing and GPT lists after switching, and retain CC Switch catalog compatibility. Listing a model does not prove third-party protocol compatibility.
- Fetch account data on opening, then every five minutes while visible; manual refresh runs immediately and resets the timer. Preserve successful values/timestamps on errors instead of showing unknown balances as zero.
- Use reset credits for any saved Codex official account without switching it active. Review the account/expiry and wait five seconds before confirmation; reuse the operation ID after an uncertain result. Subscription dates and balances reflect available upstream data.
- Claude supports saved login and third-party gateway configurations; DSH supports saved sign-ins, official API keys and queryable balances; arbitrary third-party providers must be configured in Harness. Quota, model and reasoning availability vary. Codex Fast, reset credits and Skills are not advertised as universal client features.

#### Mobile experience and notifications

- Save multiple computers, rename/remove them, sort by recent access and refresh reachability. QR pairing creates a revocable trusted-device binding that outlives ordinary browser login expiry.
- Compact search/sort/archive controls and application navigation. Hide the app bar when only one client is enabled, or collapse it from More. Swipe switching is enabled by default, has a visual drag preview and can be disabled.
- Computer cards show enabled app icons and gateway-maintained unread counts filtered by notification preferences. Open an application's list directly from its icon; acknowledge loaded notifications without clearing other apps, newly arriving events or pending approvals.
- Request notification permission on first launch and default to banners/sound after approval, while retaining user preferences. Android large icons and iOS attachments identify the source app; iOS retains Bridge's system app icon.
- Fix Android app-icon navigation URL validation, duplicate native menus, newlines, clipboard actions, attachments and back navigation. iOS/Android share host adaptation while retaining native scanning, downloads and navigation.

#### Networking and desktop organization

- **Tailscale HTTPS:** discover the official client and fixed `.ts.net` address, select public Funnel or private Serve, and run the entry with the gateway. Public Funnel needs no phone-side Tailscale installation; Serve requires authorized tailnet access. Login, system permissions and first authorization remain with Tailscale.
- **Own server + SSH:** retain ordinary-user reverse forwarding, host-key checks and endpoint verification. A blank phone address may use the server's public IP over HTTPS, provided the server already has HTTPS reverse proxying and a trusted certificate for that IP. No automatic HTTP downgrade.
- Keep LAN, bundled Cloudflare temporary HTTPS, fixed Cloudflare Tunnel, Tailscale, SSH servers and existing NAS/reverse proxies. Remove the invitation-based experimental relay entry from the desktop UI.
- Explain that Cloudflare and Tailscale public relay paths may cross overseas nodes and may be slow or time out in mainland China. A fixed hostname is not a performance guarantee; test actual Wi-Fi and cellular networks.
- Organize the gateway into Overview, Clients and access, Network access, Login and devices, Notifications, and App and maintenance. Both Overview and Clients control gateway start/stop. Fix false startup timeout reporting when the gateway is already reachable. Independently saved transfer settings no longer create a false gateway draft during asynchronous loading and block startup.

#### Security, efficiency and platforms

- Prevent authenticated DSH requests from following redirects; reject compressed relay requests that could bypass body limits. Retain authentication, CSRF, same-origin image and workspace boundaries. Harden Windows junction/reparse, `.git` case and Win32 alias handling.
- Reuse one process inventory per status refresh and merge identical concurrent read-only requests. Writes remain independent. Pause hidden terminal reads while keeping the shell and catch up by cursor on return. Use incremental mobile notification cursors and stop foreground polling in the background.
- Integrate Windows and Ubuntu validation branches covering discovery, shutdown, terminals and path safety; improve Ubuntu launchers and permission capability/confirmation handling. Linux remains experimental; Claude automatic connection supports macOS/Windows only.
- Separate shared business features, client protocols and OS integration, with independent Web host/layout modules and shared mobile injection sources. Preserve v1.4.0's login protection, native Fast, independent model/Skill loading, installed Skill links, image path normalization, tunnel retries and signed-update checks.

#### Downloads, upgrades and limits

- Desktop targets: macOS arm64/x64 DMG/ZIP, Windows x64 Setup/ZIP, experimental Ubuntu x64/ARM64 DEB/AppImage. Mac builds are ad-hoc signed, not notarized; Windows builds have no publisher certificate. No Windows ARM64 package.
- Update Bridge from the macOS/Windows app or stop the old gateway and manually install, retaining the data directory. Linux updates are manual. Avoid simultaneous gateways sharing client state.
- Install Android over the existing app to retain data. **The iOS IPA is unsigned** and needs your own signing; the source ZIP includes the Xcode project. No App Store/TestFlight distribution. Verify downloads with `SHA256SUMS.txt`.
- Claude integrates Code/Cowork, not regular Chat. Cold initialization or recovery may require an unlocked desktop, accessibility/developer permissions and directory trust. Existing connections are reused in the background; cold startup while locked is not guaranteed. Cowork context usage remains unknown where unsupported.
- Standard mobile builds synchronize in the foreground and do not guarantee background/locked/killed-app delivery. Use Bark, ntfy or PushPlus as needed; Live Activities may show the last state. Sleeping/offline computers cannot continue remote interaction.
- Original Codex update checks and handoff to its updater/store are separate from Bridge updates. Native eligibility and installation confirmation still apply; fully unattended Codex upgrades are not promised.
- Internal client APIs can change. Packaging checks, simulated protocol tests and real account/model execution are distinct. No battery-life gains are claimed without device power measurements.

Thanks to contributors and cross-platform testers, including [@qybgh](https://github.com/qybgh) ([#15](https://github.com/try2love/codex-mobile-bridge/pull/15)), [@702165405](https://github.com/702165405) ([#16](https://github.com/try2love/codex-mobile-bridge/pull/16)), and the MIT-licensed [coding-mobile](https://github.com/2389859005/coding-mobile) reference implementation. Attribution and licenses are retained.

---

## v2.0.0-preview.3 · 账号、模型与移动端体验改进

- **账号与重置卡**：整理“账号与接入”布局，当前接入置顶，刷新时保留上次成功的额度与更新时间。每个已保存的官方账号均可手动使用重置卡，无需先切换登录，也不依赖 Codex 的代理用量重置开关；确认页展示账号和卡片到期时间，等待五秒后才可使用，结果不明时重试同一请求。
- **模型与旧聊天**：模型选择器与账号页共用当前接入的模型来源，可主动刷新上游列表；官方模型的推理选项按能力匹配。切换接入后，兼容的旧聊天可继续使用新接入，修复旧供应商检查误拦发送的问题。回复旁展示该轮模型与推理强度，缺少记录的历史回复不补猜标签。
- **更轻的账号切换**：切换时只更新认证与接入配置，不再批量加载旧聊天或访问其项目目录；取消网关启动时的文稿目录探测。主动打开文件树、预览或下载附件等文件功能仍可能需要对应的系统权限。
- **手机连接与附件**：电脑列表支持重命名、移除连接及入口在线状态；聊天顶部可在保存的连接名称与真实设备名称之间切换。改进扫码后的登录保持，修复手机选完文件后附件名称、预览及上传状态缺失的问题，Android 与 iOS 同步相关界面和功能。
- **更紧凑的界面**：调整聊天列表顶部及底部按钮布局，将账号页刷新按钮放在标题旁；完善中英文操作提示、网关默认语言和手机弹窗样式。共享中继入口暂时隐藏。
- **桌面程序与更新**：新增桌面程序扫描，macOS 支持直接填写 `.app` 路径。“在电脑上检查 Codex 更新”打开原版 Codex 的“检查更新 / Check for Updates…”菜单，后续检查、下载与安装由 Codex 自身处理；网关仍使用独立的更新入口。

### 已知限制

- 订阅日期接口可能受限或无法返回日期，此时显示暂未确认，不据此判断订阅过期。真实账号查询仍存在被上游拒绝的情况。
- 已知 GPT-Load v2.0.0-rc.45 的 Antigravity → Gemini 协议转换路径可能返回 `422 protocol_conversion_unsupported`；能列出模型不代表该模型已能正常对话。
- 旧 API 聊天切回官方接入后，部分聊天可能暂不显示 Fast 开关。原版 Codex 的实际退出、启动和更新菜单、macOS 系统权限弹窗及手机真机仍需进一步验证；隔离运行时与模拟上游测试不等于这些场景已经实测通过。

这是主动选择体验的 **Pre-release**，不设为 Latest，不向 v1.4.0 正式版用户推送。Android 请覆盖安装以保留数据；**iOS IPA 未签名**，需自行签名，源码包提供 Xcode 工程，暂无 App Store / TestFlight 分发。使用 `SHA256SUMS.txt` 校验下载文件。

### English

- **Accounts and usage resets:** Improve the Accounts and access layout, keep the active connection first, and retain the last successful usage reading while refreshing. Use reset credits for any saved ChatGPT account without switching sign-in or enabling Codex's agent usage-reset setting. Review the account and credit expiry, wait five seconds to confirm, and retry the same request if its outcome is unknown.
- **Models and existing chats:** The model picker and account panel share the active connection's model source and support refreshing the upstream list. Official models show supported reasoning levels. Compatible existing chats can continue through the new connection after switching, without the stale-provider warning blocking messages. Replies show the model and reasoning level recorded for that turn; missing historical metadata is left unset.
- **Lighter account switching:** Switching updates authentication and connection configuration without loading all old chats or accessing their project folders. Remove the Documents-folder probe at gateway startup. Explicit file browsing and attachment previews or downloads may still require system file permissions.
- **Mobile connections and attachments:** Rename or remove saved computers, see connection availability, and toggle the chat header between the saved name and device name. Improve sign-in persistence after QR pairing and fix missing attachment names, previews and upload status after file selection. Android and iOS share these interface and feature updates.
- **Interface refinements:** Tighten chat-list controls, place account refresh beside the heading, and improve English/Chinese operation messages, default language selection and mobile dialogs. The shared-relay entry is temporarily hidden.
- **Desktop discovery and updates:** Scan for the desktop application and accept macOS `.app` paths directly. “Check for Codex updates on the computer” opens the original Codex app's “Check for Updates…” menu; Codex handles checking, downloading and installation. Gateway updates remain separate.

### Known limitations

- Subscription-date queries may be blocked or return no date. The interface reports an unconfirmed date rather than treating the subscription as expired; real-account queries can still be rejected by the upstream service.
- The Antigravity-to-Gemini conversion path in GPT-Load v2.0.0-rc.45 may return `422 protocol_conversion_unsupported`. A model appearing in the list does not establish that conversation requests work.
- Some former API chats may hide the Fast toggle after switching back to official sign-in. Original Codex app quit/launch and update-menu behavior, macOS permission dialogs and physical mobile devices still need further validation. Isolated runtime and mock-upstream checks do not establish that those scenarios have passed.

This is an opt-in **Pre-release**, not Latest, and is excluded from stable v1.4.0 updates. Install Android over the existing app to retain data. The **iOS IPA is unsigned** and requires your own signing; the source ZIP includes the Xcode project. There is no App Store or TestFlight distribution. Verify downloads with `SHA256SUMS.txt`.

---

## v2.0.0-preview.2 · 更新与模型兼容改进

- 优化应用更新流程，修复更新时可能卡住的问题。
- 改善第三方 API 模型列表兼容性，支持 CC Switch 模型目录。
- 保留现有 Android、iOS 和 Web 界面及操作方式，三端共用模型兼容改进。

### 感谢贡献者

- 感谢 [@qybgh](https://github.com/qybgh) 在 [#15](https://github.com/try2love/codex-mobile-bridge/pull/15) 中贡献的更新交接修复与测试，本版在确认更新助手就绪后执行交接。
- 感谢 [@702165405](https://github.com/702165405) 在 [#16](https://github.com/try2love/codex-mobile-bridge/pull/16) 中贡献的模型目录兼容改进。本次接纳该共用补丁，独立原生 Android 客户端未纳入本版。

这是主动选择体验的预发布版本，不设为 Latest；v1.4.0 正式版用户的更新通道保持不变。Android 可覆盖安装并保留数据；iOS 未签名 IPA 仍需自行签名，源码包提供 Xcode 工程。使用 SHA256SUMS.txt 校验下载文件。

### English

- Improve app update handoff reliability and third-party API model catalog compatibility, including CC Switch.
- Keep the existing Android, iOS and Web experience; all clients share the model catalog improvement.
- Thanks to [@qybgh](https://github.com/qybgh) for the update handoff work in [#15](https://github.com/try2love/codex-mobile-bridge/pull/15), and [@702165405](https://github.com/702165405) for the shared model catalog contribution in [#16](https://github.com/try2love/codex-mobile-bridge/pull/16). The standalone native Android client is not included.

This is an opt-in prerelease, not Latest. Stable v1.4.0 users remain on the stable channel. Install the Android APK over the previous preview; the unsigned iOS IPA requires your own signing.

---

## v2.0.0-preview.1 · 网页与手机远程工作台

这是主动选择体验的 **Pre-release**，不会设为 Latest，也不会向 v1.4.0 正式版用户推送更新。正式版继续保留。请先停止旧网关再试用预览，保留配置备份；体验后可重新安装正式版。

- **工作台标签**：在聊天旁浏览和传输文件，预览文本与图片，按类型区分图标，选择是否显示缩略图。
- **连续终端**：保留目录和 shell 状态，支持交互、停止与尺寸适配；手机可编写多行后再发送。
- **Git 面板**：变更与提交历史图、差异、暂存、提交、分支切换与本地合并。暂不提供远程推送或历史重写。
- **临时侧边聊天**：继承创建时的上下文，独立设置模型、思考程度、Skill 与权限，支持附件、排队和补充任务；普通／计划模式，同一网关的多设备可同步。结束或重启网关后清除，不会显示为 Codex 原生侧边标签；暂不支持 SSH 侧边聊天。
- **宽屏分屏**：拖动工具标签到右侧，调整两栏宽度。手机与窄屏保持单栏，输入区更紧凑。
- **Android 与 iOS App**：扫码保存多台电脑、直接复制、文件预览与下载、清空通知、设置中检查新版。安卓电脑列表双击返回退出，应用图标与项目 Logo 一致。

### 下载与更新

电脑端提供 macOS arm64/x64、Windows x64 和实验性 Ubuntu x64/ARM64 包。先安装本版网关，Web 打开其地址，手机 App 扫码连接。移动端使用预览通道，只有用户主动检查并选择后才打开下载或安装指引。

- **Android APK**：下载后覆盖安装，不要先卸载，以保留已保存电脑与登录状态。与本地 preview.10 使用同一签名。
- **iOS 未签名 IPA**：必须使用自己的 Apple 账号签名后安装；不能直接点开安装。`iOS-source.zip` 提供 Xcode 工程，可连接手机后签名运行。免费开发签名可能需要定期重新安装；尚无 App Store / TestFlight 发布。
- **通知**：App 前台可提醒新任务；不保证后台或锁屏送达。Bark、ntfy、PushPlus 仍可作为外部通知通道。灵动岛在后台可能显示旧状态。

使用 `SHA256SUMS.txt` 校验产物。macOS 为 ad-hoc 签名、未公证；Windows 无证书签名。预览功能仍可能存在兼容性问题。

### English

**An opt-in Web and mobile workbench preview.** This pre-release is not Latest and is excluded from stable v1.4.0 update checks.

Open project files, a continuous terminal, Git changes/history/branches, temporary side chats and saved subagent history in tabs. Wide browsers support resizable split panes. Side chats share state across clients and support models, reasoning, Skills, permissions, attachments, queued messages and Default/Plan modes. They are temporary Bridge chats, not native desktop side tabs; ending them or restarting the gateway clears them. SSH side chats are not supported.

Install the preview desktop gateway for the Web workbench. Android and iOS apps save multiple computers, copy directly, preview/download attachments and check for new releases from settings. Android can update by downloading the APK and installing over the existing App. The unsigned iOS IPA requires your own signing; the source ZIP includes the Xcode project. There is no App Store or TestFlight distribution yet.

**Notifications work primarily while the App is active; background/lock-screen delivery is not guaranteed.** Use external Bark, ntfy or PushPlus where needed. Live Activities may show an older state in the background.

Stop the old gateway before trying another build on the same port, and retain a configuration backup. Stable downloads remain available. Verify assets with `SHA256SUMS.txt`.

---

## v1.4.0 · 连接更简单，远程使用更顺畅

- **开箱即用的外网连接**：App 内置 cloudflared，临时 HTTPS 无需额外安装；完善固定 Cloudflare 域名、普通用户 SSH 和 NAS / 已有反代方案。
- **更清晰的配置体验**：每种连接均可折叠并直接打开对应在线指引；地址框自动补入 HTTPS 前缀，错误字段自动展开定位，短期通知显示 5 秒。保存的凭据可遮罩查看，SSH 私钥选择器支持隐藏目录。
- **更完整的登录保护**：支持记住登录 7 天、密码尝试次数提示、IP 封禁及电脑端解除，可选发送安全通知。
- **模型和 Skill 更易用**：保留原生 Fast 和自定义 API 模型目录，模型与 Skill 独立加载；Skill 支持缓存、搜索分页和完整描述，兼容正常安装的符号链接。
- **聊天与更新更稳定**：改善图片预览、临时隧道重试和更新交接；修复过期接入记录误拦发送。App 和网页均可忽略未确认发送提示，不会自动重发消息。
- **更直观的教程**：临时 HTTPS 标注“推荐”，固定域名、服务器和 NAS 标注“进阶”，提供分步图示及参考命令的适用条件说明。服务器管理员操作由用户自行完成，App 不执行 sudo。

合并 PR #11，并包含 Preview 测试期间的兼容性与体验修复。

**升级**：在正式版 App 的“应用更新”中检查更新，或先停止网关并退出，再安装对应平台的新包；保留原数据目录。Preview 用户请手动安装正式版并确认使用原数据目录。更新后重新加载手机页面。临时 HTTPS 地址可能因重启变化，请提前开启入口变化通知。

**English**

**v1.4.0 makes remote setup and everyday access easier.** The App bundles cloudflared and provides guided Cloudflare domains, regular-user SSH and NAS/reverse-proxy setups. Connection cards collapse, link directly to online guides, and expand when a field needs attention. Saved credentials remain masked and revealable.

Remember sign-in for seven days, see remaining password attempts, and manage blocked IPs from the desktop. Native Fast and provider-specific model discovery are preserved. Skills load independently with caching, search, pagination, full descriptions and support for installed symlinks. Image previews, tunnel retries and update handoff improve. Stale account records no longer block existing chats; both interfaces can dismiss unconfirmed send records without resending messages.

Includes PR #11 and fixes validated during Preview testing. Update through the stable App or stop the gateway and install manually, retaining the data directory. Preview users should install the stable App manually and select their existing data directory. Reload the phone page after upgrading. Mac packages are ad-hoc signed and not notarized; Windows packages have no certificate signature. Linux remains experimental.

---

## v1.3.3 · 能耗与通知优化

- **按需监控会话**：持续监听运行中和等待处理的会话，结束后解除通知订阅；通过实时事件和每 30 秒的变更元数据检查发现新任务，减少反复读取历史会话。
- **通知重试更独立**：完成通知发送失败时保留重试记录，不必持续订阅已结束会话。补齐会话再次运行、断线恢复、通知开关变化及完成状态先后到达的处理。
- **优化能耗**：状态查询复用进程；窗口隐藏时暂停刷新，状态不变时不重绘界面。
- **稳定窗口标题**：统一管理原生窗口标题，避免页面标题反复覆盖；切换语言时按需更新。

Mac / Windows v1.1.0 及后续版本可在“应用更新”选择“更新并重启”。保留数据目录即可保留登录、网络和通知设置。通过临时 HTTPS 远程升级前，请确认 v1.3.2 引入的“网关启动与入口通知”已开启且手机能够收到。Linux 仍为实验性支持，请停止网关并退出 App 后手动安装新版。

提供 Mac Apple Silicon / Intel 的 DMG、ZIP，Windows x64 的 Setup.exe、ZIP，以及 Ubuntu x64 / ARM64 的 DEB、AppImage。使用 `SHA256SUMS.txt` 校验下载；Mac / Windows 应用内更新使用签名的 `bridge-update.json`。Mac 仍为 ad-hoc 签名、未公证；Windows 无证书签名。

---

## English

**v1.3.3 reduces notification monitoring and desktop refresh overhead.**

- Monitor running chats and pending requests, then release notification subscriptions when idle. Live events and a metadata check every 30 seconds discover new activity without repeatedly loading historical chats.
- Retry failed completion alerts independently of subscriptions. Handle resumed chats, reconnection, notification preference changes, and completion updates arriving in separate steps.
- Reuse the desktop status worker, pause refreshes while the window is hidden, and skip unchanged UI renders.
- Keep the native window title stable and update it only when the language changes.

**Upgrade:** Mac / Windows v1.1.0 and later support App updates → Update and restart. Retain the data directory to preserve settings. Before a remote update over temporary HTTPS, enable and test the startup and entry notifications introduced in v1.3.2. Linux remains experimental and requires a manual upgrade after stopping the gateway and quitting the App.

Packages include Mac arm64/x64 DMG and ZIP, Windows x64 installer and ZIP, and Ubuntu x64/ARM64 DEB and AppImage. Verify downloads with `SHA256SUMS.txt`; Mac/Windows in-app updates use the signed manifest. Mac builds are ad-hoc signed, not notarized; Windows builds have no certificate signature.
