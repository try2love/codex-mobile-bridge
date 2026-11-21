# Agent Note: Windows 配置读取、更新缓存与卸载边界

Status: implemented

## 问题

Windows 网关曾在创建 `DesktopSessions` 时读取 `desktop-sessions/settings.json` 失败并终止启动。该 JSON 是 Bridge 自己的客户端清单、启用状态和发现偏好，不是 Codex 会话数据库或 Agent 桌面状态。以同一更新周期的缓存包重试时，Windows/macOS 控制器在新应用确认前删除了已校验归档；若 helper 已开始交接、随后升级回滚，用户再次更新会重新下载相同包。卸载清理还必须避开 `%APPDATA%\Codex` 等其他产品目录。

## 决策

- Windows 上 `replace_session_file` 委托给 `local_service.write_private`，通过当前用户 SID 的受保护安全描述符创建临时文件并原子替换。这覆盖默认客户端设置、独立客户端设置及接入登记等共用此入口的 Bridge 文件。若旧 `settings.json` 可见但不可读，管理器切换到同目录的 `settings.user.json` 继续扫描和保存；sidecar 一旦存在就保持权威，不尝试夺取所有权或放宽旧 ACL。若 sidecar 也不可读，才进入只读降级并拒绝客户端设置写入。更新备份同时保存两条路径，跳过无法读取的旧主文件；返回不识别 sidecar 的稳定版时，在备份成功后原子地把有效 sidecar 写回 `settings.json`。主文件不可读且无 sidecar 时，回退在替换应用前停止，避免旧版启动崩溃或丢失客户端选择。
- 更新器只把完整下载且匹配签名清单中 SHA-256 与长度的包放入稳定缓存。每次重试重新验证缓存；更新交接期间启动清理保留当前版本归档，只有新应用在完成启动检查并写入确认后才清理验证包。临时下载目录仍在进程重启时清理。
- Windows 卸载提供默认不选中的数据清理段，只允许删除 `%APPDATA%\codex-mobile-bridge-desktop`。递归预检遇到重解析点、无法核验的项目、`claude-accounts` 目录或网关运行控制记录时保留整个数据目录；清理前再由用户确认。自动更新跳过清理段，不触碰 `%APPDATA%\Codex`。
- 8bbf5157 增加的安装版本根目录枚举已有独立降级：普通用户不能枚举受保护 WindowsApps 时保留稳定的 `None` 成员指纹并继续扫描，Store 注册指纹仍负责版本变化。Windows 累积日志中两次 `WinError 5` 的栈位于 11 月 18 日，调用 `inventory_fingerprint -> Path.iterdir`；后续提交 22f85ee9 已捕获不可读路径，且该提交包含在当前 v2.2.2-preview.2 tag。见[受保护 WindowsApps 指纹记录](2026-11-18-windowsapps-inventory-permission.md)。

## 备选方案

为恢复扫描而递归放宽旧目录权限或以管理员身份运行网关会扩大权限并改变用户目录安全边界；删除旧配置会丢失客户端选择，因此都不采用。启动时清除所有已验证下载会让升级回滚后重试再次下载。卸载时删除 `Roaming\Codex` 会危及 Codex 本体及其本地会话，因此清理目标固定为 Bridge 自有 userData 目录。

## 现场证据与限制

隔离实机检查区分了已有文件权限状态、历史读取失败和不同完整性级别令牌的可读性。当前状态不能反推历史失败时的完整 ACL；源码与令牌探针不替代新包重启和升级验收。WindowsApps 枚举与客户端设置读取是不同故障路径，分别验证。

当前 HKCU 卸载项显示 `Codex Mobile Bridge 2.2.2-preview.2`，卸载命令指向 `F:\Codex Mobile Bridge\Uninstall Codex Mobile Bridge.exe`。`Roaming\Codex` 与 Bridge userData 两个目录均存在；检查时 Bridge 的 `updates` 目录为空。该卸载项没有证据显示其安装或卸载路径把 Bridge 数据导向 Codex 目录。Geek Uninstaller 的遗留文件判定不等于 NSIS 卸载器目标。

