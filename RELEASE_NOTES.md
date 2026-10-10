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
