# Linux 平台维护

- 本目录负责安装路径、PATH、`.desktop` 入口、shell 启动器对应 GUI 的识别，以及同用户 `/proc` 进程与命令行读取。启动/信号、Unix socket 和 PTY 复用 `../posix/`。
- Linux 专项问题优先在本目录修复。不得反向导入 `bridge.clients` 或 `bridge.features`；账号、客户端启用、任务状态、连接协议和重启决策留在共享业务层。
- 扫描不得执行 `.desktop` 命令或 shell 启动器。保留可执行权限检查，以及同名 `codex`/`claude` CLI 与桌面包的区别；不能将任意可执行文件当成已认证的客户端。
- `/proc` 只读取当前用户的候选进程。展示阶段无法读取命令时保持“未知”，不能推断为空闲；控制操作的身份核验仍由共享入口完成。
- 当前 Claude 自动连接仅提供 macOS/Windows 原生助手。Linux 能发现安装，但 `inspect_installation` 报告 `automaticConnection=unavailable` 和 `setupState=unsupported`。不得把安装发现描述为 Claude 自动接入完成，也不能调用 macOS 助手作为 Linux 回退。
- Codex、DSH 的安装包形式、运行时和桌面 profile 必须以当前发现结果为准；不要新增未验证的发行版、AppImage 或上游版本支持声明。
- 路径测试先规范化临时目录，避免 macOS `/var` 与 `/private/var` 别名造成跨平台 fixture 误判。Linux 权限、符号链接及进程测试不能由其他系统的模拟检查替代。
