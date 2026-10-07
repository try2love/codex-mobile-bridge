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
