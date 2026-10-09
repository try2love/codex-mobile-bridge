# Windows Preview 3 分支整合

## 来源与保留范围

整合目标为已按功能、客户端、操作系统和界面宿主分类的 `refactor/platform-feature-layout` 分支。合并前提交为 `1283bea3deaf732f3bb5c6f21522d8460a139a50`，本地备份分支为 `backup/pre-windows-integration-1283bea`。来源 `codex/preview3-windows` 固定在 `2776cf5c2431f97d43861aae5074cbe5dfbfe03f`，共同基点为 `d56cb0ccebc72182bf6f567062135f95a44eb9b8`。

来源相对共同基点涉及 81 个文件，包括 Windows 原生接入和共享 Web、手机、桌面交互。合并保留当前分支的安全与开销优化、Ubuntu 发现与启动适配、macOS Claude 重连和退出确认、共同移动注入脚本，以及宿主和窗口布局的独立分区。既有 macOS/Linux 原生模块与移动原生源码未被 Windows 版本覆盖。

## 修改位置

| 功能 | 合并后的维护位置 |
| --- | --- |
| Windows 注册安装发现、Store 别名、当前桌面会话 | `bridge/platforms/windows/discovery.py`、`discovery_ext.py`、`session.py` |
| Windows Claude 原生操作、回执、profile 证据 | `bridge/platforms/windows/claude.py`、`claude-helper.cs` |
| Windows Codex/DSH 正常退出、显式强制退出 | `bridge/platforms/windows/codex_quit.py`、`codex-quit-helper.cs`、`dsh_quit.py`、`force_exit.py` |
| Claude 后台连接状态、取消、重试和 generation | `bridge/clients/claude/adapter.py`、`setup.py` |
| Codex 运行时更新后的模型/账号/目标/侧边聊天 | `bridge/clients/codex/catalog.py` 及 `bridge/features/accounts/`、`sessions/` 的消费者 |
| DSH Host 识别、退出及连接器升级 | `bridge/clients/deepseek/`，Windows 原生退出委托平台层 |
| 关闭接入、正常退出和强制退出的共享规则 | `bridge/clients/manager.py`、`lifecycle.py`、`desktop_app.py` |
| Web/手机/桌面共享退出选择 | `web/features/clients/client-lifecycle.js`、`client-lifecycle.css` |
| 客户端切换、进度、重试、详情缓存和草稿 | `web/features/clients/client-navigation.js`、`desktop-sessions.js` |
| 桌面网关客户端管理 | `desktop/features/clients/desktop-sessions.js` |
| Windows 本地测试入口 | `start-preview3-windows.cmd`、`scripts/start-preview3-windows.ps1`；`git-here.cmd` 仅用于 Git worktree |

来源 `web/account.css` 中新增的退出对话框样式已归入客户端功能，不混入账号样式。资源清单、固定静态 URL 白名单、桌面引用和原生助手源码路径同步更新。没有恢复旧的扁平 Python 模块或新增全局别名层。

## 审核与验证边界

按 GPT-6-sol 合并、GPT-6-luna 初审、主审复核的顺序执行。初审发现的 macOS Claude 启用退化已纳入修复：macOS 显式启用沿用启动后接入，Windows 保持专用后台流程；只重连现有实例的入口仍不能启动或重启应用。新增模拟回归覆盖该差异。

源码级回归：

| 检查 | 结果 |
| --- | --- |
| Python 源码回归 | 全量 1,308 项，24 项跳过；唯一失败为脚本顺序的测试期望，修正后该 HTTP 用例定向复测 1/1 通过，生产源码未再改动 |
| Node 桌面与共享前端回归 | 431 项：430 通过、1 项 Windows 专属测试跳过 |
| 主审独立定向复核 | 134 项全部通过，覆盖平台依赖、资源、进程清单、客户端启停、macOS Claude 控制与中继安全 |

Python 的 24 项跳过包括 11 项原生编译/执行夹具、7 项其他 Windows 专属检查、2 项缺少可选加密依赖的检查、4 项缺少可选 SSH 依赖的检查。Node 跳过项为 Windows WinError 32 检查。

独立复核包括压缩请求拒绝、正常 JSON、上传和 Range 下载。认证、文件、终端、通知、移动原生和既有平台实现的保留还通过差异检查核对；未将文件未改动等同于真实设备验收。纯源码入口已补全六类原生编译/执行夹具的排除。

修复了合并引入的 macOS Claude 启用退化、屏保原因被通用状态覆盖，以及桌面服务缺少 `Catalog` 导入的问题；Windows GUI/CLI 身份比较采用大小写无关语义。测试夹具同步新模块位置，并保留平台模拟、资源顺序和安全断言。

浏览器使用固定合成会话和本地静态服务，未连接真实客户端：

- 来源与合并代码各通过 85 项相同检查：7 项缓存/草稿/写入前新鲜详情检查，以及 12 组退出对话框中的 78 项断言。对话框在三种尺寸、中英文、是否支持强制退出的组合下，文案与几何比较无差异。
- 合并代码另外通过 90 项宿主与窗口尺寸检查和 14 项分屏交互检查，覆盖浏览器、iOS/Android 宿主标记与 UA，窄屏、横屏和宽屏；检查草稿、标签、阅读位置及分屏选择保留。
- 页面交互使用 DOM 驱动；宿主标记模拟不等于 iOS WKWebView 或 Android WebView 真机测试。静态夹具未提供客户端图标路由，截图中的图标缺失不能用作产品图标验收；生产图标固定路由保持不变。

本轮未构建、安装或操作真实 Codex/Claude/DSH，未执行 Swift/C# 原生编译，未推送、改版本、打标签或发布。Windows 原生窗口、锁屏、进程退出和真实账号归属仍需在 Windows 测试机验证；macOS、Linux 与移动原生的完整端到端验收也不由模拟测试代替。来源分支历史 Windows 验证不计为本次验证。
