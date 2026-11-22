# Agent Note: Windows 与 Linux 的 Codex 普通启动接入及手动兜底

Status: implemented

## 问题

仅在网关指定环境下启动 Codex，不能覆盖用户退出后从原图标再次打开。Windows 还有 MSIX 安装目录变化、原生 App 下载独立 CLI 和 Explorer 环境继承；Linux 的真实菜单使用 PATH 中的 shell 启动器。将进程运行误作可操作连接，或在已连接时隐藏初始化，会让假连接用户找不到恢复入口。共享中继不开放账号接口，远程管理也不能直接依赖账号页 URL。

## 决策

Windows 初始化为当前用户登记 `CODEX_CLI_PATH`，指向私有、按二进制摘要保存的独立 C# 分派器，并通过 `WM_SETTINGCHANGE` 通知 Explorer。分派器继承 App 的标准输入输出，调用已有共享 stdio 适配器。账号、供应商、历史和任务仍属于原 App；不修改安装包、认证、系统环境或 Explorer 进程，不根据 App 版本号选择路径。已有第三方覆盖保留并报告普通启动准备失败，当前已核验连接仍可使用。

普通启动核验所选 App 的完整镜像或稳定 AUMID、同用户和会话的进程代次、配置目录及分派器摘要。MSIX 更新改变安装路径时，以同 AUMID 重新取得 CLI；App 使用安装目录外的下载缓存时复用目录发现的修改时间顺序，不固定缓存散列或版本号。显式外部 CLI 保留。只有上一个 writer 已结束才发布新的启动设置，运行中的冻结程序不覆盖。其他 App/profile 透传原生 CLI；网关文件缺失或配置不可托付时，原生分派器回退到调用 App 自带的 CLI。

分派器不全局设置 `CMB_CODEX_DESKTOP_CONFIGURATION`，避免网关本体启动时被误派为适配器。实际 Windows 验收发现 .NET Framework 的无窗口子进程在没有重定向时不设置 `STARTF_USESTDHANDLES`，会丢失原生握手管道。分派器只重定向 stderr 并并发按字节排空，使 stdin/stdout 保持 App 原始句柄；不做逐行编解码或等待输出后才读取错误。

Linux 复用 XDG 同 ID 用户菜单覆盖，保留原 Exec 参数与 URL 字段码。匹配原入口时解析 PATH，并复用系统发现逻辑，将 `/usr/bin/chatgpt` 这类 shell 启动器映射到真实 Electron GUI；不修改系统 `.desktop`。退出核验将 `/proc` 中 Z/X 状态识别为已结束，避免僵尸进程缺少 exe 链接时被当作仍在运行；权限不足和未知进程仍不能据此判定停止。能力仍限定已解包 Electron 布局，不能推断 AppImage 或直接执行 GUI 二进制经过菜单初始化。

网关最外层客户端列表和远程应用管理始终为受支持且启用的默认本机 Codex 提供初始化；已报告连接时显示“重新初始化连接”。仅在途初始化隐藏重复入口，断线聊天也可进入同一流程。桌面走既有 Accounts 事务；远程使用共享中继已允许的 `/api/clients`，严格限定 Codex 初始化字段，由网关转交同一个 `initializeDesktopRuntime`，只返回客户端状态，不返回账号清单。账号 API 的中继禁用、认证、CSRF、免密拒绝、任务和写入检查保持。已知任务阻止重启，未知任务需要额外确认，未知写入不重放；产品流程没有增加强杀兜底。

新旧 Codex 仍使用原有能力与传输探测。新增启动参数及其值原样保留，包括值恰好为 `daemon` 或 `proxy` 的未知参数；只对真正的已知辅助入口绕过共享接入。已有增量字段、通知、消息类型与流版本的兼容检查继续保留，不以“不写版本号”替代原生接口与 owner 核验。Windows 缓存发现逻辑的复用同时从 SSH 独立目录脚本中排除本地 Windows 分支，避免为远端引入未安装的 Bridge 包依赖；Linux/macOS 的显式 SSH 运行时发现保留。

## 备选方案

全局登记原有网关适配器环境实现最少，但会把网关自身误派到 stdin 入口，也无法安全区分其他安装和 profile，因此使用独立分派器。只保留网关启动可沿用既有实现，却不能满足原图标再次打开的目标。仅当未连接才显示初始化能减少按钮，但用户已报告假连接，因此保留显式手动恢复。为远程开放账号 URL 可直接复用请求，却扩大共享中继暴露面，因此复用客户端管理的严格请求并隐藏账号结果。

