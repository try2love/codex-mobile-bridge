# Agent Note: macOS 更新后 Claude 辅助功能仍归属旧安装

Status: implemented

## 问题

macOS 正式 2.2.0 已在系统设置授予网关 App 辅助功能权限，Claude 准备页仍返回 `needs-permission`。用户报告此前版本没有这一问题。通过当前网关既有管理接口执行 `requestPermission:false`，实际 helper 同样返回未授权，因此不能只归因于准备页缓存或授权后没有自动刷新。

本记录保留已确认的直接原因与代码历史。修复只改变 macOS 更新启动交接，不修改系统授权数据库。Windows [issue #155](https://github.com/try2love/codex-mobile-bridge/issues/155) 的 `staging.pending` 更新反馈属于另一问题，不能套用本篇的 macOS TCC 根因。

## 决策

新版 macOS GUI 在打开窗口、单例锁和网关 worker 前，校验既有更新事务，再用 `process.execve` 将旧助手监控的 PID 替换为原生启动监督器。监督器通过 Launch Services 创建实际 GUI，核验应用包路径并记录新 GUI PID。新 GUI 的交接同时核对原监督 PID、实际 GUI PID、安装、版本、数据目录和 token。GUI 启动原先运行的网关，确认其 `runtime.running` 后才写原有 ACK；原先停止的网关保持停止。旧助手随后发起的 start 是同实例的幂等检查。

保留原 PID 是为兼容旧版更新助手发起的第一次升级。失败回滚时，监督器只向这一实际 GUI 提交正常退出，确认它已退出后才结束，让旧助手继续恢复备份。GUI 拒绝退出时继续等待，不强制关闭、不提前覆盖程序。更新完成后退出监督器；后续更新清除已失效的启动标记。Windows 和 Linux 的启动协议不变。

系统启动需要 `createsNewApplicationInstance`，否则已 execve 但仍登记的旧 PID 会被错误复用；监督器须推进主 RunLoop，才能刷新 `NSRunningApplication.isTerminated`。两项均由真实 Electron 隔离失败复现后修正，不能用进程列表或静态自检替代。

## 已确认的直接原因

2026-11-10 两次独立只读核验得到一致结果：TCC 正在核验旧备份 App 的代码身份，而非当前已获授权的正式 App 身份。

- 实际运行 helper 的 `binary_path` 是 `/Applications/Codex Mobile Bridge.app/Contents/Resources/gateway/_internal/client-helpers/claude-bridge-helper`。
- 同一请求的 `responsible_path` 却是 `/Applications/.cmb-update-lf93f0gz/previous/Contents/MacOS/Codex Mobile Bridge`，即升级留下的 2.2.0-preview.3 GUI。
- TCC subject 仍是正式 bundle ID `io.github.try2love.codexmobilebridge`。现有授权要求的 CDHash 为正式 2.2.0 的 `6dde44c167b6554d40f9e9e13ea72248539d010a`；实际被比较的旧 preview.3 身份为 `d01cd1f48236c0de5f7d5b365faabe325237230f`。
- 日志明确记录 `SecStaticCodeCheckValidity` 返回 `-67050`，随后为 `Failed to match existing code requirement`，服务为 `kTCCServiceAccessibility`。

因此，“当前 App 已授权”与“当前 helper 实际请求被拒绝”并不矛盾：授权记录匹配当前正式 App，运行链的责任身份却仍指向旧安装。只看进程当前路径、父 PID 已为 1、App 显示名称或设置页勾选状态，都不能替代 TCC 的实际归属核验。

本地证据位于 `.tmp/claude-accessibility-20261110/tcc-filtered.log`、同目录的 `tcc-key-evidence.log`，以及 `.local/desktop-update.log`。这些文件保持忽略，不把日志、账号、令牌、私钥或聊天数据复制进笔记或提交。

## 提交追溯与置信边界

日期均为 UTC+08:00。正式发布源码以 `76acf47b91ab3ae41f57fc7a8664da1c9224d206` 为准；本地旧 `v2.2.0` 标签仍解引用为 `c797f6ffd028d206401c0c2ea78d6382075e3184`，不能用它代替正式发布提交做比较。旧 preview.3 对照为 `064834540a1960150887d3d4368e306ec87603bb`。

| 提交 | 已核实的改动 | 可据此得出的结论 |
| --- | --- | --- |
| `f73dca2a1ccad2aa4a0075e47d14a723742e1f29`，2026-10-02 02:12:54 | 首次加入应用内更新链：从旧网关复制更新 helper，将旧 App 移至 `previous`，由 helper 直接 `Popen` 新 GUI，并直接执行新网关 `start` 恢复运行 | 与当前旧责任身份相符的启动链最早在此引入，不是 2.2.0 才新增的 `Popen` |
| `4e281ac011e98b9834c869a6dfe557287b422f87`，2026-10-09 03:12:28 | 加入 Claude 原生 AX helper 及其 ad-hoc 构建签名 | 更新链与 Claude 辅助功能依赖从此叠加；不能据此证明当时已经出现本次故障 |
| `311852ce2c0da01b768f3aaa230c829ef46d44a6`，2026-11-08 16:13:47 | 正式包新增 `afterSign`，重签网关 broker、ZCode 启动器和外层 App；并不重签 Claude helper | 是正式 2.2.0 的签名流程变化，但不是已单独确证的致因提交 |

在正式 `76acf47b` 中，`bridge/features/updates/gateway.py:259–268` 复制旧 helper 并记录 `previous`；`:302–321` 直接启动新 GUI 和新网关；`:458–468` 完成目录替换后执行这两次启动。原始 `f73dca2a` 的对应代码位于当时的 `bridge/updater.py:157–166,207–217,302–312`。

精确比较 preview.3 `06483454` 到正式 `76acf47b`，`bridge/features/updates/gateway.py`、`bridge/clients/claude/setup.py`、`bridge/platforms/macos/claude-helper.swift`、`scripts/build-client-helpers.py`、`desktop/shared/controller.cjs` 五个文件全部无差异。较早的 2.1.7 源码 `d65a610ab7ce799325d284b5d982476e14b788e3` 到正式 2.2.0，更新器与 Claude Swift 也无差异；Claude setup 的改动仅涉及 Windows profile，helper 构建命令只是扩展为 Claude/ZCode 循环。

**确认范围**是当前实际拒绝由旧责任身份与新授权要求不匹配造成，且相关更新启动链可追溯至 `f73dca2a`。**未确认范围**是为何此前升级未触发、此次升级首次被用户观察到的具体条件，以及重启或未来修复后的真实结果。不能将首次观察时间等同于首次代码引入时间，也不能仅因 `311852ce` 接近正式发布时间就将其写成已经证实的唯一回归提交。

## 既有记录核对

已检索 proposed、implemented 中的 Claude、辅助功能、TCC、更新交接与 macOS 签名记录；当前没有 rejected 目录或相同主题提案。

- [桌面更新交接范围](../../implemented/bug-fix/2026-11-03-desktop-update-handoff-scope.md)部分重叠，保留并在本篇关联：它约束目标安装与更新环境变量，不证明 TCC 责任身份已切换。
- [正式包 ZCode 发布身份](../../implemented/architecture/2026-11-07-zcode-production-local-identity.md)部分重叠，保留并在本篇关联：它证明组件来源与私有 peer 身份，不代替 Claude 辅助功能验收。
- [ZCode 私有启动器打包](../../implemented/architecture/2026-11-03-zcode-private-launcher-packaging.md)仅涉及相关签名拓扑，保留原决定；[POSIX 父进程退出](../../implemented/bug-fix/2026-11-10-desktop-update-parent-exit.md)和 [Windows 安装占用预检](../../implemented/bug-fix/2026-11-10-windows-update-zcode-holder-preflight.md)处理不同更新失败，不由本提案取代。
- Claude 控制台回读与签名回执就绪时机处理连接建立后的不同阶段；Android 更新和手动钥匙串访问也不解释此处的 TCC 责任身份，保留现有记录，不合并根因。

## 备选方案

仅刷新准备页能消除界面陈旧，改动小；但当前管理接口和实际 AX 检查均返回未授权，刷新不能修复已观测的 TCC 身份错配。界面状态刷新应与底层问题分开验证。

移除后重新添加当前 App 的授权通常可以重建其权限记录；本机当前记录已经对应正式 App 的 CDHash，实际责任身份却来自旧备份。因此不能循环让用户重授权，不能把授权独立 helper 或扩大系统权限作为未查明归属时的默认恢复办法。

只调整新包签名可以改善未来身份管理，但当前更新 helper 来自旧安装，单改新包不等于能修复旧启动链负责执行的首次升级。签名调整与启动交接需分别建立因果证据，不能以 bundle ID 相同或签名完整性通过替代授权匹配。

## 验证

第三轮隔离打包 Electron 使用真实 `execve`、NSWorkspace、生产 Claude helper 无提示权限检查，证明新 GUI PID 与监督 PID 不同。TCC 日志的责任主体对应新 GUI 的当前安装路径。正常退出被拒绝时监督器继续运行；允许正常退出后监督器退出码为零。隔离 App 未获系统授权，其 `needs-permission` 是预期结果，不能据此声称授权能力已通过。

定向 Node 检查 40 项通过、1 项平台跳过；Python 更新/资源/架构检查 39 项通过、1 项沙箱跳过。覆盖旧 helper 交接契约、原运行/停止网关、失效回执拒绝、错误 PID/token、打包资源及回滚约束。生产 App 完整升级与回滚、原 Test 已授权状态的核验仍待冻结构建完成；不把这份隔离证据当作全部完成。

本轮证据存于 `.tmp/claude-update-20261114/`。2026-11-14 修改前，运行中的 .5 Test 经其实际网关执行无提示权限检查为 ready，保留为交付前后对照。正式版不在本次停止或替换范围内。

## 影响

真实升级和重启会改变当前运行状态，隔离夹具不能自动授权操作用户正式安装。当前仍保留此前升级首次触发条件的不确定性。新签名身份若需要系统重新授权，应由用户正常处理；修复权限归属不等于能绕过授权。新增的原生监督器增加一个打包组件及短期更新进程，作为兼容旧更新助手并保持安全回滚的代价。
