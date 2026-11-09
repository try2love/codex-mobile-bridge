# 验证入口

测试应匹配改动的功能和平台。源码测试不等于已经编译、安装或在三个系统上完成真实客户端联调。

## Python 后端

从仓库根目录，使用已有项目环境运行：

```sh
python -B scripts/test-backend.py --source-only
python -B scripts/test-backend.py --source-only -p 'test_desktop_discovery.py'
python -B scripts/test-backend.py --source-only -p 'test_platform_architecture.py'
python -B scripts/test-backend.py --source-only -p 'test_resource_layout.py'
```

`--source-only` 明确跳过六个原生夹具类：Claude 的 Swift Console 选择器、macOS 桌面会话检查、Windows 窗口选择器，以及 Windows Codex 退出、强制退出和 Explorer 启动器验证。这些夹具可能编译或执行原生程序；纯 Python 的平台模拟与安全检查仍运行。省略该选项与完整 unittest discovery 等价，会按当前系统和环境运行原生测试。平台、可选依赖和原生 SDK 的跳过项会在结果中列出。现有 CI 保留各平台完整 discovery。

HTTP、中继、IPC、mailbox 等测试使用本机隔离监听和合成数据，需要允许测试进程绑定 loopback/Unix socket。受限执行环境的 EPERM 不是功能通过，也不能隐藏为产品跳过。共享中继测试需要现有 `requirements-relay.txt` 环境；测试脚本自身不安装依赖。

| 修改范围 | 主要测试文件前缀 |
| --- | --- |
| 账号、额度、模型 | `test_account*`、`test_api_catalog*`、`test_saved_account*`、`test_remote_catalog.py` |
| 客户端发现/启停 | `test_client_*`、`test_desktop_discovery*`、`test_desktop_idle*` |
| Claude/DSH | `test_claude_*`、`test_deepseek_*`、`test_desktop_sessions*` |
| 认证/配对/网络 | `test_auth*`、`test_pairing*`、`test_login_security*`、`test_shared_relay*`、`test_network*`、`test_http_proxy.py` |
| 会话与发送/授权 | `test_bridge*`、`test_cli_bridge_integration.py`、`test_codex_app_server.py`、`test_codex_cli_session.py`、`test_desktop_*`、`test_goal_*`、`test_permissions*`、`test_timeline*` |
| 文件/终端 | `test_workspace*`、`test_download_ranges*`、`test_*terminal*` |
| 目录与资源边界 | `test_platform_architecture.py`、`test_resource_layout.py`、`test_web_assets.py` |

测试名仍保持在 `tests/` 顶层，方便按前缀发现并复用既有夹具。平台测试的模拟结果不能替代对应操作系统的实机结果。

Windows 打包时会校验并携带微软 ConPTY 与配套 OpenConsole，以支持 Windows 10 的终端鼠标/触摸事件；不替换系统组件。源码验收可先运行 `python scripts/bundle-conpty.py`，再运行 `python -B -m unittest discover -s tests -p test_windows_terminal.py -v`。未准备组件的源码环境沿用系统 ConPTY，鼠标专项用例会明确跳过。指定 `CMB_TEST_RUNTIME` 为打包网关 EXE 后，同组测试会加载它旁边 `_internal/conpty` 的组件，验证鼠标模式、点击坐标、VT 输入与中文，以及原有目录、尺寸、Ctrl+C、重连和关闭行为。

## Node 与浏览器

```sh
npm run test:desktop
npm run test:updater
npm run test:boundaries
npm run test:browser
```

`test:desktop` 通过 `scripts/test-frontend.cjs` 自动递归发现 `tests/` 和 `mobile/tests/` 中 `*.test.js`、`*.test.cjs`、`*.test.mjs`。新增 Node 用例不需编辑长命令清单；资源路径检查包含在该入口。