## 验证

Windows 通过用户指定的 SSH 主机，在已有交互桌面创建临时测试任务。真实 Explorer 快捷方式启动合成 GUI，连续验证主进程与 utility host 两种归属、标准句柄、原生只读请求、完整进程代次，以及安装目录外的 CLI。8 项 Windows 契约在目标 Windows 通过。原环境在测试后恢复；不替换远端安装包。

安装的真实 Codex 被复制到用户临时目录，以独立 home/userData 启动，实际握手与原生 `thread/list` 成功，零模型请求。MSIX 原入口会复用已运行的原 App，不能用它证明隔离启动；测试改用签名安装副本。副本缺少可用退出菜单时，仅对已核验、零任务、属于测试目录的副本执行清理，不能推广为产品退出策略。原 App PID 4960 保持。没有构建完整 Windows 安装器，也没有真实注销重登或旧版 Windows App 的 GUI 验收；旧协议由原回归保护。

Linux 经同一 Windows 跳板，在实际 GNOME 桌面使用原系统菜单的 PATH Exec，通过 `gio launch` 连续启动独立配置。两次约 6.3 秒和 5.5 秒建立原生 stdio 连接并读取列表，零模型请求，系统菜单字节与原 App 进程全部保留。10 项 Linux 定向用例、41 项共享运行时与 32 项 hosted 回归在该 Linux 通过。实际 AppImage 未验收。

真实 Chromium 在 1280 px 和 390 px 各执行远程 8 项、桌面 4 项断言，验证初始化、取消零请求、一次确认请求、在途去重以及已连接时保留手动恢复。认证 HTTP 11 项、客户端生命周期 121 项、共享中继 32 项通过；全量 Node 1770 项中 1769 通过、1 项跳过。浏览器使用合成状态，未对用户当前 App 发起初始化。

本机证据位于 `.local/installations/20261122-ordinary-launch-windows/`，包括 `windows-native-fixture.json`、`windows-external-runtime.json`、`windows-real-gui.json`、`linux-real-gui.json` 和 `browser-recovery.json`。源码测试、原生隔离测试和完整安装包验收分别记录，不互相代替。原生 Windows 源文件摘要逐一与当前实现核对；临时交互任务、孤立合成进程和真实安装副本已清理，用户环境恢复，远端原 App 保持。SSH 独立资源 5 项、远程目录 7 项和本地目录 41 项回归通过；笔记格式 287 篇通过，链接检查仅报告共享初始化记录中已注明的既有“本机路径”示例。

macOS arm64 Test 从独立 r4 目录运行，8788 已自动切换；切换前后的当前 App、适配器、执行器代次与配置保持，正式 8787 仍是原实例。实际管理接口报告原生连接就绪、普通启动已准备且手动重新初始化可用，三个目标聊天均接通。当前采样列表约 32 ms，已接通时间线约 2–7 ms；长聊天首次未在桌面加载，现有显式重建经原 App 约 1.7 秒接通，零消息、零 App 重启，被动历史读取没有替它加载聊天。`final-gateway-switch.json`、`live-http-verification.json`、`long-session-recovery.json` 和 `source-provenance.json` 记录本地产物与源码映射；旧活动冻结归档保留。

## 影响

初始化一次后，Windows 原图标和受支持 Linux 菜单启动可以自动加载原有共享连接；手动初始化在最外层保留。代价是一个 Windows 用户环境变量、私有分派组件或 Linux 用户菜单覆盖，均不改原 App 内容。后续只增加功能且保留现有底层接口的版本不依赖版本或缓存名白名单；移除 CLI override、改变初始化身份、关键控制结构或打包布局时仍需重新适配。

## 历史笔记核对

- [macOS 普通启动](2026-11-22-codex-ordinary-launch.md)：部分重叠，保留 macOS 决策与原验证，本篇补齐 Windows/Linux。
- [双端初始化](2026-11-22-codex-shared-initialization.md)：更新手动入口、远程通道及两平台已验收事实，不再将普通启动列为未实现。
- [Windows 原生共享](../bug-fix/2026-11-12-windows-native-side-chat-writer.md)：部分重叠，沿用私有管道和原执行器，不替换协议。
- [共享运行时边界](../architecture/2026-11-08-codex-shared-runtime-data-deletion.md)：保留唯一 writer、数据与安装隔离。
- 原共享运行时提案及 Claude、ZCode、WSL 启动记录各有独立边界，不被本篇吸收；无对应 rejected 笔记。
