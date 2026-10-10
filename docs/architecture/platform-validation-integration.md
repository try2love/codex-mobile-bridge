# Windows / Linux 验证分支整合

## 来源与本地回退点

- 整合前：`refactor/platform-feature-layout`，`9830cd208ad3c18b875ad7ccc34c3c4d596cce3c`，工作区干净。
- 本地备份：`backup/pre-platform-integration-20261010-9830cd2`。
- Linux：`github/refactor/platform-feature-layout`，`7d390f4c5e73e3cdd0286ee77a3ea79dcca2439a`，1 个提交。
- Windows：`github/codex/windows-platform-validation`，`aab5844216e7c146419237d3c3b664a85b410082`，4 个提交。

两个来源均从整合前提交继续开发。Linux 先快进，Windows 再保留原提交历史合并；来源共修改 25 个文件，没有重叠文件或文本冲突。未改动 Tailscale、自有服务器 HTTPS/IP 配置、认证配对、通知、原生客户端启停和移动端原生源码。

## 修改入口与边界

| 功能 | 维护位置与行为 |
| --- | --- |
| Windows 工作区访问 | `bridge/features/workspace/workspace.py` 拒绝 junction/reparse point，并阻止大小写和尾随点/空格形式的 `.git` 上传；普通文件与 Unicode 路径仍走既有流程。该模块也通过 SSH 独立执行，保留自包含实现。 |
| 本机 Linux 权限 | `bridge/features/sessions/linux_permissions.py` 读取运行时能力、工作区策略和桌面偏好；读取不到时禁用相关选项，提交前重新校验，不通过修改线程来试探权限。 |
| Linux 侧边聊天 | `bridge/features/sessions/linux_side_chat.py` 等待对应线程的权限变更通知；空 RPC 回执不视为已应用，不更新其他线程。 |
| 共享接口与 Web | `bridge/app/service.py`、`bridge/api/httpd.py` 和 `web/features/chat/permissions.js` 接入权限选项。只有本机 Linux 启用新流程；macOS、Windows 与 SSH 保留原有行为。认证、同源和 CSRF 边界保留。 |
| Linux 构建下载 | `scripts/bundle-cloudflared.py` 在读取许可文件遇到网络错误时，使用同仓库、同版本的 GitHub API；保留 HTTPS 校验及缺少 NOTICE 的既有处理。未在本轮执行构建或下载。 |
| 测试兼容性 | 更新跨系统路径夹具、网页脚本顺序与 reparse 模拟；Windows 启动测试使用独立命名管道及不存在的运行时路径，避免连接真实桌面。 |

平台策略仍归功能层，原生实现仍归 `bridge/platforms/`；没有复制三套公共业务或恢复旧的平铺目录。

## 验证

本地使用 macOS、Python 3.9.6 和 Node v25.9.0 运行源码测试，复用现有依赖环境：

| 检查 | 结果 |
| --- | --- |
| `python -B scripts/test-backend.py --source-only` | 1,453 项：1,428 通过，25 跳过，0 失败/错误 |
| `npm run test:desktop` | 482 项：481 通过，1 跳过，0 失败 |
| 整合修复定向回归 | 18 项全部通过 |

Python 跳过项包括 11 项原生编译/执行夹具、13 项 Windows 专属检查及 1 项需显式启用的 POSIX 进程退出检查；Node 跳过项为 Windows 专属检查。最初沙箱阻止本地监听，改用允许隔离回环端口的环境后完成上述回归。

复核发现并修正两处衔接问题：

- Linux 来源仅在普通会话接口加入权限标记，实际聊天页的渐进时间线接口未携带。新增回归先复现失败，再补齐首屏、翻页与增量元数据；macOS、Windows 和 SSH 的权限语义不变，被动读取不会探测能力或激活会话。
- 原有中继测试仍要求已移除的桌面 `shared-relay` 注册入口可用。确认该测试在整合前即已过时，调整为验证入口被拒绝且不创建配置，同时保留独立中继 CLI 的测试。没有恢复邀请码界面或注册入口。

额外 10 项 Windows 边界模拟通过，覆盖 reparse 属性识别、无法读取属性时拒绝操作、新文件及 `.git` 名称变体。这些模拟不替代 NTFS 实际 junction 测试。

本轮不构建 App/APK/IPA/DMG，不执行原生编译夹具，不替换运行网关、不启动或关闭真实客户端，不推送、不更新版本号、不打标签或发布。用户提供的 Windows/Linux 实测结果作为来源背景；整合后的两套系统与 iOS/Android 真机运行仍需另外验证。