宿主与宽窄布局变更可先运行 `node --test tests/host-environment.test.cjs tests/layouts.test.cjs tests/frontend-assets.test.cjs mobile/tests/resources.test.cjs`。这些检查覆盖宿主识别与晚注入、容器分屏边界、样式顺序和原生资源清单，不启动或编译手机 App。布局的真实渲染和窗口变化后状态保留仍需浏览器夹具验证。

[浏览器夹具](browser/README.md) 位于 `tests/browser/*.browser.js`，依赖真实 DOM 或隔离服务，应由相应驱动调用。其中有些文件只定义测试函数，单独加载不会执行断言。本轮将它们与 Node 分开，避免虚增通过计数。

`test:browser` 自动调用启动恢复、聊天历史、草稿隔离、API 表单、输入引用及 Claude/DSH 项目弹窗检查，包含 Chromium/WebKit、1280 px/390 px 四种组合。发送反馈与模型设置回归还覆盖 Claude Code/Cowork、DSH、慢响应、手动输入和真实聊天背景的明暗主题。首次安装开发依赖后运行 `PLAYWRIGHT_BROWSERS_PATH=.local/tools/playwright npx playwright install chromium webkit`；Linux CI 加 `--with-deps`。Windows 可用 PowerShell 设置同名环境变量；配置默认也使用项目内的该目录。Chromium 官方下载暂不可用时，可用本机已有 Chrome：`CMB_CHROMIUM_CHANNEL=chrome npm run test:browser`，报告中注明浏览器来源；CI 默认使用锁定版本运行时。

服务固定绑定隔离 loopback 端口 18279，拒绝复用已占用端口；浏览器请求不访问外部 origin。输出位于 `.tmp/browser-results/`，失败时包含截图和 trace。合成页面检查不替代真实登录后端、客户端协议或手机 WebView 验收。

