# 平台实现

这里存放 Windows、macOS、Linux 的原生操作，以及 macOS/Linux 共用的 POSIX 实现。客户端、账号、会话和权限规则分别由 `bridge/clients/`、`bridge/features/` 维护。

| 目录 | 可在本平台内调整的内容 |
| --- | --- |
| `windows/` | Windows 安装路径、当前桌面会话进程、窗口退出、命名管道、ConPTY、Claude C# 原生连接助手 |
| `macos/` | 应用包识别、当前用户进程、Launch Services、Claude 活跃 profile 发现及 Swift 原生连接助手 |
| `linux/` | 安装路径、`.desktop` 入口、启动器识别、当前用户 `/proc` 进程信息 |
| `posix/` | Unix socket、POSIX 进程启动与信号、PTY；其中部分终端方法同时被 Windows 实现复用 |

`desktop(platform)`、`discovery(platform)` 按目标系统加载实现。可在 Windows 机器上修改 Windows 原生代码，不必同步改动其他系统的实现；涉及共用参数、返回值或业务规则时，仍需检查调用方与其他平台。

## 共同边界

- 平台模块不反向导入 `bridge.clients` 或 `bridge.features`，不自行决定是否启用客户端、切换账号、重启应用或批准任务。
- `clients/desktop_app.py` 和 `clients/lifecycle.py` 保留精确应用身份匹配、操作前重新核验、GUI/运行时区分与退出等待。平台操作接收已选择的路径、PID 和环境变量。
- 发现客户端只检查安装元数据，不执行启动脚本、不读取账号密钥、不创建新的用户 profile。发现、已启动、已连接、上游认证成功是不同状态。
- IPC 平台模块只提供字节流。消息格式、请求编号、桌面 owner 和会话路由属于 Codex 客户端层。
- 终端所属用户/会话、重复执行防护和容量限制由终端功能层协调。不要按系统复制这些规则。

## 当前 Linux 范围

Linux 已有安装发现、同用户进程控制，以及共享 POSIX IPC/PTY 实现。扫描支持现有安装目录、PATH 和名称匹配的 `.desktop` 入口；可识别指向同目录 GUI 的 shell 启动器，但不会执行它。无执行权限的文件、未知脚本和缺少桌面包证据的同名 `codex`/`claude` CLI 不会作为 GUI 接入。

扫描到 Claude 不代表支持自动连接：当前 `clients/claude/setup.py` 仅在 macOS/Windows 报告 `native-console`；Linux 报告 `automaticConnection=unavailable`、`setupState=unsupported`，没有 Linux 原生 Console 助手。不要用其他平台实现的回退分支宣称 Linux 已支持该能力。

DeepSeek Harness 的连接器和配置逻辑仍由客户端层共享。是否已安装、是否存在原桌面 profile、是否需要重启及是否已完成认证，必须分别从实际状态判断。新增安装形式或版本需要相应发现/协议证据，目录拆分本身不增加兼容范围。

## 验证与资源

`tests/test_platform_architecture.py` 检查依赖方向、平台选择和退出前核验；`test_client_inventory.py` 检查三平台扫描范围、批量查询次数及新鲜状态。客户端发现、启动和 IPC 分别由相应测试模块覆盖。

修改 `posix/terminal.py` 时同时检查 SSH 源码装载；该文件会被发送到远端 Python。新增相对导入或移动文件时需同步检查 `bridge/resources.py`、`scripts/gateway-resources.json` 和打包入口。原生助手路径与编译脚本也必须一致。模拟测试通过不能替代对应系统上的原生集成验证。
