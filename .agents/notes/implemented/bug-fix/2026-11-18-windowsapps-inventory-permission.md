# Agent Note: 受保护 WindowsApps 目录的安装指纹降级

Status: implemented

## 问题

Windows Store 客户端的安装指纹需要读取版本父目录的一层成员名，但普通网关用户可以读取具体安装文件的元数据，却不能枚举 `C:\Program Files\WindowsApps`。`inventory_fingerprint` 将该 `PermissionError` 冒泡到客户端扫描线程，导致 Windows 网关扫描失败。

## 决策

安装指纹在版本根目录无法枚举时记录稳定的不可用标记并继续返回。该函数只用于安装清单缓存失效判断；Windows 的 package registration fingerprint 继续负责检测 Store 包版本变化。可访问目录仍保留成员名指纹，保证快速并排更新仍能失效缓存。扫描不修改目录权限、不读取凭据、不绕过 Windows ACL。

## 备选方案

直接删除目录成员指纹会失去时间戳相同的并排更新检测；提升进程权限会扩大网关权限并改变安装边界；让异常继续冒泡会让受保护系统目录阻断全部客户端扫描，因此均不采用。

## 影响

Windows Store 客户端在无法枚举 WindowsApps 时可以继续完成扫描；该受保护目录的成员变化依赖 Windows 包注册指纹检测。其他可访问安装目录的精确成员指纹行为保持不变。

## 验证

新增回归模拟 `iterdir()` 返回 `PermissionError(5)`，确认清单刷新不再抛异常，并在不可访问状态下保持指纹稳定。相关安装发现和客户端扫描回归继续运行。

安装更新回归 28 项、桌面发现回归 16 项、客户端生命周期回归 160 项均通过；新增夹具还验证目录权限恢复后会重新记录成员指纹。模块边界检查通过，保留 22 条既有例外。当前宿主为 macOS，Windows ACL 通过合成 `PermissionError(5)` 复现，尚未在 Windows 主机重新构建或实机扫描。
