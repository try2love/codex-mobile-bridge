# Agent Note: 可重建状态读取与不可枚举安装目录不再中断快照和扫描

Status: implemented

## 问题

`Desktop.snapshot()` 对 `address-notifications.json`、`security-notifications.json`、
`cloudflare-status.json`、`network-status.json` 以及每条连接的 `ssh-status-*.json`
使用严格的 `read_json`。掉电后这些文件可能留下长度正确、内容全为 `0x00` 的状态，
`read_json` 只对 `FileNotFoundError` 回退，`JSONDecodeError` 直接冒到
`bridge.app.desktop`，桌面外壳的启动快照失败，网关进程不再被拉起。

`bridge.clients.discovery.inventory_fingerprint` 对版本根目录执行 `iterdir()`，
只捕获 `FileNotFoundError`。Windows 上 `C:\Program Files\WindowsApps` 对普通用户
可 `stat` 但不可枚举，`PermissionError` 冒到
`DesktopSessions.start_enabled`，客户端扫描线程每次启动终止。

## 决策

快照中的可重建状态统一改走既有的 `read_status_json`：损坏、非 UTF-8 或非字典时记录
告警并回退默认值。严格读取器 `read_json` 不改，配置、密钥、通知投递账本继续对损坏
数据报错，避免把不可重建的事实当作可忽略状态。

`inventory_fingerprint` 对版本根目录的枚举失败记为 `None`，对单个路径的
`OSError` 记为 `unreadable`。两种结果都保留在指纹里作为变更证据，扫描继续完成；
这不把不可读目录当作“未安装”，也不改变已保存路径、所有权或更新前置检查。

## 备选方案

- 让 `read_json` 吞掉 `ValueError`：改动面最小，但会同时放过配置与凭据损坏，
  与既有“私有 JSON 原子写入”与账号账本的未知写入保护冲突。
- 在 `snapshot()` 外层整体 try/except：能保住外壳，但单条坏状态会让整个快照退回
  空值，且掩盖具体的损坏文件。
- 在 `inventory_fingerprint` 里跳过不可读路径：扫描不再崩溃，但指纹会在权限变化时
  保持不变，无法作为安装变更证据。

## 影响

快照对损坏状态给出空对象并保留损坏文件本身，不删除、不提升、不重放；通知投递账本
仍由 `AddressNotifications` 自己保存证据快照。指纹新增两种取值形状，读取方按
“有值即证据”比较，不做结构断言。目录权限变化会改变指纹，可能触发一次重新扫描，
属于预期代价。

## 测试

新增 `test_snapshot_survives_zero_filled_rebuildable_state_files`：以 327 字节
全零写入五个状态文件，`Desktop.snapshot()` 仍返回 `urls` 且两个通知状态为空对象。
新增 `test_unreadable_version_root_keeps_metadata_evidence_instead_of_failing_scan`：
在版本根目录注入 `PermissionError`，指纹返回稳定值且包含 `None`，
`inventory_current` 不误判为已变更。

修复前两用例分别以 `JSONDecodeError` 与 `PermissionError` 失败；修复后
`test_access.py`(26)、`test_installation_updates.py`(28，跳过 1)、
`test_client_lifecycle.py`(116)、`test_address_notifications.py`(22)、
`test_desktop_discovery.py`(16，跳过 2)、`test_network.py`(5) 全部通过。
另以真实 `run.py` 网关在 327 字节全零的状态文件上启动，`/api/health` 返回 200。
未在打包 Electron 外壳与 WindowsApps 实机权限下重复验收。
