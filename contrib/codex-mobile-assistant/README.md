# Codex Mobile Assistant · 独立扩展贡献

这是基于 Codex Mobile Bridge 早期版本扩展的完整源码快照，放在 `contrib/codex-mobile-assistant/` 中供上游评审。它使用自己的 Windows 入口、网关、网页和手机 App，尚未接入仓库根目录的 v2 多客户端架构。贡献背景和模块对应关系见 [UPSTREAM.md](UPSTREAM.md)。

本目录保留 Windows **1.6.27**、Android **1.0.24**（25）和 iOS **1.0.29**（30）的版本标识，以及此后本地网关和网页的源码改进。这些标识不是上游发布版本，也不代表本次已构建或验证。

## 包含的功能

- Windows 双模式图形入口：Cloudflare 临时 HTTPS、自有域名与 SSH 反向隧道、二维码、托盘、可选开机启动，以及用 Windows DPAPI 保存 SSH 密码。
- Codex 会话同步、临时对话、项目浏览、聊天管理、消息排队与停止、模型和 Skill 选择、权限与提问卡片。
- 网页消息搜索、复制和编辑重发、可折叠思考摘要、消息时间、图片缩放、图片草稿与文件上传下载；下载支持 Range 续传，兼容部分旧版 Android WebView 接口。
- 挂载子项目：保存同主机项目关联，选择范围后生成只读检查草稿，由用户发送给当前会话。
- Android / iOS 原生连接页、扫码、记住入口与登录、原生下载和文件分享。Android 包含任务提醒服务。
- iOS 包含面向 TrollStore 的实验性独立 TaskMonitor、任务提醒去重和前台网页恢复；权限、构建和限制见 [TaskMonitor 文档](mobile/ios/TaskMonitor/README.md)。

## 从源码使用

以下命令都从本扩展目录执行：

```powershell
cd contrib/codex-mobile-assistant
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/fetch_cloudflared.py
.\.venv\Scripts\python.exe 固定域名入口.pyw
```

打开同一 Windows 用户下的 Codex Desktop，选择临时访问或填写自己的固定域名和服务器。固定域名配置见 [部署说明](docs/self-hosting.md)。`fetch_cloudflared.py` 下载固定版本并校验 SHA-256，文件保存在本目录的 `vendor/bin/`。

仅运行网关时，可使用 `python -B app/run.py --lan`。网关使用 Python 标准库；Python 图形入口另需 `requirements.txt` 中的依赖。此快照通过 Codex Desktop 的内部 IPC 接入，桌面版本变化可能影响兼容性。

运行数据、登录记录和 SSH 配置写入本扩展的 `app/.local/`。默认网关端口为 8787；请避免与上游网关在同一端口同时启动。手机 App 应连接此扩展提供的入口，尚未验证与根目录 v2 网关互通。

## 构建入口

Windows 使用以下命令，输出本目录的 `dist/CodexMobileAssistant.exe`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -File scripts/build_windows.ps1
```

Android 与 iOS 的工程、工具链和打包命令见 [手机 App 文档](docs/mobile.md)。手机工作流以 [模板](scripts/mobile-packages.workflow.yml) 形式保留，不会自动加入上游 Actions。它的路径假定本扩展被单独放在一个仓库根目录。

## 目录

| 路径 | 内容 |
| --- | --- |
| `固定域名入口.pyw`、`app/desktop_support.py` | Windows 图形入口与桌面支持 |
| `app/bridge/`、`app/run.py` | 此扩展独立的 Codex 网关 |
| `app/web/` | 配套网页和本地前端依赖 |
| `mobile/android/`、`mobile/ios/` | 原生手机容器与提醒实现 |
| `scripts/`、`固定域名入口.spec` | 依赖下载、资源生成与打包入口 |
| `app/tests/` | 随原实现保留的测试材料，本次未运行 |

## 贡献与验证范围

本次仅整理源码、移除个人服务器默认值并调整便携构建路径；未运行测试、构建、网关、浏览器或真机验收。独立扩展未接入上游测试与发布流程。已有历史验证记录不代表此快照已通过验证。

MIT 许可证和上游署名保留于 [LICENSE](LICENSE)、[app/LICENSE](app/LICENSE)，第三方依赖见 [NOTICE.md](NOTICE.md)。不包含安装包、运行数据、个人服务器配置或模型凭据。
