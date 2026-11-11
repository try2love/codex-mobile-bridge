# Agent Note: WSL 从 current 原生包冷启动

Status: implemented

## 问题

真实 WSL 隔离验收已完成两个 writer 的范围预览及正常释放，原代与 endpoint 均独立确认退出；随后明确继续原 ID 时没有启动新代。只读核对确认合成 PATH 没有 Codex、目标 home 没有 releases 目录，但 current/bin/codex 存在且可执行。首次成功启动的原生回执也明确将该文件报告为 managedCodexPath。旧 WSL 候选发现只查 PATH、用户 bin 与 releases，漏掉本地及 SSH 已接受的 current 路径。

## 决策

只在原候选列表加入所选目标 home 的 packages/app-server-daemon/current/bin/codex，复用既有可执行文件检查、原生 start --help 能力检查与 start 调用。原候选优先级、目标 CODEX_HOME/cwd、其它 home 阻挡、独立 executor 检查以及只有明确动作才能启动的边界不变。不新增包布局，不要求测试夹具补造 releases。

## 备选方案

给夹具额外创建 releases 可以避开失败，但会隐藏已由 native 回执证明的 current 包合同。新增发行版配置或另写启动器没有必要，现有发现和执行路径已具备所需检查。

## 验证

定向回归使用合成 current 可执行文件、空 PATH 发现结果和不存在的 releases；原实现抛出无已安装 runtime，加入候选后通过，并断言原生 help/start 参数、原目标 CODEX_HOME/cwd 与 check=True。连同既有启动边界共 5 项通过，所有进程调用均 mock。

首次实机的范围预览与原代正常退出结果保留；当次冷启动调用的精确 stderr 未被原 helper 保留，不能把它描述为已证明的 native start 报错。本次只读候选清单为空足以定位发现缺口。修后同 ID 冷恢复与正常收尾仍由后续真实 adapter 补测确认，本 note 不将合成回归称为实机恢复通过。

## 影响

所选 WSL home 只有有效 current 原生包时，明确冷连接也能经过原检查启动。被动历史、列表、通知不会因此启动服务；不继承 Windows 或本机默认账号与代理。

## 相关记录审计

- [WSL Codex 统一入口](../architecture/2026-10-24-codex-wsl-unified.md)：保留原入口和显式启动合同，补齐已支持原生包的候选发现。
