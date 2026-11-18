# Agent Note: Linux Antigravity 全形态自动发现与运行时核验

Status: implemented

## 问题

面向不同 Linux 用户环境“开箱即用”时，仅依赖磁盘目录名称或系统预装 `unsquashfs` 存在三类兼容缺口：
1. **无 `unsquashfs` 环境**：用户系统（如默认未装 `squashfs-tools` 的发行版）以 FUSE 挂载运行 `Antigravity.AppImage` 时，因 `_appimage_metadata` 强制调用 `/usr/bin/unsquashfs` 而失败；
2. **解压版（`.tar.gz`）与 Electron `app.asar` 形态**：Antigravity 2.0 Linux 版实际携带 Electron `resources/app.asar`（非 `resources/app/product.json`），且用户手动解压目录名未必精确等于 `antigravity`；
3. **Linux 开箱自动发现缺失**：`bridge/clients/discovery.py` 的桌面扫描仅接通 `darwin` 与 `win32`（`linux` 只检查 `~/.gemini/bin/agy`），未接入 `bridge/platforms/linux/discovery.py` 的 XDG `.desktop` / `~/Applications` / 壳启动器同级 ELF 解析；
4. **`APPIMAGE_EXTRACT_AND_RUN=1` 的 `(deleted)` 链路**：临时解包后被删除导致 `/proc/<pid>/exe` 带 ` (deleted)`，原 `resolve(strict=True)` 直接忽略或抛 `FileNotFoundError`。

## 决策

- **FUSE 免 `unsquashfs` 直读验证**：在 `bridge/platforms/antigravity.py` 新增 `_fuse_installation()` 与 `_read_mounted_desktop()`；当检测到真实 FUSE 挂载与 `appimage.runtime_candidates()` 时，直接读取已挂载只读目录里的 `app.asar`/`.desktop` 核验身份。
- **解包形态统一识别**：新增 `_unpacked_installation()`（兼容 `resources/app/product.json` 与 `resources/app.asar`）及 `_unpacked_root_verified()`，不再绑定文件夹精准命名，同时防止无主程序/无有效 manifest 的伪造路径通过校验。
- **保留 `(deleted)` 保守行为与防伪造**：`bridge/platforms/linux/force_exit.py` 保留 `' (deleted)'` 后缀；`linux/desktop.py` 默认 `_process_rows()` 行为不变；`_trusted_appimage_runtime()` 在无 FUSE 模式下继续要求同用户父进程链直达在盘 `Antigravity.AppImage`。
- **接入 Linux XDG 与启动器发现**：在 `bridge/platforms/linux/discovery.py` 新增 `antigravity` 候选（含 `/usr/share`、`~/Applications`、XDG `.desktop` 与免执行 `#!` 同级 ELF 解析），并在 `bridge/clients/discovery.py` 为 `linux` 接入桌面安装发现。

## 验证

- 覆盖 `test_antigravity_discovery.py`、`test_desktop_discovery.py`、`test_linux_appimage.py`、`test_antigravity_accounts.py`、`test_antigravity_account_switch.py`、`test_antigravity_adapter.py`、`test_posix_force_exit.py`、`test_client_inventory.py`、架构/边界测试与全量 Linux / ZCode / Antigravity 回归套件，并在本机无 `libfuse2` AppImage（v2.33.0）及 `.local/share/applications/antigravity.desktop` 上通过真实扫描与原生服务连接验证。