纳入 [PR #88](https://github.com/try2love/codex-mobile-bridge/pull/88) 后，WebKit 连续资源失败恢复必须通过完整断言，不再保留 `known-baseline` 预期失败豁免。常规检查还覆盖无来源错误、非启动依赖脚本的异常和 Promise 拒绝、可缓存的错误脚本响应；真实必需脚本错误仍须停止依赖执行。详细边界见[启动错误归属记录](../.agents/notes/implemented/bug-fix/2026-10-27-startup-error-attribution-and-cache-retry.md)。

`test_module_boundaries.py` 自动运行 AST 依赖检查，也可用 `python3 -B scripts/check-boundaries.py` 单独运行（Windows 可用 `python`）。[历史例外](../docs/architecture/dependency-exceptions.json) 精确到文件和符号，禁止通配放行；移除旧依赖必须删除对应例外。检查不执行应用，无法推断变量拼接的动态 import；原有平台选择、资源路径及共享脚本测试继续保留。

`scripts/smoke-*` 中部分入口启动真实应用或依赖打包产物；运行前先看其头部与 fixture，不把它们作为无副作用的常规单元测试。

原生侧边聊天使用 `test_native_side_chat.py`、`test_store.py` 和浏览器的 `native-side-chat.spec.cjs` 验证持久化创建、父子归属、标准聊天路由与关闭视图不删除。旧 `test_side_chat.py` 中保留临时运行时回归，仅保护升级前已打开的旧页面；新增侧边聊天不再使用该运行时。独立侧边输入区和附件 UI 夹具已移除，现复用标准会话的相应用例。

`node scripts/smoke-wsl-app-ui.cjs` 在独立 Edge 配置目录中加载真实 Web 页面和合成 API，实际执行 `tests/browser/codex-entries.browser.js`，检查独立应用管理、列表隔离、Magpie 模型名称、草稿保留，以及 WSL 手机新建独立聊天、目录浏览器、选择父目录新建文件夹、新建项目和显式翻页。默认使用 Windows Edge，也可用 `CMB_BROWSER` 指定 Chromium 可执行文件；只启动自己的浏览器进程，截图和结果放在 `.tmp/`，不连接真实网关或 Codex。原生共享服务的隔离验证仍由 `scripts/smoke-wsl-shared-server.py --codex <明确的 Linux 运行时>` 单独执行。

## 手机与打包

`mobile/tests/` 中 Java/Swift 用例由相应 `scripts/test-mobile-*`、`scripts/test-ios-*` 驱动，可能编译测试程序；只在本次任务允许且环境具备时执行。Web 资源验证不代替 Android WebView 或 iOS WKWebView 的原生交互验证。

`scripts/gateway-resources.json` 与源路径测试保证清单一致，但冻结导入、签名、安装和更新必须在获准打包后、目标系统上另外验证。不要根据 macOS 单机测试宣称 Windows/Linux 已实机通过。

PR 的 `change-scope` 使用 `scripts/ci-test-scope.py` 分别选择后端、UI 资源、Node、浏览器、Android、iOS 和移动更新检查；纯 CSS 不运行后端、桌面逻辑或原生编译。未知路径、依赖、工作流与公共清单保守全量。`desktop-tests` 只负责显式汇总所选结果，不将失败、取消或意外跳过当作通过。主分支推送、版本标签与手动触发保持完整检查；普通分支推送由 PR 事件覆盖，避免重复全量。Android 可选 FCM 配置及通知真机行为仍单独验收。

Android 后台通知定向验证：`python scripts/test-mobile-events.py` 通过真实 JSON 和回环 HTTP 覆盖增量读取、服务重建续收、事件去重、流重置、登录/删除竞态、多电脑隔离与暂停，以及四地址来源选择、禁用时在途响应、重新开启的历史基线；首次运行下载并校验固定版本的 JVM JSON 测试依赖。`python scripts/test-mobile-notification-permissions.py --android-only` 验证 Android 通知权限、手动后台开关与启动失败回退。两者读取 `JAVA_HOME`。系统服务、锁屏通知、点击跳转与电池行为在 Android 设备或模拟器上另行验收。

`python scripts/test-android-task-notifications.py` 复用已准备的 JVM JSON 依赖，编译生产通知呈现器并用系统服务替身验证不同事件独立保留、重试去重、点击路由及清理边界；不代表 Android 厂商通知分组或真机点击体验已通过。

`python scripts/test-mobile-urls.py --android-only` 编译并执行 Android URL 与通知页面复用的边界验证；完整双平台入口还需 macOS 的 Swift 工具链。

iOS 通知回归在 macOS 上运行 `scripts/test-ios-events.py`、`scripts/test-ios-notification-routing.py`、`scripts/test-ios-notification-source.py` 和 `scripts/test-ios-notification-sources.py`，并进入现有移动端 CI 作业。分别覆盖真实 HTTP 的多电脑隔离与生命周期、场景与通知点击去重、来源图标文件在提交通知时仍可读，以及按地址选择提醒、在途回调失效和重新开启的基线。后两项的通知系统类型使用替身，系统授权与实际送达仍需 iPhone 验收。

Android 设置页资源合约：`node --test mobile/tests/settings.test.cjs mobile/tests/resources.test.cjs` 检查浅深色资源对齐、文字对比度及两种宿主的配置声明。原生交互另外验收整行与开关点击、后台服务启停、权限返回刷新、设置页主题作用域、主题持久化与跟随系统、提示吐司、翻译、返回原聊天，以及窄屏和大字体布局。

Android 原生电脑选项菜单：`python3 scripts/test-android-gateway-menu.py --adb <已有adb路径> --serial emulator-5556 --apk <本轮Test包>`。仅对明确可丢弃的模拟器运行，会清空该模拟器中 Test 的偏好、保存合成连接并授予通知权限；拒绝真机序列号。使用生产 Activity/WebView 检查页面颜色、原生主题回退、系统和逐级返回、晚回调取消、导航与生命周期清理、草稿保留以及通知来源偏好。结果位于 `.tmp/android-gateway-menu-tests/`，不等于真机或真实客户端会话验收。

设置页动效另外检查进入和返回、开关与权限布局、主题弹层及浅深色交叉淡化的中间帧；同时覆盖快速反向操作、系统动画时长为零时的直接呈现，以及关闭窗口后的动画和临时画面清理。
