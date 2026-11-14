# Agent Note: 客户端升级按原生契约判断兼容能力

Status: implemented

## 问题

桌面流升级触发 PR169 后，同类审计发现 Codex CLI 把正常排空、失败终态释放和账号同步限定在少数精确发行版。协议未变的 0.165、0.168 等版本也被拒绝。版本白名单不能替代每次操作已有的任务和进程核验。

## 决策

Codex CLI 共用既有 graceful_restart_supported 入口，以已验证契约的引入下限判断候选：POSIX 0.164.0，Windows 0.167.0-alpha.3.2。兼容的新小版本或稳定版不需要重新登记。无法识别的原生握手、过旧版本和不支持的平台仍不获得能力。版本只用于候选判断，不能授权退出：既有 home、映像、进程代次、socket peer、实时任务/审批/后台终端、Windows shutdown 原生标记与通道核验及派发回执全部保留。

Linux 平台 worker 只核验系统身份、内核能力及原生版本元数据是否存在，不再重复维护业务版本表；正常退出的协议资格由共享客户端 LinuxCliControl 判断，实际派发再次校验同一 generation。未知版本仍只保留已经明确授权的强制退出路径，不将其等同于正常排空能力。

退出继续走单次 SIGHUP 或已验证 Windows 私有关闭通道，不改成原生 daemon stop/restart。当前官方 app-server 源码与 daemon README 确认 SIGHUP 是 graceful-only；stop/restart 在等待窗口后可能强制结束，不能替换本项目的默认正常退出流程。

Claude CLI 的 `--permission-prompts` 自 2.1.259 才提供，而官方当前默认就是 `host`。本机和远端共用原生默认，删除仅特判 2.1.168 的版本探测和冗余参数，保留 `--permission-prompt-tool stdio`、原权限模式与审批控制请求。无需为每次创建/恢复额外启动版本查询。官方依据：[CLI reference](https://code.claude.com/docs/en/cli-reference)。

Claude Windows 会话占用记录按已知 `procStart` 字符串及进程创建 ticks 核验，不再只认可 2.1.168。PID 消失或被复用才允许继续；同一活进程仍阻止双写。包含 `pidDomain` / `procStartFt` 的其他格式、无法查询的权限与畸形字段仍为未知，不把未知当作空闲。Windows 实机本轮不操作，仅 15 项合成记录/进程测试覆盖版本变化与原保护边界。

## 备选方案

继续补精确发行号最省改动，但每次原生升级都需要网关发版。完全删除所有版本和身份检查会对早期尚未提供契约的运行时发送不适用操作。只依靠只读握手也不能证明退出是否会升级强制，因此保留已有最低契约及实际控制通道核验，不新增后台启动探测实例或持续扫描。

## 验证

同一握手版本替换复现旧门禁拒绝 0.165/0.168；新版接受兼容后续版本并保留过旧、未知来源拒绝。定向测试覆盖网络恢复、账号同步、失败终态释放、进程代次、重复退出与未知回执；Linux 在 Mac 使用合成 /proc/socket/pidfd 夹具，不代表 Linux 实机通过。Windows/Linux 本轮按用户要求不操作。

Claude 本机/SSH/WSL 命令回归先复现多带新参数后，84 项定向测试通过；Mac 已安装 Claude 2.1.292 在隔离空配置中，使用 stdio 审批参数完成真实 initialize、模型目录和设置读取，关闭 stdin 后退出码 0；未发送模型请求、未读取正式账号。

同类扫描结果：DSH 的 bridge revision/插件包格式、Antigravity 的安装来源与哈希、ZCode 的应用身份/协议字段，以及 Claude 文件归档的格式与二进制证据表，保护的是不同契约；本轮未发现足以移除这些检查的证据，保留，不以本次版本兼容为由一并放宽。

## 影响

沿用现有客户端入口和缓存，不增加全局轮询、额外运行时或配置迁移。此前精确版本取舍见 [Windows CLI 排空](../feature/2026-11-11-windows-cli-daemon-drain.md)及 [SSH/WSL 生命周期](../feature/2026-11-11-codex-remote-cli-lifecycle.md)；本记录仅替代其精确版本白名单，原进程归属和持久回执决定保留。
