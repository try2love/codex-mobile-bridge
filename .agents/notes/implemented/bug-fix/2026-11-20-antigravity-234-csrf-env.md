# Agent Note: 兼容 Antigravity 2.34.0+ 环境变量 CSRF 令牌发现

Status: implemented

## 问题

Antigravity 升级至 `2.34.0` 后（桌面端 `language_server` 与官方 CLI `agy`），安全加固将原生服务 HTTP CSRF 令牌从命令行参数 `--csrf_token` 移至同用户进程环境变量 `ANTIGRAVITY_SERVER_CSRF_TOKEN`（同时 `--host_bridge_token` 改为从 stdin 读取）。`bridge/platforms/antigravity.py` 的 `endpoint()` 仅检查 `_flag(args, '--csrf_token')`，导致升级 `2.34.0` 后原生服务发现始终失败并退化为仅历史可读（`history-only`）。

## 决策

- **兼容新旧版本**：在 `bridge/platforms/antigravity.py` 新增 `_csrf_token(pid, args)`，优先读取 `--csrf_token` 命令行参数以保持 `<= 2.33.0` 零改动兼容；缺失时按平台有界读取同用户当前进程的 `ANTIGRAVITY_SERVER_CSRF_TOKEN` 环境变量：
  - **Linux**：复用 `_proc_env(pid, keys=(b'ANTIGRAVITY_SERVER_CSRF_TOKEN',))`（含同 UID、非僵尸、64 KiB 上界与前后 `stat` 代次防重用校验），随后继续由 `endpoint()` 对端口归属与 `_identity(pid) == before` 复核；
  - **macOS**：复用 `bridge/platforms/macos/codex_daemon_env._environment(pid, ('ANTIGRAVITY_SERVER_CSRF_TOKEN',))` 经由 `sysctl KERN_PROCARGS2` 读取（仅源码与单测模拟验证）；
  - **Windows**：不引入跨进程无特权内存/PEB 读取，保持 fail-closed 仅支持命令行 `--csrf_token` 路径。
- 延续原有 `[A-Za-z0-9_-]{16,256}` 令牌格式校检与失败关闭语义，不在日志或错误消息中暴露令牌内容。

## 验证

- 本机 Linux 实机验证（Ubuntu GNOME Wayland + Antigravity AppImage `2.34.0`，LS `PID 476323`）：只读读取 `ANTIGRAVITY_SERVER_CSRF_TOKEN` 并发起 `GetUserStatus`（HTTP 200）、`adapter.status()`（`connected: True` / `原生服务已连接`）与 `desktop.py desktop-sessions clients` 全部恢复。
- `tests/test_antigravity_discovery.py` 新增 `test_endpoint_reads_env_csrf_token_for_antigravity_2_34_on_linux_and_macos`，覆盖 Linux `/proc/<pid>/environ` 读取、非法/短令牌拒绝以及 macOS `sysctl` 回退路径；并通过 `scripts/check-boundaries.py` 与全量 `test_antigravity_*.py`、`test_linux_*.py`、`test_platform_architecture.py`、`test_module_boundaries.py`、`test_desktop_discovery.py`。
