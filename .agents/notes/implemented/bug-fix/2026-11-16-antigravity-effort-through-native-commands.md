# Agent Note: Antigravity 原生指令与所有发送路径保留所选思考强度

Status: implemented

## 问题

模型分组与原生指令两项功能独立修改同一发送流程。集成后，创建时的 StartCascade 已带 effort，但共享 `_send_prompt_or_items()` 和命令目录读取只传 model，导致首条消息、普通发送、Skill、计划、目标和压缩可能使用默认强度。显式分组模型在旧命令解析中找不到扁平条目时，还会回退默认原生模型。

## 决策

适配器在命令发现、结构化消息构造、忙碌队列检查和共享发送入口中传递同一 effort。命令目录对显式模型使用与发送相同的 `_model()` 校验与原生枚举映射；显式提供不支持的强度或不可解析模型时拒绝操作，不回退默认。未显式选择模型的默认目录读取、分组模型未提供 effort 时的原有默认映射、旧扁平变体 ID 和手填 MODEL_* 保持原有路径。共享前端的原生命令与 Skill 缓存键增加 effort，保留旧作用域缓存，但不能接收新作用域的迟到回执。

## 备选方案

只修普通发送的 effort 参数可以减少改动，却仍使创建首条消息、Skill 和工作模式使用默认强度，因此同一参数必须沿现有共享入口贯通。另行复制一套结构化发送实现会分裂已有能力校验，因此继续复用原来的发送与命令组件。

## 验证

- `python3 -B scripts/test-backend.py --source-only -p test_antigravity_model_effort.py`：5 项通过，覆盖普通、创建、计划、目标、压缩、Skill 的原生枚举，以及旧扁平 ID、手填枚举、合法默认映射和错误强度在 RPC 前拒绝。
- `TZ=Asia/Macau node --test --test-name-pattern='native command cache separates|native Skill commands never' tests/desktop-sessions-ui.test.cjs`：7 项通过，包括 effort 改变后缓存隔离与迟到 Skill 回执拒绝。
- 原适配器测试发现旧用例仍调用已合并移除的 `_save_session_model()`，已改为共用 `_update_session_settings()`；Hook socket 用例在受限环境中因 bind 权限失败，不能计为产品回归通过。

## 影响

不改变原生模型目录的账号作用域、共享模型弹窗与手填协议；保留原有默认映射，仅保证显式选择的强度沿所有路径传递。真实原生写入和设备验收未执行。

相关决定分类：模型分组与原生指令两篇均为部分重叠，本篇修复二者集成后的参数契约，保留原决定并互链；其余 Antigravity 启停笔记与本次参数链无关，现有 proposed 中无同机制提案。

- [模型分组与强度选择](../feature/2026-11-16-antigravity-grouped-models-and-effort.md)
- [原生指令与 Skill](../feature/2026-11-14-antigravity-native-slash-commands.md)
