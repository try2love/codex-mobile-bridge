# Agent Note: 终端退出后继续保存的 CLI 聊天

Status: implemented

CLI 再继续直接使用原生 daemon 的现行规则见[来源执行路由](../bug-fix/2026-11-11-codex-source-execution-routing.md)；下述旧 owner 路由说明保留为历史决定，不再用于 CLI 来源。

模型、Skill、计划和本地 Goal 限制由 [CLI 会话控制](2026-10-14-cli-session-controls.md)替代；权限限制由 [CLI 权限切换](2026-10-14-cli-permission-switching.md)替代。编辑/分叉、重命名和旁支聊天限制仍有效。

下述按来源排除 Desktop 的规则由[现有执行归属路由](../bug-fix/2026-10-14-cli-existing-owner.md)部分替代：CLI 来源描述起源，已活跃的 Desktop 归属进程会被复用，不创建替代运行时。

## 问题

[CLI 接入](2026-10-14-codex-cli-access.md)中仅连接已加载线程的策略，使终端退出且原生运行时卸载线程后，可见聊天无法继续使用。用户明确要求无需先重开电脑终端，即可从手机继续。已在真实保存的 CLI 0.162.1 线程上复现旧错误，未改变来源元数据。

## 决策

显式“继续聊天”连接原原生守护进程，恢复同一线程 ID。守护进程停止时，网关调用已安装且选定的受管二进制执行 `app-server daemon start`，再通过官方代理连接。原生生命周期锁处理并发启动；网关串行化同线程附加。本地/SSH 包选择遵循检查过的 0.162.1 生命周期。PATH 和惯用 local-bin 路径均无 CLI 时，SSH 代理可使用受管二进制。

本地代理优先使用选定受管包，而非 PATH 上的另一 CLI，使包存在时代理和守护进程使用同一安装。账号切换保护在选定 `CODEX_HOME` 下，通过原生 `app-server daemon version` 命令只读查询状态；套接字和 PID 证据都不存在才判定停止，任何探测失败或无法识别的响应均保持未知并阻止切换。Windows 运行代理和生命周期探测时不打开控制台窗口。Linux、macOS、Windows 原生控制端点仍为上游 AF_UNIX 套接字，不引入命名管道协议。

被动历史/列表读取绝不启动守护进程或恢复线程。冷恢复针对目标运行时校验保存的 CLI 来源、provider 和 cwd，再复核恢复结果。不提供模型、provider、认证、cwd、信任或权限覆盖。网关不安装、引导安装、更新或重启运行时；原生守护进程保留既有配置，包括自身更新偏好。关闭查看器/网关只关闭代理。未知写入不重放。未安装兼容守护进程的配置仍可读，并说明前提。

SSH 故障提示的关闭状态限定于当前网关及精确主机故障，直到恢复、故障变化或整页重载。连接诊断位于设置，提供可读汇总状态及可复制摘要。原生导出受能力限制，只允许已认证同网关诊断端点及可选 UUID 查询；中继采用同样狭窄的 GET 规则。动态报告不支持部分下载续传。

## 备选方案

保留仅已加载接入可缩小生命周期范围，却不能满足手机流程，并产生了报告中的错误。启动独立 `app-server --listen` 或以 Desktop 回退可执行聊天，却不参与原生共享守护进程生命周期。调用原生引导安装能补齐缺失包，却会静默安装软件；因此实现要求已有受管包。

## 既有笔记审计

原 CLI 接入笔记被部分替代并双向链接，来源发现和未知写入隔离仍适用。PDF/HTML、通知设置笔记与 CLI 生命周期无关。文件传输设置读取笔记管理另一设置面板，保持不变。检查时不存在 proposed 或 rejected 笔记目录。

## 影响

真实 Web 验收还发现，完成事件遗漏了仍存在于权威 thread/read 的用户项。完成投影只保留此前已知、按 ID 去重的用户消息；其他项仍跟随完成载荷。修正前回归复现了消息气泡消失。