## 影响

Windows 网关遇到不可读的旧客户端设置时仍可启动和扫描。可读 sidecar 成为当前版本的权威配置；回退时有效 sidecar 会恢复到旧版约定的主文件路径，并使用当前用户安全 ACL。若缺少可读配置副本，更新会在替换应用前停止，而不是让旧版带着必崩设置启动。确认更新成功后清理已验证旧包，失败交接保留可校验缓存供重试。Windows 卸载默认保留数据，用户显式勾选后只清理 Bridge 自有 userData 根目录。

## 验证

- `node --test tests/updater.test.cjs tests/update-handoff.test.cjs tests/update-controller.test.cjs tests/update-smoke-contract.test.cjs tests/uninstaller-scope.test.cjs`：63 项中 62 项通过、1 项 Windows 专属 WinError 32 用例在 macOS 跳过。Mac ARM64 与 Windows x64 分别验证安装失败后复用签名校验缓存；同时覆盖篡改缓存重下、交接未确认时保留包、启动清理和确认后清理。
- 沙箱外 `npm run test:desktop`：1765 项中 1764 项通过、1 项平台专属用例跳过，无失败、取消或待办。
- Mac 定向 Python 回归：99 项中 94 项通过、5 项平台专属用例跳过。范围为安装发现、私有文件写入、离线客户端管理、升级数据保留、本机 RPC、独立实例、网关停止及账号文件替换。
- 通过已固定主机密钥的 SSH，在 Windows 用户专属 `.tmp/cmb-validation-20261121-0a8ca9111e` 中运行本次源码及合成数据：35 项中 34 项通过、1 项因该 SSH 登录没有 linked standard token 跳过。覆盖真实 Windows 文件 ACL、命名管道、原子写入中断、旧设置拒读后的 sidecar 管理、稳定版回退和备份边界。
- 独立 Windows 探针核验当前用户的 Explorer 映像、SID、非零桌面会话和非提升令牌。提升进程通过生产 `replace_session_file` 写入合成配置，文件 owner 和受保护 DACL 通过 `_check`，再模拟该普通令牌读取原字节成功；结果为 `writerElevated=true`、`readerElevated=false`、`readerSameUser=true`、`readerSession=1`、`standardRead=PASS`。该探针不改真实配置、不重启客户端。
- Windows 源码隔离验证补齐 `relay` 依赖；管理 stdio 夹具显式按生产入口的 UTF-8 协议解码。没有 linked token 的 Windows 登录只跳过该特定前提，不将其他令牌错误改为跳过。
- `python3 -B scripts/check-boundaries.py`：通过，保留 22 条既有边界例外；`git diff --check` 通过。
- 决策笔记格式检查通过（283 篇）；全树链接检查仍报告既有 `2026-11-12-chat-media-playback.md` 中的“本机路径”示例链接不存在，本次没有修改该无关笔记。
- 使用现有 electron-builder 的固定 NSIS 编译器，在 `.tmp/windows-nsis-check-20261121` 编译实际 `desktop/installer.nsh`，生成含卸载组件页和默认可选清理段的 Unicode 卸载器。编译夹具的安装和卸载入口均无条件中止，从未运行删除操作。此结果证明宏可编译，不等于完整 Windows 应用包或交互卸载已验收。
- 最近保存的沙箱外后端全量为 4375 项，2 项失败、2 项错误、93 项跳过。其中网关停止夹具的模拟范围已收窄，定向复测通过；两个 Claude first-connection 失败与缺少可选 `cryptography` 的导入错误均在 `efbb1ef0` 隔离基线中复现。未把该次全量报告标为全绿。
- 尚未在 Windows 上重新打包、安装或用新包重启真实网关；源码与普通令牌探针不替代打包后的端到端验收。Geek Uninstaller 的遗留路径推断原因仍无直接证据。
