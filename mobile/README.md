# Android 与 iOS App

手机扫描网关二维码后连接 Codex、Claude Desktop 和 DeepSeek Harness，电脑继续执行任务。手机 App 保存多台电脑，提供原生扫码、剪贴板、文件下载和通知；浏览器入口同时保留。

**[v2.2.1 发布页](https://github.com/try2love/codex-mobile-bridge/releases/tag/v2.2.1)** · [完整更新说明](../RELEASE_NOTES.md) · [桌面客户端接入](../docs/deepseek-harness.md)

## v2.2.1 安装 / Installation

**Android 安装说明：** 新版 **Bridge** 与旧版 v2.2.0 App 独立安装，不能直接覆盖旧版。请先安装新版、重新添加电脑并确认连接正常，保留好旧版中需要的草稿后，再卸载旧版。旧版绑定和草稿不会自动迁移。已安装 v2.2.1-preview.1 独立 **Bridge Preview** 的用户可直接覆盖升级，保留该预览 App 的连接和草稿。

**Android installation:** The new **Bridge** app installs alongside the old v2.2.0 app and cannot replace it in place. Install Bridge, add your computers and verify the connections first. Preserve any drafts you need from the old app before uninstalling it; old bindings and drafts do not migrate automatically. Users of the separate v2.2.1-preview.1 **Bridge Preview** app can upgrade in place and retain that preview app’s connections and drafts.

iOS 仍需自行签名；覆盖安装时使用原应用标识和签名身份。 / iOS requires your own signing; retain the existing bundle identifier and signing identity for in-place upgrades.

## v2.1.7：通知与聊天恢复 / Notifications and chat recovery

网关与手机版本为 **2.1.7**，Android/iOS 构建号 **40**。新增 Android 可选后台任务通知与按连接选择通知来源，统一两端主题，改善 iOS 前台提醒、通知返回聊天和附件选择器恢复。共享页面提供统一会话菜单、项目引用、历史图片与代码高亮。功能和关联 Issue/PR 见 [完整更新说明](../RELEASE_NOTES.md#v217)。

Android 后台接收默认关闭，启用后有常驻通知，仍受系统权限与省电策略影响。iOS 未新增 APNs 后台推送。覆盖安装应保留原签名、应用标识和数据；请勿先卸载或清空数据。

**English:** v2.1.7/build 40 adds optional Android background reception and per-connection notification choices, consistent Android/iOS themes, improved iOS foreground reminders, notification navigation and attachment picker recovery. Shared pages add session organization, project references, history image previews and code highlighting. Android background reception is opt-in and subject to OS policies; no new iOS APNs background push is included. Preserve matching identifiers, signing and app data when upgrading. See the [release notes and credits](../RELEASE_NOTES.md#v217).

## v2.1.6：会话整理、复制与阅读 / Chat organization, copying and reading

网关与手机版本为 **2.1.6**，Android/iOS 构建号 **39**。完善最近交互列表的本地优先置顶、二次确认归档、原生剪贴板复制、Markdown 阅读与文件字号，以及 API 表单和上游模型获取。归档能力取决于原生接入，部分 Codex App 会话、Cowork 等仍有限制，详见 [v2.1.6 更新说明](../RELEASE_NOTES.md#v216)。

覆盖安装匹配的原 App，不要卸载或清除数据；Android 沿用原签名，iOS 使用相同应用标识和签名身份。更新网关后刷新共享页面。正式版与 Test 如共用 DSH 连接器，应一并升级到兼容版本，并仅在原生任务停止后显式重启接入。

**English:** v2.1.6/build 39 improves local-first pinning, confirmed archival where supported, native clipboard writes, Markdown/file reading and API setup. Native archive limitations remain; see the release notes. Install over the matching app with the same signing identity and retain data. Refresh shared pages after updating the gateway; shared DSH connectors require compatible gateways and an explicit restart after tasks stop.

## v2.1.5：WSL CLI 与聊天管理 / WSL CLI and message management

网关与手机版本为 **2.1.5**，Android/iOS 构建号 **38**。支持 WSL Codex / Claude Code CLI、跨客户端新建项目、回复审查与引用、图片添加到对话，以及待发送消息的原位编辑、排序、撤回和未确认记录管理。WSL 账号管理为实验性，尚未完成真实多账号全流程验证。详情及升级边界见 [v2.1.5 更新说明](../RELEASE_NOTES.md#v215)。

覆盖安装匹配的原 App，保留数据与绑定；iOS 仍需使用自己的签名。更新网关后刷新共享页面；旧 Android 请在原电脑连接选择“更多 → 刷新页面”，再打开聊天。

**English:** v2.1.5/build 38 adds WSL CLI access, project creation, review/quote/image interactions and message management. WSL account management remains experimental without completed real multi-account end-to-end validation. Install over the matching existing app and retain data; iOS requires your own signing. Refresh the shared page after the gateway update; older Android apps may need **More → Refresh page** in the existing computer connection.

## v2.1.4：启动、长会话与 Web 复制 / Startup, long chats and Web copying

网关与手机版本为 **2.1.4**，Android/iOS 构建号 **37**。配合新版网关改善公网首次加载、资源缓存复用、长会话最近内容和按需翻页；手机/Web 可管理默认关闭的网络变化自动恢复。

覆盖安装匹配的原 App，保留电脑列表、设置和有效绑定，不要先卸载或清除数据。Android 沿用原签名；iOS 沿用正式应用标识，需要使用自己的原签名身份安装。独立 Test 与正式发布 App 使用不同标识，不能互相覆盖。

**English:** v2.1.4/build 37 accompanies public-entry startup and cache recovery, recent-history-first loading, on-demand paging and optional network recovery controls. Install over the matching existing app to retain saved computers, settings and valid bindings. Android keeps its signing identity; iOS keeps its official identifiers and requires your own signing. Independent Test apps do not replace the release app. See the [release notes](../RELEASE_NOTES.md#v214).

## v2.1.3：通知、图表与连接恢复

手机 App 与网关配合管理多个 Webhook 接收目标，提供飞书、钉钉、Discord、Telegram 和自定义模板；保存的地址与密钥默认隐藏。聊天与 Markdown 文件中的 Mermaid 图表支持源码查看、复制和全屏缩放。iOS App 修复图表子页面被导航策略拦截的问题，并在网关或隧道不可达时提供原生重试及返回电脑列表入口。手机界面也改善弹窗、焦点和补充回答输入框的键盘可见性。

网关与手机版本统一为 **2.1.3**，Android/iOS 构建号 **35**。覆盖安装并保留原电脑列表及配对；iOS 保留正式 Bundle ID，使用原签名身份安装。Mermaid 子页面和 iOS 原生连接恢复需要同时更新网关与 App。详见 [v2.1.3 更新说明](../RELEASE_NOTES.md#v213)。

**English:** v2.1.3/build 35 adds multiple Webhook destinations with editable service presets, Mermaid source and fullscreen reading, and native iOS Retry/Back to computers actions when the gateway is unreachable. The iOS app also fixes diagram-subframe navigation. Mobile dialogs, focus appearance and answer-card keyboard visibility are refined. Update both gateway and app for native iOS changes; install over the existing app to retain saved computers and pairings.

## v2.1.2：终端输入、触摸滚动与显示

手机与宽屏 Web 默认直接在终端输入，保留可选的“编写后发送模式”。新增 Enter，改善中文输入、Tab 路径补全、OpenCode 类终端 CLI 的触摸滚动及断线恢复。终端字号可独立调节为 6–24px，修复小字号重叠、字符对齐和内容覆盖工具栏。主题与语言移到设置顶部，正文字号为 10–32px、代码字号为 8–28px。

网关与手机版本统一为 **2.1.2**，手机构建号为 **34**。请同时更新网关和手机 App；Android 的小字号修复包含原生 WebView 设置。iOS 沿用正式应用标识，需自行签名，使用相同签名身份和 Bundle ID 覆盖安装。

**English:** v2.1.2/build34 improves direct terminal input, Chinese composition, Enter and Tab, touch scrolling in terminal CLIs such as OpenCode, reconnection and 6–24px rendering. Update both the gateway and mobile app; Android also includes a native WebView font correction. iOS retains its official bundle IDs and requires your own signing for installation.

### v2.1.1：模型缓存、草稿与文件定位

模型目录按电脑、客户端和接入身份缓存，显示更新时间；未发送草稿在切换聊天或电脑后恢复。文件定位自动滚动并高亮，左上角返回直接回聊天列表。新增工具截图显示控制与跳过当前版本提醒；iOS 改善场景恢复与通知跳转。网关与手机版本统一为 2.1.1，手机构建号为 33。

### 已有 CLI 与文件阅读功能

- 在手机与 Web 中发现 Codex / Claude Code CLI 聊天、读取历史并显式继续；原终端可以关闭。支持新建 CLI 聊天及执行端提供的模型、权限、Skill、上传和审批能力；Codex 兼容原生运行时另支持 Goal。SSH 使用服务器自己的配置。
- Codex 与 Claude 各自账号面板管理 CLI 接入；桌面切换可选择“同时应用到本机 CLI”。SSH 连接诊断、类型筛选和上下文圆环沿用共享界面。
- 左上角返回直接回聊天列表；文件区上级目录和系统返回保留层级导航，全屏的系统返回先退出全屏。
- 源码增加高亮、行号与复制，可选自动换行以适应屏幕；复制保留原始内容。
- PDF 支持连续滑动、双指缩放和全屏，混合尺寸页面分别适配宽度；切换标签保留阅读位置，快速缩放与邻页回看减少闪烁。
- 自包含 HTML 支持内联交互、动画与源码切换，采用隔离预览；全屏进出保留状态，切换文件标签后返回重新加载 HTML。
- 文件描述与下载、复制、全屏等操作并排，兼顾窄屏中英文显示。Claude 额度改为手动查询，并显示原成功查询时间。
- Codex 列表逐主机呈现，Claude 混合列表按最近日期排序；来源标签持续显示。iOS Safari 输入和缩放时保留原有视区，减少空白和抖动；滑动切换应用的列表布局更稳定，点按弹窗外部可关闭弹窗。

完整能力需更新网关及 Android/iOS App：源码和 PDF 阅读由网关提供，HTML 子页面导航与权限隔离还包含原生壳调整。PDF 在线预览限 20 MiB 且受普通点击下载限额约束；源码与 HTML 限 512 KiB UTF-8 文本。详见[文件阅读说明](../README.md#workspace-reading)。

## v2.0.3 更新

- Android 应用内更新，手机下载与电脑下载自主选择，已下载 APK 可恢复安装或导出。
- Android/iOS 安全保留有效扫码绑定，在 WebView 登录状态丢失时尝试恢复，无需保存网关密码。
- 第三方通知支持选择 Bark（iPhone）、ntfy（Android）、PushPlus（微信），在手机或 Web 配置、保存和测试。
- 搜索框常显、筛选按内容分配宽度，改善英文与横屏/平板布局；Claude/DSH 下拉刷新、错误恢复与工作台分屏更连贯。

## 安装与升级

- **Android 8.0 及以上**：从 Release 下载 `Codex-Mobile-Bridge-2.1.4-Android.apk`，按系统提示安装。升级时覆盖安装，不要先卸载，以保留电脑列表和登录。相机权限仅供扫码，拒绝后仍可粘贴地址登录。
- **iPhone / iPad**：Release 提供未签名 IPA 和 Xcode 源码包，需使用自己的 Apple 账号签名；不能直接点击 IPA 安装，暂无 App Store / TestFlight 分发。免费签名可能需要定期重新安装。
- 使用 Release 中的 `SHA256SUMS.txt` 校验下载。编译通过不代表已验证所有机型、系统版本和网络环境。

首次从较旧版本升级到 v2.1.4，可先下载 APK 覆盖安装；新版 Android 再次更新时可直接使用以下应用内流程。

Android 在「手机设置 → 应用 → 检查更新」查看当前版本并检查更新，iOS 使用「手机设置 → 应用更新」。正式版只检查正式发布，预览版也可检查后续预览；不会在后台自动下载或安装。确认更新后，Android 会在 App 内下载 APK 并打开系统安装器，iOS 打开新版与签名安装指引。访问公开 GitHub Release 的请求不携带网关凭据或 Cookie。

Android 在安装前核对发布信息中的大小和 SHA-256；缺少校验信息时提供版本页下载入口。首次安装更新需按系统提示允许此 App 安装应用。已完成的更新可在重新打开 App 后继续安装，也可选择「保存」导出 APK。

Android 手机无法查询 GitHub 时，会尝试通过当前已连接并登录的电脑查询。点击「下载并安装」后，由用户选择「手机下载」或「电脑下载」；选择电脑后直接使用网关，无须等待手机下载失败。下载失败不会自动切换方式，下载卡片提供「重新选择」和「GitHub ↗」入口；后者在外部浏览器打开已检测到的新版 APK 直链，由浏览器下载，无须进入版本页寻找安装包。网关完成下载和校验后传送到手机，手机重新校验并打开系统安装器。电脑需要能访问 GitHub，且手机 App 与网关都需支持此功能；共享中继部署还需更新中继服务。旧网关仍可配合手机直连更新。网关缓存仅在当前运行周期内复用，最多保留两个包，超过 24 小时的闲置缓存会在下次准备更新时清理。手机取消等待不会取消其他手机可复用的电脑下载。

### 使用 Xcode 安装到 iPhone

1. 打开源码中的 `mobile/ios/BridgePreview.xcodeproj`（工程目录保留原名）。
2. 连接并解锁 iPhone，确认信任电脑，按 iPhone 提示启用开发者模式。
3. 在 BridgePreview、BridgeActivity 和 BridgeNotifications 目标的 Signing & Capabilities 中选择自己的开发团队。账号及签名授权在本机完成。
4. 选择手机后运行。缺少设备支持或 iOS 平台时，按 Xcode 提示安装对应组件。签名团队与 Bundle ID 改变可能影响覆盖安装和原有数据。

## 连接与管理电脑

1. 打开配套 v2 网关，在「客户端与接入」完成应用配置并启用所需客户端；选择可达的网络入口并启动网关。
2. 手机点击「扫码连接电脑」，扫描网关概览中为该入口生成的配对码。也可粘贴完整网关地址，输入网关账号密码。
3. 在「我的电脑」点击卡片或应用图标，进入对应客户端聊天列表。电脑按最近访问排序，可重命名、移除或手动刷新在线状态。
4. 保持电脑、网关和相应客户端在线。局域网需手机能访问电脑；外出可选择 [Tailscale](../docs/tailscale.md)、[Cloudflare](../docs/fixed-domain.md)、[自有服务器 + SSH](../docs/server-ssh.md) 或已有 HTTPS 反代。

扫码建立可撤销的可信设备绑定，不保存网关密码。Android 使用系统密钥加密保存绑定副本，iOS 使用钥匙串保存；WebView 登录状态丢失后，先向对应网关核验，仍有效才恢复。显式退出、移除电脑或服务端撤销后不再恢复；网络暂时不可达不会直接删除副本。正常重启 App 或网关后仍可连接。手机登录不受普通浏览器登录有效期影响。电脑端可在「登录与设备」撤销绑定；移除电脑、清除 App 数据、更改认证或主动撤销后可能需要重新配对。已经失效的旧登录不会无凭据地升级为新绑定。

相机扫码不建立网络通道。固定地址也不保证国内直连速度；请在实际 Wi-Fi、蜂窝网络验证。Tailscale Funnel 不要求手机安装 Tailscale；私有 Serve 则要求手机接入有权限的 tailnet。

### 临时地址变更

普通 Cloudflare 临时地址重启后可能改变，手机不能仅凭失效网址找到电脑。建议使用固定入口，或开启网关启动/入口变化通知并获取新地址。

源码保留了依赖稳定 HTTPS 查询服务的地址发现协议，只有此前已绑定并且该查询服务可达时才能校验并恢复入口；它不是所有临时隧道自动续址的承诺。v2 桌面已移除邀请码式「自建中继 · 内测」配置入口。稳定入口变化、配对撤销或电脑数据重建后仍可能需要重新扫码。

## 聊天列表与应用切换

- 当前应用图标和名称紧邻「聊天」，右侧新建与更多设置遵循原生菜单留白。搜索框始终显示「搜索 / Search」，最近交互/按项目、Claude 会话类型等选择框按当前内容分配宽度，其余空间留给搜索；英文使用 Recent、Project、All、Code、Cowork 等短标签。
- Claude 与 DSH 支持手动下拉刷新，保留当前搜索、筛选和阅读位置。列表不常驻显示读取中或连接成功提示；连接失败仍显示原因与恢复入口，恢复成功后清除旧错误。
- 功能栏位于应用切换栏上方，账号管理、第三方通知、应用管理可选择展示，「返回电脑列表」位于右侧；普通 Web 不显示返回电脑列表。
- 仅一个应用启用时隐藏底部切换栏；更多设置可选择隐藏图标或整栏。隐藏只影响显示，不关闭电脑应用。
- 左右滑动切换应用默认开启，有拖动预览；可在显示设置关闭。此前主动关闭的选择保留，不拦截纵向滚动、输入或边缘返回手势。
- Claude Code 与 Cowork 分组可折叠；返回从当前会话回到该应用列表。模型、权限、上下文用量与账号入口均随当前应用变化，缺失能力不伪装成 Codex 的功能。
- Android 在电脑列表按一次返回提示，再在两秒内按一次退出；聊天及工作台内优先执行页面返回。退出不会清除电脑或登录。

## 账号与通知

凭据在桌面网关添加、修改或导入。手机可查看当前客户端的已保存接入、支持的额度/模型，以及切换账号。关闭客户端或切换账号前会核验任务，风险操作要求明确确认；Claude 重新初始化可能仍需在解锁的电脑上授权。

电脑卡片右侧显示已启用应用的原始图标，角标由电脑网关依据全局/单会话通知选择维护。点击图标直达对应应用列表；加载成功后标记此前该应用提醒为已读，保留其他应用与新到达的事件。角标清除不会批准待处理请求或删除历史。

同一电脑上的多个手机会在下次同步时得到一致未读数。网关收件箱保留最近 200 条事件正文，未读计数独立保留；默认不包含聊天标题和正文。

### Android 手机设置

手机设置使用全屏原生窗口，返回后继续原来的聊天与下载。页面按「通知」「系统权限」「应用」分组：关闭任务通知会同时关闭后台通知并收起权限项；仅开启任务通知时保留通知权限和十秒本机测试；开启后台通知后增加厂商后台设置与 Android 标准电池优化入口。权限、通知频道和电池豁免状态在返回页面时刷新，电脑连接异常时显示诊断入口。

「应用 → 主题」提供跟随系统、浅色、深色，选择保存在手机本地，目前仅覆盖设置页。主题旁的说明按钮提示此范围，聊天网页继续使用自己的外观设置。「后台通知」旁的说明按钮提示该功能仍在完善中及可能增加耗电或发热，点击以对话框完整显示，可阅读后关闭。

设置页进出采用 24dp 轻滑与淡入淡出，开关圆点与轨道颜色同步过渡，相关权限平滑展开收起。主题弹层从底部轻滑进出，浅深色切换保持滚动位置并交叉淡化。所有动效遵循系统动画时长设置；系统关闭动画时直接呈现最终状态。主题过渡仅短暂保存有大小上限的原生设置页画面，窗口关闭时释放。

### 系统通知

1. 网关「消息通知」开启手机 App 通知收件箱，并在聊天或全局设置选择请求处理/运行完毕提醒。
2. 首次打开 App 会申请系统通知权限；允许后默认使用横幅和提示音。明确关闭或拒绝的选择会保留，手机的静音、专注模式及系统通知设置始终优先。
3. Android 在「手机设置 → 通知」手动开启「后台通知」，通过常驻服务同步所有已登录电脑，并接收已开启来源的提醒；常驻通知提供暂停按钮。常驻提示仅显示“正在接收任务提醒”“正在重试”或“请重新登录”；多个地址中有正常接收的连接时显示接收中，其余地址独立重试。具体错误记录在 Android 的 `BridgeNotifications` 日志中。默认关闭，开启后沿用电脑端已有的全局和聊天通知选择。iOS 在前台每 5 秒独立同步各台已登录电脑，支持 Codex、Claude、DSH 的任务提醒；离开 App 或锁屏后不能保证持续通知。首次连接和事件流重置以当前收件箱建立接收起点，历史仍可在收件箱查看。
4. Android 每台电脑独立读取增量事件。连接时先立即查询能力，支持长轮询的网关最多等待 60 秒，事件到达立即返回，手机读取超时为 80 秒，成功后立即接续等待；旧网关和手机通知渠道关闭时沿用 15 秒查询。连接失败逐步退避至 120 秒，网络恢复时取消旧请求并立即重试。后台通知开启期间按登录身份持久化接收游标，服务重建后补收网关保留的近期事件（最多 200 条）；切换登录身份重新建立起点。关闭后台通知时，回前台建立新的接收起点。

5. Android 同一电脑的同一聊天仅保留最新一条系统通知；新事件按系统通知设置提醒，同一事件重试只更新已有通知。点击通知回到相应电脑和聊天；聊天加载完成并在前台显示后，自动撤下该聊天的系统通知，其他聊天的提醒和收件箱记录继续保留。手机设置的十秒测试只检验本机显示，不证明远程推送已经接通。

手机可在「通知来源」按保存的完整连接地址单独开关任务提醒，默认全部开启。Android 入口为「手机设置 → 系统权限 → 通知来源」，iOS 入口位于设置的「前台任务提醒」中。电脑名称与地址同时显示，局域网、HTTPS、正式版与 Test 可分别选择。关闭某个来源仍能聊天和同步收件箱；重新开启时以首次成功读取建立新起点，不补弹关闭期间的旧消息。选择仅影响当前手机，不改变其他手机或网关的 ntfy、Bark 等第三方通知配置。

`GET /api/mobile/events` 默认立即返回，兼容 Web、iOS 和旧版 Android。响应中的 `longPoll: 60` 表示支持等待，`revision` 标识当前通知快照。后续请求携带 `after`、`streamId`、`revision` 和 `wait=60`；新事件、已读或通知策略变化会提前返回，事件流更换会立即建立新起点。网关通过条件变量等待，桌面进程保存的偏好和应用状态保留最长 15 秒复核间隔；HTTP 连接持续等待，手机期间沿用同一请求。等待结束后再次校验登录与访问规则。

网关开启“包含聊天标题”时，手机收件箱提醒的正文仅显示聊天标题；关闭时显示运行完成或待确认的通用提示。

Android 后台通知以用户开启的 `specialUse` 前台服务运行，Android 14 及以上声明对应权限与用途，两种 Android 构建共用接收实现。长时间锁屏进入深度休眠时，系统可能延后网络读取；设置页提供系统电池优化入口供用户调整。系统强制停止后，再次打开 App 恢复接收。需要系统推送的使用方式可在「第三方通知」配置 Bark、ntfy、PushPlus；启用「通知链接优先打开手机 App」后，支持该链接的通道可跳转至已保存的电脑。

Android 通知大图标显示来源应用，状态栏遵守单色图标要求；iOS 使用来源名称和图片附件，系统小图标仍属于 Bridge，不能逐条替换成其他应用图标。图片由 App 自带，不从远端下载。

### 第三方通知

直连电脑网关时，从「外观与显示」、Web 设置或聊天列表底部进入 **第三方通知**（英文入口为 **Push alerts**）。先选择 **Bark（iPhone）、ntfy（Android）或 PushPlus（微信）**，再进入详情启用通道、填写服务信息、保存并测试已保存配置。共享中继不提供此配置入口。

手机和电脑端修改同一份网关配置，变更接收目标会影响使用该通道的提醒。已保存密钥不回传手机，留空保留；更换服务地址时重新填写。通用设置可修改通知点击地址、标题显示、安全事件和网关启动/入口变化通知；全会话与单聊天通知选择仍独立管理。接口接受测试不等于已送达，请在 Bark、ntfy 或微信实际确认。

### 灵动岛与锁屏实时活动

需要 iOS 16.2 或更新版本。在聊天更多菜单选择「在灵动岛显示此聊天」，支持的机型显示灵动岛，其他兼容机型可显示锁屏活动；点击回到聊天，选择结束显示可关闭。卡片不含聊天标题/正文。

iOS 普通构建在 App 前台更新实时活动，后台显示最后状态及同步时间。「预览灵动岛」只显示演示，不发送聊天或启动任务。

## 项目工作台

进入会话后点击「＋」打开文件、Git、终端等可用标签；Codex 还提供临时侧边聊天和子智能体历史。具体能力由客户端返回，SSH 工具在会话对应的服务器执行。标签切换保留聊天草稿和阅读位置。

### 文件、图片与下载

- 文件来自当前会话的项目目录。没有目录时提示不可用；搜索只查当前目录文件名，可切换隐藏文件和图片缩略图。
- 点击文件夹进入，点击文件预览文本、Markdown 或图片；最多保留 8 个文件标签。过大的文本不展开预览，HTML/SVG 作为文本显示。
- 图片支持双指/滚轮缩放、放大后拖动、双击适应/两倍，以及 1–8 倍按钮控制。关闭或 Android 返回先退出图片，保留原草稿和阅读位置。
- 聊天文件链接及普通下载入口先询问定位所在文件夹或下载。默认阈值 20 MiB，可在网关、Web/手机「文件传输」实时调整；超阈值只提供定位。
- 文件列表长按、电脑右键或 Shift+F10 可明确下载文件/目录，不受普通点击阈值限制。目录为 ZIP，准备完成后显示总量。浮窗显示进度、速度、总量，支持暂停、继续与取消。
- App 流式写入磁盘，完成后使用系统保存界面；iPhone 可存至「文件」，Android 可选择位置。浏览器使用站点存储，实际容量仍受设备空间、浏览器配额和网络限制。
- 上传保存到当前目录，可调整文件名，每个文件上限 20 MiB；同名不同内容不会直接覆盖。普通下载阈值不改变上传和在线预览限制。

文件面板不提供编辑与删除。符号链接、Windows junction/reparse point 和特殊文件受访问边界限制，目录下载不会打包工作区外内容。SSH 大目录准备仍可能受到远端执行超时限制。仅向可信设备授权网关访问。

### Git

查看未暂存、已暂存、未跟踪和冲突文件，审阅行内差异，逐项或批量暂存。提交前核对清单并填写说明，沿用电脑/SSH 主机的 Git 身份、签名与 hooks。

历史显示真实父提交关系、分支、标签和逐文件差异，可逐步加载最近最多 1,000 条。支持从当前提交创建分支、切换本地分支、合并与确认后中止合并；要求工作区满足安全条件，不自动丢弃修改。子目录会话的仓库级操作须回到根目录对应会话。

没有远程 push、强制切换或历史重写。领先/落后数量来自本地记录，不自动 fetch。二进制、非 UTF-8、过大差异或子模块提示在电脑查看。浮动差异窗口可移动、调整、最大化，横竖屏会约束到可用区域。

### 终端

连续终端保留 `cd`、环境变量和交互状态。电脑浏览器直接输入，手机编写多行后发送；支持 Ctrl+C、清屏和重新打开已退出终端。切换标签保留会话，关闭标签结束对应 shell。

macOS/Linux 使用登录 shell 与 PTY；Windows 10 1809 及以上使用 ConPTY 和 COMSPEC。Windows 驱动器切换在持续 shell 中处理；CMD 跨盘切换可输入 `cd /d D:\` 进入 D 盘根目录。页面后台暂停读取、保留 shell，返回时按游标补读；超过缓冲上限的内容会提示截断。终端使用当前用户权限，请核对主机和命令。

### Codex 侧边聊天

从主聊天当前上下文创建临时分支，独立设置模型、推理、Skill、权限与附件；支持普通/计划模式、排队、补充任务和停止。同一网关的多设备共享这条临时聊天。

每条主聊天保留一条，网关最多同时 8 条。结束或重启网关即清除，不写入 Codex 普通历史，也不是原生桌面侧边标签。仅支持本机，暂不支持 SSH；未确认的发送不自动重放。子智能体标签只读展示保存历史，不能给子智能体任意派发任务。

## 分屏与阅读

宽浏览器、平板或展开的折叠屏可通过标签栏分屏按钮并排查看聊天与工具，拖动分隔条调整宽度，也可将工具标签放到右侧。窗口收窄恢复单栏，重新展开恢复选择；保留草稿、标签与阅读位置。

「外观与显示」及 Web 设置顶部并列放置底部 App 图标开关、应用管理、第三方通知；英文使用 Show/Hide icons、Apps、Push alerts。只有一个应用时无需显示图标切换开关。文件传输的保存按钮只保存传输大小配置，不影响其他外观设置。关闭当前工具标签时，仍有内容的另一侧分屏保持打开。

更多设置可切换中英文、浅色/深色/跟随系统、强调色、文字和代码字号、间距、思考摘要与执行过程。正文、日志、模型/Skill 名称和用户标题不强行翻译。输入区箭头可收起/展开，保留草稿。复制消息或代码使用系统剪贴板。

## 开发与构建

[源码对照表](source-map.json) · [Android 规则](android/AGENTS.md) · [iOS 规则](ios/AGENTS.md) · [共享脚本](shared/AGENTS.md)

两端共用的页面适配、剪贴板等在 `shared/web/` 保留单一源码，原生平台分开维护。源码资源回归使用 `node --test mobile/tests/*.test.cjs`，不会运行原生编译器。

Android 入口是 `scripts/build-mobile-android.py`，使用项目 `.tmp/mobile-tools` 中已准备的 JDK、SDK 和 ZXing；构建必须通过 `--keystore` 指定已有签名文件，并使用 `CMB_ANDROID_STORE_PASSWORD` 环境变量传入密码；签名缺失时停止，不自动生成。密钥与备份应长期保存在非临时、未跟踪目录。覆盖升级必须保留既有签名，不能以新建密钥替代升级验证。`--isolated-preview` 生成独立的 `io.github.try2love.codexbridge.previewchannel`（Bridge Preview），隔离下载提供程序及远控回调，与原 App 并存，数据不自动迁移。CI 仅显式生成短期夹具签名，不发布该 APK。地址边界验证见 `scripts/test-mobile-urls.py`，会话验证见 `scripts/test-mobile-session.py`，通知事件见 `tests/test_mobile_events.py`。

iOS 构建入口 `scripts/build-mobile-ios.py` 生成未签名 IPA 和包含受版本管理源码的 Xcode ZIP；`--source-only` 只导出源码，无需 Xcode。脚本不会自动安装模拟器运行时；模拟器 SDK 编译与实际启动测试应分别记录。

可选 APNs/FCM 接入见 [推送开发说明](push/README.md)。普通 APK 不含 Firebase SDK；没有配置自有推送服务和签名时，源码能力不等于远程推送可用。iOS 远程通知图片由 BridgeNotifications 扩展处理，需一同构建和签名。第三方许可见 [NOTICE.md](NOTICE.md)。

手机 App 的主题在「手机设置」和聊天「外观与显示」之间同步，可选择跟随系统、浅色或深色；电脑连接页、列表、聊天和设置使用同一选择。切换时保留当前聊天和草稿，其他显示偏好不变。
