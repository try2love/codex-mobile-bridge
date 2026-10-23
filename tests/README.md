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
| 账号、额度、模型 | `test_account*`、`test_api_catalog*`、`test_saved_account*` |
| 客户端发现/启停 | `test_client_*`、`test_desktop_discovery*`、`test_desktop_idle*` |
| Claude/DSH | `test_claude_*`、`test_deepseek_*`、`test_desktop_sessions*` |
| 认证/配对/网络 | `test_auth*`、`test_pairing*`、`test_login_security*`、`test_shared_relay*`、`test_network*` |
| 会话与发送/授权 | `test_bridge*`、`test_cli_bridge_integration.py`、`test_codex_app_server.py`、`test_codex_cli_session.py`、`test_desktop_*`、`test_goal_*`、`test_permissions*`、`test_timeline*` |
| 文件/终端 | `test_workspace*`、`test_download_ranges*`、`test_*terminal*` |
| 目录与资源边界 | `test_platform_architecture.py`、`test_resource_layout.py`、`test_web_assets.py` |

测试名仍保持在 `tests/` 顶层，方便按前缀发现并复用既有夹具。平台测试的模拟结果不能替代对应操作系统的实机结果。

Windows 打包时会校验并携带微软 ConPTY 与配套 OpenConsole，以支持 Windows 10 的终端鼠标/触摸事件；不替换系统组件。源码验收可先运行 `python scripts/bundle-conpty.py`，再运行 `python -B -m unittest discover -s tests -p test_windows_terminal.py -v`。未准备组件的源码环境沿用系统 ConPTY，鼠标专项用例会明确跳过。指定 `CMB_TEST_RUNTIME` 为打包网关 EXE 后，同组测试会加载它旁边 `_internal/conpty` 的组件，验证鼠标模式、点击坐标、VT 输入与中文，以及原有目录、尺寸、Ctrl+C、重连和关闭行为。

## Node 与浏览器

```sh
npm run test:desktop
npm run test:updater
```

`test:desktop` 通过 `scripts/test-frontend.cjs` 自动递归发现 `tests/` 和 `mobile/tests/` 中 `*.test.js`、`*.test.cjs`、`*.test.mjs`。新增 Node 用例不需编辑长命令清单；资源路径检查包含在该入口。

宿主与宽窄布局变更可先运行 `node --test tests/host-environment.test.cjs tests/layouts.test.cjs tests/frontend-assets.test.cjs mobile/tests/resources.test.cjs`。这些检查覆盖宿主识别与晚注入、容器分屏边界、样式顺序和原生资源清单，不启动或编译手机 App。布局的真实渲染和窗口变化后状态保留仍需浏览器夹具验证。

[浏览器夹具](browser/README.md) 位于 `tests/browser/*.browser.js`，依赖真实 DOM 或隔离服务，应由相应驱动调用。其中有些文件只定义测试函数，单独加载不会执行断言。本轮将它们与 Node 分开，避免虚增通过计数。

`scripts/smoke-*` 中部分入口启动真实应用或依赖打包产物；运行前先看其头部与 fixture，不把它们作为无副作用的常规单元测试。

`node scripts/smoke-wsl-app-ui.cjs` 在独立 Edge 配置目录中加载真实 Web 页面和合成 API，实际执行 `tests/browser/codex-entries.browser.js`，检查独立应用管理、列表隔离、Magpie 模型名称、草稿保留，以及 WSL 手机新建独立聊天、目录浏览器、选择父目录新建文件夹、新建项目和显式翻页。默认使用 Windows Edge，也可用 `CMB_BROWSER` 指定 Chromium 可执行文件；只启动自己的浏览器进程，截图和结果放在 `.tmp/`，不连接真实网关或 Codex。原生共享服务的隔离验证仍由 `scripts/smoke-wsl-shared-server.py --codex <明确的 Linux 运行时>` 单独执行。

## 手机与打包

`mobile/tests/` 中 Java/Swift 用例由相应 `scripts/test-mobile-*`、`scripts/test-ios-*` 驱动，可能编译测试程序；只在本次任务允许且环境具备时执行。Web 资源验证不代替 Android WebView 或 iOS WKWebView 的原生交互验证。

`scripts/gateway-resources.json` 与源路径测试保证清单一致，但冻结导入、签名、安装和更新必须在获准打包后、目标系统上另外验证。不要根据 macOS 单机测试宣称 Windows/Linux 已实机通过。
