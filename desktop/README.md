# 桌面网关界面

`main.cjs`、`preload.cjs` 与 `index.html` 保持为 Electron 入口。主进程管理原生窗口和 IPC，预加载脚本保留现有隔离边界；页面脚本通过 `window.bridgeDesktop` 调用网关。

| 目录 | 职责与入口 |
| --- | --- |
| `shell/` | `renderer.js` 绑定设置和状态，`layout.js` 组织页面模块；样式与托盘行为同属应用外壳。 |
| `features/accounts/` | 预览环境的已有账号导入。账号界面复用 `web/features/accounts/`。 |
| `features/clients/` | 本机客户端发现、接入设置及 Harness 官方 Web 入口。 |
| `features/connections/` | 网络接口、访问入口、Cloudflare、共享中继与手机配对。 |
| `features/notifications/` | 会话通知订阅管理。 |
| `features/updates/` | 下载、校验与安装更新；可信公钥与更新器一起维护。 |
| `shared/` | Python 控制器通信、语言同步、原生翻译和敏感字段组件。 |
| `assets/` | 桌面图标。 |

`index.html` 使用相对文件路径，并直接引用 Web 的翻译和账号模块。增加共享资源时，同时检查根 `package.json` 的 `build.files`；移动 CommonJS 模块时，更新 `main.cjs` 的引用及相应测试夹具。

`npm run test:desktop` 自动发现 `tests/` 和 `mobile/tests/` 下全部 Node 测试，检查页面、控制器与资源引用；`npm run test:updater` 可单独检查更新器和进程交接。平台构建方式见 `docs/linux.md` 与根构建文档。
