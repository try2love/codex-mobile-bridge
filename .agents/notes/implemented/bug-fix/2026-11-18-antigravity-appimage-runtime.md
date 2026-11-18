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
- **保留 `(deleted)` 保守行为与防伪造**：共享 `linux/desktop.py` 默认扫描继续返回原有绝对映像路径（移除内核追加的 ` (deleted)`），包括仍运行但已 unlink 的旧程序；只在 Antigravity 专用 `_candidates()` 显式请求时保留内核标记，以便 `_trusted_appimage_runtime()` 校验临时解包身份。无 FUSE 模式仍要求同用户父进程链直达在盘 `Antigravity.AppImage`。
- **接入 Linux XDG 与启动器发现**：在 `bridge/platforms/linux/discovery.py` 新增 `antigravity` 候选（含 `/usr/share`、`~/Applications`、XDG `.desktop` 与免执行 `#!` 同级 ELF 解析），并在 `bridge/clients/discovery.py` 为 `linux` 接入桌面安装发现。

无 `unsquashfs` 时的 extract-and-run 只在仍可读的临时 APPDIR 下验证 `app.asar` 身份，且原 AppImage 文件身份与同用户父链都匹配时授权。`APPIMAGE` 环境变量不能独立证明已删除的 payload 属于 Antigravity；当临时目录已完全删除、磁盘镜像又无法读取产品元数据时，继续 fail closed，尚不能宣称覆盖该完整删除场景。

## 备选方案

把内核的 ` (deleted)` 标记暴露给所有 Linux 客户端调用方，实现简单，但共享匹配器和进程身份比较可能将仍运行的旧程序拆成不同映像，扩大本次 Antigravity 需求的兼容影响，因此只对 Antigravity 的进程候选显式保留标记。完全沿用旧扫描器而不提供该开关可维持原契约，但无法让 Antigravity 核验已删除临时解包路径，所以保留默认旧语义并提供 opt-in。

## 影响

普通 Linux 扫描继续得到绝对可执行路径，并移除内核追加的删除标记；相对或无效链接仍不进入清单。Antigravity 专用扫描可核验原始 deleted 路径，之后仍须通过 AppImage、payload 与进程来源验证。代价是候选调用处必须显式选择是否保留标记，并由两个路径的合成回归共同锁定；临时 APPDIR 和磁盘镜像证据同时不可用时仍拒绝授权。

## 验证

- 覆盖 `test_antigravity_discovery.py`、`test_desktop_discovery.py`、`test_linux_appimage.py`、`test_antigravity_accounts.py`、`test_antigravity_account_switch.py`、`test_antigravity_adapter.py`、`test_posix_force_exit.py`、`test_client_inventory.py`、架构/边界测试与全量 Linux / ZCode / Antigravity 回归套件，并在本机无 `libfuse2` AppImage（v2.33.0）及 `.local/share/applications/antigravity.desktop` 上通过真实扫描与原生服务连接验证。

本地适配复验：追加正确与伪造 APPIMAGE/APPDIR、篡改或缺失 app.asar，以及默认共享扫描保留 deleted-image 绝对路径、相对链接仍拒绝、Antigravity 专用扫描保留原始内核标记等合成回归；Antigravity/账号/force-exit 定向 75 项通过、Linux AppImage/桌面/架构定向 30 项通过，依赖边界检查通过。测试没有操作真实客户端。完整临时 APPDIR 删除且无元数据解析器时仍拒绝授权。

修复共享扫描回归后，`PYTHONPATH=tests python -B -m unittest -v test_linux test_antigravity_discovery test_posix_force_exit` 通过 43 项（1 项 Linux `/proc` 原生夹具在 macOS 跳过）；`python -B scripts/check-boundaries.py` 通过。未在本机执行 Linux 内核原生夹具。
