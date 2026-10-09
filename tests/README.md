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

`--source-only` 明确跳过两个执行 Swift 的夹具。省略该选项与现有完整 unittest discovery 等价：在具备条件的 macOS 上会运行原生助手编译/解释测试。Windows、可选依赖和原生 SDK 的跳过项会在结果中列出。现有 CI 保留各平台完整 discovery。

HTTP、中继、IPC、mailbox 等测试使用本机隔离监听和合成数据，需要允许测试进程绑定 loopback/Unix socket。受限执行环境的 EPERM 不是功能通过，也不能隐藏为产品跳过。共享中继测试需要现有 `requirements-relay.txt` 环境；测试脚本自身不安装依赖。

| 修改范围 | 主要测试文件前缀 |
| --- | --- |
| 账号、额度、模型 | `test_account*`、`test_api_catalog*`、`test_saved_account*` |
| 客户端发现/启停 | `test_client_*`、`test_desktop_discovery*`、`test_desktop_idle*` |
| Claude/DSH | `test_claude_*`、`test_deepseek_*`、`test_desktop_sessions*` |
| 认证/配对/网络 | `test_auth*`、`test_pairing*`、`test_login_security*`、`test_shared_relay*`、`test_network*` |
| 会话与发送/授权 | `test_bridge*`、`test_desktop_*`、`test_goal_*`、`test_permissions*`、`test_timeline*` |
| 文件/终端 | `test_workspace*`、`test_download_ranges*`、`test_*terminal*` |
| 目录与资源边界 | `test_platform_architecture.py`、`test_resource_layout.py`、`test_web_assets.py` |

测试名仍保持在 `tests/` 顶层，方便按前缀发现并复用既有夹具。平台测试的模拟结果不能替代对应操作系统的实机结果。

## Node 与浏览器

```sh
npm run test:desktop
npm run test:updater
```

`test:desktop` 通过 `scripts/test-frontend.cjs` 自动递归发现 `tests/` 和 `mobile/tests/` 中 `*.test.js`、`*.test.cjs`、`*.test.mjs`。新增 Node 用例不需编辑长命令清单；资源路径检查包含在该入口。

宿主与宽窄布局变更可先运行 `node --test tests/host-environment.test.cjs tests/layouts.test.cjs tests/frontend-assets.test.cjs mobile/tests/resources.test.cjs`。这些检查覆盖宿主识别与晚注入、容器分屏边界、样式顺序和原生资源清单，不启动或编译手机 App。布局的真实渲染和窗口变化后状态保留仍需浏览器夹具验证。

[浏览器夹具](browser/README.md) 位于 `tests/browser/*.browser.js`，依赖真实 DOM 或隔离服务，应由相应驱动调用。其中有些文件只定义测试函数，单独加载不会执行断言。本轮将它们与 Node 分开，避免虚增通过计数。

`scripts/smoke-*` 中部分入口启动真实应用或依赖打包产物；运行前先看其头部与 fixture，不把它们作为无副作用的常规单元测试。

## 手机与打包

`mobile/tests/` 中 Java/Swift 用例由相应 `scripts/test-mobile-*`、`scripts/test-ios-*` 驱动，可能编译测试程序；只在本次任务允许且环境具备时执行。Web 资源验证不代替 Android WebView 或 iOS WKWebView 的原生交互验证。

`scripts/gateway-resources.json` 与源路径测试保证清单一致，但冻结导入、签名、安装和更新必须在获准打包后、目标系统上另外验证。不要根据 macOS 单机测试宣称 Windows/Linux 已实机通过。