原终端与手机继续操作共享同一保存历史及原生守护进程。真实 macOS CLI 0.162.1 测试覆盖终端退出且线程已卸载、守护进程完全停止、发送/流式输出/幂等，以及重新打开 TUI 后在同一守护进程 PID 下读到两次回复。测试信任和凭据隔离于项目 .tmp，日常信任不变。provider/cwd 检查发生在操作前后，不是上游原子身份保证。[上游守护进程契约](https://github.com/openai/codex/blob/main/codex-rs/app-server-daemon/README.md)记录 Linux、macOS、Windows 支持、JSON 生命周期输出及 Windows 规范 AF_UNIX 地址 108 字节限制。本地适配器测试覆盖状态保护和选定包优先；Windows 执行及任意远程主机仍需原生验收。构建和 UI 范围见[验证记录](../../../../VERIFICATION.md)。

Linux CLI 0.160.0 在 SSH 主机上使用隔离 `CODEX_HOME`、指向该主机已安装可执行文件的符号链接、禁用更新器、合成凭据及回环 Responses 夹具测试。本地 `CliSessions` SSH 路径先被动保留保存聊天，在原生客户端关闭后显式加入，以有效 reviewer 更新切换自动审核权限，完成下一轮，创建 CLI 聊天、设置/清除 Goal，再重启隔离守护进程并冷恢复同一线程。实际 `RemoteStore` 源读取器在隔离主目录发现保存的 CLI 来源/provider/cwd。各次夹具运行中，10 次合成模型 POST 全到达回环端点，28 次无关外部 CONNECT 被拒绝。验证后停止守护进程和模拟监听器。夹具及结果见 [Linux 原生验收](../../../../tests/acceptance/codex_cli_linux_native.py)与[回环服务](../../../../tests/acceptance/codex_cli_linux_mock_server.py)。因修改用户登录配置超出范围，测试向 SSH 命令注入 `CODEX_HOME`；这证明单一主机上的传输/生命周期，不证明任意登录 shell 配置或真实终端 UI。CLI 0.160.0 将 Bridge 创建的线程保存为 `codex-tui` originator、`vscode` source，现有 CLI 来源分类器接受此组合。

所测原生 CLI 存活时，`daemon version` 返回 `status=running` 的 JSON；停止时返回非零错误。探测失败但仍留有套接字或 PID 文件时，状态未知并阻止账号切换；仅两者均不存在才视为停止。恢复需检查选定守护进程状态，确认无匹配进程后才移除过期材料。

Windows 验收将已安装 MSIX `codex.exe` 0.162.0-alpha.17.2 按字节复制到隔离受管包夹具 `<test-root>/h/packages/app-server-daemon/current/bin`；这是合成包布局，不证明仅 MSIX 安装会在用户正常主目录提供受管 CLI。首次隔离 `app-server daemon start` 被其明确的非管理员保护拒绝，因为 SSH 使用提升权限令牌。因此以普通令牌运行的[启动器](../../../../tests/acceptance/codex_cli_windows_native.cmd)和[夹具](../../../../tests/acceptance/codex_cli_windows_native.py)从用户普通终端启动，使用合成凭据及回环 Responses API。静态检查确认两份 Python 源码可解析、源码 ZIP CRC 通过、暂存 CLI 哈希与已安装副本一致。完成下述两处修正后，原生 Windows 运行通过继续、发送、生效权限修改、新聊天、Goal 设置/清除及同线程冷恢复。三次模型 POST 全部使用回环端点，十次无关外部 CONNECT 被拒绝。夹具随后停止隔离守护进程。

交互 Windows 会话中已核验同用户非提升 Explorer 令牌，但 CreateProcessAsUserW 在启动夹具前返回 ERROR_PRIVILEGE_NOT_HELD (1314)。重新评估后确认 CLI 不需要 GUI 会话。CreateProcessWithTokenW 随后在会话 0 中使用同一已核验非提升令牌尝试精确隔离启动器，但创建子进程前返回拒绝访问 (5)。两次尝试均未修改权限、计划任务、服务或桌面 ACL。随后成功验收由用户在普通终端启动。

首次普通用户 Windows 夹具暴露设置问题：守护进程状态目录继承了管理员所有的 ACL。原生 Codex 要求目录归用户所有，并使用仅含该用户可继承完全控制 ACE 的受保护 DACL。夹具现在安全创建缺失私有目录，拒绝修复已存在非私有目录。创建新私有目录前保留旧隔离目录；日常 Codex 状态和 Windows 安全设置未改变。

下一次原生运行通过热继续、发送、生效权限修改、新 CLI 聊天及 Goal 设置/清除，但冷恢复暴露产品问题。Windows SQLite 将 cwd 保存为 `\\?\C:\...`，运行时返回 `C:\...`。只读原生 `os.path.samefile` 确认它们指向同一目录。冷恢复现在仅在本地 Windows 路径不同的情况下使用实际文件身份；不可访问或不同目录仍拒绝，SSH/POSIX 维持严格字符串比较。来源、provider、线程 ID 及恢复后检查不变。回归覆盖等价、不同和不可验证 Windows 目录及未改变的 SSH/POSIX 行为；55 项相关 CLI/app-server 测试通过。该预检不提供跨上游恢复调用的原子身份保证。

## 主动释放 CLI 连接

[CLI 连接释放](2026-11-01-cli-connection-release.md)复用原会话身份与显式激活规则，提供单条和批量断开。该操作不归档聊天、不停止共享运行时；Codex 取消订阅后的原生占用限制由该记录说明，本篇其他规则不变。
