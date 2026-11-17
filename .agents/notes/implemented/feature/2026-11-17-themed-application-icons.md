# Agent Note: 桌面与网页图标跟随明暗外观

Status: implemented

## Problem

明亮和暗黑 SVG 已经存在，但 Windows/macOS 桌面、网页页头和安装资源仍引用旧 PNG/ICO。只替换静态资源不能在系统或网页外观改变时切换图标；Windows 的应用主题与任务栏主题还可以分别设置。

## Decision

- 两份已确认的 SVG 是彩色图标的设计源。现有 `npm run icons:desktop` 使用项目已有 Electron 渲染器导出所有桌面、网页和官网尺寸，包括两套多尺寸 Windows ICO；不引入新的渲染依赖。
- Windows 运行中窗口/任务栏和托盘使用 `nativeTheme.shouldUseDarkColorsForSystemIntegratedUI`，缺少该属性时回退到应用主题。macOS Dock 使用系统应用外观，菜单栏使用独立的单色 Template 图标，由系统着色。退出时释放主题监听。
- 网页标识、favicon、触摸图标和安装 manifest 跟随最终的 `documentElement.dataset.theme`。没有显式页面主题的桌面页头和官网跟随 `prefers-color-scheme`。主题变化只更新图像地址，保留会话、草稿和页面。
- 安装器、文件管理器里的应用文件图标及已安装 PWA 的缓存图标不受运行时 API 控制，保持新明亮版作为静态默认。新 PWA 安装可选用当前网页主题的 manifest；不承诺已经固定到任务栏的快捷方式立即刷新。
- 所有新增公开资源继续经 `web/assets.json` 与固定白名单提供，主题图片的地址参与现有内容哈希版本化；动态创建的欢迎标识也复用同一切换函数。

## 既有记录核对

检索活跃记录中的图标、托盘和外观：未发现图标生成或品牌外观的既有决定。[安卓统一主题](../../implemented/bug-fix/2026-10-28-android-app-theme.md)与[iOS 全局主题](../../implemented/feature/2026-10-28-ios-theme-and-foreground-notifications.md)持有原生到网页的最终主题状态；本篇只读取该状态，不改变其契约，两篇属于互补记录。托盘快照缓存、原生退出和远控菜单等命中与图标内容无关。没有完全吸收或过时提案。

## Alternatives considered

- 只覆盖旧 PNG/ICO 最简单，但不能满足用户要求的三端实时明暗切换。
- 对同一张图片使用 CSS 反色可以减少资源，却会改变蓝青品牌色，也不能覆盖原生 Dock/托盘，因此保留两份真实设计稿。
- 重建窗口或刷新网页能重新选择默认图标，却会影响正在进行的聊天和界面状态；采用原位图像更新。

## Testing

- Electron 44.5.1 实际导出两套 PNG、各九尺寸 ICO 以及 18/36 像素菜单栏模板；图像尺寸、透明圆角、ICO 内嵌 PNG 与模板单色检查通过。
- Node 完整入口运行 1629 项：1628 通过，1 项 POSIX IPC 按 Windows 平台跳过。图标专项覆盖 Windows 独立系统/应用外观、旧 API 回退、macOS Dock/模板及退出解绑；原有启动夹具补齐 Electron 的 `nativeTheme` 替身。
- Python 资源版本化与布局共 14 项通过，模块边界检查保持原有 22 项例外。新资源在固定白名单内，两个 manifest、HTML 的主题地址和动态 JS 地址均使用内容版本化。
- Chromium（本机 Edge）与 WebKit 的宽/窄屏共 8 项通过，包括生产页面启动、真实外观选择控件、系统切换、原生同步、favicon/manifest、动态标识以及草稿与 DOM 保留。最初的测试内联注入被生产 CSP 拒绝，夹具改为同源脚本加载，产品 CSP 保持原样。
- 启动成功、脚本失败后重试以及原生主题既有回归共 12 项通过。再次导出 22 个生成文件与前次逐字节一致。
- Windows 隔离 Electron 窗口/托盘实际接受亮→暗→亮图像调用；主题事件为合成输入，没有修改系统设置。macOS 分支经过替身测试，尚无 Mac 原生显示验收；未打包安装完整应用。
- 笔记格式 216 篇与归档校验通过；全树链接检查仅报告上游既有 `2026-11-12-chat-media-playback.md` 中的行内 Markdown 示例 `本机路径`，该文件与 `upstream/main` 一致。本篇链接目标均存在，保留该基线问题供独立修复。

## Consequences

已有桌面、网页与官网图标由同一设计源维护，明暗变化原位更新，不创建会话或刷新聊天。代价是增加两套导出资源及固定 URL；更改设计或网页切换脚本后需运行导出命令。官网的切换脚本是共享源的生成副本。

macOS 原生显示需要在 macOS 上另行验证；Windows 固定快捷方式及 PWA 图标更新受系统缓存影响。Electron 的跨平台渲染可能产生少量像素差异，提交导出的资产可避免安装时依赖本地渲染器。运行时 API 无法控制的安装器和文件管理器图标沿用新明亮版。
