# Agent Note: 私有 JSON 原子写入不被历史临时文件锁死

Status: implemented

## 问题

`bridge.app.lifecycle.private_json` 使用目标去掉扩展名后的固定 `.tmp` 路径，以 `O_EXCL` 创建。写入被进程中断或替换失败会遗留临时文件，之后保存反复报 `FileExistsError`，即使目标 JSON 仍完整。相同文件名不同扩展名也会争用同一临时路径。

## 决策

写入以 `tempfile.mkstemp` 在目标所在目录创建包含完整目标文件名的唯一 `.tmp` 文件，创建权限为 0600，沿用 UTF-8 JSON 序列化。在关闭文件并原子替换目标前，先 `flush` 用户态缓冲并 `fsync` 文件数据。序列化、同步、替换或可捕获的中断失败时，`finally` 只删除本次创建的临时文件；替换前的失败保留原目标。

不搜索、提升或删除任何历史 `.tmp`，也不推断其他写入者临时文件是否过期。进程直接退出留下的私有临时文件是未提交证据，下一次写入使用独立名称，不能阻断保存。调用者原有目录创建、业务锁与数据校验契约保持不变。

## 备选方案

- 删除固定旧 `.tmp` 后重试：保留原命名并可恢复单次保存，但无法可靠判断它是否属于仍在写入的其他调用者，也会丢弃故障证据。
- 复用通知模块 `write_json`：已有唯一同目录名称，但先普通创建写入、再 chmod，不能满足凭据从创建起私有，也未提供失败清理及数据同步；故在现有生命周期私有写入边界使用标准库安全创建。
- 自动将历史 `.tmp` 提升为目标：能恢复部分已写完整数据，但无法判断是否是最后确认提交的内容，不符合账号和操作账本的未知写入保护。

## 影响

成功替换仍是原来的同目录原子安装，读取者不会看到部分 JSON；每次写入增加文件数据同步开销。这里不新增跨进程串行化，竞争写入仍由既有调用者的锁与事务处理。文件 `fsync` 不等于跨平台保证目录项在突然断电后持久化，不作此承诺；硬中断无法执行清理，历史文件保留供诊断。Windows 私有访问仍依赖原数据目录 ACL。

历史笔记审计：[CLI 释放回执](2026-11-10-cli-release-and-safe-recovery.md)决定调用者创建目录和未知结果不重放，本次不改变该决定；共享运行时数据删除与 ZCode 共享能力笔记的私有写入要求继续成立；跨平台 CI 与 Android 菜单笔记不决定本函数临时文件策略。

## 测试

修复前新增回归复现残留导致连续 `FileExistsError`、替换及序列化失败残留、中断残留和缺少同步。修复后 `python3 -B -m unittest tests.test_private_json tests.test_cli_release tests.test_module_boundaries tests.test_pairing.PairingTests.test_local_control_requests_are_private_and_cleaned` 30 项通过，覆盖旧 `.tmp` 不提升、其他写入者文件不删除、创建即私有、flush/fsync 在替换前完成、序列化/同步/替换失败保留目标并清理、`KeyboardInterrupt` 清理、隔离子进程硬中断后仍可保存，以及既有本地配对文件请求/响应清理。未启停真实 App 或 daemon，未验证 Windows 原生文件锁或真实断电。
